- [x] Podpięcie Modelu AI, zastanowienie się jaki to model → `docs/adr/0001-modele-ai.md`, `src/prawdowid/llm/`
- [x] Przeanalizuj jakie bazy danych potrzebujemy: relacyjna, vectorowa, grafowa do zbudowania pełnoprawnego systemu RAG, odpowiedz na pytanie czy powinna być to odpowiedzialność tego REPO → `docs/adr/0002-bazy-danych.md`
- [ ] zastanów się w jaki sposób ma działać Walidator cytatów
- [ ] Adapter `Retriever` dla Postgresa (pgvector + tsvector, RRF, bge-m3 + reranker)
- [ ] Graf etap 1: spaCy NER w `ingestion` → `entity`/`mention`, MinHash → `document_relation`; krok `graph_expand` w retrieval
- [ ] Graf etap 2: relacje argumentacyjne `claim_relation` (LLM offline, cytaty przez walidator)
- [ ] Eval: porównanie modeli per rola (Opus/Sonnet/Haiku 5.5, Mistral, Bielik) na ≥ 30 przypadkach

- Wróbel
- jed
- posiadaj filtr na głupie pytania

i