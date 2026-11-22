"""Read-side queries behind the pages."""
from __future__ import annotations

import threading
from datetime import date, timedelta

from . import db
from .reporting import KPIS, new_sites_sql
from .sources import CATEGORIES, CATEGORY_LABELS, SOURCE_ORDER, SOURCES

PENDING_BUCKETS = [("First day", 0, 0), ("1–2 days", 1, 2), ("3–6 days", 3, 6),
                   ("7–13 days", 7, 13), ("14–29 days", 14, 29), ("30 days or more", 30, 10 ** 9)]
HOUR_BUCKETS = [("Up to 5 hours", 0, 5), ("6–11 hours", 6, 11), ("12–23 hours", 12, 23),
                ("All day", 24, 10 ** 9)]


# ---- dates -----------------------------------------------------------------------------
def data_dates() -> list[date]:
    """Report dates that have imported KPI data, newest first."""
    return [r["report_date"] for r in db.query(
        "SELECT DISTINCT report_date FROM import_files WHERE status = 'ok' AND report_date IS NOT NULL "
        "ORDER BY 1 DESC")]


def report_dates() -> list[dict]:
    return db.query("SELECT report_date, is_seed FROM reports ORDER BY report_date DESC")


def latest_data_date() -> date | None:
    return db.scalar("SELECT max(report_date) FROM import_files WHERE status = 'ok'")


# ---- files -----------------------------------------------------------------------------
def source_status(d: date) -> list[dict]:
    rows = {r["source"]: r for r in db.query(
        """SELECT DISTINCT ON (source) * FROM import_files
           WHERE report_date = %s AND status = 'ok' ORDER BY source, imported_at DESC""", (d,))}
    return [{"key": k, "label": SOURCES[k].label, "short": SOURCES[k].short, "vendor": SOURCES[k].vendor,
             "file": rows.get(k)} for k in SOURCE_ORDER]


# ---- reports ---------------------------------------------------------------------------
def get_report(d: date) -> dict | None:
    return db.query_one(
        """SELECT r.*, u.username AS generated_by_name, coalesce(nullif(u.full_name, ''), u.username) AS generated_by_label
           FROM reports r LEFT JOIN users u ON u.id = r.generated_by WHERE r.report_date = %s""", (d,))


def previous_report(d: date) -> dict | None:
    return db.query_one("SELECT * FROM reports WHERE report_date < %s ORDER BY report_date DESC LIMIT 1", (d,))


def next_report(d: date) -> dict | None:
    return db.query_one("SELECT * FROM reports WHERE report_date > %s ORDER BY report_date LIMIT 1", (d,))


def report_items(report: dict) -> list[dict]:
    return db.query(
        f"""SELECT ri.id, ri.kpi, ri.site_id, ri.site_name AS name, ri.category, ri.sample_count AS hours,
                   ri.pending_days AS pending, ri.feedback, (ri.feedback_edited_at IS NOT NULL) AS edited,
                   s.source, coalesce(({new_sites_sql('s')}), false) AS is_new,
                   CASE ri.kpi WHEN 'PD' THEN sd.pd_max WHEN 'JITTER' THEN sd.jitter_max ELSE sd.rtt_max END AS peak
            FROM report_items ri
            LEFT JOIN sites s ON s.id = ri.site_id
            LEFT JOIN site_daily sd ON sd.site_id = ri.site_id AND sd.report_date = %(d)s
            WHERE ri.report_id = %(rid)s
            ORDER BY ri.sample_count DESC, lower(ri.site_name)""",
        {"d": report["report_date"], "rid": report["id"]})


def report_history(limit: int = 14) -> list[dict]:
    return db.query(
        """SELECT report_date, is_seed, site_count, new_site_count, pd_count, jitter_count, rtt_count,
                  cardinality(sources_missing) AS missing, generated_at
           FROM reports ORDER BY report_date DESC LIMIT %s""", (limit,))


def new_sites(d: date, limit: int = 8) -> list[dict]:
    return db.query(
        f"SELECT s.id, s.name, s.source, s.category FROM sites s WHERE {new_sites_sql()} "
        f"ORDER BY lower(s.name) LIMIT %(n)s", {"d": d, "n": limit})


def all_new_sites(d: date) -> list[dict]:
    return db.query(
        f"""SELECT s.id, s.name, s.source, s.category, sd.pd_hours, sd.jitter_hours, sd.rtt_hours,
                   sd.pd_max, sd.jitter_max, sd.rtt_max, sd.path_count
            FROM sites s LEFT JOIN site_daily sd ON sd.site_id = s.id AND sd.report_date = %(d)s
            WHERE {new_sites_sql()} ORDER BY s.source, lower(s.name)""", {"d": d})


def _matrix(items: list[dict], buckets: list[tuple[str, callable]]) -> dict:
    rows = []
    for label, test in buckets:
        rows.append({"label": label, **{k: sum(1 for i in items if i["kpi"] == k and test(i)) for k in KPIS}})
    return {"rows": rows, "max": {k: max((r[k] for r in rows), default=0) for k in KPIS}}


def breakdowns(items: list[dict]) -> dict:
    return {
        "category": _matrix(items, [(f"{c} · {CATEGORY_LABELS[c]}", (lambda i, c=c: i["category"] == c))
                                    for c in CATEGORIES]),
        "pending": _matrix(items, [(label, (lambda i, lo=lo, hi=hi: lo <= i["pending"] <= hi))
                                   for label, lo, hi in PENDING_BUCKETS]),
        "hours": _matrix(items, [(label, (lambda i, lo=lo, hi=hi: lo <= i["hours"] <= hi))
                                 for label, lo, hi in HOUR_BUCKETS]),
    }


def category_summary(d: date, items: list[dict]) -> list[dict]:
    """For each site category: sites measured that day, sites on the report, and listed sites per KPI."""
    totals = {r["category"]: r["n"] for r in db.query(
        """SELECT s.category, count(*) AS n FROM site_daily sd JOIN sites s ON s.id = sd.site_id
           WHERE sd.report_date = %s GROUP BY s.category""", (d,))}
    out = []
    for c in CATEGORIES:
        mine = [i for i in items if i["category"] == c]
        total = totals.get(c, 0)
        flagged = len({i["site_id"] or i["name"] for i in mine})
        out.append({"code": c, "label": CATEGORY_LABELS[c], "total": total, "flagged": flagged,
                    "share": flagged / total * 100 if total else 0,
                    **{k: sum(1 for i in mine if i["kpi"] == k) for k in KPIS}})
    return out


_profile_cache: dict[tuple, list[dict]] = {}
_profile_lock = threading.Lock()


def hourly_profile(d: date, thresholds: dict) -> list[dict]:
    """For each hour of the day: how many sites were at or above each threshold."""
    version = db.scalar("SELECT max(id) FROM import_files WHERE report_date = %s", (d,))
    key = (d, version, thresholds["pd_pct"], thresholds["jitter_ms"], thresholds["rtt_ms"])
    with _profile_lock:
        if key in _profile_cache:
            return _profile_cache[key]
    rows = db.query(
        """SELECT x.hour_ts, count(*) AS sites,
                  count(*) FILTER (WHERE x.pd >= %(pd)s) AS pd,
                  count(*) FILTER (WHERE x.jit >= %(jit)s) AS jitter,
                  count(*) FILTER (WHERE x.rtt >= %(rtt)s) AS rtt
           FROM (SELECT p.site_id, h.hour_ts, max(h.pd_pct) AS pd, max(h.jitter_ms) AS jit, max(h.rtt_ms) AS rtt
                 FROM kpi_hourly h JOIN paths p ON p.id = h.path_id
                 WHERE h.report_date = %(d)s GROUP BY p.site_id, h.hour_ts) x
           GROUP BY x.hour_ts ORDER BY x.hour_ts""",
        {"d": d, "pd": thresholds["pd_pct"], "jit": thresholds["jitter_ms"], "rtt": thresholds["rtt_ms"]})
    # Huawei reports one extra closing hour that the ZTE files do not have; an hour that
    # only part of the network covers would look like a sudden drop, so it is left out.
    most = max((r["sites"] for r in rows), default=0)
    rows = [r for r in rows if r["sites"] * 10 >= most * 9]
    with _profile_lock:
        if len(_profile_cache) > 40:
            _profile_cache.clear()
        _profile_cache[key] = rows
    return rows


def last_import_at(d: date):
    return db.scalar("SELECT max(imported_at) FROM import_files WHERE report_date = %s AND status = 'ok'", (d,))


# ---- sites -----------------------------------------------------------------------------
SITE_SORTS = {
    "name": "lower(s.name)",
    "newest": "s.first_seen DESC, lower(s.name)",
    "pd": "sd.pd_hours DESC NULLS LAST, sd.pd_max DESC NULLS LAST, lower(s.name)",
    "jitter": "sd.jitter_hours DESC NULLS LAST, sd.jitter_max DESC NULLS LAST, lower(s.name)",
    "rtt": "sd.rtt_hours DESC NULLS LAST, sd.rtt_max DESC NULLS LAST, lower(s.name)",
}


def _like(text: str) -> str:
    return "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


WORST_HOURS = "greatest(sd.pd_hours, sd.jitter_hours, sd.rtt_hours)"
SITE_STATUS = {          # condition on the latest day's roll-up; %(minh)s = hours needed to be listed
    "listed": f"{WORST_HOURS} >= %(minh)s",
    "near": f"{WORST_HOURS} BETWEEN 1 AND %(minh)s - 1",
    "healthy": f"{WORST_HOURS} = 0",
}
SITES_FROM = "sites s LEFT JOIN site_daily sd ON sd.site_id = s.id AND sd.report_date = %(latest)s"


def sites_page(*, q: str, source: str, category: str, show: str, sort: str, page: int,
               latest: date | None, status: str = "", min_hours: int = 3, per_page: int = 50) -> dict:
    where, params = ["true"], {"latest": latest, "d": latest, "minh": min_hours}
    if status in SITE_STATUS and latest:
        where.append(SITE_STATUS[status])
    if q:
        where.append("(s.name ILIKE %(q)s OR s.ext_key ILIKE %(q)s OR EXISTS "
                     "(SELECT 1 FROM site_names n WHERE n.site_id = s.id AND n.name ILIKE %(q)s))")
        params["q"] = _like(q)
    if source in SOURCES:
        where.append("s.source = %(source)s")
        params["source"] = source
    if category in CATEGORIES:
        where.append("s.category = %(category)s")
        params["category"] = category
    if latest:
        if show == "new":
            where.append(new_sites_sql())
        elif show == "silent":
            where.append("s.last_seen < %(latest)s")
        elif show == "flagged":
            where.append("EXISTS (SELECT 1 FROM report_items ri JOIN reports r ON r.id = ri.report_id "
                         "WHERE ri.site_id = s.id AND r.report_date = %(latest)s)")
        elif show == "renamed":
            where.append("(SELECT count(*) FROM site_names n WHERE n.site_id = s.id) > 1")
    cond = " AND ".join(where)
    total = db.scalar(f"SELECT count(*) FROM {SITES_FROM} WHERE {cond}", params)
    pages = max(1, -(-total // per_page))
    page = min(max(page, 1), pages)
    params.update({"limit": per_page, "offset": (page - 1) * per_page})
    rows = db.query(
        f"""SELECT s.id, s.name, s.source, s.category, s.first_seen, s.last_seen,
                   sd.pd_hours, sd.jitter_hours, sd.rtt_hours, sd.pd_max, sd.jitter_max, sd.rtt_max, sd.samples,
                   (SELECT count(*) FROM site_names n WHERE n.site_id = s.id) AS name_count
            FROM {SITES_FROM}
            WHERE {cond} ORDER BY {SITE_SORTS.get(sort, SITE_SORTS['name'])}
            LIMIT %(limit)s OFFSET %(offset)s""", params)
    return {"rows": rows, "total": total, "page": page, "pages": pages, "per_page": per_page}


def site_summary(latest: date | None, min_hours: int) -> dict:
    """Counts behind the tiles and chips of the Sites page (always for all sites, whatever the filters)."""
    params = {"latest": latest, "d": latest, "minh": min_hours}
    rows = db.query(
        f"""SELECT s.category, count(*) AS total,
                   count(*) FILTER (WHERE {SITE_STATUS['listed']}) AS listed,
                   count(*) FILTER (WHERE {SITE_STATUS['near']}) AS near,
                   count(*) FILTER (WHERE {SITE_STATUS['healthy']}) AS healthy
            FROM {SITES_FROM} GROUP BY s.category""", params)
    out = {k: sum(r[k] for r in rows) for k in ("total", "listed", "near", "healthy")}
    out["by_category"] = {r["category"]: r["total"] for r in rows}
    out["new"] = db.scalar(f"SELECT count(*) FROM sites s WHERE {new_sites_sql()}", params) if latest else 0
    return out


_hours_cache: dict = {}


def day_hours(d: date) -> list:
    """Every measured hour of a report day, in order (the shared time axis of the 24-hour strips)."""
    seq = db.scalar("SELECT coalesce(max(id), 0) FROM import_files")
    hit = _hours_cache.get(d)
    if hit and hit[0] == seq:
        return hit[1]
    span = db.query_one("SELECT min(hour_ts) AS lo, max(hour_ts) AS hi FROM kpi_hourly WHERE report_date = %s", (d,))
    lo, hi = span["lo"], span["hi"]
    hours = []
    if lo and hi:
        t = lo
        while t <= hi and len(hours) < 49:
            hours.append(t)
            t += timedelta(hours=1)
    _hours_cache.clear()
    _hours_cache[d] = (seq, hours)
    return hours


def site_strips(site_ids: list[int], d: date | None) -> dict:
    """Worst-path value per hour for a handful of sites: {"hours": [...], "sites": {id: {"PD": [...], ...}}}."""
    if not site_ids or not d:
        return {"hours": [], "sites": {}}
    hours = day_hours(d)
    index = {t: i for i, t in enumerate(hours)}
    sites = {i: {k: [None] * len(hours) for k in KPIS} for i in site_ids}
    for r in db.query(
            """SELECT p.site_id, h.hour_ts, max(h.pd_pct) AS pd, max(h.jitter_ms) AS jitter, max(h.rtt_ms) AS rtt
               FROM kpi_hourly h JOIN paths p ON p.id = h.path_id
               WHERE p.site_id = ANY(%s) AND h.report_date = %s GROUP BY 1, 2""", (site_ids, d)):
        i = index.get(r["hour_ts"])
        if i is None:
            continue
        row = sites[r["site_id"]]
        for k, col in (("PD", "pd"), ("JITTER", "jitter"), ("RTT", "rtt")):
            if r[col] is not None:
                row[k][i] = round(float(r[col]), 2)
    return {"hours": hours, "sites": sites}


def site_counts() -> dict:
    row = db.query_one("SELECT count(*) AS total FROM sites")
    by_source = {r["source"]: r["n"] for r in db.query("SELECT source, count(*) AS n FROM sites GROUP BY 1")}
    return {"total": row["total"], "by_source": by_source}


def site_detail(site_id: int) -> dict | None:
    site = db.query_one("SELECT * FROM sites WHERE id = %s", (site_id,))
    if not site:
        return None
    site["names"] = db.query(
        "SELECT name, first_seen, last_seen FROM site_names WHERE site_id = %s ORDER BY last_seen DESC, first_seen DESC",
        (site_id,))
    site["dates"] = [r["report_date"] for r in db.query(
        "SELECT report_date FROM site_daily WHERE site_id = %s ORDER BY 1 DESC LIMIT 400", (site_id,))]
    site["history"] = db.query(
        """SELECT report_date, samples, path_count, pd_hours, jitter_hours, rtt_hours, pd_max, jitter_max, rtt_max
           FROM site_daily WHERE site_id = %s ORDER BY report_date DESC LIMIT 60""", (site_id,))
    site["listed"] = db.query(
        """SELECT r.report_date, r.is_seed, ri.kpi, ri.sample_count, ri.pending_days, ri.feedback
           FROM report_items ri JOIN reports r ON r.id = ri.report_id
           WHERE ri.site_id = %s ORDER BY r.report_date DESC, ri.kpi LIMIT 120""", (site_id,))
    return site


def site_day(site_id: int, d: date, thresholds: dict) -> dict:
    daily = db.query_one("SELECT * FROM site_daily WHERE site_id = %s AND report_date = %s", (site_id, d))
    hourly = db.query(
        """SELECT h.hour_ts, max(h.pd_pct) AS pd, max(h.jitter_ms) AS jitter, max(h.rtt_ms) AS rtt
           FROM kpi_hourly h JOIN paths p ON p.id = h.path_id
           WHERE p.site_id = %s AND h.report_date = %s GROUP BY h.hour_ts ORDER BY h.hour_ts""", (site_id, d))
    paths = db.query(
        """SELECT p.label, count(*) AS samples,
                  max(h.pd_pct) AS pd_max, avg(h.pd_pct) AS pd_avg,
                  max(h.jitter_ms) AS jitter_max, max(h.rtt_ms) AS rtt_max,
                  count(*) FILTER (WHERE h.pd_pct >= %(pd)s) AS pd_hours,
                  count(*) FILTER (WHERE h.jitter_ms >= %(jit)s) AS jitter_hours,
                  count(*) FILTER (WHERE h.rtt_ms >= %(rtt)s) AS rtt_hours
           FROM kpi_hourly h JOIN paths p ON p.id = h.path_id
           WHERE p.site_id = %(s)s AND h.report_date = %(d)s
           GROUP BY p.label ORDER BY max(h.pd_pct) DESC NULLS LAST, p.label""",
        {"s": site_id, "d": d, "pd": thresholds["pd_pct"], "jit": thresholds["jitter_ms"],
         "rtt": thresholds["rtt_ms"]})
    return {"daily": daily, "hourly": hourly, "paths": paths}


# ---- imports ---------------------------------------------------------------------------
def import_days(limit: int = 45) -> list[dict]:
    rows = db.query(
        """SELECT DISTINCT ON (report_date, source) report_date, source, filename, row_count, site_count,
                  new_site_count, renamed_count, imported_at, message
           FROM import_files WHERE status = 'ok' AND report_date IS NOT NULL
           ORDER BY report_date DESC, source, imported_at DESC""")
    reports = {r["report_date"]: r for r in db.query(
        "SELECT report_date, generated_at, is_seed FROM reports")}
    days: dict[date, dict] = {}
    for r in rows:
        day = days.setdefault(r["report_date"], {"date": r["report_date"], "files": {}, "rows": 0})
        day["files"][r["source"]] = r
        day["rows"] += r["row_count"] or 0
    out = []
    for d in sorted(days, reverse=True)[:limit]:
        day = days[d]
        day["report"] = reports.get(d)
        day["missing"] = [SOURCES[k].label for k in SOURCE_ORDER if k not in day["files"]]
        out.append(day)
    return out


def import_log(limit: int = 80) -> list[dict]:
    return db.query("SELECT * FROM import_files ORDER BY imported_at DESC, id DESC LIMIT %s", (limit,))


def storage() -> dict:
    return db.query_one(
        """SELECT pg_size_pretty(pg_database_size(current_database())) AS size,
                  (SELECT count(DISTINCT report_date) FROM import_files WHERE status = 'ok') AS days,
                  (SELECT count(*) FROM sites) AS sites""")


# ---- admin -----------------------------------------------------------------------------
def users() -> list[dict]:
    return db.query("SELECT id, username, full_name, role, is_active, created_at, last_login_at "
                    "FROM users ORDER BY lower(username)")


def activity(limit: int = 40) -> list[dict]:
    return db.query("SELECT at, username, action, detail FROM activity_log ORDER BY at DESC, id DESC LIMIT %s",
                    (limit,))


def seeds() -> list[dict]:
    return db.query(
        """SELECT r.report_date, r.pd_count, r.jitter_count, r.rtt_count, r.generated_at,
                  (SELECT count(*) FROM report_items ri WHERE ri.report_id = r.id AND ri.site_id IS NOT NULL) AS matched,
                  (SELECT count(*) FROM report_items ri WHERE ri.report_id = r.id) AS items
           FROM reports r WHERE r.is_seed ORDER BY r.report_date DESC""")
