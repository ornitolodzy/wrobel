from collections.abc import AsyncIterator

from app.db import DocumentIn
from app.extract import parse_date
from app.fetch import FetchError
from app.sources.base import AdapterContext, SourceSkipped


def review_to_document(claim: dict, review: dict, query: str, source_id: str, source_type: str) -> DocumentIn | None:
    url = review.get("url")
    if not url:
        return None
    publisher = review.get("publisher") or {}
    publisher_name = publisher.get("name") or publisher.get("site") or "Weryfikator faktów"
    lines = [
        f"Twierdzenie: {claim.get('text')}" if claim.get("text") else None,
        f"Autor twierdzenia: {claim.get('claimant')}" if claim.get("claimant") else None,
        f"Data twierdzenia: {claim.get('claimDate', '')[:10]}" if claim.get("claimDate") else None,
        f"Ocena weryfikatora: {review.get('textualRating')}" if review.get("textualRating") else None,
        f"Tytuł weryfikacji: {review.get('title')}" if review.get("title") else None,
        f"Weryfikator: {publisher_name}" + (f" ({publisher.get('site')})" if publisher.get("site") else ""),
    ]
    return DocumentIn(
        url=url,
        source_id=source_id,
        source_type=source_type,
        title=review.get("title") or claim.get("text"),
        publisher=publisher_name,
        published_at=parse_date(review.get("reviewDate")),
        text="\n".join(line for line in lines if line),
        language=review.get("languageCode"),
        extra={
            "claim": claim.get("text"),
            "claimant": claim.get("claimant"),
            "rating": review.get("textualRating"),
            "query": query,
        },
    )


async def collect(ctx: AdapterContext) -> AsyncIterator[DocumentIn]:
    source = ctx.source
    key = ctx.settings.google_factcheck_api_key
    if not key:
        raise SourceSkipped("Brak GOOGLE_FACTCHECK_API_KEY - źródło pominięte.")
    options = source.options
    seen: set[str] = set()
    count = 0
    for query in options.get("queries") or []:
        params = {
            "query": query,
            "languageCode": options.get("language", "pl"),
            "maxAgeDays": options.get("max_age_days", 365),
            "pageSize": 50,
            "key": key,
        }
        try:
            data = await ctx.fetcher.get_json(source.url, params)
        except FetchError as exc:
            raise RuntimeError(f"Google Fact Check API: {exc.message}") from None
        for claim in data.get("claims", []):
            for review in claim.get("claimReview", []):
                doc = review_to_document(claim, review, query, source.id, source.source_type)
                if doc is None or doc.url in seen or ctx.should_skip(doc.url):
                    continue
                seen.add(doc.url)
                yield doc
                count += 1
                if count >= ctx.max_items:
                    return
