"""Send a steady mix of traffic to a running GreenRoute so the dashboard fills up.

    python scripts/loadgen.py --url http://localhost:8000 --rps 2 --minutes 10
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
from pathlib import Path

import httpx


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--rps", type=float, default=2.0)
    ap.add_argument("--minutes", type=float, default=5.0)
    ap.add_argument("--batch-every", type=int, default=50, help="submit a batch job every N requests")
    args = ap.parse_args()

    prompts = [json.loads(line)["prompt"] for line in
               Path("benchmark/dataset.jsonl").read_text().splitlines() if line.strip()]
    total = int(args.rps * args.minutes * 60)
    async with httpx.AsyncClient(base_url=args.url, timeout=120) as http:
        async def one(i: int) -> None:
            try:
                if i and i % args.batch_every == 0:
                    body = {"requests": [{"prompt": p} for p in random.sample(prompts, 5)],
                            "deadline_hours": random.choice([2, 6, 12, 24])}
                    r = await http.post("/v1/batch", json=body)
                else:
                    r = await http.post("/v1/chat", json={"prompt": random.choice(prompts)})
                r.raise_for_status()
            except httpx.HTTPError as e:
                print(f"request {i} failed: {e}")

        tasks = []
        for i in range(total):
            tasks.append(asyncio.create_task(one(i)))
            await asyncio.sleep(random.expovariate(args.rps))
        await asyncio.gather(*tasks)
    print(f"sent {total} requests")


if __name__ == "__main__":
    asyncio.run(main())
