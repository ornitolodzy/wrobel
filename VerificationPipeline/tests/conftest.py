import os

# Tests run on conf/base only: never on conf/local (real models, tunnels, keys).
os.environ["PRAWDOWID_ENV"] = "test"
# ...and never on deployment knobs from a developer's .env: LiteLLM calls load_dotenv() on import,
# which would switch every role to a real model. Set (empty) values win over load_dotenv.
for _var in ("LLM_PROVIDER", "LLM_MODEL", "LLM_API_BASE", "RETRIEVAL_BACKEND", "EMBEDDINGS_PROVIDER",
             "EMBEDDINGS_MODEL", "EMBEDDINGS_API_BASE", "RERANKER_ENABLED", "RERANKER_PROVIDER",
             "RERANKER_API_BASE", "RERANKER_FALLBACK"):
    os.environ[f"PRAWDOWID_{_var}"] = ""
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
