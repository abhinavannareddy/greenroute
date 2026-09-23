"""Cheap, zero-inference difficulty estimate used to pick the starting tier.

Deliberately conservative: most prompts start at the smallest tier and rely on
the quality gate to escalate. Only clearly heavy prompts skip ahead, which saves
the wasted small-model call on requests that would almost certainly escalate.
"""
from __future__ import annotations

import re

_REASONING_HINTS = (
    "prove", "derive", "step by step", "analy", "compare", "trade-off", "tradeoff",
    "design", "architect", "optimi", "debug", "refactor", "why does", "in detail",
    "evaluate", "critique", "plan ", "strategy", "algorithm", "complexity",
)
_CODE = re.compile(r"```|\bdef \w+\(|\bclass \w+|function\s*\(|=>|#include")
_MATH = re.compile(r"\d+\s*[\+\-\*/\^]\s*\d+|\bintegral\b|\bderivative\b|\bprobability\b")


def estimate_difficulty(prompt: str) -> float:
    """Return a score in [0, 1]."""
    lower = prompt.lower()
    score = min(len(prompt.split()) / 400, 0.35)
    score += min(sum(h in lower for h in _REASONING_HINTS) * 0.15, 0.45)
    if _CODE.search(prompt):
        score += 0.15
    if _MATH.search(lower):
        score += 0.05
    return min(score, 1.0)


def starting_tier(prompt: str, n_tiers: int) -> int:
    return min(int(estimate_difficulty(prompt) * n_tiers * 0.8), n_tiers - 1)
