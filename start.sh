#!/usr/bin/env bash
# PRAWDOWID: pełne uruchomienie stacku z monorepo.
#
#   1. sprawdza wymagania (docker + compose v2 jako `docker compose` lub `docker-compose`, działający silnik)
#   2. sprawdza, czy w monorepo są katalogi wszystkich serwisów
#   3. tworzy .env z .env.example, jeśli go nie ma
#   4. buduje obrazy i uruchamia stack, czekając aż serwisy będą zdrowe
#   5. sprawdza połączenia: gateway -> pipeline / scrapper / OCR
#   6. przy pustej bazie wiedzy uruchamia pierwsze pobranie źródeł (scrapper -> pipeline)
#
# Użycie (Git Bash na Windows, Linux, macOS), z dowolnego katalogu (albo `make start`):
#   ./start.sh              pełny start
#   ./start.sh --no-build   start bez przebudowy obrazów
#   ./start.sh --no-scrape  bez pierwszego pobrania źródeł

set -euo pipefail

# ------------------------------------------------------------------ konfiguracja
ADMIN_API_KEY="${ADMIN_API_KEY:-change-me-admin}"   # musi być zgodny z docker-compose.yml
GATEWAY_URL="${GATEWAY_URL:-http://localhost:8000}"
APP_URL="${APP_URL:-http://localhost:4200}"
WAIT_TIMEOUT="${WAIT_TIMEOUT:-900}"                 # sekundy na healthchecki (pierwszy build OCR jest długi)

# serwisy (katalogi w monorepo) budowane przez docker-compose.yml
SERVICES=(APIGateway Frontend ImageRecognitionServcie PageScrapper VerificationPipeline)

BUILD=true
SCRAPE=true
for arg in "$@"; do
  case "$arg" in
    --no-build) BUILD=false ;;
    --no-scrape) SCRAPE=false ;;
    -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Nieznana opcja: $arg (zobacz --help)" >&2; exit 2 ;;
  esac
done

# ------------------------------------------------------------------ wyjście
if [ -t 1 ]; then
  C_OK=$'\e[32m'; C_WARN=$'\e[33m'; C_ERR=$'\e[31m'; C_STEP=$'\e[1;36m'; C_END=$'\e[0m'
else
  C_OK=""; C_WARN=""; C_ERR=""; C_STEP=""; C_END=""
fi
step() { printf '\n%s==> %s%s\n' "$C_STEP" "$*" "$C_END"; }
ok()   { printf '  %s✔%s %s\n' "$C_OK" "$C_END" "$*"; }
warn() { printf '  %s!%s %s\n' "$C_WARN" "$C_END" "$*"; }
die()  { printf '\n%s✘ %s%s\n' "$C_ERR" "$*" "$C_END" >&2; exit 1; }

# ------------------------------------------------------------------ ścieżki
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"          # katalog główny monorepo
cd "$ROOT"

# ------------------------------------------------------------------ 1. wymagania
step "Sprawdzam wymagania"
command -v docker >/dev/null 2>&1 || die "Brak Dockera. Zainstaluj Docker Desktop: https://docs.docker.com/get-docker/"
if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1 && docker-compose version >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
else
  die "Brak Docker Compose v2 ('docker compose' ani 'docker-compose'). Zaktualizuj Dockera."
fi
docker info >/dev/null 2>&1 || die "Silnik Dockera nie działa. Uruchom Dockera i spróbuj ponownie."
ok "docker $(docker version --format '{{.Server.Version}}'), compose $("${COMPOSE[@]}" version --short) (${COMPOSE[*]})"
command -v curl >/dev/null 2>&1 || die "Brak curl (potrzebny do sprawdzenia serwisów)."

# ------------------------------------------------------------------ 2. serwisy w monorepo
step "Serwisy w $ROOT"
for name in "${SERVICES[@]}"; do
  [ -d "$ROOT/$name" ] || die "Brak katalogu $ROOT/$name. Uruchom skrypt z pełnego checkoutu monorepo."
  ok "$name"
done
# Integracja pipeline'u z gatewayem (/verify, /ingest) musi być w kodzie, inaczej stack nie połączy się.
grep -q '"/verify"' "$ROOT/VerificationPipeline/src/prawdowid/api/gateway.py" 2>/dev/null \
  || die "VerificationPipeline nie ma integracji ze stackiem (brak /verify w src/prawdowid/api/gateway.py)."

# ------------------------------------------------------------------ 3. .env
step "Konfiguracja (.env)"
if [ -f .env ]; then
  ok ".env już jest"
else
  cp .env.example .env
  ok "utworzono .env z .env.example"
fi
if ! grep -qE '^(ANTHROPIC|MISTRAL|OPENAI)_API_KEY=.+' .env; then
  warn "Brak klucza LLM w .env: werdykty będą 'brak dowodów' (pipeline bez modelu nie ocenia źródeł)."
  warn "Ustaw PRAWDOWID_LLM_PROVIDER, PRAWDOWID_LLM_MODEL i klucz w $ROOT/.env"
fi

# ------------------------------------------------------------------ 4. build + start
step "Uruchamiam stack (docker compose, projekt 'wrobel')"
$BUILD && echo "  Pierwszy build trwa kilkanaście minut (modele OCR, Chromium, zależności Pythona)."
up_args=(up -d --wait --wait-timeout "$WAIT_TIMEOUT")
$BUILD && up_args+=(--build)
if ! "${COMPOSE[@]}" "${up_args[@]}"; then
  echo
  "${COMPOSE[@]}" ps
  for svc in $("${COMPOSE[@]}" ps --format '{{.Service}} {{.Status}}' | awk '/unhealthy|Exited|Restarting/ {print $1}'); do
    printf '\n%s--- ostatnie logi: %s ---%s\n' "$C_ERR" "$svc" "$C_END"
    "${COMPOSE[@]}" logs --tail 40 "$svc"
  done
  die "Nie wszystkie serwisy wystartowały. Szczegóły: ${COMPOSE[*]} logs <serwis>"
fi
ok "wszystkie kontenery działają"

# ------------------------------------------------------------------ 5. połączenia
step "Sprawdzam połączenia przez API Gateway"
services_json=""
for _ in $(seq 1 30); do
  services_json="$(curl -fsS "$GATEWAY_URL/health/services" 2>/dev/null || true)"
  [ -n "$services_json" ] && ! grep -q '"status":"down"' <<<"$services_json" && break
  sleep 2
done
[ -n "$services_json" ] || die "Gateway nie odpowiada pod $GATEWAY_URL"
for svc in llm page_scrapper image_recognition; do
  status="$(grep -oE "\"name\":\"$svc\",\"configured\":[a-z]+,\"status\":\"[a-z]+\"" <<<"$services_json" | grep -oE '[a-z]+"$' | tr -d '"' || true)"
  case "$status" in
    ok) ok "gateway -> $svc: ok" ;;
    *) case "$svc" in llm) cs=verification-pipeline ;; page_scrapper) cs=page-scrapper ;; *) cs=ocr ;; esac
       warn "gateway -> $svc: ${status:-brak} (${COMPOSE[*]} logs $cs)" ;;
  esac
done
curl -fsS -o /dev/null "$APP_URL/" && ok "aplikacja przez proxy: $APP_URL" || warn "proxy $APP_URL nie odpowiada"

# ------------------------------------------------------------------ 6. baza wiedzy
step "Baza wiedzy"
docs="$(curl -fsS "$GATEWAY_URL/api/v1/corpus/stats" 2>/dev/null | grep -oE '"documents":[0-9]+' | head -1 | cut -d: -f2 || true)"
docs="${docs:-0}"
if [ "$docs" -gt 0 ]; then
  ok "korpus ma $docs dokumentów, pomijam pobieranie (scrapper odświeża źródła co 6 h)"
elif ! $SCRAPE; then
  warn "korpus jest pusty, a pobieranie wyłączono (--no-scrape)"
else
  run="$(curl -fsS -X POST "$GATEWAY_URL/api/v1/scrap/all" -H "X-API-Key: $ADMIN_API_KEY" \
         -H 'Content-Type: application/json' -d '{}' 2>/dev/null || true)"
  run_id="$(grep -oE '"run_id":"[^"]+"' <<<"$run" | cut -d'"' -f4 || true)"
  if [ -n "$run_id" ]; then
    ok "uruchomiono pierwsze pobranie źródeł w tle (run $run_id), potrwa kilka minut"
    echo "    postęp: curl -H 'X-API-Key: $ADMIN_API_KEY' $GATEWAY_URL/api/v1/scrap/runs/$run_id"
    echo "    indeks: curl http://127.0.0.1:8003/corpus/stats"
  else
    warn "nie udało się uruchomić pobierania: $run"
  fi
fi

# ------------------------------------------------------------------ podsumowanie
step "Gotowe"
cat <<EOF
  Aplikacja            $APP_URL
  API Gateway (docs)   $GATEWAY_URL/docs
  Stan serwisów        $GATEWAY_URL/health/services
  Pipeline (docs)      http://127.0.0.1:8003/docs
  PageScrapper (docs)  http://127.0.0.1:8002/docs
  OCR (docs)           http://127.0.0.1:8001/docs

  Logi:       make logs        (albo: ${COMPOSE[*]} logs -f, w $ROOT)
  Zatrzymaj:  make down        (albo: ${COMPOSE[*]} down, w $ROOT)
  Więcej:     $ROOT/README.md
EOF
