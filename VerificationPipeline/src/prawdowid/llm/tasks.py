"""The three narrow LLM tasks. Prompts live in prompts/*.md (versioned), outputs are JSON schemas.

Content from users and sources is wrapped in tagged blocks and treated as data.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from prawdowid.models import Claim, ClaimType, Evidence, StanceLabel, Stance, Verdict

from . import usage
from .base import LLMResult
from .registry import get_provider

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"


# ---------- output schemas (what the model must return) ----------

class ClaimOut(BaseModel):
    text: str
    type: ClaimType
    entities: list[str] = Field(default_factory=list)
    time_ref: str | None = None


class ClaimsOut(BaseModel):
    claims: list[ClaimOut]


class StanceItem(BaseModel):
    source_id: int
    label: StanceLabel
    quote: str
    rationale: str


class StanceOut(BaseModel):
    items: list[StanceItem]
    injection_suspected: bool = False


class ExplainSentence(BaseModel):
    text: str
    sources: list[int]  # numbers of sources in <sources> that back this sentence


class ExplainOut(BaseModel):
    sentences: list[ExplainSentence]


# ---------- helpers ----------

@lru_cache(maxsize=None)
def load_prompt(name: str) -> tuple[str, str]:
    text = (PROMPTS / f"{name}.md").read_text(encoding="utf-8")
    m = re.search(r"<!--\s*version:\s*([\w.\-]+)\s*-->", text)
    return re.sub(r"<!--.*?-->", "", text, flags=re.S).strip(), (m.group(1) if m else "0")


def _call(role: str, llm_params: dict, user: str, schema: type[BaseModel]) -> LLMResult:
    cfg = llm_params["roles"][role]
    system, version = load_prompt(role)
    provider = get_provider(cfg, llm_params.get("prices_per_mtok"))
    try:
        res = provider.complete_json(system, user, schema)
    except Exception as e:
        usage.record(role=role, model=cfg.get("model"), prompt_version=version,
                     error=f"{type(e).__name__}: {e}")
        raise
    usage.record(role=role, model=res.model, prompt_version=version,
                 input_tokens=res.input_tokens, output_tokens=res.output_tokens,
                 cost_usd=res.cost_usd, latency_ms=round(res.latency_ms, 1))
    return res


def _escape(text: str) -> str:
    return text.replace("<", "&lt;").replace(">", "&gt;")


# ---------- tasks ----------

def extract_claims_llm(text: str, llm_params: dict) -> list[Claim]:
    user = f"<user_content>\n{_escape(text)}\n</user_content>"
    out: ClaimsOut = _call("claims", llm_params, user, ClaimsOut).parsed
    max_claims = llm_params.get("max_claims", 3)
    return [
        Claim(id=f"c{i}", text=c.text, type=c.type, entities=c.entities, time_ref=c.time_ref)
        for i, c in enumerate(out.claims[:max_claims], start=1)
        if c.text.strip()
    ]


def judge_stance_llm(claim: Claim, evidence: list[Evidence], llm_params: dict) -> list[Stance]:
    provider = get_provider(llm_params["roles"]["stance"], llm_params.get("prices_per_mtok"))
    if hasattr(provider, "judge_stance"):  # non-generative stance models (NLI)
        return provider.judge_stance(claim, evidence)

    sources = "\n".join(
        f'<source id="{i}" publisher="{_escape(e.publisher)}" type="{e.source_type}" '
        f'date="{e.published_at:%Y-%m-%d}">\n{_escape(e.text)}\n</source>'
        if e.published_at else
        f'<source id="{i}" publisher="{_escape(e.publisher)}" type="{e.source_type}">\n'
        f"{_escape(e.text)}\n</source>"
        for i, e in enumerate(evidence, start=1)
    )
    user = f"<claim>\n{_escape(claim.text)}\n</claim>\n<sources>\n{sources}\n</sources>"
    out: StanceOut = _call("stance", llm_params, user, StanceOut).parsed
    if out.injection_suspected:
        usage.record(role="stance", event="injection_suspected", claim_id=claim.id)
    return [
        Stance(claim_id=claim.id, chunk_id=evidence[it.source_id - 1].chunk_id,
               label=it.label, quote=it.quote, rationale=it.rationale)
        for it in out.items
        if 1 <= it.source_id <= len(evidence)
    ]


def explain_llm(claim: Claim, verdict: Verdict, llm_params: dict) -> list[ExplainSentence]:
    sources = "\n".join(
        f'<source n="{r.n}" role="{r.role}" publisher="{_escape(r.publisher)}"'
        f'{f" date={chr(34)}{r.published_at:%Y-%m-%d}{chr(34)}" if r.published_at else ""}>'
        f"{_escape(r.quote)}</source>"
        for r in verdict.sources
    )
    user = (
        f"<claim>{_escape(claim.text)}</claim>\n<verdict>{verdict.label}</verdict>\n"
        f"<premises>{_escape(' '.join(verdict.reasons))}</premises>\n<sources>\n{sources}\n</sources>"
    )
    return _call("compose", llm_params, user, ExplainOut).parsed.sentences
