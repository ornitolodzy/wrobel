# ADR 0001: Wymienne modele AI przez jeden uniwersalny adapter

Status: przyjęte · 2026-11-21

## Kontekst
Pipeline wywołuje model językowy w trzech wąskich zadaniach: ekstrakcja twierdzeń (krok 1),
ocena stanowiska źródeł (krok 4), krótkie wyjaśnienie (krok 6). Werdykt liczy kod (`policy.py`), nie model.
Zadanie MSZ wymaga wymiany modelu bez przebudowy i premiuje dostawców z EOG lub self-hosting.

## Decyzja
- Node'y znają tylko interfejs `LLMProvider.complete_json(system, user, schema) -> LLMResult`
  (`src/prawdowid/llm/base.py`). Nie importują żadnej biblioteki dostawcy.
- Jedyny „prawdziwy” provider to **LiteLLM** (`llm/litellm_provider.py`): jeden format wywołania dla Claude,
  Mistrala, OpenAI, Gemini, vLLM i Ollamy. Model zmienia się stringiem w configu.
- Model wybiera się **per rola** w `conf/*/parameters.yml` (`llm.roles.{claims,stance,compose}`).
  `conf/base` ma `provider: mock` (testy bez kluczy), prawdziwe modele włącza `conf/local/parameters.yml`
  (wzór: `conf/local/parameters.yml.example`).
- Wyjście zawsze walidowane przez Pydantic. Modele z natywnym JSON Schema dostają `response_format` w trybie strict;
  pozostałe (np. self-host Bielik) dostają `json_object` + schemat w prompcie + jedną próbę naprawy.
- Błędy mapowane na statusy techniczne, nigdy na werdykt: 429 → `api_limit`, odmowa → `error`,
  awaria przy ekstrakcji → fallback „cały tekst = 1 twierdzenie” (wpis w trace).
- Każde wywołanie trafia do trace: model, wersja promptu, tokeny, koszt, czas.
- Dla stance można zamiast LLM użyć modelu NLI (`provider: nli`), bez generowania tekstu.

## Jakie modele
| Rola | Domyślnie | Do porównania w `kedro run --pipeline eval` |
|---|---|---|
| claims | `anthropic/claude-opus-5-5` | `anthropic/claude-haiku-5-5` (najtańszy), Bielik 11B (self-host) |
| stance | `anthropic/claude-opus-5-5` | `anthropic/claude-sonnet-5-5`, `mistral/mistral-large-latest` (EOG), NLI mDeBERTa (fallback, druga opinia) |
| compose | `anthropic/claude-opus-5-5` | `anthropic/claude-haiku-5-5`, Bielik |

Modele nie-LLM w pipeline: `BAAI/bge-m3` (embeddingi), `BAAI/bge-reranker-v2-m3` (reranker),
klasyfikator prompt injection, spaCy `pl_core_news_lg` (PII, NER pod graf).
O przejściu na tańsze warianty decydują wyniki eval (macro-F1, odrzucone cytaty, koszt/1000 zapytań).

## Konsekwencje
- (+) Zmiana modelu, dostawcy albo self-hosting = zmiana configu. Brak SDK konkretnych dostawców w kodzie.
- (+) LiteLLM też jest wymienny: wystarczy inna implementacja `LLMProvider`.
- (−) LiteLLM to duża zależność. Cennik nowych modeli bywa nieaktualny, dlatego `prices_per_mtok` w configu ma pierwszeństwo.
- (−) Aktualne modele Claude odrzucają `temperature`. Dla nich go nie ustawiamy; powtarzalność daje cache po hashu wejścia.
- Uwaga: minimalna wersja `litellm>=1.110` (sprawdzone wsparcie Claude 5.5 i JSON Schema).
