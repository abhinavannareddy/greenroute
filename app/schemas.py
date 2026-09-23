"""API request/response models."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    prompt: str | None = None
    messages: list[Message] | None = None
    system: str | None = None
    max_tokens: int | None = Field(None, ge=1, le=8192)
    min_tier: int | None = Field(None, ge=0)
    max_tier: int | None = Field(None, ge=0)
    response_format: Literal["text", "json"] = "text"
    baseline: bool = Field(False, description="Force the largest model (A/B baseline).")

    @model_validator(mode="after")
    def _has_input(self) -> "ChatRequest":
        if not self.prompt and not self.messages:
            raise ValueError("provide 'prompt' or 'messages'")
        return self

    def to_messages(self) -> list[dict]:
        if self.messages:
            return [m.model_dump() for m in self.messages]
        return [{"role": "user", "content": self.prompt}]


class FootprintOut(BaseModel):
    energy_wh: float
    co2_g: float
    cost_usd: float


class AttemptOut(BaseModel):
    model: str
    tier: int
    passed: bool
    score: float | None
    reasons: list[str]
    input_tokens: int
    output_tokens: int
    latency_ms: float


class ChatResponse(BaseModel):
    answer: str
    model: str
    tier: int
    escalated: bool
    attempts: list[AttemptOut]
    grid_intensity_g_per_kwh: float
    footprint: FootprintOut
    baseline_footprint: FootprintOut
    savings_pct: dict[str, float]
    latency_ms: float


class BatchSubmit(BaseModel):
    requests: list[ChatRequest] = Field(min_length=1, max_length=1000)
    deadline_hours: float | None = Field(
        None, gt=0, le=168, description="Latest acceptable start, in hours from now.")


class JobState(str, Enum):
    scheduled = "scheduled"
    running = "running"
    done = "done"
    failed = "failed"


class BatchJob(BaseModel):
    id: str
    state: JobState
    created_at: datetime
    deadline: datetime
    scheduled_for: datetime
    expected_intensity: float
    intensity_at_submit: float
    intensity_at_run: float | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    requests: list[ChatRequest]
    results: list[ChatResponse | None] = []
    errors: list[str] = []
    co2_avoided_g: float | None = None
