"""Carbon-aware scheduler for non-urgent batch jobs.

On submit, the job is placed at the earliest forecast slot (before its deadline)
whose intensity is within `window_tolerance` of the greenest slot. The loop then
runs jobs when their slot arrives, when the deadline hits, or early if live
intensity is already as low as the slot we were waiting for.
"""
from __future__ import annotations

import asyncio
import logging
import math
import uuid
from datetime import datetime, timedelta

from . import metrics
from .carbon import CarbonProvider, IntensityPoint, utcnow
from .config import Settings
from .router import Router
from .schemas import BatchJob, BatchSubmit, JobState
from .store import JobStore

log = logging.getLogger(__name__)


def choose_start(points: list[IntensityPoint], now: datetime, deadline: datetime,
                 tolerance: float = 0.0) -> tuple[datetime, float | None]:
    candidates = sorted((p for p in points if p.at <= deadline), key=lambda p: p.at)
    if not candidates:
        return now, None
    best = min(p.g_per_kwh for p in candidates)
    for p in candidates:
        if p.g_per_kwh <= best * (1 + tolerance):
            return max(p.at, now), p.g_per_kwh
    return now, None  # unreachable


class BatchScheduler:
    def __init__(self, store: JobStore, router: Router, carbon: CarbonProvider,
                 settings: Settings):
        self.store, self.router, self.carbon, self.settings = store, router, carbon, settings
        self._running: set[asyncio.Task] = set()

    async def submit(self, sub: BatchSubmit) -> BatchJob:
        now = utcnow()
        deadline = now + timedelta(hours=sub.deadline_hours or self.settings.default_deadline_hours)
        current = await self.carbon.current()
        hours = math.ceil((deadline - now).total_seconds() / 3600) + 1
        forecast = await self.carbon.forecast(hours)
        start, expected = choose_start([IntensityPoint(now, current.g_per_kwh), *forecast],
                                       now, deadline, self.settings.window_tolerance)
        job = BatchJob(
            id=uuid.uuid4().hex, state=JobState.scheduled, created_at=now, deadline=deadline,
            scheduled_for=start, expected_intensity=expected or current.g_per_kwh,
            intensity_at_submit=current.g_per_kwh, requests=sub.requests)
        await self.store.put(job)
        metrics.BATCH_JOBS.labels("scheduled").inc()
        log.info("job %s: %d requests, start %s (%.1f g/kWh expected vs %.1f now)",
                 job.id, len(sub.requests), start.isoformat(), job.expected_intensity,
                 current.g_per_kwh)
        return job

    async def tick(self) -> int:
        now = utcnow()
        current = await self.carbon.current()
        metrics.GRID_INTENSITY.labels(self.carbon.zone).set(current.g_per_kwh)
        scheduled = await self.store.list_scheduled()
        started = 0
        for job in scheduled:
            due = job.scheduled_for <= now or job.deadline <= now
            green_now = current.g_per_kwh <= job.expected_intensity * (1 + self.settings.window_tolerance)
            if (due or green_now) and await self.store.claim(job.id):
                task = asyncio.create_task(self._run(job, current.g_per_kwh))
                self._running.add(task)
                task.add_done_callback(self._running.discard)
                started += 1
        metrics.BATCH_PENDING.set(len(scheduled) - started)
        return started

    async def run_forever(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:  # noqa: BLE001
                log.exception("scheduler tick failed")
            await asyncio.sleep(self.settings.scheduler_interval_seconds)

    async def wait_idle(self) -> None:
        if self._running:
            await asyncio.gather(*self._running, return_exceptions=True)

    async def _run(self, job: BatchJob, intensity: float) -> None:
        job.state, job.started_at, job.intensity_at_run = JobState.running, utcnow(), intensity
        await self.store.put(job)
        metrics.BATCH_JOBS.labels("running").inc()
        metrics.BATCH_DEFERRAL.observe((job.started_at - job.created_at).total_seconds() / 3600)

        sem = asyncio.Semaphore(self.settings.batch_concurrency)

        async def one(req):
            async with sem:
                try:
                    return await self.router.route(req, intensity=intensity, workload="batch"), None
                except Exception as e:  # noqa: BLE001
                    return None, str(e)

        outcomes = await asyncio.gather(*(one(r) for r in job.requests))
        job.results = [res for res, _ in outcomes]
        job.errors = [f"request {i}: {err}" for i, (_, err) in enumerate(outcomes) if err]
        energy_wh = sum(r.footprint.energy_wh for r in job.results if r)
        job.co2_avoided_g = round(energy_wh / 1000 * (job.intensity_at_submit - intensity), 8)
        if job.co2_avoided_g > 0:
            metrics.BATCH_CO2_AVOIDED.inc(job.co2_avoided_g)
        job.state = JobState.failed if all(r is None for r in job.results) else JobState.done
        job.finished_at = utcnow()
        await self.store.put(job)
        metrics.BATCH_JOBS.labels(job.state.value).inc()
