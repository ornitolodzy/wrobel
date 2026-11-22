"""Load the fictional demo corpus through the HTTP API and print the context for the demo claim.

python scripts/seed_example.py [--api http://localhost:8100]
"""

from __future__ import annotations

import argparse
import json

import httpx

from context_graph.example_data import CLAIM, CONTRADICTION, IMAGES, PAGES


def chunk_with(client: httpx.Client, page_url: str, quote: str) -> str:
    """Find the chunk id of `page_url` that contains `quote` (via the context endpoint)."""
    res = client.post("/context", json={"text": quote, "limit": 50, "save": False}).json()
    for item in res["items"]:
        if item["url"] and item["url"].rstrip("/") == page_url.rstrip("/") and quote in item["text"]:
            return item["chunk_id"]
    raise SystemExit(f"no chunk on {page_url} contains {quote!r}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8100")
    args = ap.parse_args()

    with httpx.Client(base_url=args.api, timeout=30) as client:
        for page in PAGES:
            r = client.post("/ingest/page", json=page)
            r.raise_for_status()
            print("page ", page["url"], r.json())
        for image in IMAGES:
            r = client.post("/ingest/image", json=image)
            r.raise_for_status()
            print("image", image["image_id"], r.json())

        src = chunk_with(client, CONTRADICTION["src_page"], CONTRADICTION["quote"])
        dst = chunk_with(client, CONTRADICTION["dst_page"], "naruszył polską przestrzeń powietrzną")
        r = client.post(
            "/relations",
            json={
                "type": CONTRADICTION["type"],
                "src": src,
                "dst": dst,
                "quote": CONTRADICTION["quote"],
                "method": CONTRADICTION["method"],
            },
        )
        r.raise_for_status()
        print("rel  ", r.json())

        ctx = client.post("/context", json={"text": CLAIM, "hops": 2, "limit": 8}).json()
        print(json.dumps(ctx, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
