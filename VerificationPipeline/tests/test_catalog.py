from pathlib import Path

import pytest

from prawdowid.catalog import load_catalog, match_source, seed_urls

ROOT = Path(__file__).resolve().parents[1]
CATALOG = load_catalog("conf/base/sources.yml")


def test_every_basic_link_from_linki_md_is_a_seed():
    links = [l.strip() for l in (ROOT / "Linki.md").read_text().splitlines() if l.strip()]
    seeds = {url for url, _ in seed_urls(CATALOG)}
    assert links and not [l for l in links if l not in seeds]


def test_every_seed_matches_a_primary_source():
    for url, _ in seed_urls(CATALOG):
        src = match_source(url, CATALOG)
        assert src is not None and src["type"] == "primary", url


@pytest.mark.parametrize("url, publisher", [
    ("https://www.gov.pl/web/rcb/raport-dobowy3", "Rządowe Centrum Bezpieczeństwa"),
    ("https://www.gov.pl/web/dyplomacja/komunikat", "Ministerstwo Spraw Zagranicznych"),
    ("https://www.gov.pl/web/inny-urzad/x", "gov.pl (portal administracji)"),
    ("https://isap.sejm.gov.pl/isap.nsf/x", "ISAP (Sejm RP)"),          # subdomain of gov.pl wins
    ("https://hydro.imgw.pl/#/", "IMGW-PIB"),
    ("https://mazowiecka.policja.gov.pl/ra/x", "Policja (komendy wojewódzkie)"),
    ("https://www.gov.pl/web/rcbx/udaje", "gov.pl (portal administracji)"),  # prefix, not substring
])
def test_most_specific_entry_wins(url, publisher):
    assert match_source(url, CATALOG)["publisher"] == publisher


def test_unknown_domain_is_not_in_catalog():
    assert match_source("https://gov.pl.evil.example.com/web/rcb", CATALOG) is None
