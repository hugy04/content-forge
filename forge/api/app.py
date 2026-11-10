"""HTTP surface.

Four ways in, one pipeline behind them: a browser form (what a content team
actually uses), a JSON API, a generic webhook, and a batch endpoint. The webhook
exists because the brief asks for "webhook, form, API, or any trigger" and a
generic shape is what lets Zapier/Make/a CMS call this without a custom adapter.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from forge.api.schemas import BatchRequest, GenerateRequest
from forge.config import get_settings
from forge.diversity.taxonomy import ART_DIRECTIONS, DIMENSIONS, STYLES
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
OPEN_PATHS = {"/healthz"}


class BasicAuthMiddleware(BaseHTTPMiddleware):
    """Gate the whole surface behind one shared credential.

    A deployment handed to a reviewer should not also be an open endpoint that
    spends the project's free-tier quota for anyone who finds the URL. This is
    deliberately the simplest thing that holds: one shared username and
    password, no sessions, no user store - and no auth at all when the
    variables are unset, so local runs and the test suite are untouched. Real
    multi-user auth is listed in docs/DESIGN.md under what production needs.
    """

    def __init__(self, app, user: str, password: str) -> None:
        super().__init__(app)
        self._user = user
        self._password = password

    async def dispatch(self, request: Request, call_next):
        if request.url.path in OPEN_PATHS:
            return await call_next(request)

        header = request.headers.get("authorization", "")
        if header.startswith("Basic "):
            try:
                user, _, password = base64.b64decode(
                    header[6:]).decode("utf-8").partition(":")
            except Exception:
                user = password = ""
            # compare_digest on both halves: a plain == leaks length and
            # position through timing, and it costs nothing to avoid.
            if (secrets.compare_digest(user, self._user)
                    and secrets.compare_digest(password, self._password)):
                return await call_next(request)

        return Response(
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="content-forge"'},
        )


_settings = get_settings()
if _settings.basic_auth_user and _settings.basic_auth_pass:
    app.add_middleware(BasicAuthMiddleware,
                       user=_settings.basic_auth_user,
                       password=_settings.basic_auth_pass)
    log.info("basic auth is on; /healthz stays open")

_settings.out_dir.mkdir(parents=True, exist_ok=True)
app.mount("/artifacts", StaticFiles(directory=str(_settings.out_dir)), name="artifacts")


def _store() -> Store:
    return Store(get_settings().db_path)


# ----------------------------------------------------------------- browser UI

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    settings = get_settings()
    return templates.TemplateResponse(request, "index.html", {
        "styles": {k: v.label for k, v in STYLES.items()},
        "art_directions": list(ART_DIRECTIONS),
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
