"""HTTP surface.

Four ways in, one pipeline behind them: a browser form (what a content team
actually uses), a JSON API, a generic webhook, and a batch endpoint. The webhook
exists because the brief asks for "webhook, form, API, or any trigger" and a
generic shape is what lets Zapier/Make/a CMS call this without a custom adapter.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from urllib.parse import quote

from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from forge.api.schemas import BatchRequest, GenerateRequest
from forge.config import get_settings
from forge.diversity.labels import options as dimension_options
from forge.diversity.taxonomy import (
    ANGLES,
    ART_DIRECTIONS,
    AUDIENCES,
    COMPOSITIONS,
    DIMENSIONS,
    PALETTES,
    STYLES,
)
from forge.pipeline.context import RunContext
from forge.pipeline.runner import build_deps, run_pipeline
from forge.providers.retry import BREAKER
from forge.store.db import Store

logging.basicConfig(
    level=logging.INFO,
    format='{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
)
log = logging.getLogger("forge.api")

BASE = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.out_dir.mkdir(parents=True, exist_ok=True)
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    Store(settings.db_path).stats()  # force schema creation at boot
    log.info("content-forge up | mode=%s | images=%s | sinks=%s | fake=%s",
             settings.run_mode, settings.image_provider_chain,
             settings.sink_names, settings.fake_providers)
    yield


app = FastAPI(
    title="content-forge",
    version="1.0.0",
    description="Topic in, a 500-800 word piece and a matching AI image out.",
    lifespan=lifespan,
)

# Paths that stay reachable without credentials. An uptime monitor has to be
# able to prove the service is alive without being trusted with a password, and
# a 401 on /healthz would make a gated deployment look permanently down.
OPEN_PATHS = {"/healthz", "/login"}
SESSION_COOKIE = "forge_session"


def _session_value(settings) -> str:
    """A cookie value only someone who knows the credentials can produce.

    Derived from the credentials rather than stored, so there is no session
    table to keep and changing the password invalidates every cookie already
    issued. It carries no user identity because there is only one account.
    """
    secret = f"{settings.basic_auth_user}:{settings.basic_auth_pass}".encode()
    return hmac.new(secret, b"forge-session-v1", hashlib.sha256).hexdigest()


def _credentials_ok(settings, user: str, password: str) -> bool:
    # compare_digest on both halves: a plain == leaks length and position
    # through timing, and it costs nothing to avoid.
    return (secrets.compare_digest(user, settings.basic_auth_user)
            and secrets.compare_digest(password, settings.basic_auth_pass))


class AuthMiddleware(BaseHTTPMiddleware):
    """Gate the whole surface behind one shared credential.

    A deployment handed to a reviewer should not also be an open endpoint that
    spends the project's free-tier quota for anyone who finds the URL. Two ways
    in, because two kinds of caller arrive: a browser gets a sign-in page
    styled like the rest of the app, and a script can still send an
    `Authorization: Basic` header, so curl and CI need no cookie jar.

    Deliberately the simplest thing that holds: one shared account, no user
    store, no session table. Real multi-user auth is listed in docs/DESIGN.md
    under what production needs.
    """

    def __init__(self, app, settings) -> None:
        super().__init__(app)
        self._settings = settings
        self._expected = _session_value(settings)

    def _authorised(self, request: Request) -> bool:
        cookie = request.cookies.get(SESSION_COOKIE, "")
        if cookie and secrets.compare_digest(cookie, self._expected):
            return True
        header = request.headers.get("authorization", "")
        if header.startswith("Basic "):
            try:
                user, _, password = base64.b64decode(
                    header[6:]).decode("utf-8").partition(":")
            except Exception:
                return False
            return _credentials_ok(self._settings, user, password)
        return False

    async def dispatch(self, request: Request, call_next):
        if request.url.path in OPEN_PATHS or self._authorised(request):
            return await call_next(request)

        # A browser should land on the sign-in page; a script should get a
        # status code it can act on rather than a page of HTML it cannot.
        if "text/html" in request.headers.get("accept", ""):
            target = request.url.path
            if request.url.query:
                target = f"{target}?{request.url.query}"
            return RedirectResponse(f"/login?next={quote(target, safe='')}",
                                    status_code=303)
        return JSONResponse({"error": "authentication required"}, status_code=401)


_settings = get_settings()
AUTH_ON = bool(_settings.basic_auth_user and _settings.basic_auth_pass)
if AUTH_ON:
    app.add_middleware(AuthMiddleware, settings=_settings)
    log.info("auth is on; /healthz stays open")

_settings.out_dir.mkdir(parents=True, exist_ok=True)
app.mount("/artifacts", StaticFiles(directory=str(_settings.out_dir)), name="artifacts")


def _store() -> Store:
    return Store(get_settings().db_path)


# ----------------------------------------------------------------- browser UI

@app.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, next: str = "/"):
    # Already signed in, or no gate configured: nothing to ask for.
    if not AUTH_ON or request.cookies.get(SESSION_COOKIE) == _session_value(
            get_settings()):
        return RedirectResponse(_safe_next(next), status_code=303)
    return templates.TemplateResponse(request, "login.html",
                                      {"next": _safe_next(next), "error": None})


@app.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request):
    form = await request.form()
    settings = get_settings()
    target = _safe_next(str(form.get("next") or "/"))

    if AUTH_ON and not _credentials_ok(settings, str(form.get("username") or ""),
                                       str(form.get("password") or "")):
        # One message for both halves: saying which of the two was wrong tells
        # someone guessing whether the username exists.
        return templates.TemplateResponse(
            request, "login.html",
            {"next": target, "error": "That username and password did not match."},
            status_code=401,
        )

    response = RedirectResponse(target, status_code=303)
    if AUTH_ON:
        response.set_cookie(
            SESSION_COOKIE, _session_value(settings),
            max_age=60 * 60 * 12, httponly=True, samesite="lax",
            # Secure in production; a local http:// run would never see the
            # cookie come back if this were unconditional.
            secure=request.url.scheme == "https",
        )
    return response


@app.post("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


def _safe_next(target: str) -> str:
    """Only ever redirect inside this site.

    `?next=` comes from the URL bar, so without this an attacker could send a
    link that signs someone in and then bounces them to a site of their
    choosing, with the referrer to make it look like the trip started here.
    """
    if not target.startswith("/") or target.startswith("//"):
        return "/"
    return target


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    settings = get_settings()
    return templates.TemplateResponse(request, "index.html", {
        "styles": {k: v.label for k, v in STYLES.items()},
        "style_options": [(k, v.label, v.guidance.split(".")[0].rstrip() + ".")
                          for k, v in STYLES.items()],
        "art_directions": list(ART_DIRECTIONS),
        # Every dimension the engine can choose, so the page can offer the same
        # control the API already accepts. Each description is the one the
        # prompt itself is built from - what the reader sees here is literally
        # what the model is told.
        "dimensions": [
            (key, label, dimension_options(key, pool), auto_hint)
            for key, label, pool, auto_hint in (
                ("angle", "Focus on", ANGLES,
                 "a focus recent runs have not used"),
                ("audience", "Written for", AUDIENCES,
                 "a readership recent runs have not used"),
                ("art_direction", "Image style", ART_DIRECTIONS,
                 "a look recent runs have not used"),
                ("palette", "Colours", PALETTES,
                 "colours recent runs have not used"),
                ("composition", "How close", COMPOSITIONS,
                 "a distance that suits the subject"),
            )
        ],
        "mode": settings.run_mode,
        "fake": settings.fake_providers,
        "image_chain": " → ".join(settings.image_provider_chain),
    })


# ------------------------------------------------------------------- JSON API

@app.post("/v1/generate")
async def generate(req: GenerateRequest):
    settings = get_settings()
    deps = build_deps(settings)

    # Replay protection: an identical topic+style on the same day returns the
    # existing asset instead of paying for it twice.
    ctx = RunContext(topic=req.topic, overrides=req.overrides(),
                     notify_url=req.notify_url or "")
    existing = deps.store.find_by_idempotency_key(ctx.idempotency_key)
    if existing:
        return JSONResponse({
            "run_id": existing["run_id"], "status": existing["status"],
            "idempotent_replay": True, "detail": existing["payload"],
        })

    if settings.run_mode == "queue":
        from forge.worker.tasks import generate_task
        task = generate_task.delay(req.topic, req.overrides(), req.notify_url or "")
        return JSONResponse(status_code=202, content={
            "run_id": None, "task_id": task.id, "status": "queued",
            "poll": "/v1/runs/{run_id} once the worker assigns one",
        })

    result = await run_pipeline(ctx, deps)
    return JSONResponse(
        status_code=200 if result.status in ("completed", "partial") else 500,
        content=result.summary(),
    )


@app.post("/webhooks/topic")
async def webhook(request: Request):
    """Permissive trigger: accepts {topic} / {text} / {subject} / a bare string."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - form posts and bare text both land here
        raw = (await request.body()).decode(errors="replace").strip()
        body = {"topic": raw}
    if isinstance(body, str):
        body = {"topic": body}
    topic = body.get("topic") or body.get("text") or body.get("subject")
    if not topic:
        raise HTTPException(400, "no topic found; send {\"topic\": \"...\"}")
    return await generate(GenerateRequest(topic=topic, style=body.get("style")))


@app.post("/v1/batch")
async def batch(req: BatchRequest, background: BackgroundTasks):
    """Bounded-concurrency fan-out. One topic failing never affects the others."""
    settings = get_settings()
    if settings.run_mode == "queue":
        from forge.worker.tasks import generate_task
        tasks = [generate_task.delay(t, {"style": req.style} if req.style else {}, "")
                 for t in req.topics]
        return JSONResponse(status_code=202, content={
            "queued": len(tasks), "task_ids": [t.id for t in tasks],
        })

    deps = build_deps(settings)
    semaphore = asyncio.Semaphore(req.concurrency)

    async def one(topic: str):
        async with semaphore:
            ctx = RunContext(topic=topic,
                             overrides={"style": req.style} if req.style else {})
            try:
                result = await run_pipeline(ctx, deps)
                return {"topic": topic, "run_id": result.run_id,
                        "status": result.status, "word_count": result.word_count,
                        "style": result.selection.style if result.selection else None,
                        "art_direction": (result.selection.art_direction
                                          if result.selection else None)}
            except Exception as exc:  # noqa: BLE001 - isolate per topic
                return {"topic": topic, "status": "failed", "error": str(exc)}

    results = await asyncio.gather(*(one(t) for t in req.topics))
    return {"count": len(results), "results": results}


@app.get("/v1/runs/{run_id}")
async def get_run(run_id: str):
    record = _store().get_run(run_id)
    if not record:
        raise HTTPException(404, f"no run {run_id}")
    return record


@app.get("/v1/runs")
async def list_runs(limit: int = 20):
    from forge.store.db import _connect
    with _connect(get_settings().db_path) as conn:
        rows = conn.execute(
            "SELECT run_id, topic, title, status, style, angle, art_direction,"
            " word_count, revisions, image_provider, duration_ms, created_at"
            " FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return {"runs": [dict(r) for r in rows]}


@app.get("/v1/stats")
async def stats():
    return {"runs": _store().stats(), "circuit_breakers": BREAKER.snapshot()}


@app.get("/v1/taxonomy")
async def taxonomy():
    return {"dimensions": DIMENSIONS,
            "combinations": len(DIMENSIONS["style"]) * len(DIMENSIONS["angle"])
                            * len(DIMENSIONS["art_direction"])}


@app.get("/healthz")
async def healthz():
    """Cheap and dependency-free, so an uptime monitor can hit it every minute."""
    settings = get_settings()
    return {"ok": True, "mode": settings.run_mode,
            "fake_providers": settings.fake_providers,
            "text_provider": settings.text_provider,
            # The configured model id, because "which model is this instance
            # actually asking for" is the first question when a deployment is
            # slow, and guessing it from the outside is not possible.
            "text_model": settings.active_text_model,
            "text_provider_configured": settings.text_provider_configured,
            "text_timeout_seconds": settings.text_timeout_seconds,
            "image_chain": settings.image_provider_chain}
