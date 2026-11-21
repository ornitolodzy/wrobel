"""Step 2: query building (code). (a) dense = claim text, (b) sparse = keywords."""

from __future__ import annotations

import re

from prawdowid.models import Claim, Query

_TOKEN = re.compile(r"\w+", re.UNICODE)
_STOP = {
    "i", "w", "z", "na", "do", "że", "to", "jest", "się", "nie", "o", "a", "od", "po",
    "za", "przez", "dla", "jak", "czy", "ten", "ta", "te", "są", "był", "była",
}


def build_queries(claims: list[Claim]) -> list[Query]:
    queries = []
    for c in claims:
        if c.type != "factual":
            continue
        tokens = [t for t in _TOKEN.findall(c.text) if t.lower() not in _STOP]
        keywords = [t for t in tokens if t[0].isupper() or t.isdigit() or len(t) > 6]
        sparse = " ".join(dict.fromkeys(c.entities + keywords)) or c.text
        queries.append(Query(claim_id=c.id, dense=c.text, sparse=sparse))
    return queries
