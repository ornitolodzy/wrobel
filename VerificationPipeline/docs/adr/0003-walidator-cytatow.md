# ADR 0003: Walidator cytatów, rodzaje cytatów i łańcuch źródeł

Status: przyjęte · 2026-11-21 · diagram: `hacknation-llm-wiki/Excalidraw/walidator-cytatow.excalidraw.md`

## Kontekst
Odpowiedź ma wskazywać, na jakich źródłach opiera się werdykt, np.:

> Nie. Demagog ocenił twierdzenie o usunięciu depeszy jako fałsz: depesza była nadal dostępna w niezmienionej formie. [1]
> Źródło pierwotne: PAP „<nagłówek>” [2], dostępne w chwili sprawdzenia. To potwierdza dostępność materiału, a nie prawdziwość jego treści.

Dawny walidator (rapidfuzz ≥ 90) przepuszczał cytat „MSZ” i prawie przepuszczał odwróconą negację
(„pochodzi” vs „nie pochodzi” = 88,9). Nie odróżniał też oceny fact-checkera od zwykłego zdania.

## Decyzja

### Poziom 1: cytat musi być w źródle dosłownie
- Sprawdzamy w tekście, który model dostał (`Evidence.text`). Bez bazy grafowej (dane pochodne,
  krawędzie z LLM to interpretacja) i bez ponownego pobierania strony (mogła się zmienić, paywall, injection).
- Normalizacja: małe litery, cudzysłowy i myślniki ujednolicone, interpunkcja usunięta, białe znaki scalone.
- Dopasowanie dokładne, na granicach słów; „…”, „[...]”, „(…)” dzielą cytat na segmenty, które muszą wystąpić w kolejności.
- Cytat z treści: co najmniej 4 słowa i 20 znaków (`citations.min_words`, `min_chars`).
- Fuzzy (rapidfuzz) tylko jako diagnostyka w trace; nigdy nie akceptuje cytatu.

### Poziom 2: rodzaj cytatu określa, co wolno powiedzieć
| Rodzaj | Skąd | Co uprawnia powiedzieć |
|---|---|---|
| `ocena` | etykieta fact-checkera znaleziona na stronie („Fałsz”) | „X ocenił … jako fałsz” |
| `tresc` | zdanie źródła | słowa wydawcy, z atrybucją |
| `naglowek` | tytuł pobranej strony | istnienie i dokładne brzmienie tytułu |
| (obserwacja) | HTTP 200 i data pobrania | „dostępne w chwili sprawdzenia”, nic więcej |

- Etykieta oceny musi być równa etykiecie na stronie (bez progu długości).
- Zdanie wyjaśnienia ze słowem „ocenił/ocena” bez cytatu rodzaju `ocena` → wyjaśnienie odrzucone, zostaje szablon.
- Dostępność ≠ treść: „w niezmienionej formie” tylko jako słowa źródła. Sami nie porównujemy wersji (archiwum).

### Poziom 3: łańcuch źródeł
- Ze strony fact-checkera bierzemy linki wychodzące do innych domen, pasujące do twierdzenia (max `web_fetch.follow_links`).
  Nigdy wyniki wyszukiwarki, nawigacja ani media społecznościowe.
- Podlinkowany materiał jest pobierany; jego nagłówek pochodzi dosłownie ze strony i trafia do źródeł
  z rolą `podlinkowane` i `via` = numer fact-checkera. Nie wpływa na werdykt.
- Gdy pobranie się nie uda, nagłówka nie przytaczamy (ani z pamięci, ani z opisu w wyszukiwarce).

### Odpowiedź
Pierwsze słowo wynika z werdyktu (`Verdict.lead`):

| Werdykt | Pytanie | Stwierdzenie |
|---|---|---|
| potwierdzone | Tak. | Prawda. |
| falszywe | Nie. | Fałsz. |
| wprowadzajace_w_blad | Wprowadzające w błąd. | |
| brak_dowodow, brak źródeł | Nie znaleziono informacji. | |
| brak_dowodow, źródła słabe/sprzeczne | Nie da się tego jednoznacznie potwierdzić. | |
| nie_podlega_weryfikacji | Niemożliwe do weryfikacji. | |

Pytanie jest zamieniane na twierdzenie z zachowaniem przeczenia („Czy X nie zrobił…?” → „X nie zrobił…”),
więc Tak/Nie odpowiada wprost na pytanie.

## Konsekwencje
- (+) Każde zdanie odpowiedzi da się wskazać w źródle i ma określony zasięg (ocena / treść / nagłówek / dostępność).
- (+) Znane luki zamknięte testami (`tests/test_citations.py`, `tests/test_source_chain.py`).
- (−) Dokładne dopasowanie odrzuci cytaty z drobnymi różnicami (literówka modelu). To celowe: lepiej
  „Nie da się potwierdzić” niż źle przypisany cytat. Liczba odrzuceń jest w trace (miernik jakości modelu).
- (−) Wykrywanie etykiet ocen opiera się na słowniku etykiet polskich fact-checkerów (`RATING_LABELS`).
