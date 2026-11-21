# ADR 0005: Kontekst weryfikacji wyłącznie z grafu wiedzy (Neo4j), bez dostępu do sieci

Status: przyjęte · 2026-11-21 · zastępuje część ADR 0003 (pobieranie źródła pierwotnego) i ADR 0004 (dowody ze stron WWW)

## Decyzja
- Pipeline weryfikacji **nie korzysta z sieci**: usunięte kroki `web_search` i `fetch_web_evidence`,
  dostawcy wyszukiwarek (ddgs/searxng/brave) i podążanie za linkami w trakcie sprawdzenia.
- Jedynym źródłem kontekstu jest **graf wiedzy w Neo4j, zasilany przez inny zespół**; czytamy go tylko do odczytu
  (`Neo4jRetriever`, kontrakt: `docs/neo4j-kontrakt.md`).
- Wyszukiwanie: indeks wektorowy + pełnotekstowy Neo4j, fuzja RRF w Pythonie z pełnym śladem rang (jak w ADR 0004).
- Graf dodaje kontekst: etykietę oceny fact-checkera (`Document.rating`) i nagłówek materiału, który fact-checker
  sprawdzał (relacja `CITES`) → łańcuch źródeł z ADR 0003 bez pobierania stron.
- Źródło pierwotne: zamiast „dostępne w chwili sprawdzenia” piszemy „w bazie źródeł od <fetched_at>”,
  bo nic nie pobieramy w trakcie sprawdzenia.
- Selekcja kontekstu (`select_context`): reranker, próg, różnorodność, budżet, powody odrzuceń w trace.
  Ewaluacja (ADR 0004) pokazała, że bez rerankera próg nie odróżnia „jest dowód” od „brak” (trafność 0,81,
  pytania spoza korpusu ~0,9); z rerankerem 1,0 (trafne 0,95–1,0, spoza ≤ 0,002). Reranker zalecany w produkcji.

## Co zostaje
- Nasz Postgres (`ingestion`, `PgRetriever`) jako alternatywny backend (`retrieval.backend: postgres`) i do audytu.
- `websearch/fetch.py` tylko dla offline `ingestion`.

## Konsekwencje
- (+) Brak zależności od wyszukiwarek, brak treści niezaufanych z sieci w trakcie sprawdzenia, przewidywalny czas.
- (+) Wynik zależy od wersji grafu, nie od stanu internetu: powtarzalność i audyt.
- (−) Świeżość informacji = świeżość grafu; nowe zdarzenia spoza grafu dają „Nie znaleziono informacji”.
- (−) Jakość zależy od kontraktu z drugim zespołem (dosłowny tekst fragmentów, ten sam model embeddingów).
