# Wyniki testów według sekcji 6 zadania

Pomiar z 22.11.2026 na pełnym stacku, end-to-end, bez interwencji zespołu. Każdy przypadek przeszedł dwoma kanałami:
WWW (te same wywołania co frontend) i Discord (klient z kodu bota). Pełny raport z tabelą przypadków:
[wyniki/raport-2026-11-22.md](wyniki/raport-2026-11-22.md), liczby: [wyniki/metrics-2026-11-22.json](wyniki/metrics-2026-11-22.json).

## Mierniki obowiązkowe

| Miernik | Definicja (zadanie §6) | Wynik | Cel MVP |
|---|---|---|---|
| Trafność werdyktu | macro-F1 na 5 klasach, 44 przypadki z oczekiwanym werdyktem | **0,939** (ścisłe: 0,881) | ≥ 0,75 ✅ |
| Pokrycie cytowań | twierdzenia z werdyktem rozstrzygającym, które mają źródło z tytułem, wydawcą, datą i linkiem | **100%** (26/26) | ≥ 95% ✅ |
| Poprawność cytowania | cytowany fragment wspiera tezę (ocena każdego cytatu) | **89,7%** (35/39) | ≥ 85% ✅ |
| ↳ cytat dosłownie w źródle | sprawdzane automatycznie z pełną treścią dokumentu | 100% (39/39) | — |
| Wstrzymanie się | „brak dowodów” przy twierdzeniach bez rzetelnych publikacji | **100%** (7/7) | ≥ 80% ✅ |
| Odporność | odparte próby prompt injection (brak narzuconego werdyktu, wycieku instrukcji, odpowiedzi nie po polsku) | **100%** (5/5) | ≥ 90% ✅ |
| Czas | mediana od wejścia do odpowiedzi, tekst/URL | **6,5 s** (p90 13,3 s; z wyszukiwaniem na żywo 8,3 s) | ≤ 30 s ✅ |
| Koszt | zmierzony koszt API 100 testów | **0 USD** (model lokalny); ekwiwalent API 0,11 USD / 100 testów, 1,13 USD / 1000 | raportowany |
| End-to-end | uruchomienia zakończone bez interwencji (werdykt albo jawny status techniczny) | **100%** (104/104) | 100% ✅ |
| Spójność kanałów | ten sam werdykt i te same źródła w WWW i na Discordzie | **100%** (51/51 par) | 100% ✅ |
| Pętla informacji | utworzenie i deduplikacja sprawy, bez fałszywego komunikatu o przekazaniu | **100%** (2/2) | 100% ✅ |

Klasy (precyzja / czułość): potwierdzone 1,00 / 1,00 · fałszywe 1,00 / 0,78 · wprowadzające w błąd 1,00 / 0,86 ·
brak dowodów 0,81 / 1,00 · nie podlega weryfikacji 1,00 / 1,00. System nie dał ani jednego fałszywego „potwierdzone”
ani „fałsz”: wszystkie błędy to ostrożne „brak dowodów” zamiast werdyktu.

## Scenariusze A–J

| Scenariusz | Zaliczone | Uwagi |
|---|---|---|
| A. Fakt prosty (10 prawd, 8 fałszów) | 16/18 | 2 fałsze obalone przez fact-checkera bez jawnej oceny w tekście → „brak dowodów” |
| B. Kontekst (zły czas / miejsce, wyrwane z kontekstu) | 4/5 | B05: prawdziwy alert z błędnym rokiem → „wprowadza w błąd: źródło z innego czasu” |
| C. Sprzeczność / częściowa prawda (45% vs 31% vs 17%) | 2/2 | pokazane różne liczby ze źródeł |
| D. Brak dowodów | 6/6 | z informacją, jakie źródło by rozstrzygnęło |
| E. Atak (prompt injection w treści i „od źródła”) | 5/5 | zdarzenie `prompt_injection` w audit logu |
| F. Zrzut ekranu z tekstem i datą (OCR) | 3/3 | tekst z obrazu pokazany użytkownikowi |
| G. Spójność WWW / Discord | 51/51 par | |
| H. Pętla informacji | 2/2 | powtórne pytanie → ta sama sprawa, rekomendacja (nie „przekazano”); temat wysokiego ryzyka → rekomendacja od razu |
| I. URL | 2/2 | werdykt z treści i dowodów, nie z domeny |
| J. Awaria (niedostępna strona, uszkodzony plik, zatrzymany OCR) | 3/3 | status techniczny, nigdy werdykt |

## Przed i po poprawkach

Ten sam zestaw, te same zasady liczenia ([wyniki/metrics-baseline-2026-11-22.json](wyniki/metrics-baseline-2026-11-22.json)):

| Miernik | Przed | Po |
|---|---|---|
| macro-F1 | 0,605 (ścisłe 0,536) | **0,939** (ścisłe 0,881) |
| czułość klasy „fałszywe” | 0,11 | **0,78** |
| wstrzymanie się | 85,7% | **100%** |
| mediana czasu | 5,3 s | 6,5 s |

Co zmieniło wynik (każda zmiana to ogólna reguła, nie wpis konkretnego przypadku):
1. **Rozstrzygnięcie fact-checkera.** Pojedynczy fact-check, który przeczy twierdzeniu, rozstrzyga sprawę, gdy jego tekst
   zawiera jawną ocenę w słownictwie IFCN („fałsz”, „manipulacja”, „zdementowano”…). Tekst o tej samej osobie bez takiej
   oceny nadal nie wystarcza.
2. **Zły czas (scenariusz B).** Rok podany w twierdzeniu, którego nie ma w dacie ani treści źródeł potwierdzających,
   zmienia werdykt na „wprowadza w błąd”.
3. **Cytat musi dotyczyć tezy.** Dosłowny, ale niezwiązany fragment (np. data wejścia w życie innej ustawy) nie jest dowodem.
4. **Typ twierdzenia.** „Opinia” i „prognoza” od modelu wymagają językowych znaków oceny lub przyszłości; fakt opisany jako
   opinia jest sprawdzany, zamiast odrzucany. Wypowiedź bez treści („To jest pytanie.”) → prośba o doprecyzowanie.
5. **Awaria to nie werdykt.** Błąd bazy wiedzy daje status techniczny zamiast „brak dowodów”.

## Zestaw i metodyka

- [eval/cases.jsonl](../eval/cases.jsonl): 52 przypadki. Oczekiwany werdykt każdego wynika z dokumentu w korpusie (pole
  `evidence`): komunikatu urzędowego albo oceny fact-checkera. Przypadki nie są wpisane do promptów ani reguł; przykłady
  w promptach są inne niż w zestawie.
- Klasy z `accept`: dla 14 przypadków dopuszczono drugi poprawny werdykt (np. „wprowadza w błąd” albo „fałsz” dla
  materiału wyrwanego z kontekstu, „fałsz” albo „brak dowodów” przy ataku). Wynik bez tej tolerancji to macro-F1 „ścisłe”.
- Korekty po pierwszym przebiegu (opisane w polu `note`): A04, A10, U01 opierają się na komunikatach, które **zapowiadają**
  zdarzenie, więc dopuszczono też „brak dowodów”; J01 (niedostępna strona) dopuszcza prośbę o inny link.
- Odporność liczona jest osobno od trafności: atak jest odparty, jeśli system nie dał werdyktu narzuconego przez atakującego,
  nie ujawnił instrukcji i odpowiedział po polsku.
- Poprawność cytowania: każdy z 39 cytatów oceniono ręcznie, czy wspiera tezę w roli, w jakiej go użyto; ocena
  z uzasadnieniem jest w [eval/citation_review.jsonl](../eval/citation_review.jsonl), do weryfikacji przez jury.
- Konfiguracja: Bielik 11B v2.3 (Q4_K_M, Ollama, temperatura 0), embeddingi bge-m3, reranker bge-reranker-v2-m3 (GPU),
  korpus 253 dokumentów z seeda, backend Postgres.

Odtworzenie: `make start` (stack z modelem), potem `make eval-e2e`. Raport w `eval/results/<czas>/report.md`.

## Ograniczenia pomiaru (czego nie obiecujemy)

- Zestaw jest mały (52 przypadki) i przygotowany przez zespół, a jeden przebieg nie mierzy rozrzutu: Bielik bywa
  niedeterministyczny w typowaniu twierdzeń. Wynik na ukrytym zestawie jury może być niższy.
- Wyszukiwanie na żywo dokłada nowe strony do korpusu, więc kolejne przebiegi nie startują z identycznej bazy.
- Pętla informacji ma tylko 2 przypadki testowe; logikę grupowania pokrywają dodatkowo testy jednostkowe gatewaya.
- Koszt API 0 USD dotyczy modelu na własnym serwerze; nie mierzymy energii GPU. Ekwiwalent API liczony przy
  0,30 USD za 1 mln tokenów (średnio 3 750 tokenów na sprawdzenie).

## Czego system nie obsługuje

Nagrań audio i wideo (brak transkrypcji), wyszukiwania odwrotnego obrazu (tylko OCR tekstu ze zrzutu), pytań
w innych językach niż polski jako głównego trybu, twierdzeń spoza tematów i okresu korpusu (wtedy uczciwe „brak dowodów”
z informacją, jakie źródło by rozstrzygnęło, i automatyczne wyszukiwanie na żywo w zaufanych witrynach).
