"""Fetches KPI dump files from a server over SFTP into the inbox folder.

The server is only read: nothing on it is changed or deleted. A routine check takes files
modified in the last few days that were not fetched before. An administrator can also ask
for the files of a range of days, which fetches them again even if they were fetched before
(an identical file is recognised by the importer and changes nothing).
"""
from __future__ import annotations

import base64
import hashlib
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

# ---- server settings ----------------------------------------------------------------------
# The .env file gives the starting values. Once an administrator saves the server on the Imports
# page, the saved values (kept in the settings table, key "sftp") are used instead. Passwords are
# stored encrypted with this installation's secret key (instance/secret.key), never as plain text.
SETTING_KEY = "sftp"
FIELDS = ("host", "port", "user", "password", "key_file", "key_passphrase", "folder", "minutes", "days")
SECRET_FIELDS = ("password", "key_passphrase")
_cfg: dict | None = None


def _fernet():
    from cryptography.fernet import Fernet

    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("sftp:" + config.secret_key).encode()).digest()))


def _seal(text: str) -> str:
    return _fernet().encrypt(text.encode()).decode() if text else ""


def _open(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except Exception:  # noqa: BLE001 - a key that no longer matches: treat as not set
        log.warning("A saved server password could not be read (the secret key changed?). Enter it again.")
        return ""


def _from_env() -> dict:
    return {"host": config.sftp_host, "port": config.sftp_port, "user": config.sftp_user, "password": config.sftp_password,
            "key_file": config.sftp_key_file, "key_passphrase": config.sftp_key_passphrase, "folder": config.sftp_dir,
            "minutes": config.sftp_minutes, "days": config.sftp_days, "source": "env"}


def load() -> dict:
    """Read the settings in use (saved ones if an administrator saved any, else .env)."""
    global _cfg
    saved = db.get_setting(SETTING_KEY)
    if saved:
        cfg = {k: saved.get(k, "") for k in FIELDS}
        for k in SECRET_FIELDS:
            cfg[k] = _open(saved.get(k, ""))
        cfg.update(port=int(cfg["port"] or 22), minutes=int(cfg["minutes"] or 5), days=int(cfg["days"] or 2),
                   source="saved", updated_at=None)
    else:
        cfg = _from_env()
    _cfg = cfg
    return cfg


def current() -> dict:
    return _cfg if _cfg is not None else load()


def save(values: dict, user_id: int | None) -> None:
    """Store complete new settings and use them from the next check on."""
    stored = {k: (_seal(values.get(k) or "") if k in SECRET_FIELDS else values.get(k)) for k in FIELDS}
    db.set_setting(SETTING_KEY, stored, user_id)
    load()
    with _lock:
        _state.update(error=None, last_result=None, last_check=None, last_ok=None, last_files=[], fetched=0)
        _state["seq"] += 1
    start()
    if enabled():
        check_now()


def problems(values: dict) -> str | None:
    """What is wrong with settings an administrator entered, or None."""
    if not values["host"]:
        return "Enter the server address."
    if any(c.isspace() for c in values["host"]) or "/" in values["host"]:
        return "The server address should be a name or IP address only, for example 10.0.0.5."
    if not 1 <= values["port"] <= 65535:
        return "The port must be between 1 and 65535 (SSH normally uses 22)."
    if not values["user"]:
        return "Enter the user name for the server."
    if not values["folder"]:
        return "Enter the folder on the server, for example /home/admin_np/ippm_dump."
    if not values["password"] and not values["key_file"]:
        return "Enter the password, or the path of a key file on this PC."
    if not 1 <= values["minutes"] <= 1440:
        return "Check every 1 to 1440 minutes."
    if not 1 <= values["days"] <= 31:
        return "Look back 1 to 31 days."
    return None


def turn_off(user_id: int | None) -> None:
    """Stop fetching. The other settings are kept, so the server can be switched on again easily."""
    stored = dict(db.get_setting(SETTING_KEY) or {})
    if not stored:
        env = _from_env()
        stored = {k: (_seal(env[k]) if k in SECRET_FIELDS else env[k]) for k in FIELDS}
    stored["host"] = ""
    db.set_setting(SETTING_KEY, stored, user_id)
    load()
    with _lock:
        _state.update(error=None, last_result=None, busy=False)
        _state["seq"] += 1


def enabled() -> bool:
    c = current()
    return bool(c["host"] and c["user"] and c["folder"])


def status() -> dict:
    c = current()
    with _lock:
        s = dict(_state, last_files=list(_state["last_files"]))
    s.update(enabled=enabled(), host=c["host"], port=c["port"], user=c["user"], folder=c["folder"],
             minutes=c["minutes"], days=c["days"], key_file=c["key_file"], source=c["source"],
             has_password=bool(c["password"]), has_passphrase=bool(c["key_passphrase"]),
             login="key file" if c["key_file"] else "password")
    return s


def _explain(exc: Exception, c: dict | None = None) -> str:
    """A reason an administrator can act on."""
    import paramiko

    c = c or current()
    where = f"{c['host']}:{c['port']}"
    if isinstance(exc, paramiko.BadHostKeyException):
        return (f"The identity of {where} is different from the first time this system connected to it, so the "
                "connection was refused. If the server was rebuilt, delete instance\\known_hosts and check again.")
    if isinstance(exc, paramiko.AuthenticationException):
        login = "key file" if c["key_file"] else "password"
        return f"{where} refused the login for user '{c['user']}'. Check the user name and the {login}."
    if isinstance(exc, _FolderMissing):
        return f"The folder '{c['folder']}' was not found on the server, or this user may not open it."
    if isinstance(exc, _KeyFileMissing):
        return f"The key file '{c['key_file']}' was not found on this PC."
    if isinstance(exc, (socket.timeout, TimeoutError, socket.gaierror, ConnectionError, paramiko.ssh_exception.NoValidConnectionsError)):
        return f"Could not reach {where}. Check the address, the network or VPN, and that the server is on."
    if isinstance(exc, paramiko.SSHException):
        return f"The connection to {where} failed: {exc}"
    return f"{type(exc).__name__}: {exc}"


class _FolderMissing(Exception):
    pass


class _KeyFileMissing(Exception):
    pass


def _connect(c: dict | None = None):
    import paramiko

    c = c or current()
    if c["key_file"] and not Path(c["key_file"]).is_file():
        raise _KeyFileMissing()
    known = BASE_DIR / "instance" / "known_hosts"
    known.parent.mkdir(parents=True, exist_ok=True)
    known.touch(exist_ok=True)
    client = paramiko.SSHClient()
    client.load_host_keys(str(known))
    # The server's identity is remembered on the first connection; a different one later is refused.
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(c["host"], port=c["port"], username=c["user"],
                   password=c["password"] or None, key_filename=c["key_file"] or None,
                   passphrase=c["key_passphrase"] or None, allow_agent=False, look_for_keys=False,
                   timeout=TIMEOUT, banner_timeout=TIMEOUT, auth_timeout=TIMEOUT)
    return client


def _wanted(entries: list, date_from: date | None, date_to: date | None) -> list:
    now, days = time.time(), current()["days"]
    files = [e for e in entries
             if stat.S_ISREG(e.st_mode or 0) and PurePosixPath(e.filename).suffix.lower() in watcher.EXTENSIONS
             and not e.filename.startswith(("~$", ".")) and e.st_size and e.st_mtime <= now - SETTLE_SECONDS]
    if date_from:
        files = [e for e in files if date_from <= datetime.fromtimestamp(e.st_mtime).date() <= date_to]
    else:
        files = [e for e in files if e.st_mtime >= now - days * 86400]
        done = {(r["name"], r["size_bytes"], int(r["modified_at"].timestamp())) for r in db.query(
            "SELECT name, size_bytes, modified_at FROM remote_files WHERE modified_at >= %s",
            (datetime.fromtimestamp(now - days * 86400 - 86400, timezone.utc),))}
        files = [e for e in files if (e.filename, e.st_size, int(e.st_mtime)) not in done]
    return sorted(files, key=lambda e: e.st_mtime)


def _download(sftp, entry, folder: str) -> None:
    name = Path(entry.filename).name
    target = config.inbox_dir / name
    if target.exists():
        target = config.inbox_dir / f"{target.stem}__{datetime.now():%H%M%S}{target.suffix}"
    part = config.inbox_dir / f".{target.name}.part"       # hidden from the watcher until complete
    try:
        sftp.get(str(PurePosixPath(folder) / entry.filename), str(part))
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
    c = current()
    client = _connect(c)
    try:
        sftp = client.open_sftp()
        sftp.get_channel().settimeout(120)
        try:
            entries = sftp.listdir_attr(c["folder"])
        except (FileNotFoundError, PermissionError) as exc:
            raise _FolderMissing() from exc
        files = _wanted(entries, date_from, date_to)
        for entry in files:
            if _stop.is_set():
                break
            log.info("Fetching %s (%.1f MB)", entry.filename, entry.st_size / 1048576)
            _download(sftp, entry, c["folder"])
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


def test(values: dict) -> tuple[bool, str]:
    """Try settings an administrator entered, without saving them: log in and look at the folder."""
    try:
        client = _connect(values)
    except Exception as exc:  # noqa: BLE001
        return False, _explain(exc, values)
    try:
        sftp = client.open_sftp()
        try:
            entries = sftp.listdir_attr(values["folder"])
        except (FileNotFoundError, PermissionError):
            return False, _explain(_FolderMissing(), values)
        excel = [e for e in entries if stat.S_ISREG(e.st_mode or 0) and PurePosixPath(e.filename).suffix.lower() in watcher.EXTENSIONS]
        if not excel:
            return True, "Connected. The folder was found, but it has no Excel files in it yet."
        newest = max(excel, key=lambda e: e.st_mtime)
        return True, (f"Connected. The folder has {len(excel)} Excel file{'' if len(excel) == 1 else 's'}; the newest is "
                      f"{newest.filename} ({datetime.fromtimestamp(newest.st_mtime):%d %b %Y %H:%M}).")
    except Exception as exc:  # noqa: BLE001
        return False, _explain(exc, values)
    finally:
        client.close()


def _loop() -> None:
    c = current()
    log.info("Checking %s@%s:%s%s every %s minutes", c["user"], c["host"], c["port"], c["folder"], c["minutes"])
    _wake.wait(3)
    while not _stop.is_set():
        _wake.clear()
        with _lock:
            pending, _request[:] = list(_request), []
        if not enabled():
            _wake.wait(60)
            continue
        if pending:
            for date_from, date_to in pending:
                _run(date_from, date_to, manual=True)
        else:
            _run(None, None, manual=False)
        with _lock:
            more = bool(_request)
        if not more:
            _wake.wait(current()["minutes"] * 60)


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
    load()
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
