"""Live grid carbon intensity for Swedish bidding zones (SE-SE1..SE-SE4).

Primary source: Electricity Maps API v3. If the forecast endpoint isn't on your
plan, a persistence forecast is built from the last 24 h of history (same hour
tomorrow ~= same hour today). If the API is unreachable, the last known value is
reused; only as a last resort is a synthetic diurnal curve used.
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

import httpx

from . import metrics

log = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class IntensityPoint:
    at: datetime
    g_per_kwh: float


class CarbonProvider(Protocol):
    zone: str
    source: str
    async def current(self) -> IntensityPoint: ...
    async def forecast(self, hours: int) -> list[IntensityPoint]: ...
    async def aclose(self) -> None: ...


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _window(points: list[IntensityPoint], hours: int) -> list[IntensityPoint]:
    now, end = utcnow(), utcnow() + timedelta(hours=hours)
    return sorted((p for p in points if now - timedelta(hours=1) < p.at <= end), key=lambda p: p.at)


class ElectricityMapsProvider:
    source = "electricitymaps"

    def __init__(self, token: str, zone: str, cache_seconds: int = 300,
                 base_url: str = "https://api.electricitymap.org/v3",
                 client: httpx.AsyncClient | None = None):
        self.zone = zone
        self.cache_seconds = cache_seconds
        self._http = client or httpx.AsyncClient(base_url=base_url, timeout=10,
                                                 headers={"auth-token": token})
        self._cache: dict[str, tuple[float, dict]] = {}

    async def _get(self, path: str) -> dict:
        hit = self._cache.get(path)
        if hit and time.monotonic() - hit[0] < self.cache_seconds:
            return hit[1]
        resp = await self._http.get(path, params={"zone": self.zone})
        resp.raise_for_status()
        data = resp.json()
        self._cache[path] = (time.monotonic(), data)
        return data

    async def current(self) -> IntensityPoint:
        d = await self._get("/carbon-intensity/latest")
        return IntensityPoint(_parse(d["datetime"]), float(d["carbonIntensity"]))

    async def forecast(self, hours: int) -> list[IntensityPoint]:
        try:
            d = await self._get("/carbon-intensity/forecast")
            pts = [IntensityPoint(_parse(p["datetime"]), float(p["carbonIntensity"]))
                   for p in d.get("forecast", []) if p.get("carbonIntensity") is not None]
            if pts:
                return _window(pts, hours)
        except httpx.HTTPStatusError as e:
            log.info("forecast endpoint unavailable (%s); using persistence forecast",
                     e.response.status_code)
        d = await self._get("/carbon-intensity/history")
        pts = []
        for p in d.get("history", []):
            if p.get("carbonIntensity") is None:
                continue
            t = _parse(p["datetime"]) + timedelta(hours=24)
            pts.append(IntensityPoint(t, float(p["carbonIntensity"])))
        return _window(pts, hours)

    async def aclose(self) -> None:
        await self._http.aclose()


class SyntheticProvider:
    """Offline diurnal curve: cleanest around 05:00 UTC, dirtiest around 17:00 UTC."""
    source = "synthetic"
    _BASE = {"SE-SE1": 15, "SE-SE2": 18, "SE-SE3": 35, "SE-SE4": 70}

    def __init__(self, zone: str):
        self.zone = zone
        self.base = self._BASE.get(zone, 40)

    def at(self, t: datetime) -> float:
        hour = t.hour + t.minute / 60
        return round(self.base + 0.35 * self.base * math.sin(2 * math.pi * (hour - 11) / 24), 2)

    async def current(self) -> IntensityPoint:
        now = utcnow()
        return IntensityPoint(now, self.at(now))

    async def forecast(self, hours: int) -> list[IntensityPoint]:
        start = utcnow().replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        return [IntensityPoint(start + timedelta(hours=h), self.at(start + timedelta(hours=h)))
                for h in range(hours)]

    async def aclose(self) -> None:
        return None


class FallbackProvider:
    """Wraps a live provider: last-known-good value first, synthetic curve last."""

    def __init__(self, primary: CarbonProvider, fallback: CarbonProvider):
        self.primary, self.fallback = primary, fallback
        self.zone = primary.zone
        self._last: IntensityPoint | None = None

    @property
    def source(self) -> str:
        return self.primary.source

    async def current(self) -> IntensityPoint:
        try:
            self._last = await self.primary.current()
            return self._last
        except Exception as e:  # noqa: BLE001 - never fail a request over carbon data
            log.warning("carbon API failed: %s", e)
            if self._last and utcnow() - self._last.at < timedelta(hours=3):
                metrics.CARBON_FALLBACKS.labels("last_known").inc()
                return self._last
            metrics.CARBON_FALLBACKS.labels("synthetic").inc()
            return await self.fallback.current()

    async def forecast(self, hours: int) -> list[IntensityPoint]:
        try:
            pts = await self.primary.forecast(hours)
            if pts:
                return pts
        except Exception as e:  # noqa: BLE001
            log.warning("carbon forecast failed: %s", e)
        metrics.CARBON_FALLBACKS.labels("forecast_synthetic").inc()
        return await self.fallback.forecast(hours)

    async def aclose(self) -> None:
        await self.primary.aclose()


def build_carbon_provider(zone: str, token: str | None, cache_seconds: int) -> CarbonProvider:
    synthetic = SyntheticProvider(zone)
    if not token:
        log.warning("GREENROUTE_ELECTRICITYMAPS_TOKEN not set: using SYNTHETIC carbon data")
        return synthetic
    return FallbackProvider(ElectricityMapsProvider(token, zone, cache_seconds), synthetic)
