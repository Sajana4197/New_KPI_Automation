"""The daily report as a formatted Excel workbook."""
from __future__ import annotations

import io
from datetime import date
from pathlib import Path

import xlsxwriter

from . import db, queries
from .config import APP_DIR, APP_NAME, config
from .reporting import KPI_LABELS, KPIS
from .sources import CATEGORIES, CATEGORY_LABELS, SOURCES

BRAND, SKY, GREEN = "#0056A2", "#00B4EB", "#50B748"
INK, INK_2, MUTED, LINE, WASH = "#0F1B2D", "#4A5A70", "#7A889B", "#E3E8F0", "#F3F6FB"
KPI_COLOR = {"PD": "#2A78D6", "JITTER": "#EB6834", "RTT": "#1BAF7A"}
KPI_TINT = {"PD": "#B5D0F2", "JITTER": "#F8C6AF", "RTT": "#AEE3CF"}   # light bars that keep the number readable
KPI_UNIT = {"PD": "%", "JITTER": "ms", "RTT": "ms"}
KPI_SHEET = {"PD": "Packet Drop", "JITTER": "Jitter", "RTT": "RTT"}
FONT = "Segoe UI"
LOGO = APP_DIR / "static" / "img" / "sltmobitel.png"


def filename_for(d: date) -> str:
    return f"TX KPI Daily Report {d:%Y-%m-%d}.xlsx"


def _rule_text(kpi: str, th: dict) -> str:
    limit = {"PD": f"{th['pd_pct']:g} %", "JITTER": f"{th['jitter_ms']:g} ms", "RTT": f"{th['rtt_ms']:g} ms"}[kpi]
    return f"{th['min_hours']:g} or more hours at or above {limit}"


SHEETS = [("summary", "Summary", BRAND), ("pd", "Packet Drop", KPI_COLOR["PD"]), ("jitter", "Jitter", KPI_COLOR["JITTER"]),
          ("rtt", "RTT", KPI_COLOR["RTT"]), ("new", "New Sites", GREEN), ("classic", "Classic layout", MUTED)]
SHEET_KPI = {"pd": "PD", "jitter": "JITTER", "rtt": "RTT"}
# Classic layout: one list, one row per site and KPI.
CLASSIC_HEADS = ["Date", "Site Name", "PD/RTT/JTR/", "Sample count exceeding the threshold", "Category", "pending days", "TXP Remark"]
CLASSIC_CODE = {"PD": "PD", "JITTER": "JTR", "RTT": "RTT"}


def collect(report_date: date) -> dict:
    """Everything that goes into the workbook. The on-screen preview is drawn from this same data."""
    report = queries.get_report(report_date)
    if not report or report["is_seed"]:
        raise ValueError(f"There is no generated report for {report_date:%d %b %Y}.")
    items = queries.report_items(report)
    th = report["thresholds"] or db.get_setting("thresholds")
    window = db.query_one(
        "SELECT min(hour_min) AS lo, max(hour_max) AS hi FROM import_files WHERE report_date = %s AND status = 'ok'",
        (report_date,))
    breakdown = queries.breakdowns(items)
    measured = f"Measured {window['lo']:%d %b %H:%M} to {window['hi']:%d %b %H:%M}" if window and window["lo"] else ""
    made = f"Generated {report['generated_at'].astimezone():%d %b %Y %H:%M}"
    if report.get("generated_by_label"):
        made += f" by {report['generated_by_label']}"
    rules = [f"{KPI_LABELS[k]}: a site is listed with {_rule_text(k, th)} (worst path in each hour)." for k in KPIS]
    rules.append("Pending days: the previous report's value plus one while the site stays listed; 0 on the first day.")
    rules.append("Feedback is carried forward from the previous report until someone changes it.")
    return {
        "report": report, "items": items, "th": th, "window": window, "breakdown": breakdown,
        "sources": queries.source_status(report_date),
        "by_kpi": {k: [i for i in items if i["kpi"] == k] for k in KPIS},
        "fresh": queries.all_new_sites(report_date),
        "missing": [SOURCES[k].label for k in report["sources_missing"]],
        "flagged_sites": len({i["site_id"] or i["name"] for i in items}),
        "cat_rows": [{"label": f"{c} - {CATEGORY_LABELS[c]}", "code": c, **{k: r[k] for k in KPIS}}
                     for c, r in zip(CATEGORIES, breakdown["category"]["rows"])],
        "headline": " · ".join(x for x in (measured, made) if x),
        "rules": rules, "rule_text": {k: _rule_text(k, th) for k in KPIS},
    }


def build(report_date: date) -> bytes:
    c = collect(report_date)
    report, items, sources, th = c["report"], c["items"], c["sources"], c["th"]
    by_kpi, breakdown, fresh, window = c["by_kpi"], c["breakdown"], c["fresh"], c["window"]

    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})
    wb.set_properties({"title": f"TX KPI Daily Report {report_date:%d %b %Y}", "author": APP_NAME,
                       "company": "SLT-Mobitel"})

    def fmt(**kw):
        base = {"font_name": FONT, "font_size": 10, "font_color": INK, "valign": "vcenter"}
        base.update(kw)
        return wb.add_format(base)

    f = {
        "title": fmt(font_size=20, bold=True, font_color=BRAND),
        "subtitle": fmt(font_size=11, font_color=INK_2),
        "muted": fmt(font_size=9, font_color=MUTED),
        "section": fmt(font_size=12, bold=True, font_color=BRAND, bottom=2, bottom_color=SKY),
        "warn": fmt(bold=True, font_color="#7A4B00", bg_color="#FFF3D1", border=1, border_color="#FAB219",
                    text_wrap=True, indent=1),
        "ok": fmt(bold=True, font_color="#006300", bg_color="#E7F6E7", border=1, border_color="#0CA30C", indent=1),
        "tile_label": fmt(font_size=9, bold=True, font_color=INK_2, bg_color=WASH, top=1, left=1, right=1,
                          border_color=LINE, indent=1),
        "tile_note": fmt(font_size=9, font_color=MUTED, bg_color=WASH, bottom=1, left=1, right=1,
                         border_color=LINE, indent=1, valign="top"),
        "head": fmt(bold=True, font_color="#FFFFFF", bg_color=BRAND, border=1, border_color=BRAND, text_wrap=True),
        "head_c": fmt(bold=True, font_color="#FFFFFF", bg_color=BRAND, border=1, border_color=BRAND,
                      align="center", text_wrap=True),
        "cell": fmt(border=1, border_color=LINE),
        "cell_alt": fmt(border=1, border_color=LINE, bg_color="#F8FAFD"),
        "num": fmt(border=1, border_color=LINE, align="center", num_format="#,##0"),
        "num_alt": fmt(border=1, border_color=LINE, align="center", num_format="#,##0", bg_color="#F8FAFD"),
        "dec": fmt(border=1, border_color=LINE, align="center", num_format="0.00"),
        "dec_alt": fmt(border=1, border_color=LINE, align="center", num_format="0.00", bg_color="#F8FAFD"),
        "wrap": fmt(border=1, border_color=LINE, text_wrap=True),
        "wrap_alt": fmt(border=1, border_color=LINE, text_wrap=True, bg_color="#F8FAFD"),
        "total": fmt(bold=True, border=1, border_color=LINE, top=2, top_color=BRAND, bg_color=WASH),
        "total_num": fmt(bold=True, border=1, border_color=LINE, top=2, top_color=BRAND, bg_color=WASH,
                         align="center", num_format="#,##0"),
        "pend_long": wb.add_format({"bg_color": "#FBE3E3", "font_color": "#9C1F1F", "bold": True}),
        "pend_mid": wb.add_format({"bg_color": "#FFF3D1", "font_color": "#7A4B00", "bold": True}),
        "new_flag": fmt(border=1, border_color=LINE, align="center", bold=True, font_color="#006300",
                        bg_color="#E7F6E7"),
        "sheet_title": fmt(font_size=15, bold=True, font_color="#FFFFFF", bg_color=BRAND, indent=1),
        "sheet_sub": fmt(font_size=10, font_color="#FFFFFF", bg_color=BRAND, indent=1),
    }

    # ================= Summary =================
    ws = wb.add_worksheet("Summary")
    ws.hide_gridlines(2)
    ws.set_tab_color(BRAND)
    ws.set_column("A:A", 2)
    ws.set_column("B:B", 30)
    ws.set_column("C:F", 14)
    ws.set_column("G:G", 3)
    ws.set_column("H:O", 12)
    ws.set_row(0, 8)
    for r in (1, 2, 3):
        ws.set_row(r, 22)
    if LOGO.exists():
        ws.insert_image(1, 1, str(LOGO), {"x_scale": 0.085, "y_scale": 0.085, "x_offset": 2, "y_offset": 2,
                                           "object_position": 3})
    ws.write(1, 2, "TX KPI Daily Report", f["title"])
    ws.write(2, 2, f"{report_date:%A, %d %B %Y}", f["subtitle"])
    if window and window["lo"]:
        measured = f"Measured {window['lo']:%d %b %H:%M} to {window['hi']:%d %b %H:%M}"
    else:
        measured = ""
    made = f"Generated {report['generated_at'].astimezone():%d %b %Y %H:%M}"
    if report.get("generated_by_label"):
        made += f" by {report['generated_by_label']}"
    ws.write(3, 2, " · ".join(x for x in (measured, made) if x), f["muted"])

    missing = [SOURCES[k].label for k in report["sources_missing"]]
    ws.set_row(5, 30)
    if missing:
        ws.merge_range(5, 1, 5, 5,
                       f"Warning: {len(missing)} of 4 KPI files was not received ({', '.join(missing)}). "
                       "Sites from the missing file are not in this report.", f["warn"])
    else:
        ws.merge_range(5, 1, 5, 5, "All 4 KPI files were received for this report.", f["ok"])

    flagged_sites = len({i["site_id"] or i["name"] for i in items})
    tiles = [
        ("Sites monitored", report["site_count"], INK, f"{report['new_site_count']:,} new today"),
        ("Sites flagged", flagged_sites, BRAND, "on at least one KPI"),
        ("Packet Drop", report["pd_count"], KPI_COLOR["PD"], _rule_text("PD", th)),
        ("Jitter", report["jitter_count"], KPI_COLOR["JITTER"], _rule_text("JITTER", th)),
        ("RTT", report["rtt_count"], KPI_COLOR["RTT"], _rule_text("RTT", th)),
    ]
    ws.set_row(7, 18)
    ws.set_row(8, 34)
    ws.set_row(9, 28)
    for n, (label, value, color, note) in enumerate(tiles):
        col = 1 + n
        ws.write(7, col, label, f["tile_label"])
        ws.write(8, col, value, fmt(font_size=22, bold=True, font_color=color, bg_color=WASH, left=1, right=1,
                                     border_color=LINE, indent=1, num_format="#,##0", align="left"))
        ws.write(9, col, note, fmt(font_size=8, font_color=MUTED, bg_color=WASH, bottom=1, left=1, right=1,
                                    border_color=LINE, indent=1, valign="top", text_wrap=True))

    def matrix(top: int, title: str, first_col: str, rows: list[dict], total: bool) -> int:
        ws.merge_range(top, 1, top, 5, title, f["section"])
        ws.set_row(top, 22)
        ws.write(top + 1, 1, first_col, f["head"])
        for n, k in enumerate(KPIS):
            ws.write(top + 1, 2 + n, KPI_LABELS[k], f["head_c"])
        ws.write(top + 1, 5, "All KPIs", f["head_c"])
        for n, row in enumerate(rows):
            r = top + 2 + n
            alt = n % 2 == 1
            ws.write(r, 1, row["label"], f["cell_alt" if alt else "cell"])
            for m, k in enumerate(KPIS):
                ws.write(r, 2 + m, row[k], f["num_alt" if alt else "num"])
            ws.write(r, 5, sum(row[k] for k in KPIS), f["num_alt" if alt else "num"])
        last = top + 1 + len(rows)
        if total:
            last += 1
            ws.write(last, 1, "Total", f["total"])
            for m, k in enumerate(KPIS):
                ws.write(last, 2 + m, sum(row[k] for row in rows), f["total_num"])
            ws.write(last, 5, sum(row[k] for row in rows for k in KPIS), f["total_num"])
        return last

    def chart(kind: dict, title: str, top: int, n_rows: int, kpis: list[str], anchor: tuple[int, int],
              legend: bool, size: tuple[int, int], label_col: int = 1):
        ch = wb.add_chart(kind)
        ch.show_hidden_data()
        for k in kpis:
            col = 2 + KPIS.index(k)
            ch.add_series({
                "name": ["Summary", top + 1, col],
                "categories": ["Summary", top + 2, label_col, top + 1 + n_rows, label_col],
                "values": ["Summary", top + 2, col, top + 1 + n_rows, col],
                "fill": {"color": KPI_COLOR[k]}, "border": {"color": "#FFFFFF", "width": 1.5}, "gap": 90,
                "data_labels": {"value": True, "font": {"name": FONT, "size": 8, "color": INK_2}}
                if len(kpis) == 1 else {},
            })
        ch.set_title({"name": title, "name_font": {"name": FONT, "size": 10, "bold": True, "color": INK},
                      "overlay": False})
        ch.set_legend({"position": "bottom", "font": {"name": FONT, "size": 8, "color": INK_2}}
                      if legend else {"none": True})
        axis_font = {"name": FONT, "size": 8, "color": INK_2}
        ch.set_x_axis({"num_font": axis_font, "line": {"color": "#C3CAD6"}, "major_tick_mark": "none"})
        ch.set_y_axis({"num_font": axis_font, "line": {"none": True}, "major_tick_mark": "none",
                       "major_gridlines": {"visible": True, "line": {"color": "#E3E8F0", "width": 0.75}}, "min": 0})
        ch.set_chartarea({"border": {"none": True}, "fill": {"color": "#FFFFFF"}})
        ch.set_plotarea({"border": {"none": True}})
        ch.set_size({"width": size[0], "height": size[1]})
        ws.insert_chart(anchor[0], anchor[1], ch, {"object_position": 3})

    cat_rows = [{"label": f"{c} - {CATEGORY_LABELS[c]}", **{k: r[k] for k in KPIS}}
                for c, r in zip(CATEGORIES, breakdown["category"]["rows"])]
    top = 11
    end = matrix(top, "Flagged sites by category", "Category", cat_rows, total=True)
    # Short category codes for the chart axes, kept in a hidden column beside the charts.
    code_col = 16
    for n, c in enumerate(CATEGORIES):
        ws.write(top + 2 + n, code_col, c)
    ws.set_column(code_col, code_col, None, None, {"hidden": True})
    chart({"type": "column"}, "Packet drop sites by category", top, len(cat_rows), ["PD"], (7, 7), False, (330, 250),
          label_col=code_col)
    chart({"type": "column"}, "Jitter and RTT sites by category", top, len(cat_rows), ["JITTER", "RTT"], (7, 11),
          True, (330, 250), label_col=code_col)

    top2 = end + 2
    end2 = matrix(top2, "How long sites have been pending", "Pending", breakdown["pending"]["rows"], total=False)
    chart({"type": "column", "subtype": "stacked"}, "Sites by pending days", top2, len(breakdown["pending"]["rows"]),
          KPIS, (20, 7), True, (665, 260))

    top3 = end2 + 2
    end3 = matrix(top3, "Hours above the threshold", "Hours in the day", breakdown["hours"]["rows"], total=False)

    top4 = end3 + 2
    ws.merge_range(top4, 1, top4, 5, "Files in this report", f["section"])
    ws.set_row(top4, 22)
    for n, h in enumerate(["KPI file", "Status", "Rows", "Sites", "New sites"]):
        ws.write(top4 + 1, 1 + n, h, f["head" if n == 0 else "head_c"])
    for n, s in enumerate(sources):
        r = top4 + 2 + n
        row = s["file"]
        ws.write(r, 1, s["label"], f["cell"])
        if row:
            ws.write(r, 2, "Received", fmt(border=1, border_color=LINE, align="center", bold=True,
                                           font_color="#006300", bg_color="#E7F6E7"))
            ws.write(r, 3, row["row_count"], f["num"])
            ws.write(r, 4, row["site_count"], f["num"])
            ws.write(r, 5, 0 if row["is_initial"] else row["new_site_count"], f["num"])
        else:
            ws.write(r, 2, "Missing", fmt(border=1, border_color=LINE, align="center", bold=True,
                                          font_color="#7A4B00", bg_color="#FFF3D1"))
            for c in (3, 4, 5):
                ws.write(r, c, "-", f["num"])

    top5 = top4 + 3 + len(sources)
    ws.merge_range(top5, 1, top5, 5, "Rules used", f["section"])
    ws.set_row(top5, 22)
    rules = [f"{KPI_LABELS[k]}: a site is listed with {_rule_text(k, th)} (worst path in each hour)." for k in KPIS]
    rules.append("Pending days: the previous report's value plus one while the site stays listed; 0 on the first day.")
    rules.append("Feedback is carried forward from the previous report until someone changes it.")
    for n, text in enumerate(rules):
        ws.merge_range(top5 + 1 + n, 1, top5 + 1 + n, 5, text, fmt(font_color=INK_2, text_wrap=True))
    ws.set_landscape()
    ws.set_paper(9)
    ws.fit_to_pages(1, 1)

    # ================= one sheet per KPI =================
    for kpi in KPIS:
        rows = by_kpi[kpi]
        ws = wb.add_worksheet(KPI_SHEET[kpi])
        ws.hide_gridlines(2)
        ws.set_tab_color(KPI_COLOR[kpi])
        widths = [6, 56, 11, 13, 12, 12, 44, 24, 9]
        for c, w in enumerate(widths):
            ws.set_column(c, c, w)
        ws.set_row(0, 28)
        ws.merge_range(0, 0, 0, 8, f"{KPI_LABELS[kpi]}  ·  {len(rows):,} sites", f["sheet_title"])
        ws.merge_range(1, 0, 1, 8, f"{report_date:%d %B %Y}  ·  listed with {_rule_text(kpi, th)}", f["sheet_sub"])
        ws.set_row(2, 6)
        heads = ["No.", "Site", "Category", "Hours above threshold", f"Peak ({KPI_UNIT[kpi]})", "Pending days",
                 "Feedback", "KPI file", "New site"]
        ws.set_row(3, 32)
        for c, h in enumerate(heads):
            ws.write(3, c, h, f["head" if c in (1, 6, 7) else "head_c"])
        for n, i in enumerate(rows):
            r = 4 + n
            alt = "_alt" if n % 2 else ""
            ws.write(r, 0, n + 1, f["num" + alt])
            ws.write(r, 1, i["name"], f["cell" + alt])
            ws.write(r, 2, i["category"], f["num" + alt])
            ws.write(r, 3, i["hours"], f["num" + alt])
            if i["peak"] is None:
                ws.write(r, 4, "", f["cell" + alt])
            else:
                ws.write(r, 4, round(float(i["peak"]), 2), f["dec" + alt])
            ws.write(r, 5, i["pending"], f["num" + alt])
            ws.write(r, 6, i["feedback"], f["wrap" + alt])
            ws.write(r, 7, SOURCES[i["source"]].label if i["source"] in SOURCES else "", f["cell" + alt])
            if i["is_new"]:
                ws.write(r, 8, "NEW", f["new_flag"])
            else:
                ws.write(r, 8, "", f["cell" + alt])
        last = 3 + max(len(rows), 1)
        if rows:
            ws.conditional_format(4, 3, last, 3, {"type": "data_bar", "bar_color": KPI_TINT[kpi], "bar_solid": True,
                                                  "min_type": "num", "min_value": 0, "max_type": "num",
                                                  "max_value": 25, "bar_no_border": True})
            ws.conditional_format(4, 5, last, 5, {"type": "cell", "criteria": ">=", "value": 30,
                                                  "format": f["pend_long"]})
            ws.conditional_format(4, 5, last, 5, {"type": "cell", "criteria": "between", "minimum": 7, "maximum": 29,
                                                  "format": f["pend_mid"]})
        else:
            ws.merge_range(4, 0, 4, 8, "No sites reached the threshold on this day.", f["cell"])
        ws.autofilter(3, 0, last, 8)
        ws.freeze_panes(4, 2)
        ws.set_landscape()
        ws.set_paper(9)
        ws.fit_to_pages(1, 0)
        ws.repeat_rows(3)

    # ================= New sites =================
    ws = wb.add_worksheet("New Sites")
    ws.hide_gridlines(2)
    ws.set_tab_color(GREEN)
    for c, w in enumerate([6, 56, 24, 11, 9, 12, 12, 12]):
        ws.set_column(c, c, w)
    ws.set_row(0, 28)
    ws.merge_range(0, 0, 0, 7, f"New sites  ·  {len(fresh):,}", f["sheet_title"])
    ws.merge_range(1, 0, 1, 7, f"{report_date:%d %B %Y}  ·  sites that were not in any earlier day's files",
                   f["sheet_sub"])
    ws.set_row(2, 6)
    ws.set_row(3, 32)
    for c, h in enumerate(["No.", "Site", "KPI file", "Category", "Paths", "PD hours", "Jitter hours", "RTT hours"]):
        ws.write(3, c, h, f["head" if c in (1, 2) else "head_c"])
    for n, s in enumerate(fresh):
        r = 4 + n
        alt = "_alt" if n % 2 else ""
        ws.write(r, 0, n + 1, f["num" + alt])
        ws.write(r, 1, s["name"], f["cell" + alt])
        ws.write(r, 2, SOURCES[s["source"]].label, f["cell" + alt])
        ws.write(r, 3, s["category"], f["num" + alt])
        for c, key in enumerate(["path_count", "pd_hours", "jitter_hours", "rtt_hours"]):
            ws.write(r, 4 + c, s[key] if s[key] is not None else "", f["num" + alt])
    if not fresh:
        ws.merge_range(4, 0, 4, 7, "No new sites appeared in this day's files.", f["cell"])
    else:
        ws.autofilter(3, 0, 3 + len(fresh), 7)
    ws.freeze_panes(4, 2)

    # ================= Classic layout (same columns as the old notebook report) =================
    ws = wb.add_worksheet("Classic layout")
    ws.set_tab_color(MUTED)
    head = wb.add_format({"font_name": FONT, "font_size": 10, "bold": True, "font_color": "#FFFFFF",
                          "bg_color": BRAND, "border": 1, "border_color": "#FFFFFF",
                          "text_wrap": True, "align": "center", "valign": "vcenter"})
    day = wb.add_format({"font_name": FONT, "font_size": 10, "num_format": "dd/mm/yyyy", "align": "center",
                         "valign": "vcenter", "border": 1, "border_color": LINE})
    for c, (h, w) in enumerate(zip(CLASSIC_HEADS, (13, 56, 14, 20, 12, 13, 36))):
        ws.set_column(c, c, w)
        ws.write(0, c, h, head)
    r = 0
    for kpi in KPIS:
        for i in by_kpi[kpi]:
            r += 1
            ws.write_datetime(r, 0, report_date, day)
            ws.write(r, 1, i["name"], f["cell"])
            ws.write(r, 2, CLASSIC_CODE[kpi], f["num"])
            ws.write(r, 3, i["hours"], f["num"])
            ws.write(r, 4, i["category"], f["num"])
            ws.write(r, 5, i["pending"], f["num"])
            ws.write(r, 6, i["feedback"], f["cell"])
    if r:
        ws.autofilter(0, 0, r, 6)
    ws.set_row(0, 44)
    ws.freeze_panes(1, 2)

    wb.close()
    return buf.getvalue()


def save_copy(report_date: date) -> Path:
    """Write the workbook into the reports folder and return its path."""
    data = build(report_date)
    path = config.reports_dir / filename_for(report_date)
    try:
        path.write_bytes(data)
    except PermissionError:
        # The previous copy is open in Excel; keep both.
        from datetime import datetime
        path = config.reports_dir / f"{path.stem} ({datetime.now():%H%M%S}).xlsx"
        path.write_bytes(data)
    return path
