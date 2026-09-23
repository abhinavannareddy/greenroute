"""Energy, CO2 and cost estimation per model call."""
from __future__ import annotations

from dataclasses import dataclass

from .config import ModelTier


@dataclass(frozen=True)
class Footprint:
    energy_wh: float = 0.0
    co2_g: float = 0.0
    cost_usd: float = 0.0

    def __add__(self, other: "Footprint") -> "Footprint":
        return Footprint(self.energy_wh + other.energy_wh,
                         self.co2_g + other.co2_g,
                         self.cost_usd + other.cost_usd)


def estimate(tier: ModelTier, input_tokens: int, output_tokens: int,
             intensity_g_per_kwh: float, pue: float) -> Footprint:
    k_in, k_out = input_tokens / 1000, output_tokens / 1000
    energy_wh = (k_in * tier.wh_per_1k_input + k_out * tier.wh_per_1k_output) * pue
    co2_g = energy_wh / 1000 * intensity_g_per_kwh
    cost = k_in * tier.input_usd_per_1k + k_out * tier.output_usd_per_1k
    return Footprint(energy_wh, co2_g, cost)


def savings_pct(actual: Footprint, baseline: Footprint) -> dict[str, float]:
    def pct(a: float, b: float) -> float:
        return round(100 * (1 - a / b), 2) if b > 0 else 0.0
    return {
        "energy": pct(actual.energy_wh, baseline.energy_wh),
        "co2": pct(actual.co2_g, baseline.co2_g),
        "cost": pct(actual.cost_usd, baseline.cost_usd),
    }
