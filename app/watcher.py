"""Watches the inbox folder and imports each KPI file once it has finished copying."""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from pathlib import Path

from .config import config
from .importer import import_file

log = logging.getLogger("kpi.watcher")
POLL_SECONDS = 4
EXTENSIONS = {".xlsx", ".xlsm", ".xls"}

_state_lock = threading.Lock()
_state: dict = {"running": False, "busy": None, "waiting": [], "last_scan": None, "last_import": None,
                "imported": 0}
_sizes: dict[str, tuple[int, float]] = {}      # file name -> (size, modified time) at the previous poll
_wake = threading.Event()
_stop = threading.Event()
_thread: threading.Thread | None = None


def status() -> dict:
    with _state_lock:
        return dict(_state, waiting=list(_state["waiting"]))


def _candidates() -> list[Path]:
    files = [p for p in config.inbox_dir.iterdir()
             if p.is_file() and p.suffix.lower() in EXTENSIONS and not p.name.startswith(("~$", "."))]
    return sorted(files, key=lambda p: p.stat().st_mtime)


def _is_ready(path: Path) -> bool:
    """True when the file has stopped growing and nothing else is still writing it."""
    try:
        st = path.stat()
        seen = _sizes.get(path.name)
        _sizes[path.name] = (st.st_size, st.st_mtime)
        if seen != (st.st_size, st.st_mtime) or st.st_size == 0:
            return False
        with path.open("r+b"):          # fails while Excel or a copy still holds the file
            return True
    except OSError:
        return False


def scan_once() -> int:
    """Import every file in the inbox that is ready. Returns how many were handled."""
    handled = 0
    files = _candidates()
    with _state_lock:
        _state["waiting"] = [p.name for p in files]
        _state["last_scan"] = datetime.now()
    for path in files:
        if _stop.is_set():
            break
        if not _is_ready(path):
            continue
        with _state_lock:
            _state["busy"] = path.name
        try:
            row = import_file(path)
            handled += 1
            with _state_lock:
                _state["imported"] += 1
                _state["last_import"] = {"filename": path.name, "status": row["status"],
                                         "message": row.get("message"), "at": datetime.now()}
        finally:
            _sizes.pop(path.name, None)
            with _state_lock:
                _state["busy"] = None
                _state["waiting"] = [n for n in _state["waiting"] if n != path.name]
    for name in list(_sizes):
        if not (config.inbox_dir / name).exists():
            _sizes.pop(name, None)
    return handled


def _loop() -> None:
    log.info("Watching %s", config.inbox_dir)
    while not _stop.is_set():
        try:
            scan_once()
        except Exception:  # noqa: BLE001 - the watcher must survive anything
            log.exception("Inbox scan failed")
        _wake.wait(POLL_SECONDS)
        _wake.clear()


def nudge() -> None:
    """Ask for an immediate scan (used after an upload and by the Scan now button)."""
    _wake.set()


def start() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="inbox-watcher", daemon=True)
    _thread.start()
    with _state_lock:
        _state["running"] = True


def stop() -> None:
    _stop.set()
    _wake.set()
    if _thread:
        _thread.join(timeout=60)
    with _state_lock:
        _state["running"] = False
