"""Batch job storage. In-memory for dev; Redis for multi-replica deployments.

claim() is atomic, so only one replica ever runs a given job.
"""
from __future__ import annotations

import asyncio
from typing import Protocol

from .schemas import BatchJob, JobState


class JobStore(Protocol):
    async def put(self, job: BatchJob) -> None: ...
    async def get(self, job_id: str) -> BatchJob | None: ...
    async def list_scheduled(self) -> list[BatchJob]: ...
    async def claim(self, job_id: str) -> bool: ...


class InMemoryJobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, BatchJob] = {}
        self._lock = asyncio.Lock()

    async def put(self, job: BatchJob) -> None:
        async with self._lock:
            self._jobs[job.id] = job.model_copy(deep=True)

    async def get(self, job_id: str) -> BatchJob | None:
        job = self._jobs.get(job_id)
        return job.model_copy(deep=True) if job else None

    async def list_scheduled(self) -> list[BatchJob]:
        return [j.model_copy(deep=True) for j in self._jobs.values()
                if j.state == JobState.scheduled]

    async def claim(self, job_id: str) -> bool:
        async with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.state != JobState.scheduled:
                return False
            job.state = JobState.running
            return True


class RedisJobStore:
    PREFIX = "greenroute:"
    TTL = 7 * 86400

    def __init__(self, url: str):
        import redis.asyncio as redis
        self.r = redis.from_url(url, decode_responses=True)

    def _key(self, job_id: str) -> str:
        return f"{self.PREFIX}job:{job_id}"

    async def put(self, job: BatchJob) -> None:
        await self.r.set(self._key(job.id), job.model_dump_json(), ex=self.TTL)
        if job.state == JobState.scheduled:
            await self.r.sadd(f"{self.PREFIX}scheduled", job.id)

    async def get(self, job_id: str) -> BatchJob | None:
        raw = await self.r.get(self._key(job_id))
        return BatchJob.model_validate_json(raw) if raw else None

    async def list_scheduled(self) -> list[BatchJob]:
        ids = list(await self.r.smembers(f"{self.PREFIX}scheduled"))
        if not ids:
            return []
        raws = await self.r.mget([self._key(i) for i in ids])
        return [BatchJob.model_validate_json(r) for r in raws if r]

    async def claim(self, job_id: str) -> bool:
        return await self.r.srem(f"{self.PREFIX}scheduled", job_id) == 1
