import os

# Tests run on conf/base only: never on conf/local (real models, tunnels, keys).
os.environ["PRAWDOWID_ENV"] = "test"
# ...and never download models (Hugging Face) during tests.
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

import pytest  # noqa: E402

from prawdowid.websearch import fetch as web_fetch


class _NoNetworkFetcher:
    def fetch(self, url):
        raise RuntimeError(f"network fetch in tests: {url}")


@pytest.fixture(autouse=True)
def _no_network_fetch():
    """Tests never download pages; tests that need pages install their own fetcher."""
    web_fetch.override(_NoNetworkFetcher())
    yield
    web_fetch.override(None)
