"""Deployment knobs from the environment (docker-compose / .env) on top of conf/*."""

import pytest

from prawdowid.runtime import _env_overrides

_VARS = ("PRAWDOWID_EMBEDDINGS_PROVIDER", "PRAWDOWID_EMBEDDINGS_MODEL", "PRAWDOWID_EMBEDDINGS_API_BASE",
         "PRAWDOWID_RERANKER_ENABLED", "PRAWDOWID_RERANKER_PROVIDER", "PRAWDOWID_RERANKER_API_BASE",
         "PRAWDOWID_RERANKER_FALLBACK", "PRAWDOWID_LLM_PROVIDER", "PRAWDOWID_LLM_MODEL",
         "PRAWDOWID_LLM_API_BASE", "PRAWDOWID_RETRIEVAL_BACKEND")


def _params():
    return {"llm": {"roles": {"claims": {"provider": "mock", "model": "m"}}},
            "retrieval": {"backend": "mock"},
            "embeddings": {"provider": "mock", "model": "bge-m3", "api_base": "http://127.0.0.1:11435", "dim": 1024},
            "reranker": {"enabled": False, "model": "BAAI/bge-reranker-v2-m3"}}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for v in _VARS:
        monkeypatch.delenv(v, raising=False)


def test_no_env_keeps_conf():
    assert _env_overrides(_params()) == _params()


def test_embeddings_from_env(monkeypatch):
    monkeypatch.setenv("PRAWDOWID_EMBEDDINGS_PROVIDER", "ollama")
    monkeypatch.setenv("PRAWDOWID_EMBEDDINGS_API_BASE", "http://host.docker.internal:11435")
    emb = _env_overrides(_params())["embeddings"]
    assert emb == {"provider": "ollama", "model": "bge-m3",
                   "api_base": "http://host.docker.internal:11435", "dim": 1024}


@pytest.mark.parametrize("value,expected", [("true", True), ("1", True), ("false", False), ("no", False)])
def test_reranker_from_env(monkeypatch, value, expected):
    monkeypatch.setenv("PRAWDOWID_RERANKER_ENABLED", value)
    rr = _env_overrides(_params())["reranker"]
    assert rr == {"enabled": expected, "model": "BAAI/bge-reranker-v2-m3"}


def test_remote_reranker_from_env(monkeypatch):
    monkeypatch.setenv("PRAWDOWID_RERANKER_PROVIDER", "remote")
    monkeypatch.setenv("PRAWDOWID_RERANKER_API_BASE", "http://host.docker.internal:8081")
    monkeypatch.setenv("PRAWDOWID_RERANKER_FALLBACK", "local")
    rr = _env_overrides(_params())["reranker"]
    assert rr == {"enabled": False, "model": "BAAI/bge-reranker-v2-m3", "provider": "remote",
                  "api_base": "http://host.docker.internal:8081", "fallback": "local"}
