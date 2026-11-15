"""Settings loaded from the .env file in the project folder (or real environment variables)."""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
APP_DIR = Path(__file__).resolve().parent
APP_NAME = "TX KPI Monitor"


def _load_env_file() -> None:
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def _dir(name: str, default: str) -> Path:
    p = Path(os.environ.get(name, default))
    if not p.is_absolute():
        p = BASE_DIR / p
    p.mkdir(parents=True, exist_ok=True)
    return p


def _secret_key() -> str:
    """Key that signs login cookies. Created once and kept in instance/secret.key."""
    key_file = BASE_DIR / "instance" / "secret.key"
    if key_file.exists():
        return key_file.read_text(encoding="utf-8").strip()
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_urlsafe(48)
    key_file.write_text(key, encoding="utf-8")
    return key


@dataclass(frozen=True)
class Config:
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str
    host: str
    port: int
    inbox_dir: Path
    archive_dir: Path
    failed_dir: Path
    reports_dir: Path
    secret_key: str

    @property
    def conninfo(self) -> str:
        from psycopg.conninfo import make_conninfo

        return make_conninfo(
            host=self.db_host, port=self.db_port, dbname=self.db_name,
            user=self.db_user, password=self.db_password,
        )


def load_config() -> Config:
    _load_env_file()
    env = os.environ
    return Config(
        db_host=env.get("KPI_DB_HOST", "localhost"),
        db_port=int(env.get("KPI_DB_PORT", "5432")),
        db_name=env.get("KPI_DB_NAME", "tx_kpi"),
        db_user=env.get("KPI_DB_USER", "kpi_app"),
        db_password=env.get("KPI_DB_PASSWORD", ""),
        host=env.get("KPI_HOST", "127.0.0.1"),
        port=int(env.get("KPI_PORT", "8000")),
        inbox_dir=_dir("KPI_INBOX_DIR", "inbox"),
        archive_dir=_dir("KPI_ARCHIVE_DIR", "archive"),
        failed_dir=_dir("KPI_FAILED_DIR", "failed"),
        reports_dir=_dir("KPI_REPORTS_DIR", "reports"),
        secret_key=_secret_key(),
    )


config = load_config()
