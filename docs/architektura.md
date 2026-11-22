# PRAWDOWID: architektura w skrócie

Diagram: [architektura.html](architektura.html)

## Jaki problem rozwiązujemy

Dezinformacja rozchodzi się szybciej, niż da się ją sprawdzić. Dobry przykład to kampania wymierzona w relacje polsko-czeskie z sierpnia 2026 r.:

1. Pojawiło się sfałszowane pismo rzekomo z MSZ z żądaniami terytorialnymi wobec Czech.
2. Po włamaniu do Radia PiK opublikowano spreparowany artykuł na ten temat.
3. Inne media lokalne go przedrukowały.
4. Doszły konta podszywające się pod dyplomatów i deepfake'i.

Obywatel, dziennikarz czy urzędnik, który trafia na taki materiał, napotyka kilka barier:

- **Sprostowania są rozproszone.** Leżą u fact-checkerów (Demagog, FakeNews.pl, Konkret24, EUvsDisinfo) i na stronach urzędów. Ręczne przeszukanie ich zajmuje czas, w którym fałszywka dalej się rozchodzi.
- **Przedruki udają potwierdzenia.** Pięć portali powtarzających ten sam tekst to wciąż jedno źródło, a nie pięć niezależnych.
- **Fakt miesza się z oceną.** „Rosja jest najbardziej prawdopodobnym autorem” to analityczna ocena PISM, a nie ustalony fakt. Odpowiedź musi to rozróżniać.
- **Zwykły czatbot nie pomoże.** Model językowy bez źródeł zmyśla cytaty i linki, podaje swoją opinię jako fakt i łatwo daje się zmanipulować treścią, którą dostał do sprawdzenia.

PRAWDOWID odpowiada na to tak: użytkownik wkleja tekst, link lub zrzut ekranu. Dostaje werdykt, a pod nim numerowane źródła z dosłownymi cytatami, datą publikacji i datą pobrania, tak aby każdy mógł go samodzielnie sprawdzić. Gdy dowodów nie ma, system mówi to wprost. Działa tak samo na stronie WWW i na Discordzie, a każde sprawdzenie zostawia ślad w dzienniku audytu.

## Jak to działa

PRAWDOWID sprawdza twierdzenia z mediów i sieci. Każdy werdykt opiera na zweryfikowanych cytatach z konkretnych źródeł: fact-checkerów, źródeł pierwotnych i serwisów informacyjnych. Odpowiedź zawsze podaje, co mówi źródło, gdzie to jest i kiedy zostało pobrane. Gdy dowodów brakuje, system mówi „nie znaleziono informacji” zamiast zgadywać.

## Kanały wejścia

- **Frontend** (Angular, za proxy nginx) i **bot Discord** wysyłają tekst albo zrzut ekranu.
- **APIGateway** (FastAPI) przyjmuje zapytanie, maskuje dane osobowe, prowadzi dziennik audytu i przekazuje sprawę dalej. Obrazy trafiają najpierw do serwisu **OCR**.

## Weryfikacja

**VerificationPipeline** (Kedro + FastAPI) przeprowadza każde twierdzenie przez stałą sekwencję kroków:

1. **Wyodrębnienie twierdzeń.** LLM rozbija tekst na samodzielne, jednoznaczne tezy i oznacza opinie oraz prognozy jako nieweryfikowalne.
2. **Zapytania.** Każda teza dostaje dwie wersje zapytania: semantyczną (wektorową) i słownikową (słowa kluczowe, nazwy, liczby).
3. **Wyszukiwanie hybrydowe.** Wyniki z wyszukiwania wektorowego (bge-m3) i pełnotekstowego są łączone metodą RRF, a następnie sortowane przez reranker bge-reranker-v2-m3.
4. **Stanowisko źródła.** LLM ocenia, czy dany fragment potwierdza, zaprzecza, czy brakuje mu kontekstu. Musi przy tym podać dosłowny cytat.
5. **Reguły werdyktu.** Werdykt i jego siłę wyznaczają jawne reguły, a nie LLM. Reguły uwzględniają liczbę niezależnych potwierdzeń, typ źródła i rozbieżne liczby.
6. **Odpowiedź i walidacja cytatów.** Każdy cytat jest sprawdzany co do znaku z tekstem źródła. Cytat, który nie przejdzie walidacji, nie trafia do odpowiedzi.

Krok 6 zapobiega halucynacjom: model może się pomylić w ocenie, ale nie może wymyślić cytatu.

## Wiedza

- **PageScrapper** (FastAPI + Playwright) zbiera artykuły z katalogu zaufanych źródeł: RSS, Google Fact Check i strony fact-checkerów. Odczytuje ich znaczniki ClaimReview, czyli ocenę i autora twierdzenia. Tabela `documents` w Postgresie jest **jedynym źródłem prawdy** o treści.
  - Gdy w korpusie brakuje dowodów, **live-search** pobiera na żądanie nowe strony.
- **ContextGraph** (Neo4j) jest indeksem do wyszukiwania. Przechowuje:
  - dokumenty, ich fragmenty z wektorami i powiązania między źródłami;
  - sprawdzone twierdzenia (`CheckedClaim`) z ocenami fact-checkerów;
  - przypisanie twierdzenia do osoby, ale tylko tam, gdzie podał je sam fact-checker.
- **Synchronizacja.** Scraper wysyła każdą zmianę do grafu od razu. Dodatkowo co 10 minut graf porównuje numery rewizji ze scraperem i dociąga to, czego brakuje. Zgubiony zapis lub brak wektora naprawia się więc sam.

## Modele

| Rola | Model |
|---|---|
| Embeddingi | bge-m3 (lokalnie, na serwerze GPU) |
| Reranker | bge-reranker-v2-m3 (lokalnie, na serwerze GPU) |
| LLM | do wyboru przez LiteLLM: Bielik 11B lokalnie, Mistral (dane w EOG) lub Claude |

Embeddingi i reranker działają na własnym serwerze GPU, dzięki czemu korpus nie opuszcza naszej infrastruktury.

## Zasady projektowe

- **Cytat albo nic.** Odpowiedź może zawierać tylko cytaty zweryfikowane dosłownie w źródle.
- **Werdykt z reguł, nie z modelu.** Każdy werdykt ma jawne przesłanki.
- **Błąd techniczny to nie werdykt.** Status `error` lub `api_limit` jest zwracany osobno od oceny prawdziwości.
- **Treść użytkownika to dane.** Próby manipulacji (prompt injection) system traktuje jak zwykły tekst i zapisuje w dzienniku audytu.
