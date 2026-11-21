# ADR 0004: RAG – indeks źródeł, retriever Postgres, reranker, wyjaśnialność

Status: przyjęte · 2026-11-21 · kontynuacja ADR 0002 (bazy) i ADR 0003 (cytaty)

## Kontekst
Dowody pochodziły tylko z wyszukiwarki na żywo, oceniane dopasowaniem słów. Oficjalne źródła z `Linki.md`
trafiały do werdyktu przypadkiem. Brakowało indeksu, embeddingów, rerankera i pomiaru jakości wyszukiwania.

## Decyzja

### Indeks (pipeline `ingestion`)
- Strony startowe z katalogu (`conf/base/sources.yml`, 40 linków z `Linki.md`) + **1 poziom**: linki tej samej
  pozycji katalogu (ta sama instytucja), wyglądające na artykuł (≥ 2 segmenty ścieżki, nagłówek jako tekst linku,
  bez plików i nawigacji), max `links_per_seed`.
- Pobieranie tym samym `Fetcher` co dowody z sieci: publiczne IP, robots.txt, limity, odstęp `delay_s` per domena.
- Duplikaty dokładne (hash) odrzucane, przedruki → `dup_group` (shingle, stabilny crc32).
- Fragmenty z zakładką (`chunk_overlap`), pozycja w dokumencie (do sąsiadów).
- **Przyrostowo**: dokument z niezmienionym hashem nie jest liczony ponownie; zmiana modelu embeddingów
  przelicza wszystko. Każda zmiana indeksu = nowy wiersz `corpus_version`.
- Embeddingi `bge-m3` (1024) przez Ollamę na serwerze z Bielikiem (`embeddings.provider: ollama`).

### Wyszukiwanie (`PgRetriever`)
- Jedno zapytanie SQL: `dense` (pgvector, cosinus) i `sparse` (tsvector, alternatywa prefiksów słów; skróty
  RCB/MSZ/PAP dokładnie), łączone RRF `1/(k+rank)`, remisy po id → kolejność deterministyczna dla wersji korpusu.
- Ten sam model embeddingów dla korpusu i zapytania; niezgodność = brak wyników + zdarzenie w trace.
- Awaria bazy nie przerywa sprawdzenia (jak wyszukiwarka): zdarzenie w trace, dalej dowody z sieci.

### Reranker i kontekst (`merge_evidence`)
- `bge-reranker-v2-m3` (CPU, włączany w configu) ocenia kandydatów z korpusu **i** ze stron: jedna skala 0–1.
  Bez rerankera: korpus = RRF znormalizowane do 0–1, strony = pokrycie słów.
- Poniżej `rerank_min` odrzucane; potem limity na dokument i grupę przedruków, `top_k`, budżet znaków.

### Wyjaśnialność
Każdy fragment niesie `RetrievalTrace`: `found_by` (dense/sparse/web), rangi i wyniki z każdej listy,
`matched` (ts_headline), `rrf`, `lexical`, `rerank_score`, `final_rank`, `corpus_version`, sąsiedzi.
Trace sprawdzenia zawiera: zapytanie tsquery, wersję korpusu, model embeddingów, `hnsw.ef_search`,
oraz **każdego odrzuconego kandydata z powodem** (próg, limit dokumentu, przedruk, budżet, top_k).

### Ewaluacja
`kedro run --pipeline eval`: recall@10 i MRR na `tests/eval/retrieval_cases.jsonl` (twierdzenie → oczekiwane URL-e;
pusta lista = dowodów być nie powinno), odsetek poprawnych cytatów i pokrycie werdyktów źródłami,
propozycja progu `rerank_min` (raportowana, nie stosowana automatycznie).

## Konsekwencje
- (+) Oficjalne źródła są w dowodach niezależnie od wyszukiwarki; każdy wynik da się odtworzyć i uzasadnić.
- (−) Strony aplikacji JS (hydro.imgw, monitoring.paa, mapa GDDKiA) nie dają tekstu: raportowane, do obsłużenia przez API.
- (−) Pełnotekstowe `simple` bez polskiego stemmera; prefiksy to przybliżenie.
- (−) Reranker na CPU dodaje kilka sekund; domyślnie wyłączony w `conf/base`.
