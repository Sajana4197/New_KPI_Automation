"""Load a report made by the old notebook (PD / JITTER / RTT lists side by side) as starting history.

The next generated report then carries its pending days and feedback forward,
just as the notebook did with "yesterday's" Excel file.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from . import db
from .reporting import KPIS, resolve_seed_items
from .sources import _norm, _read_first_sheet, category_for


class SeedError(Exception):
    pass


def _to_int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def read_old_report(path: Path) -> list[dict]:
    rows = _read_first_sheet(Path(path))
    header_idx = next((i for i, r in enumerate(rows[:30])
                       if {"pd", "jitter", "rtt"} <= {_norm(c) for c in r}), None)
    if header_idx is None:
        raise SeedError("This does not look like an old IPPM report: no PD / JITTER / RTT header row was found.")
    header = [_norm(c) for c in rows[header_idx]]
    starts = sorted((header.index(k.lower()), k) for k in KPIS)
    default_feedback = db.get_setting("default_feedback") or db.DEFAULT_SETTINGS["default_feedback"]
    items: list[dict] = []
    for n, (start, kpi) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else len(header)
        block = header[start:end]

        def col(prefix: str) -> int | None:
            return next((start + i for i, h in enumerate(block) if h.startswith(prefix)), None)

        c_count, c_cat, c_pend, c_fb = col("sample count"), col("category"), col("no of pending"), col("feedback")
        if c_fb is None:   # newer sheets call the column "TXP Remark"
            c_fb = col("txp") if col("txp") is not None else col("remark")
        seen: set[str] = set()
        for r in rows[header_idx + 1:]:
            name = str(r[start]).strip() if start < len(r) and r[start] not in (None, "") else ""
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())

            def cell(idx):
                return r[idx] if idx is not None and idx < len(r) else None

            feedback = str(cell(c_fb)).strip() if cell(c_fb) not in (None, "") else default_feedback
            category = str(cell(c_cat)).strip() if cell(c_cat) not in (None, "") else category_for(name)
            items.append({"kpi": kpi, "site_name": name, "category": category,
                          "sample_count": _to_int(cell(c_count)), "pending_days": _to_int(cell(c_pend)),
                          "feedback": feedback})
    if not items:
        raise SeedError("The file has the right headers but no rows.")
    return items


def load_seed(path: Path, report_date: date, user: dict | None = None) -> dict:
    items = read_old_report(path)
    with db.tx() as conn:
        existing = conn.execute("SELECT id, is_seed FROM reports WHERE report_date = %s", (report_date,)).fetchone()
        if existing and not existing["is_seed"]:
            raise SeedError(f"A generated report already exists for {report_date:%d %b %Y}. "
                            "Choose the date of the old report instead.")
        if existing:
            conn.execute("DELETE FROM reports WHERE id = %s", (existing["id"],))
        counts = {k: sum(1 for i in items if i["kpi"] == k) for k in KPIS}
        report = conn.execute(
            """INSERT INTO reports (report_date, is_seed, generated_by, pd_count, jitter_count, rtt_count)
               VALUES (%s, true, %s, %s, %s, %s) RETURNING *""",
            (report_date, user["id"] if user else None, counts["PD"], counts["JITTER"], counts["RTT"])).fetchone()
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO report_items (report_id, kpi, site_name, category, sample_count, pending_days, feedback)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                [(report["id"], i["kpi"], i["site_name"], i["category"], i["sample_count"],
                  i["pending_days"], i["feedback"]) for i in items])
        resolve_seed_items(conn, report["id"])
        matched = conn.execute(
            "SELECT count(*) AS n FROM report_items WHERE report_id = %s AND site_id IS NOT NULL",
            (report["id"],)).fetchone()["n"]
    return {"report": report, "items": len(items), "matched": matched, "counts": counts}
