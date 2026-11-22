from context_graph.chunking import chunk_text, split_sentences
from context_graph.extract import RegexExtractor
from context_graph.textnorm import lucene_query, stem


def test_split_sentences_keeps_dates():
    text = "Obiekt pojawił się 20.11.2026 nad Lubelszczyzną. MON potwierdziło. Koniec!"
    assert split_sentences(text) == [
        "Obiekt pojawił się 20.11.2026 nad Lubelszczyzną.",
        "MON potwierdziło.",
        "Koniec!",
    ]


def test_chunk_text_groups_whole_sentences():
    text = " ".join(f"Zdanie numer {i} jest tutaj." for i in range(10))
    chunks = chunk_text(text, max_chars=60)
    assert all(c.endswith(".") for c in chunks)
    assert " ".join(chunks) == text
    assert all(len(c) <= 60 for c in chunks)


def test_stem_unifies_inflection():
    assert stem("Polska") == stem("Polsce") == stem("Polski")
    assert stem("Białoruś") == stem("Białorusi")
    assert stem("Ukraina") == stem("Ukrainie")


def test_lucene_query_uses_prefixes_and_escapes():
    q = lucene_query("Naruszono przestrzeń powietrzną dnia 20.11 (MSZ)?")
    terms = q.split(" OR ")
    assert "narusz*" in terms
    assert "przestr*" in terms
    assert "20.11" in terms
    assert "dnia" not in q  # stopword
    assert "msz" in terms


def keys(text):
    return {e.key: e for e in RegexExtractor().extract(text)}


def test_extract_dates_numeric_and_wordy_share_key():
    assert "date:--11-20" in keys("Naruszono przestrzeń powietrzną dnia 20.11")
    assert "date:--11-20" in keys("W nocy z 19 na 20 listopada coś się stało.")
    assert "date:2026-11-20" in keys("Komunikat z 20.11.2026.")


def test_extract_acronyms_and_names():
    ents = keys("Rzecznik MON i Dowództwo Operacyjne mówią o Białorusi.")
    assert ents["mon"].type == "ORG"
    assert "dowód opera" in ents
    assert ents[stem("Białorusi")].type == "GPE"


def test_sentence_initial_word_is_not_an_entity_unless_known_place():
    ents = keys("Według portalu obiekt nadleciał. Rosja zaprzecza.")
    assert stem("Według") not in ents
    assert stem("Rosja") in ents


def test_date_year_from_reference_date_is_marked_inferred():
    from datetime import date

    ents = {e.key: e for e in RegexExtractor().extract("Alert z 20.11 dla mieszkańców.", ref_date=date(2025, 11, 21))}
    assert ents["date:2025-11-20"].year_inferred is True
    explicit = {e.key: e for e in RegexExtractor().extract("Alert z 20.11.2026.", ref_date=date(2025, 1, 1))}
    assert explicit["date:2026-11-20"].year_inferred is False


def test_iso_and_invalid_dates():
    assert "date:2026-11-20" in keys("Opublikowano 2026-11-20 o 10:00.")
    assert not [k for k in keys("Dnia 31.02.2026 nic się nie stało.") if k.startswith("date:")]
