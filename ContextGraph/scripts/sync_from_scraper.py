"""Copy every document PageScrapper has into the graph (one-off backfill or after a reset).

New documents reach the graph on their own: PageScrapper POSTs them to RAG_INGEST_URL
(= http://<contextgraph>:8100/ingest/document). This script covers what was scraped before that.

python scripts/sync_from_scraper.py --scraper http://127.0.0.1:8002 --graph http://127.0.0.1:8100
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter

import httpx

FIELDS = (
    "url",
    "source_id",
    "source_type",
    "title",
    "publisher",
    "published_at",
    "retrieved_at",
    "language",
    "text",
    "content_hash",
)


def scraper_documents(client: httpx.Client, scraper: str, page_size: int):
    offset = 0
    while True:
        page = client.get(f"{scraper}/documents", params={"limit": page_size, "offset": offset}).raise_for_status()
        items = page.json()["items"]
        if not items:
            return
        for item in items:
            yield client.get(f"{scraper}/documents/{item['id']}").raise_for_status().json()
        offset += len(items)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scraper", default="http://127.0.0.1:8002")
    parser.add_argument("--graph", default="http://127.0.0.1:8100")
    parser.add_argument("--page-size", type=int, default=200)
    args = parser.parse_args()

    stats: Counter[str] = Counter()
    with httpx.Client(timeout=120) as client:
        for doc in scraper_documents(client, args.scraper.rstrip("/"), args.page_size):
            payload = {k: doc.get(k) for k in FIELDS}
            # ClaimReview stored by PageScrapper (extra.claim_reviews) -> CheckedClaim in the graph.
            payload["claims"] = (doc.get("extra") or {}).get("claim_reviews") or []
            res = client.post(f"{args.graph.rstrip('/')}/ingest/document", json=payload)
            if res.status_code >= 400:
                stats["failed"] += 1
                print(f"FAILED {res.status_code} {doc['url']}: {res.text[:200]}", file=sys.stderr)
                continue
            body = res.json()
            stats["documents"] += 1
            stats["chunks"] += body["chunks"]
            stats["embedded"] += body["embedded"]
            stats["claims"] += body["relations"].get("CHECKS", 0)
            stats[f"source:{doc['source_id']}"] += 1
    for key, value in sorted(stats.items()):
        print(f"{key:32} {value}")
    return 1 if stats["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
