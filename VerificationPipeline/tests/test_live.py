"""One real model call. Skipped unless PRAWDOWID_LIVE_MODEL is set (costs tokens).

Example: PRAWDOWID_LIVE_MODEL=anthropic/claude-opus-5-5 ANTHROPIC_API_KEY=... pytest -m live
"""

import os

import pytest

from prawdowid.llm.litellm_provider import LiteLLMProvider
from prawdowid.llm.tasks import ClaimsOut, load_prompt

MODEL = os.environ.get("PRAWDOWID_LIVE_MODEL")


@pytest.mark.live
@pytest.mark.skipif(not MODEL, reason="set PRAWDOWID_LIVE_MODEL to run")
def test_live_claim_extraction():
    system, _ = load_prompt("claims")
    res = LiteLLMProvider({"model": MODEL, "max_tokens": 1000}).complete_json(
        system, "<user_content>MSZ wysłało 16 sierpnia 2026 pismo do Czech.</user_content>", ClaimsOut)
    assert res.parsed.claims and res.parsed.claims[0].type == "factual"
