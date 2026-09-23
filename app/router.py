"""Cascade router: smallest capable model first, escalate on quality-gate failure."""
from __future__ import annotations

import logging
import time

from . import metrics
from .accounting import Footprint, estimate, savings_pct
from .carbon import CarbonProvider
from .complexity import starting_tier
from .config import ModelTier, Settings
from .llm import Completion, LLMClient
from .quality import QualityChecker
from .schemas import AttemptOut, ChatRequest, ChatResponse, FootprintOut

log = logging.getLogger(__name__)


class NoTierSucceeded(RuntimeError):
    pass


def _flatten(messages: list[dict]) -> str:
    if len(messages) == 1:
        return messages[0]["content"]
    return "\n\n".join(f"{m['role'].upper()}: {m['content']}" for m in messages)


def _fp_out(fp: Footprint) -> FootprintOut:
    return FootprintOut(energy_wh=round(fp.energy_wh, 6), co2_g=round(fp.co2_g, 8),
                        cost_usd=round(fp.cost_usd, 8))


class Router:
    def __init__(self, client: LLMClient, tiers: list[ModelTier], checker: QualityChecker,
                 carbon: CarbonProvider, settings: Settings):
        self.client, self.tiers, self.checker = client, tiers, checker
        self.carbon, self.settings = carbon, settings

    def _bounds(self, req: ChatRequest) -> tuple[int, int]:
        last = len(self.tiers) - 1
        if req.baseline:
            return last, last
        lo = min(req.min_tier or 0, last)
        hi = last if req.max_tier is None else min(max(req.max_tier, lo), last)
        return lo, hi

    async def route(self, req: ChatRequest, *, intensity: float | None = None,
                    workload: str = "interactive") -> ChatResponse:
        t0 = time.perf_counter()
        if req.baseline:
            workload = "baseline"   # keep A/B traffic out of the savings panels
        if intensity is None:
            intensity = (await self.carbon.current()).g_per_kwh

        messages = req.to_messages()
        prompt = _flatten(messages)
        lo, hi = self._bounds(req)
        start = max(lo, min(hi, starting_tier(prompt, len(self.tiers))))
        pue = self.settings.pue

        attempts: list[AttemptOut] = []
        errors: list[str] = []
        total = Footprint()
        final: Completion | None = None
        final_tier = start

        for i in range(start, hi + 1):
            tier, is_last = self.tiers[i], i == hi
            try:
                comp = await self.client.complete(
                    tier.model_id, messages, system=req.system,
                    max_tokens=req.max_tokens or self.settings.max_output_tokens,
                    temperature=self.settings.temperature)
            except Exception as e:  # noqa: BLE001 - throttling/outage: try the next tier
                log.warning("tier %s failed: %s", tier.name, e)
                metrics.MODEL_CALLS.labels(tier.name, "answer", "error").inc()
                errors.append(f"{tier.name}: {e}")
                continue

            fp = estimate(tier, comp.input_tokens, comp.output_tokens, intensity, pue)
            metrics.record_footprint(tier.name, workload, fp)
            verdict = await self.checker.check(prompt, comp, req.response_format,
                                               run_judge=not is_last)
            if verdict.judge_completion is not None:
                jt = self.tiers[self.checker.judge_tier]
                jc = verdict.judge_completion
                jfp = estimate(jt, jc.input_tokens, jc.output_tokens, intensity, pue)
                metrics.record_footprint(jt.name, workload, jfp)
                metrics.MODEL_CALLS.labels(jt.name, "judge", "ok").inc()
                fp = fp + jfp   # the judge's cost is part of this attempt's price
            total = total + fp

            metrics.MODEL_CALLS.labels(tier.name, "answer",
                                       "pass" if verdict.passed else "fail").inc()
            attempts.append(AttemptOut(
                model=tier.name, tier=i, passed=verdict.passed, score=verdict.score,
                reasons=verdict.reasons, input_tokens=comp.input_tokens,
                output_tokens=comp.output_tokens, latency_ms=round(comp.latency_ms, 1)))
            final, final_tier = comp, i
            if verdict.passed:
                break
            if not is_last:
                metrics.ESCALATIONS.labels(tier.name, self.tiers[i + 1].name).inc()

        if final is None:
            raise NoTierSucceeded("; ".join(errors) or "no tier available")

        # Counterfactual: the largest model producing an answer of the same length.
        baseline = estimate(self.tiers[-1], final.input_tokens, final.output_tokens,
                            intensity, pue)
        escalated = len(attempts) > 1
        elapsed = time.perf_counter() - t0

        final_name = self.tiers[final_tier].name
        metrics.REQUESTS.labels(workload, final_name, str(escalated).lower()).inc()
        metrics.BASELINE_ENERGY.labels(workload).inc(baseline.energy_wh)
        metrics.BASELINE_CO2.labels(workload).inc(baseline.co2_g)
        metrics.BASELINE_COST.labels(workload).inc(baseline.cost_usd)
        metrics.REQUEST_ENERGY.labels(workload).observe(total.energy_wh)
        metrics.REQUEST_CO2.labels(workload).observe(total.co2_g)
        metrics.LATENCY.labels(workload).observe(elapsed)

        return ChatResponse(
            answer=final.text, model=final_name, tier=final_tier, escalated=escalated,
            attempts=attempts, grid_intensity_g_per_kwh=intensity,
            footprint=_fp_out(total), baseline_footprint=_fp_out(baseline),
            savings_pct=savings_pct(total, baseline), latency_ms=round(elapsed * 1000, 1))
