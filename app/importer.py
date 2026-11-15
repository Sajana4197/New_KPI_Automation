"""Load one KPI file into the database and register any sites not seen before."""
from __future__ import annotations

import hashlib
import io
import logging
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

from . import db
from .config import config
from .reporting import rebuild_daily
from .sources import SOURCES, ParsedFile, UnknownFileFormat, category_for, parse_file

log = logging.getLogger("kpi.import")
_import_lock = threading.Lock()          # one import at a time


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _move(path: Path, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / path.name
    if target.exists():
        if target.stat().st_size == path.stat().st_size and _sha256(target) == _sha256(path):
            path.unlink()                # the very same file is already kept there
            return target
        target = folder / f"{path.stem}__{datetime.now():%Y%m%d_%H%M%S}{path.suffix}"
    shutil.move(str(path), str(target))
    return target


def _store(parsed: ParsedFile, filename: str, sha: str, size: int) -> dict:
    df = parsed.data
    d = parsed.report_date
    src = parsed.source
    with db.tx() as conn:
        # ---- sites: add new ones, follow renames -------------------------------------
        existing = {r["ext_key"]: r for r in conn.execute(
            "SELECT id, ext_key, name, last_seen FROM sites WHERE source = %s", (src,))}
        is_initial = not existing
        names = df.drop_duplicates("ext_key").set_index("ext_key")["name"].to_dict()

        new_keys = [k for k in names if k not in existing]
        if new_keys:
            for r in conn.execute(
                    """INSERT INTO sites (source, ext_key, name, category, first_seen, last_seen)
                       SELECT %s, k, n, c, %s, %s
                       FROM unnest(%s::text[], %s::text[], %s::text[]) AS t(k, n, c)
                       RETURNING id, ext_key, name, last_seen""",
                    (src, d, d, new_keys, [names[k] for k in new_keys],
                     [category_for(names[k]) for k in new_keys])):
                existing[r["ext_key"]] = r

        renamed = [(existing[k]["id"], n) for k, n in names.items()
                   if k not in new_keys and existing[k]["name"] != n and d >= existing[k]["last_seen"]]
        if renamed:
            conn.execute(
                """UPDATE sites s SET name = t.n, category = t.c
                   FROM unnest(%s::int[], %s::text[], %s::text[]) AS t(id, n, c) WHERE s.id = t.id""",
                ([r[0] for r in renamed], [r[1] for r in renamed], [category_for(r[1]) for r in renamed]))

        site_ids = [existing[k]["id"] for k in names]
        conn.execute(
            """UPDATE sites SET first_seen = LEAST(first_seen, %s), last_seen = GREATEST(last_seen, %s)
               WHERE id = ANY(%s)""", (d, d, site_ids))
        conn.execute(
            """INSERT INTO site_names (site_id, name, first_seen, last_seen)
               SELECT id, n, %s, %s FROM unnest(%s::int[], %s::text[]) AS t(id, n)
               ON CONFLICT (site_id, name) DO UPDATE
               SET first_seen = LEAST(site_names.first_seen, EXCLUDED.first_seen),
                   last_seen = GREATEST(site_names.last_seen, EXCLUDED.last_seen)""",
            (d, d, site_ids, list(names.values())))

        # ---- paths -------------------------------------------------------------------
        df = df.assign(site_id=df["ext_key"].map({k: v["id"] for k, v in existing.items()}).astype("int64"))
        pairs = df[["site_id", "path"]].drop_duplicates()
        conn.execute(
            """INSERT INTO paths (site_id, label)
               SELECT * FROM unnest(%s::int[], %s::text[]) ON CONFLICT (site_id, label) DO NOTHING""",
            (pairs["site_id"].tolist(), pairs["path"].tolist()))
        path_rows = conn.execute("SELECT id, site_id, label FROM paths WHERE site_id = ANY(%s)", (site_ids,)).fetchall()
        path_map = pd.DataFrame(path_rows).rename(columns={"id": "path_id", "label": "path"})
        df = df.merge(path_map, on=["site_id", "path"], how="left", validate="many_to_one")

        # ---- hourly data: replace whatever this source had for this date ---------------
        conn.execute(
            """DELETE FROM kpi_hourly h USING paths p, sites s
               WHERE h.path_id = p.id AND p.site_id = s.id AND s.source = %s AND h.report_date = %s""",
            (src, d))
        out = pd.DataFrame({
            "report_date": d.isoformat(),
            "path_id": df["path_id"].astype("int64"),
            "hour_ts": df["hour_ts"].dt.strftime("%Y-%m-%d %H:%M:%S"),
            "pd_pct": df["pd_pct"], "rtt_ms": df["rtt_ms"], "jitter_ms": df["jitter_ms"],
            "tx_packets": df["tx_packets"].round().astype("Int64"),
        })
        buf = io.StringIO()
        out.to_csv(buf, header=False, index=False, na_rep="")
        with conn.cursor() as cur:
            with cur.copy("COPY kpi_hourly (report_date, path_id, hour_ts, pd_pct, rtt_ms, jitter_ms, tx_packets) "
                          "FROM STDIN WITH (FORMAT csv, NULL '')") as copy:
                copy.write(buf.getvalue())

        rebuild_daily(conn, d, src)

        conn.execute(
            "UPDATE import_files SET status = 'replaced' WHERE report_date = %s AND source = %s AND status = 'ok'",
            (d, src))
        return conn.execute(
            """INSERT INTO import_files (report_date, source, filename, sha256, size_bytes, row_count, site_count,
                                         new_site_count, renamed_count, is_initial, hour_min, hour_max, status, message)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'ok', %s) RETURNING *""",
            (d, src, filename, sha, size, parsed.raw_rows, len(names), len(new_keys), len(renamed), is_initial,
             df["hour_ts"].min().to_pydatetime(), df["hour_ts"].max().to_pydatetime(),
             " ".join(parsed.warnings) or None)).fetchone()


def import_file(path: Path, move: bool = True) -> dict:
    """Import one file. Returns the import_files row (status ok / duplicate / failed)."""
    path = Path(path)
    with _import_lock:
        started = time.perf_counter()
        sha, size = _sha256(path), path.stat().st_size
        try:
            parsed = parse_file(path)
            already = db.query_one(
                "SELECT id FROM import_files WHERE sha256 = %s AND report_date = %s AND source = %s AND status = 'ok'",
                (sha, parsed.report_date, parsed.source))
            if already:
                row = db.query_one(
                    """INSERT INTO import_files (report_date, source, filename, sha256, size_bytes, status, message)
                       VALUES (%s, %s, %s, %s, %s, 'duplicate', %s) RETURNING *""",
                    (parsed.report_date, parsed.source, path.name, sha, size,
                     "Identical file was already imported; nothing changed."))
            else:
                row = _store(parsed, path.name, sha, size)
            log.info("%s: %s %s (%s rows, %s new sites) in %.1fs", path.name, row["status"],
                     SOURCES[parsed.source].label, row["row_count"], row["new_site_count"],
                     time.perf_counter() - started)
            if move:
                stored = _move(path, config.archive_dir / f"{parsed.report_date:%Y-%m-%d}")
                db.execute("UPDATE import_files SET stored_path = %s WHERE id = %s", (str(stored), row["id"]))
                row["stored_path"] = str(stored)
            return row
        except Exception as exc:  # noqa: BLE001 - every failure is logged and shown on the Imports page
            message = str(exc) if isinstance(exc, UnknownFileFormat) else f"{type(exc).__name__}: {exc}"
            log.exception("Import of %s failed", path.name)
            stored = None
            if move and path.exists():
                try:
                    stored = str(_move(path, config.failed_dir))
                except OSError:
                    stored = None
            return db.query_one(
                """INSERT INTO import_files (filename, sha256, size_bytes, status, message, stored_path)
                   VALUES (%s, %s, %s, 'failed', %s, %s) RETURNING *""",
                (path.name, sha, size, message[:1000], stored))
