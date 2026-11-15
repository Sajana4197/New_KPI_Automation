"""Developer check: import the sample files (without moving them) and print what was stored.

Usage:  .venv\\Scripts\\python.exe -m tools.verify_import <folder-with-sample-files>
"""
import sys
import time
from pathlib import Path

from app import db
from app.importer import import_file

folder = Path(sys.argv[1])
db.init_schema()
for f in sorted(folder.glob("*.xlsx")):
    if f.name.startswith("~$"):
        continue
    t = time.perf_counter()
    row = import_file(f, move=False)
    print(f"{row['status']:9} {str(row['source']):12} date={row['report_date']} rows={row['row_count']} "
          f"sites={row['site_count']} new={row['new_site_count']} renamed={row['renamed_count']} "
          f"{time.perf_counter() - t:5.1f}s  {f.name[:50]}")
    if row["message"]:
        print("          ->", row["message"])

print("sites per source:", {r["source"]: r["n"] for r in db.query("SELECT source, count(*) n FROM sites GROUP BY 1 ORDER BY 1")})
print("hourly rows:", db.scalar("SELECT count(*) FROM kpi_hourly"), "| paths:", db.scalar("SELECT count(*) FROM paths"),
      "| site_daily:", db.scalar("SELECT count(*) FROM site_daily"))
print("size:", db.scalar("SELECT pg_size_pretty(pg_total_relation_size('kpi_hourly'))"))
db.close_pool()
