"""LiteLLM provider without network: litellm.completion is monkeypatched."""

import json
from types import SimpleNamespace

import litellm
import pytest

from prawdowid.llm import ApiLimitError, LLMError, LLMRefusal
from prawdowid.llm.litellm_provider import LiteLLMProvider, strict_schema
from prawdowid.llm.tasks import ClaimsOut

PRICES = {"anthropic/claude-opus-5-5": [4.0, 20.0]}
GOOD = json.dumps({"claims": [{"text": "MSZ wysłało pismo.", "type": "factual",
                               "entities": ["MSZ"], "time_ref": None}]})


def resp(content, finish="stop"):
    return SimpleNamespace(
        choices=[SimpleNamespace(finish_reason=finish, message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=100),
    )


@pytest.fixture
def calls(monkeypatch):
    log, queue = [], []

    def fake_completion(**kwargs):
        log.append(kwargs)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(litellm, "completion", fake_completion)
    return log, queue


def provider(**cfg):
    return LiteLLMProvider({"model": "anthropic/claude-opus-5-5", **cfg}, PRICES)


def test_claude_uses_strict_json_schema_and_no_temperature(calls):
    log, queue = calls
    queue.append(resp(GOOD))
    res = provider().complete_json("sys", "user", ClaimsOut)
    assert res.parsed.claims[0].entities == ["MSZ"]
    sent = log[0]
    assert sent["response_format"]["type"] == "json_schema"
    assert sent["response_format"]["json_schema"]["strict"] is True
    assert "temperature" not in sent
    assert res.cost_usd == pytest.approx((1000 * 4 + 100 * 20) / 1e6)


def test_strict_schema_closes_objects_and_requires_all_fields():
    s = strict_schema(ClaimsOut)
    claim = s["$defs"]["ClaimOut"]
    assert claim["additionalProperties"] is False
    assert set(claim["required"]) == {"text", "type", "entities", "time_ref", "is_question"}


def test_self_hosted_model_uses_json_object_with_schema_in_prompt(calls):
    log, queue = calls
    queue.append(resp("```json\n" + GOOD + "\n```"))
    p = LiteLLMProvider({"model": "openai/bielik", "api_base": "http://localhost:8001/v1",
                         "temperature": 0}, {})
    res = p.complete_json("sys", "user", ClaimsOut)
    assert res.parsed.claims[0].type == "factual"
    assert log[0]["response_format"] == {"type": "json_object"}
    assert "Schemat JSON" in log[0]["messages"][0]["content"]
    assert log[0]["temperature"] == 0 and log[0]["api_base"].endswith("/v1")


def test_example_mode_shows_example_not_schema_and_retries_fresh(calls):
    log, queue = calls
    queue += [resp('{"$defs": {}, "properties": {}}'), resp(GOOD)]   # model echoed a schema first
    p = LiteLLMProvider({"model": "openai/bielik", "json_mode": "example"}, {})
    res = p.complete_json("sys", "user", ClaimsOut)
    system = log[0]["messages"][0]["content"]
    assert "$defs" not in system and '"claims": [{"text": "..."' in system and "factual|" in system
    assert len(log) == 2 and log[1]["messages"] == log[0]["messages"]    # same prompt, nothing appended
    assert res.parsed.claims[0].text == "MSZ wysłało pismo."


def test_invalid_json_is_repaired_once(calls):
    log, queue = calls
    queue += [resp('{"claims": [{"text": "x"}]}'), resp(GOOD)]
    res = provider().complete_json("sys", "user", ClaimsOut)
    assert len(log) == 2 and res.parsed.claims[0].text == "MSZ wysłało pismo."


def test_invalid_json_twice_is_llm_error(calls):
    _, queue = calls
    queue += [resp("nie json"), resp("dalej nie")]
    with pytest.raises(LLMError):
        provider().complete_json("sys", "user", ClaimsOut)


def test_rate_limit_maps_to_api_limit(calls):
    _, queue = calls
    queue.append(litellm.RateLimitError("429", llm_provider="anthropic", model="m"))
    with pytest.raises(ApiLimitError):
        provider().complete_json("sys", "user", ClaimsOut)


@pytest.mark.parametrize("finish", ["content_filter", "refusal"])
def test_refusal_finish_reason(calls, finish):
    _, queue = calls
    queue.append(resp("", finish=finish))
    with pytest.raises(LLMRefusal):
        provider().complete_json("sys", "user", ClaimsOut)


def test_truncated_output_is_error(calls):
    _, queue = calls
    queue.append(resp('{"claims": [', finish="length"))
    with pytest.raises(LLMError):
        provider().complete_json("sys", "user", ClaimsOut)


def test_ollama_models_use_schema_mode():
    """Ollama constrains decoding to the schema; LiteLLM's model map does not report it."""
    from prawdowid.llm.litellm_provider import LiteLLMProvider

    assert LiteLLMProvider({"model": "ollama/SpeakLeash/bielik-11b-v2.3-instruct:Q4_K_M"}).json_mode == "schema"
    assert LiteLLMProvider({"model": "ollama/bielik", "json_mode": "object"}).json_mode == "object"
