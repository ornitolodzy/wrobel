- [x] Podpięcie Modelu AI, zastanowienie się jaki to model → `docs/adr/0001-modele-ai.md`, `src/prawdowid/llm/`
- [x] Przeanalizuj jakie bazy danych potrzebujemy: relacyjna, vectorowa, grafowa do zbudowania pełnoprawnego systemu RAG, odpowiedz na pytanie czy powinna być to odpowiedzialność tego REPO → `docs/adr/0002-bazy-danych.md`
- [x] zastanów się w jaki sposób ma działać Walidator cytatów → `docs/adr/0003-walidator-cytatow.md`
- [x] RAG 1: indeksowanie źródeł z `Linki.md` do Postgresa (179 dok., 8 domen; embeddingi na razie mock) – po ADR 0005 tylko backend alternatywny
- [x] Weryfikacja bez dostępu do sieci; kontekst wyłącznie z grafu Neo4j → `docs/adr/0005-kontekst-tylko-z-grafu.md`
- [ ] Neo4j: uzgodnić z drugim zespołem schemat/zapytania (`docs/neo4j-kontrakt.md`), dostęp read-only, model embeddingów
- [ ] Neo4j: ewaluacja wyszukiwania na ich grafie (`tests/eval/retrieval_cases.jsonl` pod ich dane) + test integracyjny
- [x] RAG 2: retriever Postgres (wektor + pełnotekstowe, RRF, jawne rangi, wersja korpusu) → `docs/adr/0004-rag-indeks-i-retriever.md`
- [x] RAG 3: reranker bge-reranker-v2-m3 (eval: trafność progu 0,81 → 1,0)
- [x] RAG 4: składanie kontekstu (budżet znaków, sąsiednie fragmenty, ślad odrzuconych kandydatów) → `select_context`
- [x] RAG 5: ewaluacja wyszukiwania (recall@10, MRR) + metryki cytowań + kalibracja `rerank_min`
- [ ] Graf etap 1: spaCy NER w `ingestion` → `entity`/`mention`, MinHash → `document_relation`; krok `graph_expand` w retrieval
- [ ] Graf etap 2: relacje argumentacyjne `claim_relation` (LLM offline, cytaty przez walidator)
- [ ] Eval: porównanie modeli per rola (Opus/Sonnet/Haiku 5.5, Mistral, Bielik) na ≥ 30 przypadkach
- [ ] Podłączenie do https://euvsdisinfo.eu/about/#aaad5def9c9014028bbdba28e341feb7


- Wróbel
- jed
- posiadaj filtr na głupie pytania
