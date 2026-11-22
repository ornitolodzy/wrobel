# ADR 0006: Jedno źródło prawdy, Neo4j jako jedyny indeks, rekoncyliacja zamiast ufania pushowi

Status: przyjęte (faza 1 wdrożona) · 2026-11-22 · kontynuacja ADR 0004 (indeks) i ADR 0005 (kontekst z grafu)

## Kontekst
Te same dokumenty trafiały do dwóch niezależnych indeksów z różnym chunkingiem: korpusu pipeline'u w Postgresie
(`source/document/chunk`, pgvector) i grafu ContextGraph w Neo4j. Rozjazd był wbudowany:
- push z PageScrappera nie miał ponowień (błąd zwiększał tylko `rag_errors`);
- graf dostawał nowe dokumenty wyłącznie przez ręczne `make graph-sync`;
- live-search zapisywał tylko do Postgresa, a pipeline czytał graf;
- `content_hash` scrapera nie obejmuje metadanych (ClaimReview), więc ich zmiana była niewidoczna;
- zmiana modelu embeddingów zostawiała w grafie wektory dwóch modeli;
- seedy obu baz robiono w różnych chwilach.

## Decyzja
- **Źródło prawdy:** tabela `documents` PageScrappera. Każda zapisana zmiana (tekst lub metadane) zwiększa
  `documents.revision`.
- **Jeden indeks pochodny:** Neo4j, a jedynym pisarzem jest ContextGraph. Korpus pipeline'u w Postgresie zostanie
  usunięty (faza 3), gdy eval na grafie będzie co najmniej równy (faza 0/2).
- **Push jest szybką ścieżką, a gwarancję daje rekoncyliacja.** Scraper wysyła do `RAG_INGEST_URLS` (z `revision`).
  ContextGraph co `RECONCILE_INTERVAL_MINUTES` porównuje `GET /documents/revisions` z `Document.scraper_revision`,
  dosyła brakujące i nieaktualne dokumenty i dolicza wektory. Sieroty tylko raportuje.
- **Kolejność zapisów:** starsza rewizja niż zapisana jest ignorowana, sprawdzana pod blokadą węzła w tej samej
  transakcji co zapis.
- **Push przyjęty = zero węzłów bez wektora** (`unembedded = 0`); dotyczy live-search, który od razu weryfikuje ponownie.
- **Jedna przestrzeń wektorów:** zmiana modelu przelicza też wektory starego modelu. Do końca przeliczania
  `CorpusVersion.consistent = false`, a pipeline traktuje to jak niezgodny model (zdarzenie `embedding_model_mixed`).
- **Seedy:** `make seed-dump-all` robi zrzuty dopiero po rekoncyliacji z `lag = 0`.

## Fazy
0. Eval na obu backendach (bazowa liczba dla grafu).
1. Rewizje, push do wielu odbiorców, rekoncyliacja, `consistent` (wdrożone; Postgres działa równolegle).
2. `retrieval.backend: neo4j` na stałe, gdy eval na grafie jest co najmniej równy, a `lag = 0` utrzymuje się przez kilka dni.
3. Usunięcie korpusu pipeline'u z Postgresa (`corpus.py`, `retrieval/postgres.py`, `/ingest`, pipeline `ingestion`,
   tabele `source/document/chunk/corpus_version/entity/mention/document_relation/claim_relation`).

## Konsekwencje
- (+) Zgubiony push, awaria tunelu do Ollamy czy restart grafu same się naprawiają w ciągu jednego cyklu.
- (+) `lag` w `GET /sync/status` mówi wprost, czy indeks jest zgodny ze źródłem.
- (−) Neo4j staje się jedynym indeksem wyszukiwania: jego niedostępność to brak dowodów (faza 2 dodaje jawny błąd).
- (−) Rekoncyliacja co cykl pobiera listę rewizji wszystkich dokumentów. Przy obecnej skali (setki) to pomijalne,
  przy setkach tysięcy trzeba przejść na znacznik `updated_at`.
