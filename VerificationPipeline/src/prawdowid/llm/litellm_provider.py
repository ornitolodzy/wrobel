"""Universal LLM provider via LiteLLM: one call format for Claude, Mistral, OpenAI, Gemini,
vLLM/Ollama (Bielik, PLLuM). Swapping a model = changing the `model` string in config.
"""

from __future__ import annotations

import json
import os
import time

from pydantic import BaseModel, ValidationError

from .base import ApiLimitError, LLMError, LLMRefusal, LLMResult, T

# Use the price map bundled with the package instead of fetching it from GitHub at import.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import litellm  # noqa: E402

litellm.telemetry = False
litellm.suppress_debug_info = True

_REFUSAL_FINISH = {"content_filter", "refusal"}


class LiteLLMProvider:
    """Role config keys: model, api_base, api_key, max_tokens, timeout_s, temperature,
    json_mode (auto | schema | object | example). Omit temperature for current Claude models.
    """

    def __init__(self, role_cfg: dict, prices_per_mtok: dict | None = None):
        self.cfg = role_cfg
        self.model = role_cfg["model"]
        self.prices = (prices_per_mtok or {}).get(self.model)
        mode = role_cfg.get("json_mode", "auto")
        if mode == "auto":
            try:
                mode = "schema" if litellm.supports_response_schema(model=self.model) else "object"
            except Exception:
                mode = "object"
        self.json_mode = mode

    # ---- public ----

    def complete_json(self, system: str, user: str, schema: type[T]) -> LLMResult[T]:
        messages = [{"role": "system", "content": self._system(system, schema)},
                    {"role": "user", "content": user}]
        t0 = time.perf_counter()
        resp = self._complete(messages, schema)
        content = self._content(resp)
        try:
            parsed = schema.model_validate_json(content)
        except ValidationError as err:
            # One repair attempt with the validation error (models without strict schema).
            # example mode (small context windows): plain retry, appending the bad answer would overflow.
            if self.json_mode != "example":
                messages += [{"role": "assistant", "content": content},
                             {"role": "user", "content": f"Odpowiedź nie pasuje do schematu: {err}. "
                                                         "Zwróć poprawiony JSON i nic więcej."}]
            resp = self._complete(messages, schema)
            content = self._content(resp)
            try:
                parsed = schema.model_validate_json(content)
            except ValidationError as err2:
                raise LLMError(f"invalid JSON from {self.model}: {err2}") from err2
        return self._result(parsed, resp, time.perf_counter() - t0)

    # ---- internals ----

    def _system(self, system: str, schema: type[BaseModel]) -> str:
        if self.json_mode == "schema":
            return system
        if self.json_mode == "example":
            # Small local models (Bielik Q4) sometimes echo a JSON Schema back; an example they fill in.
            return (f"{system}\n\nOdpowiedz wyłącznie jednym obiektem JSON o tej strukturze "
                    f"(wartości są przykładowe, a|b oznacza jedną z wartości):\n"
                    f"{json.dumps(example_json(schema), ensure_ascii=False)}")
        # No native schema support: put the schema in the prompt, validate afterwards.
        return f"{system}\n\nSchemat JSON odpowiedzi:\n{json.dumps(strict_schema(schema), ensure_ascii=False)}"

    def _response_format(self, schema: type[BaseModel]) -> dict:
        if self.json_mode == "schema":
            return {"type": "json_schema",
                    "json_schema": {"name": schema.__name__, "schema": strict_schema(schema),
                                    "strict": True}}
        return {"type": "json_object"}

    def _complete(self, messages: list[dict], schema: type[BaseModel]):
        kwargs = {
            "model": self.model,
            "messages": messages,
            "response_format": self._response_format(schema),
            "max_tokens": self.cfg.get("max_tokens", 2000),
            "timeout": self.cfg.get("timeout_s", 30),
            "drop_params": True,  # silently drop params a provider rejects
            "num_retries": self.cfg.get("num_retries", 1),
        }
        for key in ("api_base", "api_key", "temperature"):
            if self.cfg.get(key) is not None:
                kwargs[key] = self.cfg[key]
        try:
            return litellm.completion(**kwargs)
        except litellm.RateLimitError as e:
            raise ApiLimitError(f"{self.model}: rate limit") from e
        except litellm.ContentPolicyViolationError as e:
            raise LLMRefusal(f"{self.model}: content policy") from e
        except Exception as e:  # timeout, connection, 4xx/5xx, auth
            raise LLMError(f"{self.model}: {type(e).__name__}: {e}") from e

    def _content(self, resp) -> str:
        choice = resp.choices[0]
        if choice.finish_reason in _REFUSAL_FINISH:
            raise LLMRefusal(f"{self.model}: finish_reason={choice.finish_reason}")
        if choice.finish_reason == "length":
            raise LLMError(f"{self.model}: output truncated (max_tokens)")
        content = choice.message.content or ""
        if not content.strip():
            raise LLMError(f"{self.model}: empty response")
        return _strip_fences(content)

    def _result(self, parsed, resp, seconds: float) -> LLMResult:
        u = getattr(resp, "usage", None)
        tin = getattr(u, "prompt_tokens", 0) or 0
        tout = getattr(u, "completion_tokens", 0) or 0
        cost = None
        if self.prices:  # explicit config price wins (self-host, new models)
            cost = (tin * self.prices[0] + tout * self.prices[1]) / 1_000_000
        else:
            try:
                cost = litellm.completion_cost(completion_response=resp)
            except Exception:
                cost = None
        return LLMResult(parsed=parsed, model=self.model, input_tokens=tin, output_tokens=tout,
                         cost_usd=cost, latency_ms=seconds * 1000)


def strict_schema(schema: type[BaseModel]) -> dict:
    """Pydantic JSON schema made strict-mode compatible: every object closed
    (additionalProperties: false) and every property required (optional ones stay nullable)."""
    root = schema.model_json_schema()

    def fix(node):
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"])
                for prop in node["properties"].values():
                    prop.pop("default", None)
            for value in node.values():
                fix(value)
        elif isinstance(node, list):
            for item in node:
                fix(item)

    fix(root)
    return root


def example_json(schema: type[BaseModel]):
    """Example instance of a Pydantic schema (json_mode: example): one item per list, enums as a|b."""
    root = schema.model_json_schema()
    defs = root.get("$defs", {})

    def ex(node: dict):
        if "$ref" in node:
            return ex(defs[node["$ref"].rsplit("/", 1)[-1]])
        if "enum" in node:
            return "|".join(str(v) for v in node["enum"])
        if "const" in node:
            return node["const"]
        for key in ("anyOf", "oneOf", "allOf"):
            if key in node:
                options = [o for o in node[key] if o.get("type") != "null"] or node[key]
                return ex(options[0])
        kind = node.get("type")
        if kind == "object":
            return {k: ex(v) for k, v in node.get("properties", {}).items()}
        if kind == "array":
            return [ex(node.get("items", {}))]
        return {"integer": 1, "number": 0.5, "boolean": False, "null": None}.get(kind, "...")

    return ex(root)


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return text.strip()
