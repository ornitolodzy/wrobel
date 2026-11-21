import httpx
import pytest

from bot.format import VERDICTS, cut, help_embed, result_embed
from bot.gateway import GatewayClient, Request, classify
from bot.limits import UserLimiter


def check(**over):
    base = {
        "check_id": "3141296a-9b11-424e-b405-f7dd7440bc06", "status": "completed", "input_type": "claim",
        "channel": "discord", "duration_ms": 1234, "claims": [], "summary": None, "limitations": [],
        "clarification_question": None, "ocr_text": None, "page": None, "mock": False,
    }
    return {**base, **over}


def claim(verdict="false", sources=3, quote="x" * 400):
    return {
        "claim": "RCB wysłało alert o ataku z powietrza.", "verdict": verdict,
        "explanation": "Fałsz. Komunikat RCB mówi o treningu systemów alarmowania.",
        "evidence_strength": "strong",
        "sources": [{"title": f"Alert RCB {i}", "publisher": "Rządowe Centrum Bezpieczeństwa",
                     "url": f"https://www.gov.pl/web/rcb/a{i}", "published_at": "2026-11-20",
                     "fragment": quote, "source_type": "primary"} for i in range(sources)],
        "limitations": [],
    }


def embed_size(e) -> int:
    return len(e.title or "") + len(e.description or "") + len(e.footer.text or "") + sum(
        len(f.name) + len(f.value) for f in e.fields)


# ---------------------------------------------------------------- routing
@pytest.mark.parametrize("text,kind,value", [
    ("https://www.gov.pl/web/rcb", "url", "https://www.gov.pl/web/rcb"),
    ("<https://demagog.org.pl/x>", "url", "https://demagog.org.pl/x"),
    ("  RCB wysłało alert  ", "claim", "RCB wysłało alert"),
    ("zobacz https://x.pl to fałsz", "claim", "zobacz https://x.pl to fałsz"),
])
def test_classify(text, kind, value):
    req = classify(text)
    assert req.kind == kind and req.text == value


def test_classify_empty():
    assert classify("   ") is None


# ---------------------------------------------------------------- embeds
def test_single_claim_uses_verdict_color_and_sources():
    e = result_embed(check(claims=[claim("false", quote="krótki cytat ze źródła")]), asked="pytanie")
    assert e.title.startswith("❌") and e.color.value == VERDICTS["false"][2]
    value = e.fields[0].value
    assert "[1]" in value and "https://www.gov.pl/web/rcb/a0" in value and "krótki cytat" in value


def test_limits_hold_for_huge_answers():
    data = check(claims=[claim(v, sources=10) for v in VERDICTS] * 3, summary="s" * 5000,
                 limitations=["ograniczenie " * 50] * 10)
    e = result_embed(data)
    assert embed_size(e) <= 6000 and len(e.fields) <= 25
    assert all(len(f.value) <= 1024 and len(f.name) <= 256 for f in e.fields)
    assert len(e.description or "") <= 4096


@pytest.mark.parametrize("status", ["error", "api_limit"])
def test_technical_status_is_not_a_verdict(status):
    e = result_embed(check(status=status))
    assert "nie ocena prawdziwości" in e.description and not any(em in e.title for em in ("✅", "❌"))


def test_needs_clarification_shows_question():
    e = result_embed(check(status="needs_clarification", clarification_question="Co dokładnie sprawdzić?"))
    assert "Co dokładnie sprawdzić?" in e.description


def test_mock_is_flagged():
    e = result_embed(check(claims=[claim("insufficient_evidence", sources=0)], mock=True))
    assert any("MOCK" in f.value for f in e.fields)


def test_url_check_shows_page():
    e = result_embed(check(claims=[claim()], page={"final_url": "https://x.pl/a", "title": "Tytuł"}))
    assert "https://x.pl/a" in e.description


def test_help_fits():
    assert embed_size(help_embed()) < 6000


def test_cut():
    assert cut("a  b\n c", 10) == "a b c" and len(cut("x" * 50, 10)) == 10


# ---------------------------------------------------------------- limiter
def test_user_limiter_per_user_window():
    now = [0.0]
    lim = UserLimiter(2, clock=lambda: now[0])
    assert lim.hit(1) == 0 and lim.hit(1) == 0 and lim.hit(1) > 0
    assert lim.hit(2) == 0  # other users unaffected
    now[0] = 61
    assert lim.hit(1) == 0


# ---------------------------------------------------------------- gateway client
async def _client(handler):
    return GatewayClient("http://gw", transport=httpx.MockTransport(handler))


async def test_gateway_routes_and_channel_header():
    seen = []

    def handler(request: httpx.Request):
        seen.append((request.url.path, request.headers.get("X-Channel")))
        return httpx.Response(200, json=check())

    c = await _client(handler)
    for req in (Request("claim", "abc"), Request("url", "https://x.pl"), Request("image", image=b"png")):
        assert (await c.verify(req)).ok
    await c.close()
    assert seen == [("/api/v1/verify/claim", "discord"), ("/api/v1/verify/url", "discord"),
                    ("/api/v1/verify/image", "discord")]


async def test_gateway_technical_status_passes_through():
    c = await _client(lambda r: httpx.Response(502, json=check(status="error")))
    res = await c.verify(Request("claim", "abc"))
    await c.close()
    assert res.ok and res.data["status"] == "error"


async def test_gateway_rate_limit_and_validation():
    c = await _client(lambda r: httpx.Response(429, json={"detail": "x"}, headers={"Retry-After": "7"}))
    res = await c.verify(Request("claim", "abc"))
    assert not res.ok and res.retry_after == 7
    c2 = await _client(lambda r: httpx.Response(400, json={"detail": "Tekst jest za krótki."}))
    res2 = await c2.verify(Request("claim", "a"))
    assert not res2.ok and res2.problem == "Tekst jest za krótki."
    await c.close(); await c2.close()


async def test_gateway_down():
    def handler(request):
        raise httpx.ConnectError("down")

    c = await _client(handler)
    res = await c.verify(Request("claim", "abc"))
    await c.close()
    assert not res.ok and "niedostępny" in res.problem
