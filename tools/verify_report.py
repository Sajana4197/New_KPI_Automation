"""Developer check: compare the generated report with the notebook's output file.

Usage:  .venv\\Scripts\\python.exe -m tools.verify_report <notebook-output.xlsx> <previous-report.xlsx> <report-date>
"""
import re
import sys
import warnings
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from app import db
from app.reporting import KPIS, generate_report
from app.seed import load_seed

warnings.filterwarnings("ignore")
old_out, old_prev, d = Path(sys.argv[1]), Path(sys.argv[2]), date.fromisoformat(sys.argv[3])


def mine() -> dict[str, pd.DataFrame]:
    rows = db.query(
        """SELECT ri.kpi, ri.site_name, ri.category, ri.sample_count, ri.pending_days, ri.feedback
           FROM report_items ri JOIN reports r ON r.id = ri.report_id WHERE r.report_date = %s""", (d,))
    df = pd.DataFrame(rows)
    return {k: df[df.kpi == k].set_index("site_name") for k in KPIS}


def theirs() -> dict[str, pd.DataFrame]:
    r = pd.read_excel(old_out)
    out = {}
    for n, k in enumerate(KPIS):
        sfx = "" if n == 0 else f".{n}"
        t = r[[k, f"Sample count exceeding the threshold{sfx}", f"category{sfx}", f"No of Pending Days{sfx}", f"Feedback{sfx}"]].dropna(subset=[k])
        t.columns = ["site_name", "sample_count", "category", "pending_days", "feedback"]
        out[k] = t.set_index("site_name")
    return out


print("=== 1. No previous report: lists, counts and categories ===")
db.execute("DELETE FROM reports WHERE report_date < %s", (d,))
rep = generate_report(d)
print("report:", {k: rep[k] for k in ("site_count", "new_site_count", "pd_count", "jitter_count", "rtt_count", "sources_missing")})
m, t = mine(), theirs()
for k in KPIS:
    a, b = set(m[k].index), set(t[k].index)
    both = sorted(a & b)
    cnt = (m[k].loc[both, "sample_count"].astype(int).values == t[k].loc[both, "sample_count"].astype(int).values).sum()
    cat = (m[k].loc[both, "category"].values == t[k].loc[both, "category"].values).sum()
    print(f"{k:7} mine={len(a):5} notebook={len(b):5} common={len(both):5} only_mine={len(a - b):3} only_notebook={len(b - a):3} "
          f"| same count {cnt}/{len(both)} | same category {cat}/{len(both)}")
    if k == "PD" and (a ^ b):
        print("   differences:", sorted(a ^ b)[:6])

print("\n=== 2. With the previous report loaded: pending days and feedback ===")
res = load_seed(old_prev, d - timedelta(days=1))
print("seed:", res["counts"], "items:", res["items"], "matched to sites:", res["matched"])
generate_report(d)
m = mine()
for k in KPIS:
    both = sorted(set(m[k].index) & set(t[k].index))
    mp, tp = m[k].loc[both, "pending_days"].astype(int), t[k].loc[both, "pending_days"].astype(int)
    mf, tf = m[k].loc[both, "feedback"].str.strip(), t[k].loc[both, "feedback"].astype(str).str.strip()
    diff = mp[mp.values != tp.values]
    renamed_gain = int(((mp.values > 0) & (tp.values == 0)).sum())
    print(f"{k:7} common={len(both):5} | same pending {int((mp.values == tp.values).sum())} | differ {len(diff)} "
          f"(of which mine>0 where notebook=0, i.e. history kept through a rename: {renamed_gain}) "
          f"| same feedback {int((mf.values == tf.values).sum())}")
    other = [(n, int(mp[n]), int(tp[n])) for n in diff.index if not (mp[n] > 0 and tp[n] == 0)]
    if other:
        print("   unexplained:", other[:8])
db.close_pool()
