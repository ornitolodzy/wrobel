import pytest

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
