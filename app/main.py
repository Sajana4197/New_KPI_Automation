"""TX KPI Monitor: the web application."""
from __future__ import annotations

import json
import logging
import re
import shutil
import threading
import time
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from . import db, excel_report, queries, remote, security, watcher
from .config import APP_DIR, APP_NAME, BASE_DIR, config
from .reporting import KPI_LABELS, KPIS, delete_date, generate_report
from .seed import SeedError, load_seed
from .sources import CATEGORIES, CATEGORY_LABELS, SOURCE_ORDER, SOURCES

log = logging.getLogger("kpi.web")
ASSET_VERSION = str(int(time.time()))
KPI_UNITS = {"PD": "%", "JITTER": "ms", "RTT": "ms"}
_generate_lock = threading.Lock()


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_schema()
    watcher.start()
    remote.start()
    yield
    remote.stop()
    watcher.stop()
    db.close_pool()


app = FastAPI(title=APP_NAME, docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=config.secret_key, session_cookie="txkpi_session",
                   max_age=12 * 60 * 60, same_site="lax", https_only=False)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=APP_DIR / "templates")

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Content-Security-Policy", CSP)
    if not request.url.path.startswith("/static/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


# ---- template helpers --------------------------------------------------------------------
def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def data_json(value) -> Markup:
    text = json.dumps(value, default=_json_default, separators=(",", ":"))
    return Markup(text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


def fmt_date(value, style: str = "medium") -> str:
    if not value:
        return ""
    if style == "long":
        return f"{value:%A}, {value.day} {value:%B %Y}"
    if style == "short":
        return f"{value.day} {value:%b}"
    return f"{value.day} {value:%b %Y}"


def fmt_datetime(value) -> str:
    if not value:
        return ""
    if value.tzinfo:
        value = value.astimezone()
    return f"{value.day} {value:%b %Y, %H:%M}"


def fmt_num(value, digits: int = 0) -> str:
    if value is None:
        return "–"
    return f"{value:,.{digits}f}"


def fmt_size(value) -> str:
    if not value:
        return "–"
    for unit in ("bytes", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:,.0f} {unit}" if unit in ("bytes", "KB") else f"{value:,.1f} {unit}"
        value /= 1024
    return ""


def icon(name: str, cls: str = "") -> Markup:
    """An icon from the sprite in _sprite.html."""
    return Markup('<svg class="icon{}" aria-hidden="true"><use href="#i-{}"/></svg>').format(
        f" {cls}" if cls else "", name)


def fmt_value(value, kpi: str) -> str:
    """A measured value with its unit: 1.25 % or 12.4 ms."""
    if value is None:
        return "–"
    return f"{value:,.2f} %" if kpi == "PD" else f"{value:,.1f} ms"


def fmt_plain(value) -> str:
    """A setting without trailing zeros: 0.5, 7, 20."""
    return f"{float(value):g}"


templates.env.filters.update(data_json=data_json, d=fmt_date, dt=fmt_datetime, num=fmt_num, size=fmt_size,
                             kpi_value=fmt_value, g=fmt_plain)
templates.env.globals.update(
    icon=icon, app_name=APP_NAME, asset_v=ASSET_VERSION, SOURCES=SOURCES, SOURCE_ORDER=SOURCE_ORDER, KPIS=KPIS,
    KPI_LABELS=KPI_LABELS, KPI_UNITS=KPI_UNITS, CATEGORIES=CATEGORIES, CATEGORY_LABELS=CATEGORY_LABELS,
    ROLES=security.ROLES, ROLE_LABELS=security.ROLE_LABELS, ROLE_HELP=security.ROLE_HELP,
    MIN_PASSWORD=security.MIN_PASSWORD_LENGTH)


def flash(request: Request, kind: str, message: str) -> None:
    request.session.setdefault("flash", []).append([kind, message])


def render(request: Request, name: str, status_code: int = 200, **ctx):
    user = security.current_user(request)
    ctx.update(user=user, csrf=security.csrf_token(request), flashes=request.session.pop("flash", []),
               can=lambda role: security.can(user, role))
    ctx.setdefault("active", "")
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def go(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def _safe_next(target: str | None) -> str:
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return "/"


def _parse_date(text: str | None) -> date | None:
    try:
        return date.fromisoformat(text) if text else None
    except ValueError:
        return None


def _wants_json(request: Request) -> bool:
    return request.url.path.startswith("/api/")


CSRF = Depends(security.csrf_protect)


# ---- errors ------------------------------------------------------------------------------
@app.exception_handler(security.NeedLogin)
def _need_login(request: Request, _: security.NeedLogin):
    if _wants_json(request):
        return JSONResponse({"error": "Your session has ended. Please sign in again."}, status_code=401)
    if not security.has_users():
        return go("/setup")
    target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    if request.method == "GET" and target != "/":
        return go(f"/login?next={quote(target, safe='')}")
    return go("/login")


@app.exception_handler(security.Forbidden)
def _forbidden(request: Request, exc: security.Forbidden):
    if _wants_json(request):
        return JSONResponse({"error": exc.message}, status_code=403)
    return render(request, "error.html", 403, title="Not allowed", message=exc.message)


@app.exception_handler(StarletteHTTPException)
def _http_error(request: Request, exc: StarletteHTTPException):
    if _wants_json(request):
        return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code)
    if exc.status_code == 404:
        return render(request, "error.html", 404, title="Page not found",
                      message="That page does not exist, or the data behind it was deleted.")
    return render(request, "error.html", exc.status_code, title="Something went wrong", message=str(exc.detail))


@app.exception_handler(Exception)
def _crash(request: Request, exc: Exception):
    log.exception("Unhandled error on %s", request.url.path)
    if _wants_json(request):
        return JSONResponse({"error": "The server hit an unexpected error."}, status_code=500)
    return render(request, "error.html", 500, title="Something went wrong",
                  message="The server hit an unexpected error. The details are in the server window.")


def _not_found() -> StarletteHTTPException:
    return StarletteHTTPException(status_code=404)


# ---- sign in, first run ------------------------------------------------------------------
@app.get("/setup")
def setup_page(request: Request):
    if security.has_users():
        return go("/login")
    return render(request, "setup.html")


@app.post("/setup", dependencies=[CSRF])
def setup_submit(request: Request, full_name: str = Form(""), password: str = Form(""), confirm: str = Form("")):
    if security.has_users():
        return go("/login")
    problem = security.password_problem(password, confirm)
    if problem:
        return render(request, "setup.html", 400, error=problem, full_name=full_name)
    user = db.query_one(
        "INSERT INTO users (username, full_name, password_hash, role) VALUES ('admin', %s, %s, 'admin') RETURNING *",
        (full_name.strip()[:80], security.hash_password(password)))
    security.sign_in(request, user)
    db.log_activity(user, "Created the first administrator")
    flash(request, "good", "Welcome. Your administrator account is ready.")
    return go("/")


@app.get("/login")
def login_page(request: Request, next: str = "/"):
    if not security.has_users():
        return go("/setup")
    if security.current_user(request):
        return go(_safe_next(next))
    return render(request, "login.html", next=_safe_next(next))


@app.post("/login", dependencies=[CSRF])
def login_submit(request: Request, username: str = Form(""), password: str = Form(""), next: str = Form("/")):
    username = username.strip()
    key = f"{username.lower()}|{request.client.host if request.client else ''}"
    wait = security.locked_for(key)
    if wait:
        minutes = max(1, round(wait / 60))
        return render(request, "login.html", 429, next=_safe_next(next), username=username,
                      error=f"Too many wrong attempts. Try again in about {minutes} minute{'s' if minutes > 1 else ''}.")
    user = db.query_one("SELECT * FROM users WHERE lower(username) = lower(%s) AND is_active", (username,))
    stored = user["password_hash"] if user else security.hash_password("-")   # same work either way
    if not security.verify_password(password, stored) or not user:
        security.note_failure(key)
        return render(request, "login.html", 401, next=_safe_next(next), username=username,
                      error="The username or password is not correct.")
    security.note_success(key)
    security.sign_in(request, user)
    db.log_activity(user, "Signed in")
    return go(_safe_next(next))


@app.post("/logout", dependencies=[CSRF])
def logout(request: Request):
    security.sign_out(request)
    return go("/login")


# ---- dashboard ---------------------------------------------------------------------------
def _delta(now: int | None, before: int | None) -> dict | None:
    if now is None or before is None:
        return None
    return {"value": now - before, "direction": "up" if now > before else "down" if now < before else "flat"}


@app.get("/")
def dashboard(request: Request, date: str | None = None, user: dict = Depends(security.require())):
    dates = queries.data_dates()
    d = _parse_date(date)
    if d not in dates:
        d = dates[0] if dates else None
    if d is None:
        return render(request, "dashboard.html", active="dashboard", empty=True, inbox=str(config.inbox_dir),
                      dates=[], d=None)

    current = db.get_setting("thresholds")
    report = queries.get_report(d)
    if report and report["is_seed"]:
        report = None
    sources = queries.source_status(d)
    received = sum(1 for s in sources if s["file"])
    ctx: dict = dict(active="dashboard", empty=False, dates=dates, d=d, sources=sources, received=received,
                     report=report, thresholds=(report["thresholds"] if report else current),
                     inbox=str(config.inbox_dir))
    if not report:
        return render(request, "dashboard.html", **ctx)

    th = report["thresholds"]
    imported_at = queries.last_import_at(d)
    stale = []
    if imported_at and imported_at > report["generated_at"]:
        stale.append("A KPI file for this day was imported after the report was generated.")
    if any(float(th.get(k, 0)) != float(current[k]) for k in ("pd_pct", "jitter_ms", "rtt_ms", "min_hours")):
        stale.append("The thresholds were changed after this report was generated.")

    items = queries.report_items(report)
    prev = queries.previous_report(d)
    history = queries.report_history(14)
    flagged = len({i["site_id"] or i["name"] for i in items})
    prev_real = prev if prev and not prev["is_seed"] else None
    tiles = [
        {"key": "sites", "label": "Sites monitored", "value": report["site_count"],
         "delta": _delta(report["site_count"], prev_real["site_count"] if prev_real else None), "tone": "neutral",
         "href": "/sites"},
        {"key": "new", "label": "New sites", "value": report["new_site_count"], "delta": None, "tone": "neutral",
         "href": "/sites?show=new", "note": "not in any earlier day's files"},
    ]
    for kpi, col in (("PD", "pd_count"), ("JITTER", "jitter_count"), ("RTT", "rtt_count")):
        tiles.append({"key": kpi, "label": f"{KPI_LABELS[kpi]} sites", "value": report[col],
                      "delta": _delta(report[col], prev[col] if prev else None), "tone": "bad-up",
                      "href": f"/report/{d.isoformat()}#{kpi}"})
    chronological = list(reversed(history))
    page_data = {
        "profile": [{"t": r["hour_ts"], "PD": r["pd"], "JITTER": r["jitter"], "RTT": r["rtt"]}
                    for r in queries.hourly_profile(d, th)],
        "trend": {"dates": [r["report_date"] for r in chronological],
                  "PD": [r["pd_count"] for r in chronological],
                  "JITTER": [r["jitter_count"] for r in chronological],
                  "RTT": [r["rtt_count"] for r in chronological]},
        "current": d,
    }
    longest = sorted((i for i in items if i["pending"] > 0), key=lambda i: (-i["pending"], -i["hours"]))[:8]
    ctx.update(stale=stale, tiles=tiles, flagged=flagged, prev=prev, history=history, longest=longest,
               breakdown=queries.breakdowns(items), new_sites=queries.new_sites(d), page_data=page_data,
               share=(flagged / report["site_count"] * 100 if report["site_count"] else 0))
    return render(request, "dashboard.html", **ctx)


@app.post("/generate", dependencies=[CSRF])
def generate(request: Request, date: str = Form(""), user: dict = Depends(security.require("admin"))):
    d = _parse_date(date)
    if not d:
        flash(request, "bad", "Choose a day first.")
        return go("/")
    try:
        with _generate_lock:
            report = generate_report(d, user)
    except ValueError as exc:
        flash(request, "bad", str(exc))
        return go("/")
    try:
        excel_report.save_copy(d)
    except Exception:  # noqa: BLE001 - the report itself is saved; the Excel copy can be downloaded later
        log.exception("Could not save the Excel copy for %s", d)
    missing = [SOURCES[k].label for k in report["sources_missing"]]
    db.log_activity(user, "Generated report",
                    f"{d:%d %b %Y}: PD {report['pd_count']}, jitter {report['jitter_count']}, RTT {report['rtt_count']}"
                    + (f"; missing {', '.join(missing)}" if missing else ""))
    if missing:
        flash(request, "warn", f"Report generated without {len(missing)} of the 4 files: {', '.join(missing)}.")
    else:
        flash(request, "good", f"Report for {fmt_date(d)} generated from all 4 files.")
    return go(f"/report/{d.isoformat()}")


# ---- report ------------------------------------------------------------------------------
@app.get("/report")
def report_latest(request: Request, user: dict = Depends(security.require())):
    latest = db.scalar("SELECT max(report_date) FROM reports WHERE NOT is_seed") \
        or db.scalar("SELECT max(report_date) FROM reports")
    if latest:
        return go(f"/report/{latest.isoformat()}")
    return render(request, "report.html", active="report", report=None, d=None)


@app.get("/report/{d}")
def report_page(request: Request, d: date, user: dict = Depends(security.require())):
    report = queries.get_report(d)
    if not report:
        if d in queries.data_dates():
            flash(request, "info", f"No report has been generated for {fmt_date(d)} yet.")
            return go(f"/?date={d.isoformat()}")
        raise _not_found()
    items = queries.report_items(report)
    suggestions = [r["feedback"] for r in db.query(
        """SELECT feedback, count(*) AS n FROM report_items WHERE report_id = %s
           GROUP BY feedback ORDER BY n DESC, feedback LIMIT 40""", (report["id"],))]
    th = report["thresholds"] or db.get_setting("thresholds")
    page_data = {
        "date": d, "canEdit": security.can(user, "admin"), "kpis": KPIS, "labels": KPI_LABELS, "units": KPI_UNITS,
        "sources": {k: {"label": s.label, "short": s.short} for k, s in SOURCES.items()},
        "items": [[i["id"], i["kpi"], i["site_id"], i["name"], i["category"], i["hours"], i["pending"],
                   i["feedback"], i["source"], 1 if i["is_new"] else 0,
                   None if i["peak"] is None else round(float(i["peak"]), 3), 1 if i["edited"] else 0]
                  for i in items],
    }
    prev, nxt = queries.previous_report(d), queries.next_report(d)
    return render(request, "report.html", active="report", report=report, d=d, thresholds=th,
                  missing=[SOURCES[k].label for k in report["sources_missing"]], page_data=page_data,
                  suggestions=suggestions, prev=prev, next=nxt, has_data=d in queries.data_dates(),
                  flagged=len({i["site_id"] or i["name"] for i in items}))


@app.get("/report/{d}/excel")
def report_excel(d: date, user: dict = Depends(security.require())):
    try:
        data = excel_report.build(d)
    except ValueError:
        raise _not_found() from None
    name = excel_report.filename_for(d)
    try:
        (config.reports_dir / name).write_bytes(data)
    except OSError:
        pass                                    # the saved copy is open in Excel; the download still works
    db.log_activity(user, "Downloaded Excel report", f"{d:%d %b %Y}")
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/report/{d}/preview")
def report_preview(request: Request, d: date, sheet: str = "summary", user: dict = Depends(security.require())):
    """The Excel workbook shown on screen, one sheet at a time, before it is downloaded."""
    try:
        c = excel_report.collect(d)
    except ValueError:
        raise _not_found() from None
    if sheet not in {key for key, _, _ in excel_report.SHEETS}:
        sheet = "summary"
    kpi = excel_report.SHEET_KPI.get(sheet)
    classic = []
    if sheet == "classic":
        classic = [(excel_report.CLASSIC_CODE[k], i) for k in KPIS for i in c["by_kpi"][k]]
    return render(request, "preview.html", active="report", d=d, sheet=sheet, sheets=excel_report.SHEETS, kpi=kpi,
                  rows=c["by_kpi"][kpi] if kpi else [], classic=classic, classic_heads=excel_report.CLASSIC_HEADS, filename=excel_report.filename_for(d),
                  counts={"pd": len(c["by_kpi"]["PD"]), "jitter": len(c["by_kpi"]["JITTER"]),
                          "rtt": len(c["by_kpi"]["RTT"]), "new": len(c["fresh"])}, **c)


class FeedbackIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=5000)
    feedback: str = Field(max_length=500)


@app.post("/api/feedback", dependencies=[CSRF])
def save_feedback(payload: FeedbackIn, user: dict = Depends(security.require("admin"))):
    text = re.sub(r"\s+", " ", payload.feedback).strip()
    if not text:
        return JSONResponse({"error": "Feedback cannot be empty."}, status_code=400)
    rows = db.query(
        """UPDATE report_items SET feedback = %s, feedback_edited_at = now(), feedback_edited_by = %s
           WHERE id = ANY(%s) RETURNING id, site_name""", (text, user["id"], payload.ids))
    if rows:
        what = rows[0]["site_name"] if len(rows) == 1 else f"{len(rows)} sites"
        db.log_activity(user, "Edited feedback", f"{what}: {text[:120]}")
    return {"saved": [r["id"] for r in rows], "feedback": text}


# ---- sites -------------------------------------------------------------------------------
@app.get("/sites")
def sites_page(request: Request, q: str = "", source: str = "", category: str = "", show: str = "",
               status: str = "", sort: str = "name", page: int = 1, user: dict = Depends(security.require())):
    latest = queries.latest_data_date()
    th = db.get_setting("thresholds")
    min_hours = int(th["min_hours"])
    if status not in queries.SITE_STATUS:
        status = ""
    result = queries.sites_page(q=q.strip()[:80], source=source, category=category, show=show, sort=sort,
                                status=status, min_hours=min_hours, page=page, latest=latest)
    params = {k: v for k, v in (("q", q.strip()), ("source", source), ("category", category), ("show", show),
                                ("status", status), ("sort", sort if sort != "name" else "")) if v}

    def url(**change) -> str:
        """The current list address with some filters changed (an empty value removes that filter)."""
        merged = {**params, **change}
        text = "&".join(f"{k}={quote(str(v))}" for k, v in merged.items() if v)
        return "/sites" + ("?" + text if text else "")

    strips = queries.site_strips([r["id"] for r in result["rows"]], latest)
    limits = {"PD": th["pd_pct"], "JITTER": th["jitter_ms"], "RTT": th["rtt_ms"]}
    page_data = {"hours": strips["hours"], "sites": strips["sites"], "limits": limits, "min_hours": min_hours,
                 "ids": [r["id"] for r in result["rows"]]}
    base = "&".join(f"{k}={quote(str(v))}" for k, v in params.items())
    return render(request, "sites.html", active="sites", result=result, latest=latest, q=q.strip(), source=source,
                  category=category, show=show, status=status, sort=sort, base_query=base, url=url,
                  summary=queries.site_summary(latest, min_hours), counts=queries.site_counts(),
                  strips=strips, limits=limits, min_hours=min_hours, page_data=page_data,
                  filtered=bool(params.keys() - {"sort"}))


@app.get("/api/sites/{site_id}")
def api_site(site_id: int, date: str | None = None, user: dict = Depends(security.require())):
    """Everything the slide-in panel of the Sites page shows for one site and day."""
    site = queries.site_detail(site_id)
    if not site:
        raise _not_found()
    d = _parse_date(date)
    if d not in site["dates"]:
        d = site["dates"][0] if site["dates"] else None
    th = db.get_setting("thresholds")
    day = queries.site_day(site_id, d, th) if d else {"daily": None, "hourly": [], "paths": []}
    daily = day["daily"] or {}
    src = SOURCES[site["source"]]
    return {
        "id": site["id"], "name": site["name"], "category": site["category"],
        "category_label": CATEGORY_LABELS.get(site["category"], ""), "source": src.label,
        "ext_id": site["ext_key"] if src.vendor == "ZTE" else None,
        "first_seen": site["first_seen"], "last_seen": site["last_seen"], "date": d,
        "name_count": len(site["names"]),
        "limits": {"PD": th["pd_pct"], "JITTER": th["jitter_ms"], "RTT": th["rtt_ms"]},
        "min_hours": int(th["min_hours"]),
        "daily": {"samples": daily.get("samples"), "paths": daily.get("path_count"),
                  "PD": {"hours": daily.get("pd_hours"), "peak": daily.get("pd_max")},
                  "JITTER": {"hours": daily.get("jitter_hours"), "peak": daily.get("jitter_max")},
                  "RTT": {"hours": daily.get("rtt_hours"), "peak": daily.get("rtt_max")}} if daily else None,
        "hourly": [{"t": r["hour_ts"], "PD": r["pd"], "JITTER": r["jitter"], "RTT": r["rtt"]} for r in day["hourly"]],
        "paths": [{"label": r["label"], "samples": r["samples"], "PD": r["pd_max"], "JITTER": r["jitter_max"],
                   "RTT": r["rtt_max"]} for r in day["paths"][:12]],
        "path_total": len(day["paths"]),
        "listed": [{"kpi": r["kpi"], "hours": r["sample_count"], "pending": r["pending_days"],
                    "feedback": r["feedback"]} for r in site["listed"] if r["report_date"] == d and not r["is_seed"]],
    }


@app.get("/sites/{site_id}")
def site_page(request: Request, site_id: int, date: str | None = None, user: dict = Depends(security.require())):
    site = queries.site_detail(site_id)
    if not site:
        raise _not_found()
    d = _parse_date(date)
    if d not in site["dates"]:
        d = site["dates"][0] if site["dates"] else None
    th = db.get_setting("thresholds")
    day = queries.site_day(site_id, d, th) if d else {"daily": None, "hourly": [], "paths": []}
    history = list(reversed(site["history"]))
    page_data = {
        "thresholds": th,
        "hourly": [{"t": r["hour_ts"], "PD": r["pd"], "JITTER": r["jitter"], "RTT": r["rtt"]} for r in day["hourly"]],
        "history": {"dates": [r["report_date"] for r in history], "PD": [r["pd_hours"] for r in history],
                    "JITTER": [r["jitter_hours"] for r in history], "RTT": [r["rtt_hours"] for r in history]},
    }
    listed_today = [r for r in site["listed"] if r["report_date"] == d]
    return render(request, "site.html", active="sites", site=site, d=d, day=day, thresholds=th,
                  page_data=page_data, listed_today=listed_today)


# ---- imports -----------------------------------------------------------------------------
def _import_seq() -> int:
    return db.scalar("SELECT coalesce(max(id), 0) FROM import_files") or 0


@app.get("/imports")
def imports_page(request: Request, user: dict = Depends(security.require("admin"))):
    return render(request, "imports.html", active="imports", days=queries.import_days(),
                  history=queries.import_log(), inbox=str(config.inbox_dir), archive=str(config.archive_dir),
                  failed=str(config.failed_dir), watch=watcher.status(), storage=queries.storage(),
                  seq=_import_seq(), server=remote.status(), today=date.today(),
                  max_range=remote.MAX_RANGE_DAYS)


@app.get("/api/status")
def api_status(user: dict = Depends(security.require())):
    state = watcher.status()
    last = state["last_import"]
    server = remote.status()
    return {"busy": bool(state["busy"]), "waiting": len(state["waiting"]), "seq": _import_seq(),
            "server": {"seq": server["seq"], "busy": server["busy"]},
            "last": {"filename": last["filename"], "status": last["status"]} if last else None}


@app.post("/imports/upload", dependencies=[CSRF])
def imports_upload(request: Request, files: list[UploadFile] = File(...),
                   user: dict = Depends(security.require("admin"))):
    saved, skipped = [], []
    for upload in files:
        name = Path(upload.filename or "").name
        if not name or Path(name).suffix.lower() not in watcher.EXTENSIONS or name.startswith(("~$", ".")):
            skipped.append(name or "unnamed file")
            continue
        target = config.inbox_dir / name
        if target.exists():
            target = config.inbox_dir / f"{target.stem}__{datetime.now():%H%M%S}{target.suffix}"
        part = config.inbox_dir / f".{target.name}.part"          # hidden from the watcher until complete
        with part.open("wb") as out:
            shutil.copyfileobj(upload.file, out, 1 << 20)
        part.replace(target)
        saved.append(name)
    if saved:
        db.log_activity(user, "Uploaded files", ", ".join(saved)[:400])
        watcher.nudge()
        flash(request, "good", f"{len(saved)} file{'s' if len(saved) != 1 else ''} received. Importing now; "
                               "this page updates when it finishes.")
    if skipped:
        flash(request, "warn", "Skipped (not an Excel file): " + ", ".join(skipped)[:300])
    return go("/imports")


@app.post("/imports/scan", dependencies=[CSRF])
def imports_scan(request: Request, user: dict = Depends(security.require("admin"))):
    watcher.nudge()
    flash(request, "info", "Checking the inbox folder now.")
    return go("/imports")


@app.post("/imports/server", dependencies=[CSRF])
def imports_server(request: Request, date_from: str = Form(""), date_to: str = Form(""),
                   user: dict = Depends(security.require("admin"))):
    """Visit the dump server now: new files only, or every file of the chosen days."""
    if not remote.enabled():
        flash(request, "bad", "No server is set up yet. Fill in the KPI_SFTP settings in the .env file and restart.")
        return go("/imports")
    if date_from or date_to:
        try:
            first, last = date.fromisoformat(date_from), date.fromisoformat(date_to)
        except ValueError:
            flash(request, "bad", "Choose both the first and the last day.")
            return go("/imports")
        problem = remote.range_problem(first, last)
        if problem:
            flash(request, "bad", problem)
            return go("/imports")
        remote.check_now(first, last)
        db.log_activity(user, "Fetched older days from the server", f"{first:%d %b %Y} to {last:%d %b %Y}")
        flash(request, "info", f"Fetching the server's files dated {fmt_date(first)} to {fmt_date(last)}. "
                               "This page updates when it finishes.")
    else:
        remote.check_now()
        flash(request, "info", "Checking the server now. This page updates when it finishes.")
    return go("/imports")


@app.post("/imports/delete", dependencies=[CSRF])
def imports_delete(request: Request, date: str = Form(""), confirm: str = Form(""),
                   user: dict = Depends(security.require("admin"))):
    d = _parse_date(date)
    if not d or confirm.strip() != date:
        flash(request, "bad", "Nothing was deleted: the date typed to confirm did not match.")
        return go("/imports")
    result = delete_date(d)
    db.log_activity(user, "Deleted a day's data",
                    f"{d:%d %b %Y}: {result['hourly_rows']:,} hourly rows, {result['sites_removed']:,} sites")
    flash(request, "good", f"Deleted everything stored for {fmt_date(d)} "
                           f"({result['hourly_rows']:,} hourly rows). The original files stay in the archive folder.")
    return go("/imports")


# ---- settings (administrators) -----------------------------------------------------------
@app.get("/settings")
def settings_page(request: Request, user: dict = Depends(security.require("admin"))):
    return render(request, "settings.html", active="settings", thresholds=db.get_setting("thresholds"),
                  default_feedback=db.get_setting("default_feedback"), users=queries.users(),
                  activity=queries.activity(), seeds=queries.seeds(), storage=queries.storage(),
                  first_report=db.scalar("SELECT min(report_date) FROM reports WHERE NOT is_seed"),
                  folders={"Inbox": config.inbox_dir, "Archive": config.archive_dir, "Failed": config.failed_dir,
                           "Excel reports": config.reports_dir})


@app.post("/settings/rules", dependencies=[CSRF])
def settings_rules(request: Request, pd_pct: float = Form(...), jitter_ms: float = Form(...),
                   rtt_ms: float = Form(...), min_hours: int = Form(...), default_feedback: str = Form(""),
                   user: dict = Depends(security.require("admin"))):
    if not (0 < pd_pct <= 100 and 0 < jitter_ms <= 100000 and 0 < rtt_ms <= 100000 and 1 <= min_hours <= 24):
        flash(request, "bad", "Those values are out of range. Nothing was changed.")
        return go("/settings")
    new = {"pd_pct": pd_pct, "jitter_ms": jitter_ms, "rtt_ms": rtt_ms, "min_hours": min_hours}
    old = db.get_setting("thresholds")
    feedback = re.sub(r"\s+", " ", default_feedback).strip()[:200] or db.DEFAULT_SETTINGS["default_feedback"]
    db.set_setting("thresholds", new, user["id"])
    db.set_setting("default_feedback", feedback, user["id"])
    if any(float(old[k]) != float(new[k]) for k in new):
        db.log_activity(user, "Changed thresholds",
                        f"PD {new['pd_pct']:g} %, jitter {new['jitter_ms']:g} ms, RTT {new['rtt_ms']:g} ms, "
                        f"{new['min_hours']} hours")
        flash(request, "good", "Thresholds saved. Regenerate a day's report to apply them to it.")
    else:
        flash(request, "good", "Settings saved.")
    return go("/settings")


@app.post("/settings/seed", dependencies=[CSRF])
def settings_seed(request: Request, date: str = Form(""), file: UploadFile = File(...),
                  user: dict = Depends(security.require("admin"))):
    d = _parse_date(date)
    if not d:
        flash(request, "bad", "Choose the date of the old report.")
        return go("/settings#history")
    tmp_dir = BASE_DIR / "instance" / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / f"seed_{int(time.time() * 1000)}.xlsx"
    try:
        with tmp.open("wb") as out:
            shutil.copyfileobj(file.file, out, 1 << 20)
        result = load_seed(tmp, d, user)
    except SeedError as exc:
        flash(request, "bad", str(exc))
        return go("/settings#history")
    except Exception:  # noqa: BLE001
        log.exception("Starting history upload failed")
        flash(request, "bad", "That file could not be read as an old IPPM report.")
        return go("/settings#history")
    finally:
        tmp.unlink(missing_ok=True)
    counts = result["counts"]
    db.log_activity(user, "Loaded starting history",
                    f"{d:%d %b %Y}: PD {counts['PD']}, jitter {counts['JITTER']}, RTT {counts['RTT']}")
    flash(request, "good", f"Starting history loaded for {fmt_date(d)}: {result['items']:,} rows, "
                           f"{result['matched']:,} matched to known sites. Regenerate later reports to use it.")
    return go("/settings#history")


@app.post("/settings/seed/delete", dependencies=[CSRF])
def settings_seed_delete(request: Request, date: str = Form(""), user: dict = Depends(security.require("admin"))):
    d = _parse_date(date)
    if d and db.execute("DELETE FROM reports WHERE report_date = %s AND is_seed", (d,)):
        db.log_activity(user, "Removed starting history", f"{d:%d %b %Y}")
        flash(request, "good", f"Starting history for {fmt_date(d)} removed.")
    return go("/settings#history")


_USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")


def _active_admins(excluding: int | None = None) -> int:
    return db.scalar("SELECT count(*) FROM users WHERE role = 'admin' AND is_active AND id IS DISTINCT FROM %s",
                     (excluding,))


@app.post("/settings/users/add", dependencies=[CSRF])
def settings_user_add(request: Request, username: str = Form(""), full_name: str = Form(""),
                      role: str = Form("user"), password: str = Form(""),
                      user: dict = Depends(security.require("admin"))):
    username = username.strip().lower()
    problem = None
    if not _USERNAME.match(username):
        problem = "Usernames are 3 to 32 characters: letters, numbers, dot, dash or underscore."
    elif role not in security.ROLES:
        problem = "Choose a role."
    elif db.scalar("SELECT 1 FROM users WHERE lower(username) = %s", (username,)):
        problem = f"The username '{username}' is already taken."
    else:
        problem = security.password_problem(password)
    if problem:
        flash(request, "bad", problem)
        return go("/settings#users")
    db.execute("INSERT INTO users (username, full_name, password_hash, role) VALUES (%s, %s, %s, %s)",
               (username, full_name.strip()[:80], security.hash_password(password), role))
    db.log_activity(user, "Added user", f"{username} ({security.ROLE_LABELS[role]})")
    flash(request, "good", f"User '{username}' added. Give them the password you typed; they can change it "
                           "under My account.")
    return go("/settings#users")


@app.post("/settings/users/{user_id}", dependencies=[CSRF])
def settings_user_update(request: Request, user_id: int, full_name: str = Form(""), role: str = Form(""),
                         is_active: str = Form(""), password: str = Form(""),
                         user: dict = Depends(security.require("admin"))):
    target = db.query_one("SELECT * FROM users WHERE id = %s", (user_id,))
    if not target or role not in security.ROLES:
        raise _not_found()
    active = is_active == "1"
    if target["role"] == "admin" and target["is_active"] and (role != "admin" or not active) \
            and _active_admins(excluding=user_id) == 0:
        flash(request, "bad", "There must always be at least one active administrator.")
        return go("/settings#users")
    if password:
        problem = security.password_problem(password)
        if problem:
            flash(request, "bad", problem)
            return go("/settings#users")
    db.execute("UPDATE users SET full_name = %s, role = %s, is_active = %s WHERE id = %s",
               (full_name.strip()[:80], role, active, user_id))
    changes = []
    if role != target["role"]:
        changes.append(f"role {security.ROLE_LABELS[role]}")
    if active != target["is_active"]:
        changes.append("enabled" if active else "disabled")
    if password:
        new_hash = security.hash_password(password)
        db.execute("UPDATE users SET password_hash = %s WHERE id = %s", (new_hash, user_id))
        changes.append("password reset")
        if user_id == user["id"]:
            security.refresh_session(request, new_hash)
    db.log_activity(user, "Updated user", f"{target['username']}: {', '.join(changes) or 'details'}")
    flash(request, "good", f"User '{target['username']}' updated.")
    return go("/settings#users")


# ---- my account --------------------------------------------------------------------------
@app.get("/account")
def account_page(request: Request, user: dict = Depends(security.require())):
    return render(request, "account.html", active="account")


@app.post("/account/profile", dependencies=[CSRF])
def account_profile(request: Request, full_name: str = Form(""), user: dict = Depends(security.require())):
    db.execute("UPDATE users SET full_name = %s WHERE id = %s", (full_name.strip()[:80], user["id"]))
    flash(request, "good", "Your name was updated.")
    return go("/account")


@app.post("/account/password", dependencies=[CSRF])
def account_password(request: Request, current: str = Form(""), password: str = Form(""), confirm: str = Form(""),
                     user: dict = Depends(security.require())):
    if not security.verify_password(current, user["password_hash"]):
        flash(request, "bad", "Your current password is not correct.")
        return go("/account")
    problem = security.password_problem(password, confirm)
    if problem:
        flash(request, "bad", problem)
        return go("/account")
    new_hash = security.hash_password(password)
    db.execute("UPDATE users SET password_hash = %s WHERE id = %s", (new_hash, user["id"]))
    security.refresh_session(request, new_hash)
    db.log_activity(user, "Changed own password")
    flash(request, "good", "Password changed. Other devices signed in with the old password are signed out.")
    return go("/account")
