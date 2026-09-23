from app.llm import Completion
from app.quality import QualityChecker
from tests.conftest import FakeClient


def comp(text, stop="end_turn"):
    return Completion(text, 10, 10, 1, stop)


checker = QualityChecker(FakeClient({}), "judge", 1, judge_enabled=False)


def test_structural_checks():
    assert checker.structural(comp(""), "text") == ["empty answer"]
    assert "truncated at max_tokens" in checker.structural(comp("abc", "max_tokens"), "text")
    assert "refusal or hedging" in checker.structural(comp("As an AI, I can't help."), "text")
    assert "invalid JSON" in checker.structural(comp("{name: 1"), "json")
    assert checker.structural(comp('```json\n{"a": 1}\n```'), "json") == []


async def test_unparseable_judge_fails_closed():
    class BadJudge(FakeClient):
        async def complete(self, *a, **k):
            return Completion("looks good to me", 1, 1, 1, "end_turn")
    q = QualityChecker(BadJudge({}), "judge", 1, judge_enabled=True)
    v = await q.check("q", comp("answer"))
    assert not v.passed and v.judge_completion is not None
