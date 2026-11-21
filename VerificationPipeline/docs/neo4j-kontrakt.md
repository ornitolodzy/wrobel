# Kontrakt danych: graf wiedzy (Neo4j) → Verification Pipeline

Pipeline weryfikacji **tylko czyta** graf (połączenie read-only). Ten dokument opisuje, czego potrzebuje.
Jeśli wasz schemat jest inny, nie musicie go zmieniać: wystarczy podać własne zapytania Cypher
w `retrieval.neo4j.queries` (conf), byle zwracały kolumny opisane niżej.

## Proponowany schemat
```
(:Source   {publisher, type})                      type: primary | factcheck | secondary
(:Document {id, url, title, published_at, fetched_at, dup_group, rating?})-[:PUBLISHED_BY]->(:Source)
(:Chunk    {id, text, position, section?, embedding})-[:PART_OF]->(:Document)
(:Document)-[:CITES]->(:Document)                  fact-checker -> materiał, który sprawdzał
(:CorpusVersion {id, embedding_model})              opcjonalnie: wersja i model embeddingów
```
| Pole | Wymagane | Znaczenie |
|---|---|---|
| `Chunk.text` | tak | fragment tekstu źródła, **dosłownie** (z niego weryfikujemy cytaty) |
| `Chunk.embedding` | tak | wektor modelu `bge-m3` (1024); ten sam model liczy zapytania |
| `Chunk.section` | nie | `tresc` (domyślnie) / `naglowek` / `ocena` |
| `Document.url`, `title` | tak | link i tytuł pokazywane w odpowiedzi |
| `Document.fetched_at` | tak | kiedy materiał pobrano do bazy („w bazie źródeł od …”) |
| `Document.published_at` | nie | data publikacji (ostrzeżenie o materiale archiwalnym) |
| `Document.dup_group` | nie | ta sama wartość dla przedruków: nie liczą się jako niezależne potwierdzenia |
| `Document.rating` | nie | dla fact-checków: etykieta werdyktu dokładnie jak na stronie („Fałsz”) |
| `Source.type` | tak | waga źródła w polityce werdyktu; nieznany wydawca → `web` (nie rozstrzyga sam) |
| `CITES` | nie | łańcuch źródeł: fact-checker → źródło pierwotne (nagłówek w odpowiedzi) |
| `CorpusVersion` | nie | jeśli jest, sprawdzamy zgodność modelu embeddingów i zapisujemy wersję w trace |

## Indeksy
```cypher
CREATE VECTOR INDEX chunk_embedding IF NOT EXISTS FOR (c:Chunk) ON (c.embedding)
  OPTIONS {indexConfig: {`vector.dimensions`: 1024, `vector.similarity_function`: 'cosine'}};
CREATE FULLTEXT INDEX chunk_text IF NOT EXISTS FOR (c:Chunk) ON EACH [c.text];
```
Nazwy indeksów są w configu (`vector_index`, `fulltext_index`).

## Zapytania i wymagane kolumny
`dense` i `sparse` muszą zwracać: `chunk_id, text, section, position, doc_id, url, title, published_at,
fetched_at, dup_group, publisher, source_type, score` (malejąco po `score`).
Parametry: `dense` dostaje `$vector_index, $k, $qvec`; `sparse` dostaje `$fulltext_index, $lucene, $k`.
`rating` dostaje `$doc_id` i zwraca `rating`. `cites` dostaje `$doc_id, $limit` i zwraca
`doc_id, url, title, published_at, fetched_at, dup_group, publisher, source_type`.
`corpus_version` zwraca `id, embedding_model`. Domyślne zapytania: `src/prawdowid/retrieval/neo4j.py`.

## Dostęp
`NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD` (zmienne środowiskowe); wystarczy użytkownik tylko do odczytu.
