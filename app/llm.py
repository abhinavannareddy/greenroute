"""LLM clients: AWS Bedrock (Converse API) and an offline mock."""
from __future__ import annotations

import asyncio
import json
import random
import re
import time
import zlib
from dataclasses import dataclass
from typing import Protocol

from .complexity import estimate_difficulty


@dataclass
class Completion:
    text: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    stop_reason: str


class LLMClient(Protocol):
    async def complete(self, model_id: str, messages: list[dict], *, system: str | None = None,
                       max_tokens: int = 1024, temperature: float = 0.2) -> Completion: ...


class BedrockClient:
    """Thin async wrapper over bedrock-runtime Converse (works across model families)."""

    def __init__(self, region: str):
        import boto3
        from botocore.config import Config

        self._client = boto3.client(
            "bedrock-runtime", region_name=region,
            config=Config(retries={"max_attempts": 4, "mode": "adaptive"},
                          read_timeout=120, max_pool_connections=50))

    async def complete(self, model_id, messages, *, system=None, max_tokens=1024, temperature=0.2):
        return await asyncio.to_thread(self._complete, model_id, messages, system,
                                       max_tokens, temperature)

    def _complete(self, model_id, messages, system, max_tokens, temperature) -> Completion:
        kwargs = {
            "modelId": model_id,
            "messages": [{"role": m["role"], "content": [{"text": m["content"]}]}
                         for m in messages],
            "inferenceConfig": {"maxTokens": max_tokens, "temperature": temperature},
        }
        if system:
            kwargs["system"] = [{"text": system}]
        t0 = time.perf_counter()
        resp = self._client.converse(**kwargs)
        text = "".join(b.get("text", "") for b in resp["output"]["message"]["content"])
        usage = resp.get("usage", {})
        latency = resp.get("metrics", {}).get("latencyMs", (time.perf_counter() - t0) * 1000)
        return Completion(text, usage.get("inputTokens", 0), usage.get("outputTokens", 0),
                          float(latency), resp.get("stopReason", "end_turn"))


GRADER_MARKER = "answer grader"   # system prompts of the judge/grader contain this phrase


def _mock_difficulty(prompt: str) -> float:
    noise = (zlib.crc32(prompt.encode()) % 100) / 100 * 0.4 - 0.1
    return max(0.0, min(1.0, estimate_difficulty(prompt) + noise))


class MockClient:
    """Offline stand-in for Bedrock so the whole stack runs without AWS.

    Larger tiers "succeed" on harder prompts. Answers carry a hidden quality tag
    that the mock grader reads, so the cascade and benchmark behave realistically.
    """

    def __init__(self, model_ids: list[str], latency_scale: float = 0.02):
        self.model_ids = model_ids
        self.latency_scale = latency_scale

    async def complete(self, model_id, messages, *, system=None, max_tokens=1024, temperature=0.2):
        text_in = (system or "") + " ".join(m["content"] for m in messages)
        in_tok = max(1, len(text_in) // 4)
        last = messages[-1]["content"]

        if system and GRADER_MARKER in system:
            tags = re.findall(r"\[mock quality=(\d+)\]", last)
            score = int(tags[-1]) if tags else 5
            await asyncio.sleep(self.latency_scale)
            return Completion(json.dumps({"score": score, "reason": "mock grader"}),
                              in_tok, 20, self.latency_scale * 1000, "end_turn")

        tier = self.model_ids.index(model_id) if model_id in self.model_ids else len(self.model_ids) - 1
        capacity = (tier + 1) / len(self.model_ids)
        difficulty = _mock_difficulty(last)
        rng = random.Random(zlib.crc32(f"{model_id}|{last}".encode()))
        quality = rng.randint(7, 9) if capacity >= difficulty else rng.randint(3, 6)
        out_tok = min(max_tokens, int(80 + difficulty * 400 * (0.8 + 0.4 * rng.random())))
        delay = self.latency_scale * (tier + 1) * out_tok / 100
        await asyncio.sleep(delay)
        text = f"Mock answer from {model_id} ({out_tok} tokens). [mock quality={quality}]"
        return Completion(text, in_tok, out_tok, delay * 1000, "end_turn")
