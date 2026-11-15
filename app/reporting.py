"""Daily roll-up and report generation.

The rules are those of the original IPPM notebook:
  * per site and hour, take the worst value across its paths;
  * count the hours at or above the threshold (PD 0.5 %, jitter 7 ms, RTT 20 ms);
  * list a site when 3 or more hours breach;
  * pending days = previous report's value + 1 when the site was listed there, else 0;
  * feedback is carried over from the previous report, else "TXOP to Check".
The only change: every source is evaluated on its own data (the notebook reused the
first ZTE file's jitter/RTT results for the other two ZTE files).
"""
from __future__ import annotations

from datetime import date

import psycopg
from psycopg.types.json import Jsonb

from . import db
from .sources import SOURCE_ORDER, category_for

KPIS = ["PD", "JITTER", "RTT"]
KPI_LABELS = {"PD": "Packet Drop", "JITTER": "Jitter", "RTT": "RTT"}
KPI_HOURS_COL = {"PD": "pd_hours", "JITTER": "jitter_hours", "RTT": "rtt_hours"}


def rebuild_daily(conn: psycopg.Connection, report_date: date, source: str | None = None) -> None:
    """Recompute site_daily for one report date (optionally one source) from the hourly data."""
    th = db.get_setting("thresholds", conn)
    params = {"d": report_date, "src": source, "pd": th["pd_pct"], "jit": th["jitter_ms"], "rtt": th["rtt_ms"]}
    conn.execute(
        """DELETE FROM site_daily sd USING sites s
           WHERE sd.site_id = s.id AND sd.report_date = %(d)s
             AND (%(src)s::text IS NULL OR s.source = %(src)s)""",
        params,
    )
    conn.execute(
        """INSERT INTO site_daily (report_date, site_id, samples, path_count, pd_hours, jitter_hours,
                                   rtt_hours, pd_max, pd_avg, rtt_max, rtt_avg, jitter_max, jitter_avg)
           SELECT %(d)s, x.site_id, count(*), max(x.paths),
                  count(*) FILTER (WHERE x.pd >= %(pd)s),
                  count(*) FILTER (WHERE x.jit >= %(jit)s),
                  count(*) FILTER (WHERE x.rtt >= %(rtt)s),
                  max(x.pd), avg(x.pd), max(x.rtt), avg(x.rtt), max(x.jit), avg(x.jit)
           FROM (SELECT p.site_id, h.hour_ts, max(h.pd_pct) AS pd, max(h.jitter_ms) AS jit,
                        max(h.rtt_ms) AS rtt, count(*) AS paths
                 FROM kpi_hourly h
                 JOIN paths p ON p.id = h.path_id
                 JOIN sites s ON s.id = p.site_id
                 WHERE h.report_date = %(d)s AND (%(src)s::text IS NULL OR s.source = %(src)s)
                 GROUP BY p.site_id, h.hour_ts) x
           GROUP BY x.site_id""",
        params,
    )


def resolve_seed_items(conn: psycopg.Connection, report_id: int) -> None:
    """Link items loaded from an old Excel report (name only) to sites in the database."""
    conn.execute(
        """UPDATE report_items ri SET site_id = s.id
           FROM sites s
           WHERE ri.report_id = %s AND ri.site_id IS NULL AND lower(s.name) = lower(btrim(ri.site_name))""",
        (report_id,),
    )
    conn.execute(
        """UPDATE report_items ri SET site_id = n.site_id
           FROM site_names n
           WHERE ri.report_id = %s AND ri.site_id IS NULL AND lower(n.name) = lower(btrim(ri.site_name))""",
        (report_id,),
    )
    # Renamed ZTE sites: match on the ID in brackets when exactly one ZTE site carries it.
    conn.execute(
        """UPDATE report_items ri SET site_id = m.site_id
           FROM (SELECT ext_key, min(id) AS site_id FROM sites
                 WHERE source LIKE 'zte%%' GROUP BY ext_key HAVING count(*) = 1) m
           WHERE ri.report_id = %s AND ri.site_id IS NULL
             AND m.ext_key = substring(ri.site_name from '\\((\\d+)\\)\\s*$')""",
        (report_id,),
    )


def new_sites_sql(alias: str = "s") -> str:
    """SQL condition: site first appeared on %(d)s, and its source had data before that day."""
    return (f"{alias}.first_seen = %(d)s AND EXISTS (SELECT 1 FROM sites o "
            f"WHERE o.source = {alias}.source AND o.first_seen < %(d)s)")


def generate_report(report_date: date, user: dict | None = None) -> dict:
    """Build (or rebuild) the report for one date. Returns the reports row."""
    with db.tx() as conn:
        th = db.get_setting("thresholds", conn)
        default_feedback = db.get_setting("default_feedback", conn)
        min_hours = int(th["min_hours"])
        rebuild_daily(conn, report_date)

        present = [r["source"] for r in conn.execute(
            """SELECT DISTINCT s.source FROM site_daily sd JOIN sites s ON s.id = sd.site_id
               WHERE sd.report_date = %s""", (report_date,))]
        if not present:
            raise ValueError(f"No KPI data has been imported for {report_date:%d %b %Y}.")
        present = [k for k in SOURCE_ORDER if k in present]
        missing = [k for k in SOURCE_ORDER if k not in present]

        flagged = conn.execute(
            """SELECT sd.site_id, s.name, sd.pd_hours, sd.jitter_hours, sd.rtt_hours
               FROM site_daily sd JOIN sites s ON s.id = sd.site_id
               WHERE sd.report_date = %(d)s
                 AND (sd.pd_hours >= %(m)s OR sd.jitter_hours >= %(m)s OR sd.rtt_hours >= %(m)s)""",
            {"d": report_date, "m": min_hours}).fetchall()

        # Previous report: carries pending days and feedback forward.
        prev = conn.execute(
            "SELECT id FROM reports WHERE report_date < %s ORDER BY report_date DESC LIMIT 1",
            (report_date,)).fetchone()
        prev_by_site: dict[tuple[str, int], dict] = {}
        if prev:
            resolve_seed_items(conn, prev["id"])
            for r in conn.execute(
                    "SELECT kpi, site_id, pending_days, feedback FROM report_items "
                    "WHERE report_id = %s AND site_id IS NOT NULL", (prev["id"],)):
                prev_by_site[(r["kpi"], r["site_id"])] = r

        # Feedback typed into this date's report survives a regenerate.
        edited: dict[tuple[str, int], dict] = {}
        old = conn.execute("SELECT id FROM reports WHERE report_date = %s", (report_date,)).fetchone()
        if old:
            for r in conn.execute(
                    """SELECT kpi, site_id, feedback, feedback_edited_at, feedback_edited_by
                       FROM report_items WHERE report_id = %s AND feedback_edited_at IS NOT NULL
                         AND site_id IS NOT NULL""", (old["id"],)):
                edited[(r["kpi"], r["site_id"])] = r
            conn.execute("DELETE FROM reports WHERE id = %s", (old["id"],))

        items = []
        for row in flagged:
            for kpi in KPIS:
                count = row[KPI_HOURS_COL[kpi]]
                if count < min_hours:
                    continue
                key = (kpi, row["site_id"])
                before = prev_by_site.get(key)
                mine = edited.get(key)
                items.append({
                    "kpi": kpi, "site_id": row["site_id"], "site_name": row["name"],
                    "category": category_for(row["name"]), "sample_count": count,
                    "pending_days": before["pending_days"] + 1 if before else 0,
                    "feedback": mine["feedback"] if mine else (before["feedback"] if before else default_feedback),
                    "edited_at": mine["feedback_edited_at"] if mine else None,
                    "edited_by": mine["feedback_edited_by"] if mine else None,
                })
        items.sort(key=lambda i: (-i["sample_count"], i["site_name"].lower()))
        counts = {k: sum(1 for i in items if i["kpi"] == k) for k in KPIS}

        site_count = conn.execute(
            "SELECT count(*) AS n FROM site_daily WHERE report_date = %s", (report_date,)).fetchone()["n"]
        new_count = conn.execute(
            f"SELECT count(*) AS n FROM sites s WHERE {new_sites_sql()}", {"d": report_date}).fetchone()["n"]

        report = conn.execute(
            """INSERT INTO reports (report_date, generated_by, thresholds, sources_present, sources_missing,
                                    site_count, new_site_count, pd_count, jitter_count, rtt_count)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
            (report_date, user["id"] if user else None, Jsonb(th), present, missing,
             site_count, new_count, counts["PD"], counts["JITTER"], counts["RTT"])).fetchone()

        if items:
            with conn.cursor() as cur:
                cur.executemany(
                    """INSERT INTO report_items (report_id, kpi, site_id, site_name, category, sample_count,
                                                 pending_days, feedback, feedback_edited_at, feedback_edited_by)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    [(report["id"], i["kpi"], i["site_id"], i["site_name"], i["category"], i["sample_count"],
                      i["pending_days"], i["feedback"], i["edited_at"], i["edited_by"]) for i in items])
        return report


def delete_date(report_date: date) -> dict:
    """Remove everything stored for one report date. Sites seen only on that date go too."""
    with db.tx() as conn:
        hourly = conn.execute("DELETE FROM kpi_hourly WHERE report_date = %s", (report_date,)).rowcount
        conn.execute("DELETE FROM site_daily WHERE report_date = %s", (report_date,))
        conn.execute("DELETE FROM reports WHERE report_date = %s", (report_date,))
        conn.execute("DELETE FROM import_files WHERE report_date = %s", (report_date,))
        sites = conn.execute(
            """DELETE FROM sites s WHERE NOT EXISTS (SELECT 1 FROM site_daily sd WHERE sd.site_id = s.id)
                 AND NOT EXISTS (SELECT 1 FROM report_items ri WHERE ri.site_id = s.id)""").rowcount
        conn.execute(
            """UPDATE sites s SET first_seen = x.lo, last_seen = x.hi
               FROM (SELECT site_id, min(report_date) AS lo, max(report_date) AS hi
                     FROM site_daily GROUP BY site_id) x
               WHERE x.site_id = s.id AND (s.first_seen <> x.lo OR s.last_seen <> x.hi)""")
        return {"hourly_rows": hourly, "sites_removed": sites}
