"""Fetches KPI dump files from a server over SFTP into the inbox folder.

The server is only read: nothing on it is changed or deleted. A routine check takes files
modified in the last few days that were not fetched before. An administrator can also ask
for the files of a range of days, which fetches them again even if they were fetched before
(an identical file is recognised by the importer and changes nothing).
"""
from __future__ import annotations

import logging
import socket
import stat
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

from . import db, watcher
from .config import BASE_DIR, config

log = logging.getLogger("kpi.remote")
SETTLE_SECONDS = 60            # a file changed more recently than this may still be being written
MAX_RANGE_DAYS = 31
TIMEOUT = 20

_lock = threading.Lock()
_state: dict = {"running": False, "busy": False, "last_check": None, "last_ok": None, "error": None,
                "fetched": 0, "last_files": [], "last_result": None, "seq": 0}
_request: list = []            # pending manual checks: (date_from, date_to) or (None, None)
_wake = threading.Event()
_stop = threading.Event()
_thread: threading.Thread | None = None


def enabled() -> bool:
    return bool(config.sftp_host and config.sftp_user and config.sftp_dir)


def status() -> dict:
    with _lock:
        s = dict(_state, last_files=list(_state["last_files"]))
    s.update(enabled=enabled(), host=config.sftp_host, port=config.sftp_port, user=config.sftp_user,
             folder=config.sftp_dir, minutes=config.sftp_minutes, days=config.sftp_days,
             login="key file" if config.sftp_key_file else "password")
    return s


def _explain(exc: Exception) -> str:
    """A reason an administrator can act on."""
    import paramiko

    where = f"{config.sftp_host}:{config.sftp_port}"
    if isinstance(exc, paramiko.BadHostKeyException):
        return (f"The identity of {where} is different from the first time this system connected to it, so the "
                "connection was refused. If the server was rebuilt, delete instance\\known_hosts and check again.")
    if isinstance(exc, paramiko.AuthenticationException):
        return f"{where} refused the login for user '{config.sftp_user}'. Check the user name and the {status()['login']}."
    if isinstance(exc, _FolderMissing):
        return f"The folder '{config.sftp_dir}' was not found on the server, or this user may not open it."
    if isinstance(exc, _KeyFileMissing):
        return f"The key file '{config.sftp_key_file}' was not found on this PC."
    if isinstance(exc, (socket.timeout, TimeoutError, socket.gaierror, ConnectionError, paramiko.ssh_exception.NoValidConnectionsError)):
        return f"Could not reach {where}. Check the address, the network or VPN, and that the server is on."
    if isinstance(exc, paramiko.SSHException):
        return f"The connection to {where} failed: {exc}"
    return f"{type(exc).__name__}: {exc}"


class _FolderMissing(Exception):
    pass


class _KeyFileMissing(Exception):
    pass


def _connect():
    import paramiko

    if config.sftp_key_file and not Path(config.sftp_key_file).is_file():
        raise _KeyFileMissing()
    known = BASE_DIR / "instance" / "known_hosts"
    known.parent.mkdir(parents=True, exist_ok=True)
    known.touch(exist_ok=True)
    client = paramiko.SSHClient()
    client.load_host_keys(str(known))
    # The server's identity is remembered on the first connection; a different one later is refused.
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(config.sftp_host, port=config.sftp_port, username=config.sftp_user,
                   password=config.sftp_password or None, key_filename=config.sftp_key_file or None,
                   passphrase=config.sftp_key_passphrase or None, allow_agent=False, look_for_keys=False,
                   timeout=TIMEOUT, banner_timeout=TIMEOUT, auth_timeout=TIMEOUT)
    return client


def _wanted(entries: list, date_from: date | None, date_to: date | None) -> list:
    now = time.time()
    files = [e for e in entries
             if stat.S_ISREG(e.st_mode or 0) and PurePosixPath(e.filename).suffix.lower() in watcher.EXTENSIONS
             and not e.filename.startswith(("~$", ".")) and e.st_size and e.st_mtime <= now - SETTLE_SECONDS]
    if date_from:
        files = [e for e in files if date_from <= datetime.fromtimestamp(e.st_mtime).date() <= date_to]
    else:
        files = [e for e in files if e.st_mtime >= now - config.sftp_days * 86400]
        done = {(r["name"], r["size_bytes"], int(r["modified_at"].timestamp())) for r in db.query(
            "SELECT name, size_bytes, modified_at FROM remote_files WHERE modified_at >= %s",
            (datetime.fromtimestamp(now - config.sftp_days * 86400 - 86400, timezone.utc),))}
        files = [e for e in files if (e.filename, e.st_size, int(e.st_mtime)) not in done]
    return sorted(files, key=lambda e: e.st_mtime)


def _download(sftp, entry) -> None:
    name = Path(entry.filename).name
    target = config.inbox_dir / name
    if target.exists():
        target = config.inbox_dir / f"{target.stem}__{datetime.now():%H%M%S}{target.suffix}"
    part = config.inbox_dir / f".{target.name}.part"       # hidden from the watcher until complete
    try:
        sftp.get(str(PurePosixPath(config.sftp_dir) / entry.filename), str(part))
        if part.stat().st_size != entry.st_size:
            raise OSError(f"{name} changed on the server while it was being fetched; it will be tried again.")
        part.replace(target)
    finally:
        part.unlink(missing_ok=True)
    db.execute(
        """INSERT INTO remote_files (name, size_bytes, modified_at) VALUES (%s, %s, %s)
           ON CONFLICT (name, size_bytes, modified_at) DO UPDATE SET fetched_at = now()""",
        (entry.filename, entry.st_size, datetime.fromtimestamp(int(entry.st_mtime), timezone.utc)))
    watcher.nudge()


def check(date_from: date | None = None, date_to: date | None = None) -> int:
    """One visit to the server. Returns how many files were fetched; raises on a connection problem."""
    client = _connect()
    try:
        sftp = client.open_sftp()
        sftp.get_channel().settimeout(120)
        try:
            entries = sftp.listdir_attr(config.sftp_dir)
        except (FileNotFoundError, PermissionError) as exc:
            raise _FolderMissing() from exc
        files = _wanted(entries, date_from, date_to)
        for entry in files:
            if _stop.is_set():
                break
            log.info("Fetching %s (%.1f MB)", entry.filename, entry.st_size / 1048576)
            _download(sftp, entry)
            with _lock:
                _state["fetched"] += 1
                _state["last_files"] = ([{"name": entry.filename, "at": datetime.now()}] + _state["last_files"])[:6]
        return len(files)
    finally:
        client.close()


def _run(date_from: date | None, date_to: date | None, manual: bool) -> None:
    with _lock:
        _state["busy"] = True
        before = _state["error"]
    error, count = None, 0
    try:
        count = check(date_from, date_to)
    except Exception as exc:  # noqa: BLE001 - every problem is shown on the Imports page
        error = _explain(exc)
        log.warning("Server check failed: %s", error)
    now = datetime.now()
    if error:
        result = error
    elif date_from:
        result = f"{count} file{'' if count == 1 else 's'} fetched for {date_from:%d %b %Y} to {date_to:%d %b %Y}."
    else:
        result = f"{count} new file{'' if count == 1 else 's'} fetched." if count else "No new files on the server."
    with _lock:
        _state.update(busy=False, last_check=now, error=error)
        if not error:
            _state["last_ok"] = now
        if manual or count or not error:
            _state["last_result"] = result
        if manual or count or error != before:
            _state["seq"] += 1


def _loop() -> None:
    log.info("Checking %s@%s:%s%s every %s minutes", config.sftp_user, config.sftp_host, config.sftp_port,
             config.sftp_dir, config.sftp_minutes)
    _wake.wait(3)
    while not _stop.is_set():
        _wake.clear()
        with _lock:
            pending, _request[:] = list(_request), []
        if pending:
            for date_from, date_to in pending:
                _run(date_from, date_to, manual=True)
        else:
            _run(None, None, manual=False)
        with _lock:
            more = bool(_request)
        if not more:
            _wake.wait(config.sftp_minutes * 60)


def check_now(date_from: date | None = None, date_to: date | None = None) -> None:
    """Ask the background checker to visit the server now (optionally for a range of days)."""
    with _lock:
        _request.append((date_from, date_to))
        _state["busy"] = True
    _wake.set()


def range_problem(date_from: date, date_to: date) -> str | None:
    if date_from > date_to:
        return "The first day must not be after the last day."
    if date_to > date.today():
        return "The last day cannot be in the future."
    if date_to - date_from > timedelta(days=MAX_RANGE_DAYS - 1):
        return f"Choose at most {MAX_RANGE_DAYS} days at a time."
    return None


def start() -> None:
    global _thread
    if not enabled() or (_thread and _thread.is_alive()):
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="server-fetch", daemon=True)
    _thread.start()
    with _lock:
        _state["running"] = True


def stop() -> None:
    _stop.set()
    _wake.set()
    if _thread:
        _thread.join(timeout=5)
    with _lock:
        _state["running"] = False
