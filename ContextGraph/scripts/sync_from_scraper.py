"""Bring the graph in line with PageScrapper (the source of truth): calls POST /sync/reconcile.

The comparison lives in ContextGraph (context_graph/sync.py), so this script and the periodic job
cannot drift apart. The graph reaches the scraper through its own SCRAPER_URL setting.
Standard library only, so it runs on a host without the project's dependencies.

python scripts/sync_from_scraper.py --graph http://127.0.0.1:8100 [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--graph", default="http://127.0.0.1:8100")
    parser.add_argument("--dry-run", action="store_true", help="report differences only")
    parser.add_argument("--scraper", help=argparse.SUPPRESS)  # obsolete: the graph uses SCRAPER_URL
    args = parser.parse_args()

    url = f"{args.graph.rstrip('/')}/sync/reconcile?dry_run={'true' if args.dry_run else 'false'}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="POST"), timeout=1800) as res:
            result = json.load(res)
    except urllib.error.URLError as exc:
        print(f"ContextGraph unavailable at {args.graph}: {exc}", file=sys.stderr)
        return 2
    for key in (
        "scraper_documents",
        "graph_documents",
        "missing",
        "stale",
        "ingested",
        "failed",
        "orphans",
        "unembedded_chunks",
        "unembedded_claims",
        "consistent",
        "lag",
        "error",
    ):
        print(f"{key:20} {result.get(key)}")
    for label in ("failed_urls", "orphan_urls"):
        for u in result.get(label) or []:
            print(f"{label[:-5]:8} {u}")
    return 0 if result.get("lag") == 0 and not result.get("error") else 1


if __name__ == "__main__":
    sys.exit(main())
