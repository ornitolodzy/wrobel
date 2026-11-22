"""Remote reranker client (deploy/rerank-server on the GPU server); httpx.post is faked."""

import httpx
import pytest

from prawdowid.llm import usage
from prawdowid.retrieval import reranker as rr


class _Resp:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=httpx.Request("POST", "http://x"),
                                        response=httpx.Response(self.status_code))

    def json(self):
        return self._data


class _FakeLocal:
    model = "local"

    def score(self, query, texts):
        return [0.5] * len(texts)


def test_remote_scores_and_trace(monkeypatch):
    sent = {}

    def post(url, json, timeout):
        sent.update(url=url, json=json)
        return _Resp({"scores": [0.9, 0.1], "device": "cuda", "ms": 12.0})

    monkeypatch.setattr(httpx, "post", post)
    usage.start()
    r = rr.RemoteReranker({"api_base": "http://gpu:8081/", "max_length": 384})
    assert r.score("q", ["a", "b"]) == [0.9, 0.1]
    assert sent["url"] == "http://gpu:8081/rerank" and sent["json"]["max_length"] == 384
    rec = [c for c in usage.drain() if c.get("role") == "reranker_remote"][0]
    assert rec["device"] == "cuda" and rec["scored"] == 2


def test_remote_failure_without_fallback_raises(monkeypatch):
    def post(url, json, timeout):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "post", post)
    with pytest.raises(rr.RerankerError):
        rr.RemoteReranker({"api_base": "http://gpu:8081"}).score("q", ["a"])


def test_remote_failure_falls_back_to_local(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda url, json, timeout: _Resp({}, status=503))
    usage.start()
    r = rr.RemoteReranker({"api_base": "http://gpu:8081"})
    r.fallback = _FakeLocal()
    assert r.score("q", ["a", "b"]) == [0.5, 0.5]
    rec = [c for c in usage.drain() if c.get("event") == "remote_failed"][0]
    assert rec["fallback"] == "local"


def test_wrong_number_of_scores_is_an_error(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda url, json, timeout: _Resp({"scores": [0.9]}))
    with pytest.raises(rr.RerankerError):
        rr.RemoteReranker({}).score("q", ["a", "b"])


def test_get_reranker_picks_provider():
    rr._cache.clear()
    assert isinstance(rr.get_reranker({"enabled": True, "provider": "remote"}), rr.RemoteReranker)
    assert isinstance(rr.get_reranker({"enabled": True}), rr.CrossEncoderReranker)
    assert rr.get_reranker({"enabled": False, "provider": "remote"}) is None
    with pytest.raises(ValueError):
        rr.get_reranker({"enabled": True, "provider": "nope"})
    rr._cache.clear()
