from app.quality import QualityChecker
from app.router import Router
from app.schemas import ChatRequest
from tests.conftest import FakeCarbon, FakeClient


def make_router(client, tiers, settings, judge=False):
    checker = QualityChecker(client, tiers[1].model_id, 1, 7.0, judge_enabled=judge)
    return Router(client, tiers, checker, FakeCarbon(30, []), settings)


async def test_easy_prompt_stays_on_smallest_tier(tiers, settings):
    client = FakeClient({})
    res = await make_router(client, tiers, settings).route(ChatRequest(prompt="Capital of Norway?"))
    assert res.model == "nova-micro" and not res.escalated
    assert res.savings_pct["energy"] > 80   # micro vs pro at same token counts


async def test_escalates_when_structural_check_fails(tiers, settings):
    client = FakeClient({tiers[0].model_id: "", tiers[1].model_id: "I'm not sure."})
    res = await make_router(client, tiers, settings).route(ChatRequest(prompt="hi"))
    assert [a.model for a in res.attempts] == ["nova-micro", "nova-lite", "nova-pro"]
    assert res.model == "nova-pro" and res.escalated
    # failed attempts are billed: total exceeds the single large-model baseline
    assert res.footprint.energy_wh > res.baseline_footprint.energy_wh


async def test_judge_low_score_escalates_and_is_skipped_on_last_tier(tiers, settings):
    client = FakeClient({}, judge_score=3)
    res = await make_router(client, tiers, settings, judge=True).route(ChatRequest(prompt="hi"))
    assert res.model == "nova-pro"
    assert res.attempts[-1].score is None      # no judge on the final tier
    assert client.calls.count(tiers[1].model_id) == 3  # lite answered once + judged twice


async def test_baseline_forces_largest(tiers, settings):
    res = await make_router(FakeClient({}), tiers, settings).route(
        ChatRequest(prompt="hi", baseline=True))
    assert res.model == "nova-pro" and res.savings_pct["energy"] == 0


async def test_max_tier_caps_escalation(tiers, settings):
    client = FakeClient({tiers[0].model_id: ""})
    res = await make_router(client, tiers, settings).route(ChatRequest(prompt="hi", max_tier=1))
    assert res.model == "nova-lite"


async def test_hard_prompt_skips_smallest_tier(tiers, settings):
    prompt = ("Design an algorithm and analyze its complexity, compare trade-offs, "
              "and explain in detail step by step:\n```python\ndef f(x): pass\n```")
    res = await make_router(FakeClient({}), tiers, settings).route(ChatRequest(prompt=prompt))
    assert res.attempts[0].tier >= 1
