"""Non-generative stance model (NLI). Cheap, deterministic; fallback or second opinion.

Optional extra: pip install -e ".[nli]". Model is loaded lazily on first use.
"""

from __future__ import annotations

import re

from prawdowid.models import Claim, Evidence, Stance

DEFAULT_MODEL = "MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7"
_SENT = re.compile(r"(?<=[.!?])\s+")
_MAP = {"entailment": "supports", "contradiction": "refutes"}


class NLIStanceProvider:
    def __init__(self, role_cfg: dict):
        # own key: role overrides are soft-merged, so `model` may be inherited from an LLM config
        self.model_name = role_cfg.get("nli_model") or DEFAULT_MODEL
        self.min_score = role_cfg.get("min_score", 0.7)
        self._pipe = None

    def _classifier(self):
        if self._pipe is None:
            from transformers import pipeline  # lazy: heavy import

            self._pipe = pipeline("text-classification", model=self.model_name, top_k=None)
        return self._pipe

    def judge_stance(self, claim: Claim, evidence: list[Evidence]) -> list[Stance]:
        clf = self._classifier()
        stances = []
        for e in evidence:
            best = ("irrelevant", 0.0, e.text[:200])
            for sent in filter(None, (s.strip() for s in _SENT.split(e.text))):
                scores = clf({"text": sent, "text_pair": claim.text})
                for s in scores if isinstance(scores, list) else [scores]:
                    label = _MAP.get(s["label"].lower())
                    if label and s["score"] > best[1]:
                        best = (label, s["score"], sent)
            label, score, quote = best
            if score < self.min_score:
                label = "irrelevant"
            stances.append(Stance(
                claim_id=claim.id, chunk_id=e.chunk_id, label=label, quote=quote,
                rationale=f"NLI {self.model_name}: {label} ({score:.2f}).",
            ))
        return stances

    def complete_json(self, system, user, schema):  # pragma: no cover
        raise NotImplementedError("NLI provider supports only the stance role")
