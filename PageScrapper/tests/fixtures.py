import json

import httpx


def html(body: str) -> httpx.Response:
    return httpx.Response(200, text=body, headers={"content-type": "text/html; charset=utf-8"})


GOVPL_LISTING = """
<html><body>
<nav><a href="/web/rcb/menu-link">Menu</a></nav>
<ul>
  <li><div><div class="event"><span class="date">20.11.2026</span></div>
      <div class="title"><a href="/web/rcb/alert-1"> Alert RCB - atak z powietrza </a></div></div></li>
  <li><div><div class="event"><span class="date">19.11.2026</span></div>
      <div class="title"><a href="/web/rcb/alert-2"> Alert RCB - woda niezdatna do picia </a></div></div></li>
  <li><div class="title"><a href="https://example.com/obcy">Obcy link</a></div></li>
</ul>
</body></html>
"""


def govpl_article(title: str, day: str, body: str) -> str:
    return f"""
<html><head><meta property="og:site_name" content="Rządowe Centrum Bezpieczeństwa"></head><body>
<header>Portal Gov.pl</header>
<article class="article-area__article " id="main-content">
  <h2>{title}</h2>
  <p class="event-date">{day}</p>
  <p class="intro">Komunikat RCB.</p>
  <div class="editor-content"><p>{body}</p></div>
</article>
<footer>stopka</footer>
</body></html>
"""


def generic_article(title: str, day: str) -> str:
    paragraphs = "".join(
        f"<p>Akapit {i}: to zdjęcie zostało przerobione, a oryginał pochodzi z 2019 roku i przedstawia inne miejsce.</p>"
        for i in range(8)
    )
    return f"""
<html><head><title>{title}</title><meta property="og:site_name" content="Stowarzyszenie Demagog">
<meta property="article:published_time" content="{day}T10:00:00+00:00"></head>
<body><article><h1>{title}</h1>
<p>Obalamy fałszywe informacje pojawiające się w mediach społecznościowych oraz na portalach.</p>
{paragraphs}</article></body></html>
"""


RSS_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Demagog</title>
<item><title>To nie jest prawdziwe zdjęcie</title><link>https://demagog.org.pl/fake_news/fake-1/</link>
<pubDate>Fri, 20 Nov 2026 14:59:06 +0000</pubDate><category>Fake news</category>
<description><![CDATA[<p>Krótki opis fałszywego zdjęcia.</p>]]></description></item>
<item><title>Fałszywy cytat polityka</title><link>https://demagog.org.pl/fake_news/fake-2/</link>
<pubDate>Thu, 19 Nov 2026 10:00:00 +0000</pubDate>
<description><![CDATA[<p>Ten cytat nigdy nie padł. Sprawdziliśmy nagrania i stenogramy z posiedzenia, nie ma tam takiej wypowiedzi.</p>]]></description></item>
</channel></rss>
"""

ODYSEUSZ_HOME = """
<html><body><main id="main-content">
<mat-sidenav><ui-nav-accordion><ul><li><a href="/kraje">Profile krajów</a></li><li><a href="/moje-podroze">Moje podróże</a></li></ul></ui-nav-accordion></mat-sidenav>
<h1>Bezpieczna podróż</h1>
<h2>Aktualności</h2>
<p>Włochy</p><p>Ostatnia aktualizacja: <strong>19 listopada 2026</strong></p>
<p>W dniu 20 listopada na terenie całego kraju zapowiedziano strajk generalny. Mogą występować utrudnienia w transporcie.</p>
<a href="/IT">Zobacz profil kraju</a>
</main></body></html>
"""


def odyseusz_profile(country: str, updated: str, level: int, label: str) -> str:
    return f"""
<html><body><main id="main-content" class="app__main">
<mat-sidenav><ui-nav-accordion><ul><li><a href="/kraje">Profile krajów</a></li><li><a href="/moje-podroze">Moje podróże</a></li></ul></ui-nav-accordion></mat-sidenav>
<span class="sr-only">1 z 3:</span>
<h1>{country}</h1>
<p>Ostatnia aktualizacja: <strong>{updated}</strong></p>
<h2>Ogólny poziom ostrzeżenia dla kraju</h2>
<p>Poziom {level} - {label}</p>
<mat-card><div class="title"><ui-page-subtitle><h2 class="page-subtitle">Subskrypcja e-mail</h2></ui-page-subtitle></div>
<p>Otrzymuj ważne informacje dotyczące tego kraju.</p><form><input></form></mat-card>
<h2>Najważniejsze informacje</h2>
<p>Bezpieczeństwo: sytuacja w kraju {country} wymaga szczególnej uwagi podróżnych, stosuj się do poleceń służb.</p>
<mat-expansion-panel><mat-expansion-panel-header>Ataki lotnicze<mat-icon>keyboard_arrow_up</mat-icon></mat-expansion-panel-header>
<p>W razie alarmu udaj się do schronu.</p></mat-expansion-panel>
</main></body></html>
"""


GOOGLE_RESPONSE = {
    "claims": [
        {
            "text": "Polska wysłała wojsko na Ukrainę",
            "claimant": "Konto w mediach społecznościowych",
            "claimDate": "2026-10-01T00:00:00Z",
            "claimReview": [
                {
                    "publisher": {"name": "Demagog", "site": "demagog.org.pl"},
                    "url": "https://demagog.org.pl/fake_news/wojsko/",
                    "title": "Polska nie wysłała wojska na Ukrainę",
                    "reviewDate": "2026-10-03T00:00:00Z",
                    "textualRating": "Fałsz",
                    "languageCode": "pl",
                }
            ],
        },
        {
            "text": "Bez recenzji",
            "claimReview": [{"publisher": {"name": "X"}, "title": "Brak URL"}],
        },
    ]
}


POLICJA_LISTING = """
<html><body>
<a href="/pol/kontakt">Kontakt</a>
<ul>
  <li><a href="/pol/aktualnosci/1001,Pierwszy-komunikat.html">Pierwszy komunikat</a></li>
  <li><a href="/pol/aktualnosci/1002,Drugi-komunikat.html">Drugi komunikat</a></li>
  <li><a href="/pol/aktualnosci/1001,Pierwszy-komunikat.html#top">duplikat</a></li>
  <li><a href="https://example.com/pol/aktualnosci/9,obcy.html">obcy</a></li>
</ul>
</body></html>
"""


def policja_article(title: str, day: str) -> str:
    body = "".join(f"<p>Policjanci zatrzymali sprawcę zdarzenia numer {i}, sprawa trafiła do prokuratury.</p>" for i in range(6))
    return f"""
<html><head><title>{title} - Policja</title></head><body>
<article><h2>{title}</h2><span class="data">Data publikacji {day}</span>{body}</article>
</body></html>
"""


SEJM_LIST = {
    "count": 3,
    "items": [
        {"pos": 1, "address": "WDU20260000001", "title": "Akt pierwszy", "promulgation": "2026-01-02"},
        {"pos": 3, "address": "WDU20260000003", "title": "Akt trzeci", "promulgation": "2026-11-19"},
        {"pos": 2, "address": "WDU20260000002", "title": "Akt drugi", "promulgation": "2026-11-10"},
    ],
}


def sejm_act(pos: int) -> dict:
    return {
        "address": f"WDU2026000000{pos}",
        "displayAddress": f"Dz.U. 2026 poz. {pos}",
        "pos": pos,
        "year": 2026,
        "publisher": "DU",
        "title": f"Ustawa z dnia 22 października 2026 r. numer {pos}",
        "type": "Ustawa",
        "status": "obowiązujący",
        "promulgation": "2026-11-19",
        "entryIntoForce": "2026-12-04",
        "releasedBy": ["SEJM"],
        "keywords": ["obrona narodowa", "bezpieczeństwo"],
        "textPDF": True,
    }


MCP_CASES = {
    "total": 2,
    "hasNextPage": False,
    "cases": [
        {
            "id": "case-1",
            "title": "Poland is preparing to annex western Ukraine",
            "claim": "<p>Poland plans to annex western Ukraine.</p>",
            "fact": "<p>There is no evidence. This is a recurring pro-Kremlin narrative.</p>",
            "date": 1763596800,
            "keywords": [{"title": "Territorial claims"}],
            "countries": [{"title": "Poland"}, {"title": "Ukraine"}],
            "linksToDisinformation": [{"headLine": "Польша готовится", "url": "https://disinfo.example/article"}],
            "url": "https://euvsdisinfo.eu/report/poland-annex/",
        },
        {"id": "case-2", "title": "bez url", "claim": "x"},
    ],
}


def sse(payload: dict) -> str:
    return f"event: message\ndata: {json.dumps(payload)}\n\n"
