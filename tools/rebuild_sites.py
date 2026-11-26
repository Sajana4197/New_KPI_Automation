"""Rebuild every site and its KPI data from the archived files, then regenerate the reports.

Run this once after a change to how sites or paths are recognised (for example the BE / Priority and
Mobitel / SLT path types). Stop TX KPI Monitor (run.bat) first, then:

    .venv\\Scripts\\python.exe -m tools.rebuild_sites

User accounts, settings, the import log and the archived Excel files are kept. Feedback typed into a
report is carried over to the regenerated report where the same site and KPI are listed again.
"""
import re
import sys

from app import db, excel_report
from app.importer import rebuild_from_archive
from app.reporting import generate_report
from app.sources import split_task


def main() -> int:
    db.init_schema()
    reports = db.query(
        "SELECT id, report_date, generated_by FROM reports WHERE NOT is_seed ORDER BY report_date")
    # Feedback someone typed by hand, kept by report date, KPI and site name (Huawei: name without _dscp_x).
    typed = {}
    for r in db.query(
            """SELECT r.report_date, ri.kpi, ri.site_name, ri.path_kind, ri.feedback, ri.feedback_edited_at,
                      ri.feedback_edited_by
               FROM report_items ri JOIN reports r ON r.id = ri.report_id
               WHERE NOT r.is_seed AND ri.feedback_edited_at IS NOT NULL"""):
        name, kind = split_task(r["site_name"]) if re.search(r"dscp", r["site_name"], re.I) else (r["site_name"], r["path_kind"])
        typed[(r["report_date"], r["kpi"], name.lower(), kind or "")] = r

    print("Rebuilding sites from the archived files…")
    result = rebuild_from_archive()
    print(f"  {result['files']} files loaded, {result['sites']:,} sites")

    users = {u["id"]: u for u in db.query("SELECT id, username FROM users")}
    for rep in reports:
        d = rep["report_date"]
        new = generate_report(d, users.get(rep["generated_by"]))
        restored = 0
        for it in db.query("SELECT id, kpi, site_name, path_kind FROM report_items WHERE report_id = %s", (new["id"],)):
            old = typed.get((d, it["kpi"], it["site_name"].lower(), it["path_kind"])) \
                or typed.get((d, it["kpi"], it["site_name"].lower(), ""))
            if old:
                db.execute("""UPDATE report_items SET feedback = %s, feedback_edited_at = %s, feedback_edited_by = %s
                              WHERE id = %s""",
                           (old["feedback"], old["feedback_edited_at"], old["feedback_edited_by"], it["id"]))
                restored += 1
        path = excel_report.save_copy(d)
        print(f"  report {d}: {new['site_count']:,} sites · PD {new['pd_count']} · jitter {new['jitter_count']} · "
              f"RTT {new['rtt_count']} rows" + (f" · {restored} typed feedback kept" if restored else "")
              + f"\n    saved {path}")

    db.log_activity(None, "Rebuilt sites",
                    f"{result['sites']:,} sites from {result['files']} archived files; "
                    f"{len(reports)} report(s) regenerated with BE / Priority and Mobitel / SLT paths")
    db.close_pool()
    return 0


if __name__ == "__main__":
    sys.exit(main())
