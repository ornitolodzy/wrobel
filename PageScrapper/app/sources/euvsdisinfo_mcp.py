import json
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from app.db import DocumentIn
from app.extract import html_to_text
from app.fetch import FetchError, FetchResult
from app.sources.base import AdapterContext, SourceSkipped

PUBLISHER = "EUvsDisinfo (EEAS East StratCom Task Force)"
PAGE_SIZE = 100


def parse_mcp_response(result: FetchResult) -> dict:
    body = result.text.strip()
    if result.content_type == "text/event-stream" or body.startswith(("event:", "data:")):
        payloads = [line[5:].strip() for line in body.splitlines() if line.startswith("data:")]
        body = payloads[-1] if payloads else "{}"
    data = json.loads(body or "{}")
    if "error" in data:
        raise FetchError("mcp_error", f"EUvsDisinfo MCP: {data['error'].get('message', 'błąd')}")
    return data.get("result", {})


def _plain(value: str | None) -> str:
    if not value:
        return ""
    return html_to_text(value) if "<" in value else value.strip()


def case_to_document(case: dict, source_id: str, source_type: str) -> DocumentIn | None:
    url = case.get("url")
    if not url:
        return None
    timestamp = case.get("date")
    published = datetime.fromtimestamp(timestamp, tz=timezone.utc).date() if isinstance(timestamp, (int, float)) else None
    def titles(key: str) -> str:
        return ", ".join(item.get("title", "") for item in case.get(key) or [] if item.get("title"))

    examples = [
        f"- {link.get('headLine') or 'publikacja'} ({link.get('url')})"
        for link in (case.get("linksToDisinformation") or [])[:5]
        if link.get("url")
    ]
    lines = [
        f"Przypadek dezinformacji: {case.get('title')}",
        f"Twierdzenie dezinformacyjne: {_plain(case.get('claim'))}",
        f"Ustalenia EUvsDisinfo: {_plain(case.get('fact'))}",
        f"Kraje / regiony: {titles('countries')}" if case.get("countries") else None,
        f"Słowa kluczowe: {titles('keywords')}" if case.get("keywords") else None,
        "Przykłady publikacji z tym przekazem:\n" + "\n".join(examples) if examples else None,
    ]
    return DocumentIn(
        url=url,
        source_id=source_id,
        source_type=source_type,
        title=case.get("title"),
        publisher=PUBLISHER,
        published_at=published,
        text="\n".join(line for line in lines if line),
        language="en",
        extra={"case_id": case.get("id"), "keywords": titles("keywords"), "countries": titles("countries")},
    )


async def collect(ctx: AdapterContext) -> AsyncIterator[DocumentIn]:
    source = ctx.source
    token = ctx.settings.euvsdisinfo_token
    if not token:
        raise SourceSkipped("Brak EUVSDISINFO_TOKEN - źródło pominięte (token wydaje organizator / EUvsDisinfo).")
    headers = {"Accept": "application/json, text/event-stream", "Authorization": f"Bearer {token}"}

    init = await ctx.fetcher.post(
        source.url,
        {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "prawdowid-scraper", "version": "0.1.0"}},
        },
        headers,
    )
    parse_mcp_response(init)
    session = next((v for k, v in init.headers.items() if k.lower() == "mcp-session-id"), None)
    if session:
        headers["mcp-session-id"] = session
    try:
        await ctx.fetcher.post(source.url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, headers)
    except FetchError:
        pass

    yielded, offset = 0, 0
    while yielded < ctx.max_items:
        limit = min(PAGE_SIZE, ctx.max_items - yielded)
        response = await ctx.fetcher.post(
            source.url,
            {"jsonrpc": "2.0", "id": offset + 1, "method": "tools/call", "params": {"name": "cases", "arguments": {"offset": offset, "limit": limit}}},
            headers,
        )
        content = parse_mcp_response(response).get("structuredContent") or {}
        cases = content.get("cases") or []
        if not cases:
            return
        for case in cases:
            doc = case_to_document(case, source.id, source.source_type)
            if doc is None or ctx.should_skip(doc.url):
                continue
            yield doc
            yielded += 1
        if not content.get("hasNextPage"):
            return
        offset += len(cases)
