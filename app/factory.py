"""Wire components together (shared by the API, benchmark and load generator)."""
from __future__ import annotations

from dataclasses import dataclass

from .carbon import CarbonProvider, build_carbon_provider
from .config import ModelTier, Settings
from .llm import BedrockClient, LLMClient, MockClient
from .quality import QualityChecker
from .router import Router
from .scheduler import BatchScheduler
from .store import InMemoryJobStore, JobStore, RedisJobStore


@dataclass
class Components:
    settings: Settings
    tiers: list[ModelTier]
    client: LLMClient
    carbon: CarbonProvider
    router: Router
    store: JobStore
    scheduler: BatchScheduler


def build(settings: Settings) -> Components:
    tiers = settings.load_tiers()
    client: LLMClient = (MockClient([t.model_id for t in tiers]) if settings.mock_bedrock
                         else BedrockClient(settings.aws_region))
    judge_tier = min(settings.judge_tier, len(tiers) - 1)
    checker = QualityChecker(client, tiers[judge_tier].model_id, judge_tier,
                             settings.quality_threshold, settings.judge_enabled)
    carbon = build_carbon_provider(settings.carbon_zone, settings.electricitymaps_token,
                                   settings.carbon_cache_seconds)
    router = Router(client, tiers, checker, carbon, settings)
    store: JobStore = RedisJobStore(settings.redis_url) if settings.redis_url else InMemoryJobStore()
    scheduler = BatchScheduler(store, router, carbon, settings)
    return Components(settings, tiers, client, carbon, router, store, scheduler)
