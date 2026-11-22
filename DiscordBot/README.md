# Wróbel — bot Discord

Drugi kanał PRAWDOWID obok Web UI. Bot to **cienki adapter na API Gateway** (nagłówek `X-Channel: discord`):
nie ma własnej logiki weryfikacji. Walidacja, maskowanie PII, audit log i werdykt są te same co w aplikacji webowej.

```
Discord ──► bot ──► API Gateway ──► VerificationPipeline / PageScrapper (linki) / OCR (obrazy)
```

## Jak użyć na Discordzie

| Sposób | Przykład |
|---|---|
| Komenda | `/wrobel tekst: Czy RCB wysłało alert o ataku z powietrza?` |
| Link | `/wrobel tekst: https://www.gov.pl/web/rcb/...` (sprawdza treść strony) |
| Obraz | `/wrobel obraz: <zrzut ekranu>` (OCR, potem weryfikacja) |
| Odpowiedź tylko dla Ciebie | `/wrobel tekst: ... prywatnie: True` |
| Cudza wiadomość | prawy klik na wiadomości → **Aplikacje → Sprawdź w Wróblu** |
| Wiadomość prywatna | napisz do bota DM z twierdzeniem, linkiem albo obrazkiem |
| Wzmianka | `@Wróbel <twierdzenie>` na kanale albo `@Wróbel` jako odpowiedź na wiadomość do sprawdzenia |
| Pomoc | `/wrobel` bez argumentów albo DM „pomoc” |

Odpowiedź to embed z werdyktem (✅ prawda, ❌ fałsz, ⚠️ wprowadza w błąd, ❔ brak dowodów, 💬 opinia),
uzasadnieniem i maksymalnie 3 źródłami na twierdzenie, każde z linkiem i dosłownym cytatem.
Problemy techniczne (`error`, `api_limit`) są opisane jako problem techniczny, **nigdy jako werdykt**.

Bot nie potrzebuje uprzywilejowanego intentu *Message Content*: Discord przekazuje treść DM-ów,
wiadomości ze wzmianką bota i wiadomości wybranych w menu kontekstowym.

## Konfiguracja aplikacji na Discordzie (jednorazowo)

1. https://discord.com/developers/applications → **New Application** → nazwa „Wróbel”.
2. Zakładka **Bot** → **Reset Token** → skopiuj token do `.env` w katalogu głównym monorepo jako `DISCORD_TOKEN=...`.
   Token to hasło bota: nie wklejaj go nigdzie indziej i nie commituj.
3. (Opcjonalnie) w zakładce **Bot** wyłącz **Public Bot**, jeśli tylko Wy macie dodawać bota na serwery.
4. Uruchom stack: `./start.sh` (albo `make start`). Na końcu wypisze **link do dodania bota na serwer**
   (jest też w logach: `docker compose logs discord-bot | grep Invite`).
   Uprawnienia w linku: widok kanałów, wysyłanie wiadomości (także w wątkach), osadzanie linków, historia wiadomości.
5. Otwórz link, wybierz serwer (musisz mieć na nim uprawnienie „Zarządzanie serwerem”) i zatwierdź.

Komendy rejestrują się globalnie przy starcie bota. Żeby na serwerze testowym pojawiły się od razu, ustaw
`DISCORD_GUILD_ID` (prawy klik na serwer → *Kopiuj ID serwera*, wymaga trybu dewelopera w ustawieniach Discorda).

## Zmienne środowiskowe

| Zmienna | Domyślnie | Znaczenie |
|---|---|---|
| `DISCORD_TOKEN` | — (wymagany) | token bota |
| `DISCORD_GUILD_ID` | pusty | serwer testowy: komendy widoczne natychmiast |
| `GATEWAY_URL` | `http://api-gateway:8000` | adres API Gateway |
| `USER_LIMIT_PER_MINUTE` | `5` | limit zapytań na użytkownika Discorda (gateway limituje cały bot, bo bot ma jedno IP) |
| `REQUEST_TIMEOUT_SECONDS` | `120` | ile czekać na weryfikację |
| `LOG_LEVEL` | `INFO` | poziom logów (treść wiadomości nie jest logowana) |

## Uruchomienie

Docker (zalecane): Dockerfile i `.dockerignore` są w tym katalogu (kontekst buildu: `DiscordBot/`). Bot startuje w ramach stacku
z profilem `discord`; `start.sh` włącza go sam, gdy w `.env` jest `DISCORD_TOKEN`. Ręcznie:

```bash
# z katalogu głównego monorepo
docker compose --profile discord up -d --build discord-bot
docker compose logs -f discord-bot
```

Bez Dockera (Python 3.12):

```bash
python -m venv .venv && .venv/Scripts/activate      # linux/mac: source .venv/bin/activate
pip install -r requirements-dev.txt
set DISCORD_TOKEN=...  &  set GATEWAY_URL=http://localhost:8000      # linux/mac: export ...
python -m bot.main
```

## Testy

```bash
pytest
```

Testy nie łączą się z Discordem ani z gatewayem: routing treści, embedy (limity Discorda: 1024 znaki na pole,
6000 na embed), limit per użytkownik i klient gatewaya (z atrapą HTTP).

## Struktura

```
bot/
  main.py      klient Discord: /wrobel, menu „Sprawdź w Wróblu”, DM, @wzmianka, heartbeat dla healthchecka
  gateway.py   klient API Gateway (claim / url / image), mapowanie błędów na komunikaty
  format.py    odpowiedź gatewaya -> embed Discorda
  limits.py    limit zapytań per użytkownik
  config.py    zmienne środowiskowe
tests/
```
