# Model grafu

Graf ma dwie warstwy. Warstwa 1 trzyma **wszystko**, co przyszło ze scrapera, dosłownie i z metadanymi.
Warstwa 2 to indeks zdarzeń („ktoś zrobił / coś się wydarzyło”) zbudowany na warstwie 1. Nigdy jej nie zastępuje:
każde zdarzenie wskazuje fragment, z którego pochodzi, z dosłownym cytatem.

Schemat warstwy 1 jest zgodny z kontraktem VerificationPipeline (`docs/neo4j-kontrakt.md` w tamtym repo),
więc pipeline czyta graf swoimi domyślnymi zapytaniami. Sprawdza to `tests/test_contract.py`.

```
WARSTWA 1: dokumenty (deterministycznie, bez LLM)
(:Chunk)-[:PART_OF]->(:Document)-[:PUBLISHED_BY]->(:Source)
(:Chunk)-[:NEXT]->(:Chunk)
(:Chunk)-[:MENTIONS]->(:Entity)-[:CO_OCCURS {weight}]->(:Entity)
(:Document)-[:REPRINTS|CITES|CORRECTS]->(:Document)
(:Document)-[:HAS_IMAGE]->(:Image)-[:HAS_TEXT]->(:Chunk {section:'ocr'})-[:PART_OF]->(:Document)
(:Chunk)-[:SUPPORTS|CONTRADICTS {quote}]->(:Chunk)
(:CorpusVersion {id, embedding_model, consistent, scraper_synced_at, scraper_updated_at_max})

TWIERDZENIA SPRAWDZONE (z ClaimReview fact-checkera, bez LLM; claims.py)
(:Document)-[:CHECKS]->(:CheckedClaim {text, rating, rating_raw, embedding})
(:CheckedClaim)-[:ATTRIBUTED_TO {declared_by:'fact_checker'}]->(:Person {name, kind})
(:CheckedClaim)-[:APPEARED_IN]->(:Appearance {url, domain})

WARSTWA 2: zdarzenia (LLM, wsadowo, tylko z cytatem)
(:Entity)-[:ACTOR {role: agent|target|other, quote}]->(:Event)-[:EVIDENCED_BY {quote}]->(:Chunk)
(:Event)-[:ON_DATE {inferred}]->(:Entity {type:'DATE'})
(:Event)-[:IN_PLACE]->(:Entity)

ODCZYT
(:Claim)-[:ABOUT]->(:Entity)
(:Claim)-[:CONTEXT {score, rank, path}]->(:Chunk)
```

## Co jest zapisywane (nic nie ginie)

| Węzeł | Klucz | Pola |
|---|---|---|
| `Source` | `id` = hash(domena + wydawca) | `publisher`, `domain`, `type` (primary / factcheck / secondary / web), `catalog_type` (typ ze scrapera), `catalog_ids`, `license` |
| `Document` | `id` = sha1(kanoniczny URL)[:16] | `scraper_revision` (rewizja z PageScrappera, do rekoncyliacji), `url`, `title`, `published_at`, `fetched_at`, `dup_group`, `rating`, `language`, `text_hash` (do przedruków), `scraper_hash`, `source_type`, `catalog_type`, `catalog_id`, `license`; `stub: true`, gdy znamy tylko link |
| `Chunk` | `<dokument>:<n>`, `:h` nagłówek, `:r` ocena | `text` (dosłownie), `section` (tresc / naglowek / ocena / ocr), `position`, `embedding` (bge-m3, 1024), `embedding_model`, `embedded_at` |
| `Entity` | `key` | `name`, `type`; daty: `date:RRRR-MM-DD`, `year`, `month_day` |
| `Event` | hash(fragment + akcja + data) | `action`, `kind` (fact / statement / plan / opinion), `date_text`, `date_inferred`, `model` |
| `Image` | `img-` + sha1(image_id) | `path`, `url` |
| `Claim` | sha1(tekst)[:16] | `text` (zapis odczytanego kontekstu) |
| `CheckedClaim` | sha1(dokument + tekst)[:16] | `text` (twierdzenie sprawdzone przez fact-checkera), `rating_raw` (etykieta ze strony), `rating` (falsz / manipulacja / czesciowa_prawda / nieweryfikowalne / prawda / satyra / inne), `claim_date`, `review_date`, `origin` (claimreview / google_factcheck / llm), `claimant_text`, `claimant_kind`, `embedding` (bge-m3 na samym twierdzeniu) |
| `Person` | sha1(nazwa)[:16] | `name`, `kind` (person / account / organization); autor przypisany przez fact-checkera |
| `Appearance` | sha1(url)[:16] | `url`, `domain`; gdzie twierdzenie krążyło (wpis, reel, archiwum) |

**Twierdzenia sprawdzone.** `CheckedClaim` to co innego niż `Claim`: `Claim` zapisuje pytania użytkowników
(`/context`), a `CheckedClaim` to twierdzenie, które fact-checker ocenił. PageScrapper czyta je ze znaczników
schema.org `ClaimReview` (Demagog, FakeNews.pl; Konkret24 i EUvsDisinfo ich nie publikują) i z Google Fact Check API,
i wysyła w polu `claims`. Embedding liczony jest na samym tekście twierdzenia, osobny indeks
`checked_claim_embedding`, żeby krótki wpis z sieci trafiał w krótkie twierdzenie, a nie w chunk 1200 znaków.
Gdy dokument nie ma własnej oceny, `Document.rating` bierze ją z ClaimReview.

**Przypisanie autora tylko od fact-checkera.** `ATTRIBUTED_TO` powstaje wyłącznie, gdy autora podał fact-checker
(`origin` claimreview / google_factcheck). Twierdzenia wyciągnięte przez LLM (`origin: llm`) nigdy nie dostają tej
relacji. Ogólnikowi autorzy („Użytkownicy Facebooka”, „Wiele źródeł”, „Konto w mediach społecznościowych”) też nie:
zostają jako `claimant_text` z `claimant_kind: generic`. Ponowny ingest dokumentu zastępuje jego twierdzenia
(znikają nieaktualne, osierocone `Person` / `Appearance` są usuwane).

**Źródło = domena + wydawca.** RCB i MON publikują na tej samej domenie www.gov.pl, a muszą być osobnymi
źródłami, bo pipeline pokazuje wydawcę w odpowiedzi.

**Typy źródeł.** Scraper używa `official / fact_check / disinfo_db`, a polityka werdyktu pipeline'u
`primary / factcheck / secondary / web`. Mapowanie jest w `config.py` (`official → primary`,
`fact_check → factcheck`, `disinfo_db → secondary`, nieznany → `web`). Oryginał zostaje w `catalog_type`.

**Daty z rokiem.** „20.11.2026” daje `date:2026-11-20`. „20.11” bez roku bierze rok z daty publikacji
dokumentu i ma `year_inferred = true` na relacji `MENTIONS`. W pytaniu bez roku („co się stało 20.11”)
klucz to `date:--11-20` (zapis ISO), który przy odczycie pasuje do tego dnia w **każdym** roku. Dzięki temu
kontekst pokazuje, że jedno źródło mówi o 2026, a inne o 2025 (scenariusz B: „prawdziwy materiał w złym czasie”).

**Przedruki.** Ten sam tekst (`text_hash`) w kilku dokumentach: `REPRINTS` wskazuje najstarszy, a wszystkie mają
wspólny `dup_group`. Pipeline nie liczy ich jako niezależnych potwierdzeń.

## Relacje

Każda relacja ma `method` i `created_at`. Relacje interpretacyjne (SUPPORTS / CONTRADICTS, CORRECTS z `/relations`,
wszystko z warstwy 2) mają `quote`. Cytat musi występować dosłownie w tekście węzła źródłowego, inaczej
API zwraca 422, a zdarzenie z LLM jest odrzucane. Krawędź od LLM to interpretacja, nie fakt (ADR 0002).

| Relacja | Metoda | Znaczenie przy odczycie kontekstu |
|---|---|---|
| `PUBLISHED_BY`, `PART_OF` | ingest | metadane i przynależność fragmentu |
| `NEXT` | ingest | sąsiednie zdania (waga 0.6) |
| `HAS_IMAGE`, `HAS_TEXT` | ingest | tekst z obrazów dokumentu (0.5) |
| `MENTIONS` | regex | fragmenty o tych samych encjach (0.5) |
| `CO_OCCURS` | ingest | encje powiązane z encjami twierdzenia; `weight` = liczba dokumentów |
| `CITES` | link | źródło pierwotne cytowane przez dokument (0.4); pipeline: łańcuch fact-checker → materiał |
| `CORRECTS` | declared / manual | sprostowanie, **zawsze dociągane** (0.9) |
| `SUPPORTS`, `CONTRADICTS` | llm / manual | argumentacja, **zawsze dociągana** (0.9) |
| `REPRINTS` | text_hash | przedruk wskazuje oryginał; w kontekście zwijany (`dup_group`) |
| `ACTOR`, `ON_DATE`, `IN_PLACE`, `EVIDENCED_BY` | llm | warstwa 2; zdarzenia z fragmentów kontekstu są zwracane w `/context` jako `events` |
| `CHECKS` | claimreview / google_factcheck / llm | fact-checker → twierdzenie, które ocenił |
| `ATTRIBUTED_TO` | claimreview / google_factcheck | twierdzenie → autor podany przez fact-checkera (`declared_by: fact_checker`) |
| `APPEARED_IN` | claimreview | twierdzenie → miejsce, gdzie krążyło |

## Odczyt kontekstu (`POST /context`)

1. **Seed:** fulltext (`chunk_text`, prefiksy rdzeni) oraz fragmenty wspominające encje twierdzenia
   (daty bez roku pasują do każdego roku).
2. **Ekspansja** do `hops` kroków po relacjach z tabeli. Wynik = wynik rodzica × waga × 0.85.
3. **Ranking:** top `limit`, plus do `limit/2` elementów z flagami `corrected`, `correction`, `contradicted`, `contradicts`.
4. **Wyjaśnienie:** każdy element ma `path`; przedruki są zwinięte (`reprinted_at:<url>`); `events` to zdarzenia
   z warstwy 2 udokumentowane przez zwrócone fragmenty (kto, co, kiedy, gdzie + cytat).

VerificationPipeline nie używa `/context`: czyta Neo4j bezpośrednio (wektor + fulltext + RRF, ADR 0005).
`/context` służy do wizualizacji łańcucha dowodowego i do eksploracji.

## Przydatne zapytania (Neo4j Browser, http://localhost:7474)

```cypher
// dokumenty jednego źródła z datami
MATCH (d:Document)-[:PUBLISHED_BY]->(s:Source {publisher: 'Rządowe Centrum Bezpieczeństwa'})
RETURN d.title, d.published_at, d.fetched_at ORDER BY d.published_at DESC;

// co wydarzyło się 20 listopada (każdy rok)
MATCH (e:Event)-[:ON_DATE]->(t:Entity {month_day: '11-20'}), (e)-[ev:EVIDENCED_BY]->(c)-[:PART_OF]->(d)
RETURN t.key, e.action, ev.quote, d.url;

// kto co zrobił
MATCH (a:Entity)-[r:ACTOR {role: 'agent'}]->(e:Event) RETURN a.name, e.action, r.quote LIMIT 50;

// twierdzenia sprawdzone: ocena, autor (tylko podany przez fact-checkera) i gdzie krążyły
MATCH (d:Document)-[:CHECKS]->(c:CheckedClaim)
OPTIONAL MATCH (c)-[:ATTRIBUTED_TO]->(p:Person)
OPTIONAL MATCH (c)-[:APPEARED_IN]->(a:Appearance)
RETURN d.url, c.text, c.rating, coalesce(p.name, c.claimant_text) AS autor, collect(a.domain) AS gdzie;

// przedruki i sprostowania
MATCH (c:Document)-[:REPRINTS]->(o:Document) RETURN o.url AS oryginal, collect(c.url) AS przedruki;
MATCH (a:Document)-[r:CORRECTS]->(b:Document) RETURN a.url, b.url, r.method;
```
