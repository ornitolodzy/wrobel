"""Step 4: stance per evidence fragment (LLM/NLI -> JSON). Judged ONLY on fragments."""

from __future__ import annotations

import contextvars
from concurrent.futures import ThreadPoolExecutor, as_completed

from prawdowid import progress
from prawdowid.llm import is_mock
from prawdowid.models import Claim, ClaimEvidence, Stance


def judge_stance(
    claims: list[Claim], evidence: list[ClaimEvidence], llm_params: dict
) -> list[Stance]:
    if is_mock(llm_params["roles"]["stance"]):
        return []  # no stance -> brak_dowodow

    from prawdowid.llm.tasks import judge_stance_llm

    by_id = {c.id: c for c in claims}
    todo = [ce for ce in evidence if not ce.early_stop and ce.evidence]  # early stop: no call
    if not todo:
        return []
    # One call per claim, in parallel; each thread gets a copy of the trace context.
    with ThreadPoolExecutor(max_workers=len(todo)) as pool:
        futures = {
            pool.submit(contextvars.copy_context().run, judge_stance_llm,
                        by_id[ce.claim_id], ce.evidence, llm_params): ce.claim_id
            for ce in todo
        }
        # tell the user as each thread is done (progress stream), keep the result order stable
        for done, f in enumerate(as_completed(futures), start=1):
            if f.exception() is None:
                progress.emit("claim_checked", claim_id=futures[f], done=done, total=len(futures))
        return [s for f in futures for s in f.result()]
