"""Benchmark GreenRoute against an always-largest-model baseline.

Runs every dataset prompt twice (routed vs baseline), grades each answer against
a reference with a grader model, and writes a CSV plus a Markdown summary.

    python -m benchmark.run_benchmark                       # uses env config
    GREENROUTE_MOCK_BEDROCK=true python -m benchmark.run_benchmark   # offline

Both modes use one fixed grid intensity so the comparison isolates routing.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path

from app.config import Settings
from app.factory import build
from app.llm import GRADER_MARKER
from app.quality import _SCORE
from app.schemas import ChatRequest

GRADER_SYSTEM = (
    f"You are GreenRoute's {GRADER_MARKER} (benchmark mode). Compare the RESPONSE with the "
    "REFERENCE answer for the QUESTION. Score 1-10 for correctness and completeness; the "
    "wording may differ from the reference. Reply with only JSON: "
    '{"score": <integer 1-10>, "reason": "<one sentence>"}'
)


async def grade(client, model_id: str, item: dict, answer: str) -> float:
    comp = await client.complete(
        model_id,
        [{"role": "user", "content": f"QUESTION:\n{item['prompt']}\n\nREFERENCE:\n"
          f"{item['reference']}\n\nRESPONSE:\n{answer}"}],
        system=GRADER_SYSTEM, max_tokens=120, temperature=0.0)
    m = _SCORE.search(comp.text)
    return float(m.group(1)) if m else 0.0


def summarize(rows: list[dict], mode: str, pass_score: float) -> dict:
    rs = [r for r in rows if r["mode"] == mode]
    return {
        "n": len(rs),
        "accuracy": sum(r["grade"] >= pass_score for r in rs) / len(rs),
        "mean_grade": statistics.mean(r["grade"] for r in rs),
        "energy_wh": sum(r["energy_wh"] for r in rs),
        "co2_g": sum(r["co2_g"] for r in rs),
        "cost_usd": sum(r["cost_usd"] for r in rs),
        "p50_latency_ms": statistics.median(r["latency_ms"] for r in rs),
        "escalation_rate": sum(r["escalated"] for r in rs) / len(rs),
        "model_mix": dict(Counter(r["model"] for r in rs)),
    }


def render_md(g: dict, b: dict, intensity: float, grader: str) -> str:
    def delta(key):
        return f"{100 * (g[key] / b[key] - 1):+.1f}%" if b[key] else "n/a"
    lines = [
        "# GreenRoute benchmark", "",
        f"Prompts: {g['n']} | grid intensity: {intensity:.1f} gCO2e/kWh | grader: {grader}", "",
        "| Metric | GreenRoute | Always-largest | Change |", "|---|---|---|---|",
        f"| Accuracy (grade ≥ pass) | {g['accuracy']:.1%} | {b['accuracy']:.1%} | "
        f"{100 * (g['accuracy'] - b['accuracy']):+.1f} pp |",
        f"| Mean grade (1-10) | {g['mean_grade']:.2f} | {b['mean_grade']:.2f} | "
        f"{g['mean_grade'] - b['mean_grade']:+.2f} |",
        f"| Energy (Wh, total) | {g['energy_wh']:.4f} | {b['energy_wh']:.4f} | {delta('energy_wh')} |",
        f"| CO2e (g, total) | {g['co2_g']:.6f} | {b['co2_g']:.6f} | {delta('co2_g')} |",
        f"| Cost (USD, total) | {g['cost_usd']:.6f} | {b['cost_usd']:.6f} | {delta('cost_usd')} |",
        f"| p50 latency (ms) | {g['p50_latency_ms']:.0f} | {b['p50_latency_ms']:.0f} | "
        f"{delta('p50_latency_ms')} |",
        f"| Escalation rate | {g['escalation_rate']:.1%} | - | |", "",
        f"GreenRoute final-model mix: {json.dumps(g['model_mix'])}", "",
        "Energy is estimated from per-token coefficients (see config/tiers.yaml); "
        "GreenRoute totals include failed attempts and judge calls.",
    ]
    return "\n".join(lines)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="benchmark/dataset.jsonl")
    ap.add_argument("--out", default="benchmark/results")
    ap.add_argument("--grader-model", help="Model ID for grading (default: largest tier). "
                    "Use a different model family to reduce self-preference bias.")
    ap.add_argument("--pass-score", type=float, default=7.0)
    ap.add_argument("--concurrency", type=int, default=4)
    args = ap.parse_args()

    c = build(Settings(scheduler_enabled=False))
    items = [json.loads(line) for line in Path(args.dataset).read_text().splitlines() if line.strip()]
    grader = args.grader_model or c.tiers[-1].model_id
    intensity = (await c.carbon.current()).g_per_kwh
    sem = asyncio.Semaphore(args.concurrency)

    async def run(item: dict, mode: str) -> dict:
        async with sem:
            res = await c.router.route(ChatRequest(prompt=item["prompt"], baseline=mode == "baseline"),
                                       intensity=intensity, workload="benchmark")
            score = await grade(c.client, grader, item, res.answer)
        return {"id": item["id"], "category": item["category"], "mode": mode,
                "model": res.model, "escalated": res.escalated, "attempts": len(res.attempts),
                "grade": score, "energy_wh": res.footprint.energy_wh, "co2_g": res.footprint.co2_g,
                "cost_usd": res.footprint.cost_usd, "latency_ms": res.latency_ms}

    rows = await asyncio.gather(*(run(i, m) for m in ("greenroute", "baseline") for i in items))
    g, b = summarize(rows, "greenroute", args.pass_score), summarize(rows, "baseline", args.pass_score)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    with open(out / f"benchmark-{stamp}.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    md = render_md(g, b, intensity, grader)
    (out / f"benchmark-{stamp}.md").write_text(md)
    print(md)
    await c.carbon.aclose()


if __name__ == "__main__":
    asyncio.run(main())
