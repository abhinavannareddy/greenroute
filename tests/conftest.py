from datetime import timedelta

import pytest

from app.carbon import IntensityPoint, utcnow
from app.config import DEFAULT_TIERS, Settings
from app.llm import Completion


class FakeClient:
    """Scripted client: answers per model_id, records calls."""

    def __init__(self, answers: dict[str, str], judge_score: int = 9):
        self.answers, self.judge_score, self.calls = answers, judge_score, []

    async def complete(self, model_id, messages, *, system=None, max_tokens=1024, temperature=0.2):
        self.calls.append(model_id)
        if system and "answer grader" in system:
            return Completion(f'{{"score": {self.judge_score}, "reason": "ok"}}', 50, 10, 1, "end_turn")
        return Completion(self.answers.get(model_id, "fine answer"), 20, 100, 1, "end_turn")


class FakeCarbon:
    zone, source = "SE-SE3", "fake"

    def __init__(self, now_g: float, forecast: list[float]):
        self.now_g, self.values = now_g, forecast

    async def current(self):
        return IntensityPoint(utcnow(), self.now_g)

    async def forecast(self, hours):
        base = utcnow().replace(minute=0, second=0, microsecond=0)
        return [IntensityPoint(base + timedelta(hours=h + 1), g)
                for h, g in enumerate(self.values[:hours])]

    async def aclose(self):
        pass


@pytest.fixture
def settings():
    return Settings(mock_bedrock=True, scheduler_enabled=False, judge_enabled=False,
                    electricitymaps_token=None)


@pytest.fixture
def tiers():
    return list(DEFAULT_TIERS)
