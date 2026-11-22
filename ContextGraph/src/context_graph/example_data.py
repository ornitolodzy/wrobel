"""Fictional demo corpus for the claim from the architecture drawing:
"Naruszono przestrzeń powietrzną dnia 20.11".

All domains are *.example and all content is made up: it only exercises the graph
(reprint, correction, citation of a primary source, OCR'd screenshot, contradiction).
"""

from __future__ import annotations

PAGES = [
    {
        "url": "https://news-a.example/kraj/obiekt-20-11",
        "publisher": "News A",
        "source_type": "secondary",
        "published_at": "2026-11-20T09:00:00+01:00",
        "title": "Obiekt nad Lubelszczyzną. Naruszenie przestrzeni powietrznej 20.11",
        "text": (
            "W nocy z 19 na 20 listopada nad Lubelszczyzną pojawił się niezidentyfikowany obiekt. "
            "Według portalu obiekt nadleciał znad Białorusi i naruszył polską przestrzeń powietrzną. "
            "Dowództwo Operacyjne poderwało dyżurne myśliwce. "
            "MON ma przedstawić szczegóły w ciągu dnia."
        ),
        "links": ["https://gov.example/komunikaty/20-11"],
    },
    {
        # Same body as News A -> REPRINTS, must not count as an independent confirmation.
        "url": "https://news-b.example/swiat/obiekt-nad-lubelszczyzna",
        "publisher": "News B",
        "source_type": "secondary",
        "published_at": "2026-11-20T11:30:00+01:00",
        "title": "Naruszenie przestrzeni powietrznej nad Lubelszczyzną",
        "text": (
            "W nocy z 19 na 20 listopada nad Lubelszczyzną pojawił się niezidentyfikowany obiekt. "
            "Według portalu obiekt nadleciał znad Białorusi i naruszył polską przestrzeń powietrzną. "
            "Dowództwo Operacyjne poderwało dyżurne myśliwce. "
            "MON ma przedstawić szczegóły w ciągu dnia."
        ),
    },
    {
        "url": "https://gov.example/komunikaty/20-11",
        "publisher": "Dowództwo Operacyjne (przykład)",
        "source_type": "primary",
        "published_at": "2026-11-20T13:00:00+01:00",
        "title": "Komunikat Dowództwa Operacyjnego z 20.11.2026",
        "text": (
            "Dowództwo Operacyjne informuje, że 20.11.2026 radary śledziły obiekt lecący wzdłuż granicy "
            "z Białorusią. Obiekt nie wleciał w polską przestrzeń powietrzną. "
            "Dyżurne myśliwce wykonały lot kontrolny i wróciły do bazy."
        ),
    },
    {
        "url": "https://factcheck.example/analizy/obiekt-20-11",
        "publisher": "FactCheck (przykład)",
        "source_type": "factcheck",
        "published_at": "2026-11-20T18:00:00+01:00",
        "title": "Czy 20.11 naruszono polską przestrzeń powietrzną? Sprawdzamy",
        "rating": "Fałsz",
        "text": (
            "Portal News A podał, że obiekt naruszył polską przestrzeń powietrzną. "
            "Komunikat Dowództwa Operacyjnego mówi jednak, że obiekt nie wleciał w polską przestrzeń "
            "powietrzną, a jedynie leciał wzdłuż granicy z Białorusią. "
            "Informację o naruszeniu oceniamy jako fałszywą."
        ),
        "links": ["https://gov.example/komunikaty/20-11", "https://news-a.example/kraj/obiekt-20-11"],
        "corrects": ["https://news-a.example/kraj/obiekt-20-11"],
    },
]

IMAGES = [
    {
        "image_id": "screenshot-news-a-001.png",
        "path": "filestorage/2026/11/20/screenshot-news-a-001.png",
        "page_url": "https://news-a.example/kraj/obiekt-20-11",
        "ocr_text": "PILNE. Obiekt znad Białorusi nad Lubelszczyzną 20.11. MON zwołuje sztab.",
    }
]

# Interpretive relation (as an LLM would produce offline): fact-check body contradicts News A.
CONTRADICTION = {
    "type": "CONTRADICTS",
    "src_page": "https://factcheck.example/analizy/obiekt-20-11",
    "dst_page": "https://news-a.example/kraj/obiekt-20-11",
    "quote": "obiekt nie wleciał w polską przestrzeń powietrzną",
    "method": "example",
}

CLAIM = "Naruszono przestrzeń powietrzną dnia 20.11"
