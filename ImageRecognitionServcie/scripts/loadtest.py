"""Load test for the OCR service: throughput, latency percentiles and error codes.

    uv run python scripts/loadtest.py IMAGE [IMAGE ...] -c 16 -n 200 --url http://localhost:8000

Sends ``-n`` requests with ``-c`` of them in flight, cycling through the given images.
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import statistics
import time
from pathlib import Path

import httpx


async def run(
    url: str, images: list[bytes], total: int, concurrency: int, api_key: str | None
) -> None:
    headers = {"X-API-Key": api_key} if api_key else {}
    latencies: list[float] = []
    server_ms: list[int] = []
    statuses: collections.Counter[str] = collections.Counter()
    counter = iter(range(total))
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)

    async with httpx.AsyncClient(
        base_url=url, headers=headers, timeout=600, limits=limits
    ) as client:

        async def worker() -> None:
            for i in counter:
                data = images[i % len(images)]
                started = time.perf_counter()
                try:
                    response = await client.post(
                        "/v1/ocr", params={"output": "text"}, files={"file": ("img", data)}
                    )
                except httpx.HTTPError as exc:
                    statuses[type(exc).__name__] += 1
                    continue
                latencies.append(time.perf_counter() - started)
                if response.status_code == 200:
                    statuses["200"] += 1
                    server_ms.append(response.json()["duration_ms"])
                elif response.status_code == 202:  # callback mode: accepted, result sent elsewhere
                    statuses["202"] += 1
                else:
                    code = response.json().get("error", {}).get("code", "?")
                    statuses[f"{response.status_code} {code}"] += 1

        started = time.perf_counter()
        await asyncio.gather(*(worker() for _ in range(concurrency)))
        elapsed = time.perf_counter() - started

    ok = statuses["200"] + statuses["202"]
    print(f"requests: {total}, concurrency: {concurrency}, wall: {elapsed:.1f}s")
    print(f"throughput: {ok / elapsed:.2f} img/s (successful)")
    if latencies:
        q = statistics.quantiles(latencies, n=100) if len(latencies) > 1 else latencies * 99
        print(
            f"latency  p50 {q[49]:.2f}s  p95 {q[94]:.2f}s  p99 {q[98]:.2f}s  "
            f"max {max(latencies):.2f}s"
        )
    if server_ms:
        print(f"server duration_ms  median {statistics.median(server_ms):.0f}")
    print("status:", dict(statuses))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("-c", "--concurrency", type=int, default=8)
    parser.add_argument("-n", "--requests", type=int, default=50)
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--api-key", default=None)
    args = parser.parse_args()
    images = [p.read_bytes() for p in args.images]
    asyncio.run(run(args.url, images, args.requests, args.concurrency, args.api_key))


if __name__ == "__main__":
    main()
