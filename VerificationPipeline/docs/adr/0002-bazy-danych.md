# ADR 0002: Bazy danych dla RAG i granica odpowiedzialności repo

Status: przyjęte · 2026-11-21

## Pytanie
Jakich baz potrzebuje pełny system RAG (relacyjna, wektorowa, grafowa) i czy to odpowiedzialność tego repo?

## Decyzja: jeden Postgres, trzy modele danych
| Potrzeba | Rozwiązanie | Dlaczego nie osobna baza |
|---|---|---|
| Relacyjna: katalog źródeł, dokumenty, audit log, sprawy | Postgres 16 | to jest naturalny rdzeń |
| Wektorowa: wyszukiwanie semantyczne | `pgvector` (HNSW) w tym samym Postgresie | korpus 10²–10⁵ fragmentów; hybryda wektor + full-text (RRF) w jednym SQL |
| Full-text: nazwiska, liczby, sygnatury aktów | `tsvector` w Postgresie | j.w. |
| Grafowa: encje, pochodzenie, argumentacja | tabele krawędzi w Postgresie (etapy niżej) | 2–3 skoki = rekurencyjne CTE |

Osobny silnik dopiero gdy: >10⁷ wektorów albo potrzeba polskiego stemmera (OpenSearch + `stempel`);
zapytania grafowe stają się rdzeniem produktu (najpierw Apache AGE: Cypher w tym samym Postgresie, potem ewentualnie Neo4j).

## Graf: po co i jak
Graf jest użyteczny do znajdowania powiązań i argumentacji w źródłach, czego nie daje samo podobieństwo tekstu:
1. **Powiązania encji (multi-hop):** twierdzenie o „piśmie MSZ do Czech” dociera do fragmentów o tych samych encjach
   (`MSZ`, `Radio PiK`, `PISM`), nawet gdy są sformułowane inaczej (sprostowanie, komunikat o włamaniu).
2. **Łańcuch pochodzenia:** oryginał → przedruki → sprostowania. „Kilka przedruków ≠ kilka potwierdzeń” wynika wtedy z grafu.
3. **Argumentacja między źródłami:** kto komu przeczy (`supports` / `attacks` / `corrects`) i na jakiej podstawie.

Zasada: krawędź wyciągnięta przez LLM to **interpretacja, nie fakt**. Każda krawędź ma wskazanie na fragment
i dosłowny cytat, który przechodzi ten sam walidator cytatów. Inaczej graf łamie audytowalność.

Etapy (tabele już są w `db/schema.sql`):
- **Etap 1, deterministyczny, bez LLM:** spaCy NER → `entity`, `mention(chunk_id, entity_id)`;
  `document_relation(reprints | cites | corrects)` z MinHash i linków. W retrieval krok `graph_expand`
  dokłada fragmenty współdzielące encje z twierdzeniem jako trzeci ranking do RRF (`rrf_merge` przyjmuje N list).
- **Etap 2, LLM offline w `ingestion`:** `claim_relation(src_chunk, dst_chunk, type, quote, model)`.
  Koszt proporcjonalny do korpusu, więc wsadowo i tylko dla fragmentów z tymi samymi encjami.

## Czy to odpowiedzialność tego repo? Częściowo
- **To repo posiada:** swój audit log (`check`, `case`), interfejs `Retriever`
  (`src/prawdowid/retrieval/base.py`) i adapter do bazy. Pipeline weryfikacji zależy od interfejsu, nie od bazy.
- **Korpus wiedzy** (ingestion, katalog źródeł, fragmenty, embeddingi, graf) to docelowo osobna usługa „knowledge base”.
  Może ją współdzielić kilka narzędzi i ma inny cykl życia (wsadowy, cron). Na hackathon zostaje tutaj jako pipeline
  `ingestion`, odseparowany pakietowo. Wyniesienie go = nowy adapter `Retriever`, bez zmian w `verification`.

## Konsekwencje
- (+) Jedna baza do postawienia, backupu i audytu; jeden `docker compose up db`.
- (+) Graf można rozwijać przyrostowo bez migracji na inny silnik.
- (−) Złożone zapytania grafowe (>3 skoki, algorytmy grafowe) będą w SQL niewygodne; wtedy Apache AGE.
