"""FastAPI entrypoint."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from prometheus_client import make_asgi_app

from . import __version__, metrics
from .carbon import IntensityPoint, utcnow
from .config import get_settings
from .factory import build
from .router import NoTierSucceeded
from .scheduler import choose_start
from .schemas import BatchJob, BatchSubmit, ChatRequest, ChatResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("greenroute")


async def _intensity_loop(c) -> None:
    while True:
        try:
            p = await c.carbon.current()
            metrics.GRID_INTENSITY.labels(c.carbon.zone).set(p.g_per_kwh)
        except Exception:  # noqa: BLE001
            log.exception("intensity refresh failed")
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    c = build(get_settings())
    app.state.c = c
    tasks = [asyncio.create_task(_intensity_loop(c))]
    if c.settings.scheduler_enabled:
        tasks.append(asyncio.create_task(c.scheduler.run_forever()))
    log.info("GreenRoute %s up: tiers=%s zone=%s carbon=%s mock=%s", __version__,
             [t.name for t in c.tiers], c.carbon.zone, c.carbon.source, c.settings.mock_bedrock)
    yield
    for t in tasks:
        t.cancel()
    await c.carbon.aclose()


app = FastAPI(title="GreenRoute", version=__version__, lifespan=lifespan)
app.mount("/metrics", make_asgi_app())


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/readyz")
async def readyz(request: Request):
    return {"status": "ready", "tiers": [t.name for t in request.app.state.c.tiers]}


@app.post("/v1/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request):
    try:
        return await request.app.state.c.router.route(req)
    except NoTierSucceeded as e:
        raise HTTPException(502, f"all model tiers failed: {e}") from e


@app.post("/v1/batch", response_model=BatchJob, status_code=202)
async def submit_batch(sub: BatchSubmit, request: Request):
    return await request.app.state.c.scheduler.submit(sub)


@app.get("/v1/batch/{job_id}", response_model=BatchJob)
async def get_batch(job_id: str, request: Request):
    job = await request.app.state.c.store.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return job


@app.get("/v1/carbon")
async def carbon(request: Request, hours: int = 24):
    c = request.app.state.c
    now = utcnow()
    current = await c.carbon.current()
    forecast = await c.carbon.forecast(hours)
    start, expected = choose_start([IntensityPoint(now, current.g_per_kwh), *forecast], now,
                                   forecast[-1].at if forecast else now,
                                   c.settings.window_tolerance)
    return {
        "zone": c.carbon.zone, "source": c.carbon.source,
        "current": {"at": current.at, "g_per_kwh": current.g_per_kwh},
        "forecast": [{"at": p.at, "g_per_kwh": p.g_per_kwh} for p in forecast],
        "greenest_window": {"start": start, "g_per_kwh": expected},
    }
