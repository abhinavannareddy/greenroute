"""Automated quality gate that decides whether to escalate.

Stage 1 is free: structural checks (empty, truncated, refusal/hedging, invalid JSON).
Stage 2 is an LLM judge on a small-to-mid tier that scores the answer 1-10.
The judge is skipped on the last allowed tier, since there is nowhere to escalate to.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .llm import GRADER_MARKER, Completion, LLMClient

_REFUSAL = re.compile(
    r"\b(I(?:'m| am) (?:unable|not able) to|I can(?:no|')t (?:help|answer|provide)|"
    r"as an AI(?: language model)?|I(?:'m| am) not sure|I don't know)\b", re.I)
_SCORE = re.compile(r'"score"\s*:\s*(\d+(?:\.\d+)?)')
_MAX_JUDGE_CHARS = 6000

JUDGE_SYSTEM = (
    f"You are GreenRoute's {GRADER_MARKER}. Rate how well the RESPONSE answers the REQUEST: "
    "correctness, completeness, and whether it follows the instructions. Be strict: any "
    "factual or logical error scores 4 or lower. Reply with only JSON: "
    '{"score": <integer 1-10>, "reason": "<one sentence>"}'
)


@dataclass
class Verdict:
    passed: bool
    score: float | None = None
    reasons: list[str] = field(default_factory=list)
    judge_completion: Completion | None = None


def _strip_fences(text: str) -> str:
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())


class QualityChecker:
    def __init__(self, client: LLMClient, judge_model_id: str, judge_tier: int,
                 threshold: float = 7.0, judge_enabled: bool = True):
        self.client = client
        self.judge_model_id = judge_model_id
        self.judge_tier = judge_tier
        self.threshold = threshold
        self.judge_enabled = judge_enabled

    def structural(self, completion: Completion, response_format: str) -> list[str]:
        text = completion.text.strip()
        problems = []
        if not text:
            return ["empty answer"]
        if completion.stop_reason in ("max_tokens", "length"):
            problems.append("truncated at max_tokens")
        if len(text) < 400 and _REFUSAL.search(text):
            problems.append("refusal or hedging")
        if response_format == "json":
            try:
                json.loads(_strip_fences(text))
            except ValueError:
                problems.append("invalid JSON")
        return problems

    async def check(self, prompt: str, completion: Completion, response_format: str = "text",
                    run_judge: bool = True) -> Verdict:
        problems = self.structural(completion, response_format)
        if problems:
            return Verdict(False, reasons=problems)
        if not (self.judge_enabled and run_judge):
            return Verdict(True, reasons=["structural checks passed"])

        judge = await self.client.complete(
            self.judge_model_id,
            [{"role": "user", "content":
              f"REQUEST:\n<<<\n{prompt[:_MAX_JUDGE_CHARS]}\n>>>\n\n"
              f"RESPONSE:\n<<<\n{completion.text[:_MAX_JUDGE_CHARS]}\n>>>"}],
            system=JUDGE_SYSTEM, max_tokens=120, temperature=0.0)
        match = _SCORE.search(judge.text)
        if not match:  # unparseable judge output: escalate rather than risk a bad answer
            return Verdict(False, reasons=["judge output unparseable"], judge_completion=judge)
        score = float(match.group(1))
        passed = score >= self.threshold
        return Verdict(passed, score, [f"judge score {score:g}/10"], judge)
