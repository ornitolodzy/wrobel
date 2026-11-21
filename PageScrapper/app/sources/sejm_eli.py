from collections.abc import AsyncIterator
from datetime import date

from app.db import DocumentIn
from app.extract import parse_date
from app.fetch import FetchError
from app.sources.base import AdapterContext

ISAP_URL = "https://isap.sejm.gov.pl/isap.nsf/DocDetails.xsp?id={address}"


def _names(values) -> str:
    if not values:
        return ""
    return ", ".join(v if isinstance(v, str) else str(v.get("name") or v) for v in values)


def act_to_document(act: dict, api_base: str, ctx: AdapterContext) -> DocumentIn:
    source = ctx.source
    publisher, year, pos = act.get("publisher"), act.get("year"), act.get("pos")
    lines = [
        f"Tytuł: {act.get('title')}",
        f"Pozycja: {act.get('displayAddress')}",
        f"Rodzaj aktu: {act.get('type')}" if act.get("type") else None,
        f"Status: {act.get('status')}" if act.get("status") else None,
        f"Data ogłoszenia: {act.get('promulgation')}" if act.get("promulgation") else None,
        f"Data wydania: {act.get('announcementDate')}" if act.get("announcementDate") else None,
        f"Wejście w życie: {act.get('entryIntoForce')}" if act.get("entryIntoForce") else None,
        f"Organ wydający: {_names(act.get('releasedBy'))}" if act.get("releasedBy") else None,
        f"Słowa kluczowe: {_names(act.get('keywords'))}" if act.get("keywords") else None,
        f"Pełny tekst (PDF): {api_base}/{publisher}/{year}/{pos}/text.pdf" if act.get("textPDF") else None,
    ]
    return DocumentIn(
        url=ISAP_URL.format(address=act.get("address")),
        source_id=source.id,
        source_type=source.source_type,
        title=act.get("title"),
        publisher=source.publisher,
        published_at=parse_date(act.get("promulgation")),
        text="\n".join(line for line in lines if line),
        extra={"eli": f"{publisher}/{year}/{pos}", "status": act.get("status"), "type": act.get("type")},
    )


async def collect(ctx: AdapterContext) -> AsyncIterator[DocumentIn]:
    source = ctx.source
    api_base = source.url.rstrip("/")
    publisher = source.options.get("publisher", "DU")
    year = int(source.options.get("year") or date.today().year)
    await ctx.require_robots(api_base)
    listing = await ctx.fetcher.get_json(f"{api_base}/{publisher}/{year}", polite=True)
    acts = sorted(listing.get("items", []), key=lambda a: a.get("pos", 0), reverse=True)
    for item in acts[: ctx.max_items]:
        url = ISAP_URL.format(address=item.get("address"))
        if ctx.should_skip(url):
            continue
        try:
            act = await ctx.fetcher.get_json(f"{api_base}/{publisher}/{year}/{item['pos']}", polite=True)
        except FetchError as exc:
            ctx.add_error(url, exc.message)
            act = item
        yield act_to_document(act, api_base, ctx)
