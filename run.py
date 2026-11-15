"""Start TX KPI Monitor on this computer.  Usage:  run.bat   (or)   .venv\\Scripts\\python.exe run.py"""
from __future__ import annotations

import sys
import threading
import webbrowser


def fail(message: str) -> None:
    print()
    print("  TX KPI Monitor could not start")
    print("  " + "-" * 30)
    for line in message.splitlines():
        print("  " + line)
    print()
    sys.exit(1)


def main() -> None:
    try:
        import psycopg
        import uvicorn
        from app.config import BASE_DIR, config
    except ImportError as exc:
        fail(f"A required package is missing ({exc.name}).\n"
             "Start the system with run.bat, or install the packages with:\n"
             "  .venv\\Scripts\\python.exe -m pip install -r requirements.txt")

    if not config.db_password:
        fail(f"The database password is not set.\nOpen {BASE_DIR / '.env'} and fill in KPI_DB_PASSWORD.")

    try:
        with psycopg.connect(config.conninfo, connect_timeout=5) as conn:
            conn.execute("SELECT 1")
    except psycopg.OperationalError as exc:
        reason = str(exc).strip().splitlines()[0] if str(exc).strip() else "no answer"
        fail(f"The PostgreSQL database '{config.db_name}' on {config.db_host}:{config.db_port} cannot be reached.\n"
             f"Reason: {reason}\n\n"
             "Check that:\n"
             "  1. the PostgreSQL service is running (Windows Services > postgresql-x64-18)\n"
             f"  2. KPI_DB_PASSWORD in {BASE_DIR / '.env'} is the password of the '{config.db_user}' user")

    shown_host = "localhost" if config.host in ("127.0.0.1", "0.0.0.0") else config.host
    url = f"http://{shown_host}:{config.port}"
    print()
    print("  TX KPI Monitor")
    print("  " + "-" * 30)
    print(f"  Open in a browser : {url}")
    print(f"  Inbox folder      : {config.inbox_dir}")
    print("  Stop the system   : press Ctrl+C in this window")
    print()
    if "--no-browser" not in sys.argv:
        threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    uvicorn.run("app.main:app", host=config.host, port=config.port, log_level="warning")


if __name__ == "__main__":
    main()
