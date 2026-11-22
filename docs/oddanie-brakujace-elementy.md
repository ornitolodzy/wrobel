# Oddanie projektu PRAWDOWID — brakujące elementy (TODO)

Źródła wymagań: `hacknation-llm-wiki/raw/MSZ - Pełna treść zadania konkursowego.pdf` (dalej: **Zadanie**, numer sekcji)
oraz `MSZ - Regulamin zadania konkursowego.pdf` (dalej: **Regulamin**). Stan repo: gałąź `feat/upgraded-rag`, 2026-11-22.

Legenda: ✅ jest · 🟡 częściowo · ❌ brak. Odniesienia `P0-x`/`P1-x` → [przeglad-architektury-todo.md](przeglad-architektury-todo.md).

> **Warunek brzegowy (Zadanie §8).** Bez niego premiowane funkcje dostają 0 punktów:
> - działanie end-to-end bez ręcznej ingerencji twórców;
> - brak sfabrykowanych źródeł;
> - poprawne wstrzymanie się przy braku dowodów;
> - 2 działające kanały.
>
> Te wymagania są w sekcjach B i C. Mają najwyższy priorytet.

---

## A. Wymagania formalne zgłoszenia (Zadanie §4)

- [ ] ❌ **Nazwa zespołu, tytuł i opis rozwiązania** (≤ 4000 znaków).
- [ ] ❌ **Prezentacja PDF, maks. 12 slajdów.** Obowiązkowe treści: problem, użytkownicy, architektura, demo, wyniki testów, ryzyka, dalsze kroki.
- [ ] ❌ **Film demonstracyjny ≤ 3 min** pod linkiem. Pokazać:
  - [ ] WWW;
  - [ ] Discord;
  - [ ] wstrzymanie się;
  - [ ] cytat;
  - [ ] atak prompt injection;
  - [ ] OCR.
- [ ] 🟡 **Link do repo + instrukcja uruchomienia.** `README.md` jest, ale opiera się na tunelu SSH do Bieliku albo na kluczu, którego jury nie ma (patrz C1).
- [ ] 🟡 **Krótki opis architektury, przepływu danych, użytych modeli, dostawców i ograniczeń.** Materiał jest rozproszony: `README.md`, `VerificationPipeline/docs/adr/0001-0005`. Zebrać w jeden krótki plik (np. `docs/architektura.md`), w tym:
  - [ ] dostawcy i miejsce przetwarzania danych (EOG / poza EOG);
  - [ ] zachowanie przy awarii każdego dostawcy.
- [ ] 🟡 **Opis źródeł: licencje, kwalifikacja, aktualizacja.** `PageScrapper/SOURCES.md` istnieje (po angielsku). Do zrobienia:
  - [ ] wersja PL albo streszczenie;
  - [ ] uzgodnić liczby z seedem (253 dok., 34 źródła vs „10 domen, ~280 dok.”).
- [ ] ❌ **Wyniki testów według §6 + dane do odtworzenia** (patrz B).
- [ ] ❌ **Lista najważniejszych ryzyk** (np. `docs/ryzyka.md`). Wymagane minimum:
  - [ ] halucynacje;
  - [ ] stronniczość źródeł;
  - [ ] prywatność;
  - [ ] prompt injection;
  - [ ] dostępność;
  - [ ] nadużycia.
  - Materiał na nadużycia i bezpieczeństwo jest w P0-1…P0-3.
- [ ] ❌ **Brak kluczy API, danych osobowych i sekretów w repo i materiałach demo:**
  - [ ] przejrzeć seed `db/seed/10-prawdowid.sql.gz` pod kątem danych osobowych (komunikaty policji zawierają nazwiska/miejsca);
  - [ ] usunąć `change-me-admin` z README i compose (P0-1);
  - [ ] sprawdzić, czy na filmie i slajdach nie widać `.env` ani tokenów.
- [ ] 🟡 Opcjonalne:
  - [ ] Docker ✅;
  - [ ] OpenAPI ✅ (FastAPI `/docs`), wyeksportować do pliku `openapi.json`;
  - [ ] makiety kanałów;
  - [ ] dashboard jakości.

## B. Testy i metryki (Zadanie §6)

- [ ] ❌ **Własny zestaw referencyjny ≥ 30 przypadków** pokrywający scenariusze A–J.
  - Dziś w `VerificationPipeline/tests/eval/` jest 10 przypadków werdyktów: `cases.jsonl` (3) i `cases_pagescrapper.jsonl` (7).
  - Brakujące scenariusze:
    - [ ] B — kontekst / zły czas lub miejsce;
    - [ ] C — sprzeczność źródeł;
    - [ ] E — atak (strona instruuje model);
    - [ ] F — zrzut ekranu z datą;
    - [ ] G — spójność WWW vs Discord;
    - [ ] H — pętla informacji;
    - [ ] I — URL lub incydent;
    - [ ] J — brak klucza / limit / awaria OCR;
    - [ ] treści częściowo prawdziwe;
    - [ ] informacje nieaktualne.
- [ ] 🟡 **Metryki obowiązkowe.** Dziś liczone są `macro_f1`, cytowania i mediana czasu (`pipelines/eval/nodes.py`). Brakuje:
  - [ ] **poprawność cytowania** — czy fragment rzeczywiście wspiera tezę; dziś liczona jest tylko zgodność dosłowna. Potrzebna ręczna albo LLM-owa ocena próbki (cel ≥ 85%);
  - [ ] **wstrzymanie się** — odsetek przypadków bez werdyktu przy braku dowodów (≥ 80%);
  - [ ] **odporność** — odsetek odpartych prób injection (≥ 90%);
  - [ ] **koszt 100 testów** — deklarowany i zmierzony. `cost_usd` jest już w `VerificationPipeline/src/prawdowid/api/gateway.py:133`; trzeba go zsumować w raporcie;
  - [ ] **end-to-end** — 100% scenariuszy uruchomionych przez gateway, nie bezpośrednio przez `verify()`;
  - [ ] **spójność kanałów** — 100% (test: to samo zgłoszenie przez `X-Channel: web` i `discord`);
  - [ ] **pętla informacji** — tylko jeśli wdrożymy E1.
- [ ] ❌ **Mediana czasu ≤ 30 s dla tekstu/URL.** README deklaruje 30–60 s z Bielikiem. Zmierzyć; ewentualnie:
  - [ ] reranker na GPU;
  - [ ] równoległe `compose` (P1-10).
- [ ] ❌ **Przebieg eval na docelowej konfiguracji** (model, embeddingi, reranker), z commitowanym raportem JSON i komendą do odtworzenia.
  - [ ] Poprawić błędy metryk z P2/Eval: hit-rate nazywany „recall@k”, kalibracja in-sample.
- [ ] ❌ **Jawna lista przypadków, których system nie obsługuje.** Wymagana w §6: np. audio, wideo, reverse image search, języki inne niż PL.
- [ ] Test na „brak zakodowanych przykładów”: wynik nie może zależeć od odpowiedzi testowej wpisanej do promptu. Sprawdzić prompty i progi kalibrowane na zbiorze testowym.

## C. Uruchomienie przez jury i warunek brzegowy

- [ ] ❌ **C1. Ścieżka dla jury z prawdziwym modelem bez naszego tunelu SSH.**
  - Problem: domyślnie jest `provider: mock`, więc każde twierdzenie kończy się `brak_dowodow`, a warunek brzegowy nie jest spełniony.
  - Do zrobienia:
    - [ ] opisać wariant „jury wkleja własny klucz” (np. Mistral, dostawca z EOG) albo udostępnić publiczne demo;
    - [ ] klucze nie mogą trafić do repo.
- [ ] ❌ **C2. Seed bge-m3 nie może zostać nadpisany wektorami mock przy pierwszym starcie** (P1-3). Inaczej wyszukiwanie jest słabe od pierwszego uruchomienia.
- [ ] ❌ **C3. Limit 30/min wspólny dla wszystkich** (P1-9). Jury puszczające 30+ przypadków przez WWW zablokuje samo siebie (każde sprawdzenie + automatyczne `/verify/live`).
- [ ] ❌ **C4. Awaria ≠ werdykt** (§2, §5 „Fakty”, scenariusz J). Błąd wyszukiwania dziś daje `brak_dowodow` ze statusem ok (P1-1). Dla jury wygląda to jak wstrzymanie się, a jest naruszeniem wymogu.
- [ ] ❌ **C5. Timeouty** (P1-10). nginx 504 przy URL/live → „ręczna ingerencja” w oczach jury.
- [ ] 🟡 **C6. Drugi kanał (Discord)** ✅ istnieje. Do zrobienia:
  - [ ] serwer testowy i link zaproszenia dla jury;
  - [ ] `DISCORD_BOT_URL` w `Frontend/src/app/core/links.ts:9` to placeholder;
  - [ ] context menu traci odpowiedź przy dużym obrazie (P2);
  - [ ] bot deklaruje formaty, które gateway odrzuca (P2).
- [ ] 🟡 **C7. Test „świeżej maszyny”:** `git clone` → `make start` → 10 przypadków bez naszej pomocy. Spisać czas buildu i wymagania (RAM, dysk 6–8 GB).
- [ ] ❌ **C8. Szacunki** (§5 „Założenia wdrożeniowe”):
  - [ ] koszt 1000 zapytań;
  - [ ] koszt utrzymania kanału (Discord: 0 zł);
  - [ ] zachowanie przy zwiększonym ruchu (wspólny limit, jeden worker, CPU reranker);
  - [ ] informacja, że usługa jest bezpłatna dla użytkownika.

## D. Braki w wymaganiach technicznych (Zadanie §2, §3 MVP, §5)

- [ ] ❌ **Czytelna informacja o ograniczeniach w WWW.** `limitations`, `summary` i `mock` nie są pokazywane (P1-12). To wymóg MVP („czytelna informacja o ograniczeniach”).
- [ ] ❌ **Możliwość usunięcia sesji demonstracyjnej z poziomu UI** (§5 Prywatność). API `DELETE /api/v1/checks/{id}` jest, ale:
  - [ ] w WWW i na Discordzie nie ma przycisku;
  - [ ] usuwanie nie obejmuje audytu (P2).
- [ ] 🟡 **Odnotowanie próby prompt injection** (scenariusz E: „zignorowanie instrukcji i odnotowanie zdarzenia”).
  - Dziś flaga trafia tylko do trace pipeline'u (`VerificationPipeline/src/prawdowid/llm/tasks.py:132-133`, `nodes/normalize.py:41`).
  - [ ] osobne zdarzenie w `audit_events` gatewaya;
  - [ ] uwzględnić flagę w werdykcie (P2).
- [ ] 🟡 **Maskowanie PII** ✅ jest, ale niszczy liczby w twierdzeniach (P1-6), co obniża trafność.
- [ ] 🟡 **Odporność na manipulacje usługą:** zatruwanie korpusu przez `/ingest`, SSRF, jawny klucz admina (P0-1…P0-3). §5 „Bezpieczeństwo” wymaga ograniczenia narzędzi i ochrony sekretów.
- [ ] ❌ **Baza incydentów od organizatora** (§7 „Baza incydentów JSON/CSV”, scenariusz I).
  - Adapter `baza-incydentow` istnieje, ale `PageScrapper/imports/` jest pusty.
  - [ ] wczytać plik organizatora;
  - [ ] pokazywać dopasowanie jako **kontekst**, bez werdyktu na podstawie domeny.
- [ ] ❌ **Neutralne ostrzeżenie o statusie źródła** (np. objęcie sankcjami UE), oddzielone od werdyktu (§5 „Ograniczenia i zakazy”).
- [ ] 🟡 **Ostrzeżenie o materiale archiwalnym** ✅ jest (`policy.py:201-203`). Sprawdzić, czy jest widoczne w WWW (zależy od wyświetlania `limitations`).
- [ ] 🟡 **Obserwowalność** (czas, modele, błędy wyszukiwania, wstrzymania, koszt) ✅ w audycie/trace. Do zrobienia:
  - [ ] błędy wyszukiwania są dziś połykane (P1-1);
  - [ ] dodać prosty widok lub eksport dla admina.
- [ ] 🟡 **Rozróżnienie fakt / opinia, wskazanie brakującego kontekstu** (scenariusz B): `wprowadzajace_w_blad` istnieje. Brak przypadków testowych dla B (patrz B).
- [ ] 🟡 **Sprzeczność źródeł pokazana jawnie** (scenariusz C): polityka zwraca mylący powód (P2, kolejność polityki). Dodać czytelny komunikat „źródła są sprzeczne”.
- [ ] 🟡 **Przedruki ≠ niezależne potwierdzenia** (§2 „liczba niezależnych potwierdzeń”) — P1-5.

## E. Funkcje premiowane (Zadanie §3) — dopiero po spełnieniu B–D

Kolejność według stosunku punktów do wysiłku:

- [ ] ❌ **E1. Zamknięta pętla informacji** (scenariusz H, miernik „Pętla informacji”, §2: powiadomienie moderatora).
  - Tabela `case` istnieje w schemacie pipeline'u, ale nic jej nie zapisuje.
  - Wymagania:
    - [ ] utworzenie lub aktualizacja **jednej** sprawy dla podobnych zgłoszeń (grupowanie);
    - [ ] rozróżnienie „rekomendacja przekazania” od „przekazano”;
    - [ ] alert dla admina o powtarzających się pytaniach bez odpowiedzi;
    - [ ] brak fałszywego komunikatu o przekazaniu.
- [ ] ❌ **E2. Eksport danych CSV/JSON** spraw i sprawdzeń (admin).
  - Opcjonalnie: mapowanie na DISARM / STIX dla OpenCTI.
- [ ] 🟡 **E3. Łańcuch dowodowy / wizualizacja dowodów:** ContextGraph (Neo4j) istnieje. Do zrobienia:
  - [ ] pokazać w UI graf „twierdzenie → dowody za / przeciw / kontekst”;
  - [ ] pipeline nie czyta jeszcze CheckedClaim (P1-8).
- [ ] 🟡 **E4. Multimodalność:** OCR ✅. Brak:
  - [ ] transkrypcji audio;
  - [ ] metadanych EXIF;
  - [ ] porównywania obrazów / wcześniejszych wystąpień (perceptual hash lokalnie).
  - Uwaga, §5: **nie wolno deklarować pełnego reverse image search** bez działającego API.
- [ ] 🟡 **E5. Otwarty / wymienny katalog źródeł z zasadami** ✅ `sources.yaml` + `SOURCES.md`. Brakuje udokumentowanej procedury wykluczania na podstawie oceny (nie domeny).
- [ ] ❌ **E6. Wielojęzyczne wyszukiwanie** z odpowiedzią po polsku: bge-m3 jest wielojęzyczny, ale nieprzetestowane i nieopisane.
- [ ] 🟡 **E7. Niezależność od dostawców spoza EOG, audyt, self-hosting:** Bielik + bge-m3 self-hosted ✅, Mistral (EU) możliwy. Opisać to jawnie w dokumentacji (kryterium 20%).
- [ ] ❌ **E8. Kontrolowana archiwizacja materiału użytkownika.** Wymaga podstawy prawnej, retencji i procedury usunięcia. Raczej pominąć i napisać, dlaczego.

## F. Regulamin i kwestie prawne

- [ ] ❌ **Prawa do kodu** (Regulamin §4 ust. 1–2): każdy uczestnik musi być twórcą swojego wkładu.
  - [ ] sprawdzić licencje zależności i zasobów: fonty `Frontend/public/fonts/`, modele (Bielik — licencja, bge-m3, bge-reranker, PaddleOCR), dane w seedzie (prawa wydawców do pełnych tekstów);
  - [ ] przygotować `THIRD_PARTY.md`.
- [ ] ❌ **Pełne teksty artykułów w seedzie a prawo autorskie** (§5 Zadania: „nie wolno archiwizować pełnych treści bez podstawy prawnej, licencji, retencji…”).
  - Seed zawiera pełne treści, np. z `demagog.org.pl` z `ai-train=no`, konkret24.
  - [ ] uzasadnić (cytat prawny, cel wyszukiwania) albo ograniczyć do fragmentów;
  - [ ] opisać retencję korpusu.
- [ ] Przygotować się do podpisania umowy przeniesienia praw przez **wszystkich** członków zespołu w 7 dni (§3 ust. 10, §4 ust. 8) i oświadczenia RODO.
- [ ] ❌ **Rozważyć dodanie pliku LICENSE.** Uwaga: przy wygranej prawa majątkowe przechodzą na MSZ. Uzgodnić w zespole przed dodaniem licencji open source.

## G. Kryteria oceny (Zadanie §8) — co zaprezentować

| Kryterium | Waga | Główne braki |
|---|---|---|
| Jakość weryfikacji i dowodów | 30% | B (zestaw ≥ 30, metryki), P1-4/P1-5/P1-6 |
| Bezpieczeństwo, odpowiedzialność, transparentność | 20% | P0-1…P0-3, D (injection event, usuwanie), ryzyka (A) |
| Potencjał wdrożeniowy i skalowalność | 20% | C8 (koszty, ruch), E7 (EOG), opis migracji dostawców |
| Użyteczność i dostępność | 15% | ograniczenia w UI, spójność kanałów, kontrast/WCAG (niezweryfikowane) |
| Innowacyjność i kompletność | 15% | E1 pętla, E3 wizualizacja, jakość filmu |
