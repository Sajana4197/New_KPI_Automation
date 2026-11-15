"""The four daily KPI file formats and how to read each into one common shape.

Files are recognised by their column headers, never by file name, because the
names carry the exporting user's name and a timestamp that changes every day.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class Source:
    key: str
    label: str
    vendor: str
    short: str


SOURCES: dict[str, Source] = {
    "huawei_ippm": Source("huawei_ippm", "Huawei IPPM", "Huawei", "HW"),
    "zte_hist": Source("zte_hist", "ZTE History Performance", "ZTE", "ZTE-H"),
    "zte_q1": Source("zte_q1", "ZTE Temporary Query 1", "ZTE", "ZTE-Q1"),
    "zte_q2": Source("zte_q2", "ZTE Temporary Query 2", "ZTE", "ZTE-Q2"),
}
SOURCE_ORDER = list(SOURCES)

# Header cells (lower case) that must all be present for a file to be that format.
_SIGNATURES: dict[str, set[str]] = {
    "huawei_ippm": {"time", "name of ippm task"},
    "zte_hist": {"start time", "managedelement name", "source ip", "destination ip"},
    "zte_q1": {"begin time", "managed element", "srcip", "dstip"},
    "zte_q2": {"begin time", "managed element", "source ip name", "destination ip name"},
}

COLUMNS = ["ext_key", "name", "path", "hour_ts", "pd_pct", "rtt_ms", "jitter_ms", "tx_packets"]


class UnknownFileFormat(Exception):
    pass


@dataclass
class ParsedFile:
    source: str
    report_date: date
    data: pd.DataFrame          # COLUMNS, one row per site + path + hour
    raw_rows: int
    warnings: list[str]


def _norm(cell) -> str:
    return re.sub(r"\s+", " ", str(cell)).strip().lower() if cell is not None else ""


def _read_first_sheet(path: Path) -> list[list]:
    """First worksheet as a list of rows. Calamine is about ten times faster than openpyxl."""
    try:
        from python_calamine import CalamineWorkbook

        return CalamineWorkbook.from_path(str(path)).get_sheet_by_index(0).to_python(skip_empty_area=False)
    except ImportError:
        import openpyxl

        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            return [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]
        finally:
            wb.close()


def _find_header(rows: list[list]) -> tuple[str, int]:
    for i, row in enumerate(rows[:40]):
        cells = {_norm(c) for c in row}
        for source, signature in _SIGNATURES.items():
            if signature <= cells:
                return source, i
    raise UnknownFileFormat("The column headers do not match any of the four known KPI file formats.")


def _col(df: pd.DataFrame, *candidates: str, prefix: bool = False, required: bool = True):
    for cand in candidates:
        for c in df.columns:
            if c == cand or (prefix and c.startswith(cand)):
                return df[c]
    if required:
        raise UnknownFileFormat(f"Expected column '{candidates[0]}' was not found.")
    return None


def _num(series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _text(series) -> pd.Series:
    return series.astype("string").str.strip()


def _zte_key(id_series, name_series) -> pd.Series:
    """Managed element ID as text; falls back to the number in brackets at the end of the name."""
    ids = _num(id_series).astype("Int64").astype("string")
    from_name = name_series.str.extract(r"\((\d+)\)\s*$")[0]
    return ids.fillna(from_name).fillna(name_series.str.lower())


def parse_file(path: Path) -> ParsedFile:
    rows = _read_first_sheet(path)
    source, h = _find_header(rows)
    header = [_norm(c) for c in rows[h]]
    df = pd.DataFrame(rows[h + 1:], columns=header)
    df = df.loc[:, [c for c in df.columns if c]]
    raw_rows = len(df)
    warnings: list[str] = []
    out = pd.DataFrame(index=df.index)

    if source == "huawei_ippm":
        out["name"] = _text(_col(df, "name of ippm task"))
        out["ext_key"] = out["name"].str.lower()
        ne = _col(df, "ne name", required=False)
        out["path"] = _text(ne) if ne is not None else "IPPM"
        out["hour_ts"] = pd.to_datetime(_col(df, "time"), errors="coerce")
        out["pd_pct"] = _num(_col(df, "average packets lost ratio of forward performance monitoring", prefix=True))
        out["rtt_ms"] = _num(_col(df, "average time delay of rtt", prefix=True))
        out["jitter_ms"] = _num(_col(df, "standard deviation of forward performance monitoring", prefix=True))
        traffic = _col(df, "local send traffic in packets", prefix=True, required=False)
        out["tx_packets"] = _num(traffic) if traffic is not None else float("nan")
        end_ts = None
    else:
        if source == "zte_hist":
            out["name"] = _text(_col(df, "managedelement name"))
            out["ext_key"] = _zte_key(_col(df, "managedelement"), out["name"])
            out["path"] = _text(_col(df, "source ip")) + " > " + _text(_col(df, "destination ip"))
            out["hour_ts"] = pd.to_datetime(_col(df, "start time"), errors="coerce")
            pd_col = _col(df, "packet loss rate", prefix=True)
        else:
            out["name"] = _text(_col(df, "managed element"))
            out["ext_key"] = _zte_key(_col(df, "managedelement id"), out["name"])
            if source == "zte_q1":
                out["path"] = _text(_col(df, "srcip")) + " > " + _text(_col(df, "dstip"))
            else:
                out["path"] = _text(_col(df, "source ip name")) + " > " + _text(_col(df, "destination ip name"))
            out["hour_ts"] = pd.to_datetime(_col(df, "begin time"), errors="coerce")
            pd_col = _col(df, "packet loss rate", prefix=True)
        # ZTE exports packet loss as a 0-1 ratio (even where the header says %), so
        # convert to percent. Rounding removes floating point noise at the threshold.
        out["pd_pct"] = (_num(pd_col) * 100).round(6)
        out["rtt_ms"] = _num(_col(df, "avg time delay", prefix=True))
        jitter = _col(df, "avg delay jitter", prefix=True, required=False)
        if jitter is None:
            warnings.append("Jitter column is missing in this file; jitter was not evaluated for these sites.")
            out["jitter_ms"] = float("nan")
        else:
            out["jitter_ms"] = _num(jitter)
        out["tx_packets"] = float("nan")
        end_ts = pd.to_datetime(_col(df, "end time", required=False), errors="coerce") \
            if "end time" in df.columns else None

    bad = out["name"].isna() | (out["name"] == "") | out["hour_ts"].isna()
    if bad.any():
        warnings.append(f"{int(bad.sum())} rows without a site name or time were skipped.")
        out = out[~bad]
    if out.empty:
        raise UnknownFileFormat("The file has the right headers but no data rows.")
    out["path"] = out["path"].fillna("-")

    report_date = _report_date(source, out["hour_ts"], end_ts)

    # The newest name wins when one ID carries two names in the same file (renamed mid-day).
    latest = out.sort_values("hour_ts").groupby("ext_key")["name"].last()
    if latest.size < out["name"].nunique():
        warnings.append(f"{out['name'].nunique() - latest.size} site(s) appear under two names; the newest name is used.")
    out["name"] = out["ext_key"].map(latest)

    # Several rows can describe the same path and hour; keep the worst value of each.
    data = out.groupby(["ext_key", "name", "path", "hour_ts"], as_index=False, sort=False).agg(
        pd_pct=("pd_pct", "max"), rtt_ms=("rtt_ms", "max"),
        jitter_ms=("jitter_ms", "max"), tx_packets=("tx_packets", "max"),
    )
    return ParsedFile(source, report_date, data[COLUMNS], raw_rows, warnings)


def _report_date(source: str, hour_ts: pd.Series, end_ts: pd.Series | None) -> date:
    """The day the data belongs to: the date on which the measured window ends."""
    if end_ts is not None and end_ts.notna().any():
        return end_ts.max().date()
    first, last = hour_ts.min(), hour_ts.max()
    # Huawei lists both ends of the window (08:00 ... 08:00 next day, 25 samples).
    if last > first and last.time() == first.time():
        return last.date()
    return (last + timedelta(hours=1)).date()


def category_for(name: str) -> str:
    """Site category from the first letters of the name, exactly as the IPPM notebook does."""
    c = (name or "").upper().strip()
    if c.startswith("COL") or c.startswith("CB"):
        return "CBD"
    if c.startswith("Z"):
        return "GC"
    if c[:1] in ("Q", "L"):
        if c[1:3] == "CC":
            return "CBD"
        if c[1:3] == "CZ":
            return "GC"
        return "OS"
    if c.startswith("I"):
        return "IBS"
    return "OS"


CATEGORIES = ["CBD", "GC", "IBS", "OS"]
CATEGORY_LABELS = {"CBD": "Central Business District", "GC": "Greater Colombo", "IBS": "In-Building", "OS": "Outstation"}
