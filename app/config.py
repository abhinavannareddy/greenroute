"""Settings and model-tier definitions.

Energy coefficients are *estimates*. No LLM provider reports per-request energy,
so GreenRoute uses per-token coefficients (Wh per 1k tokens) that you should
calibrate for your models. They matter mostly in *relative* terms: the router's
savings come from the ratio between small and large tiers.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelTier(BaseModel):
    name: str
    model_id: str
    input_usd_per_1k: float
    output_usd_per_1k: float
    wh_per_1k_input: float
    wh_per_1k_output: float


# Ordered smallest -> largest. Prices are list prices at time of writing; check the
# Bedrock pricing page for your region. The "eu." prefix is a cross-region inference
# profile; see the README for how that affects carbon accounting.
DEFAULT_TIERS: list[ModelTier] = [
    ModelTier(name="nova-micro", model_id="eu.amazon.nova-micro-v1:0",
              input_usd_per_1k=0.000035, output_usd_per_1k=0.00014,
              wh_per_1k_input=0.01, wh_per_1k_output=0.08),
    ModelTier(name="nova-lite", model_id="eu.amazon.nova-lite-v1:0",
              input_usd_per_1k=0.00006, output_usd_per_1k=0.00024,
              wh_per_1k_input=0.03, wh_per_1k_output=0.25),
    ModelTier(name="nova-pro", model_id="eu.amazon.nova-pro-v1:0",
              input_usd_per_1k=0.0008, output_usd_per_1k=0.0032,
              wh_per_1k_input=0.12, wh_per_1k_output=1.0),
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GREENROUTE_", env_file=".env", extra="ignore")

    # LLM backend
    aws_region: str = "eu-north-1"
    mock_bedrock: bool = False
    tiers_file: Path | None = None
    max_output_tokens: int = 1024
    temperature: float = 0.2

    # Quality gate
    judge_enabled: bool = True
    judge_tier: int = 1
    quality_threshold: float = 7.0

    # Carbon data
    carbon_zone: str = "SE-SE3"          # Stockholm bidding zone (where eu-north-1 runs)
    electricitymaps_token: str | None = None
    carbon_cache_seconds: int = 300
    pue: float = 1.15                     # data-centre power usage effectiveness

    # Batch scheduler
    redis_url: str | None = None
    scheduler_enabled: bool = True
    scheduler_interval_seconds: float = 60
    default_deadline_hours: float = 24
    window_tolerance: float = 0.05        # accept a slot within 5% of the best one
    batch_concurrency: int = 4

    def load_tiers(self) -> list[ModelTier]:
        if self.tiers_file:
            data = yaml.safe_load(Path(self.tiers_file).read_text())
            tiers = [ModelTier(**t) for t in data["tiers"]]
        else:
            tiers = list(DEFAULT_TIERS)
        if not tiers:
            raise ValueError("at least one model tier is required")
        return tiers


@lru_cache
def get_settings() -> Settings:
    return Settings()
