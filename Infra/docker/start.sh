#!/usr/bin/env bash
# PRAWDOWID: pełne uruchomienie stacku od zera.
#
#   1. sprawdza wymagania (git, docker, docker compose, działający silnik Dockera)
#   2. klonuje brakujące repozytoria obok repo Infra (istniejące zostawia bez zmian)
#   3. tworzy .env z .env.example, jeśli go nie ma
#   4. buduje obrazy i uruchamia stack, czekając aż serwisy będą zdrowe
#   5. sprawdza połączenia: gateway -> pipeline / scrapper / OCR
#   6. przy pustej bazie wiedzy uruchamia pierwsze pobranie źródeł (scrapper -> pipeline)
#
# Użycie (Git Bash na Windows, Linux, macOS), z dowolnego katalogu:
#   ./Infra/docker/start.sh              pełny start
#   ./Infra/docker/start.sh --pull       dodatkowo aktualizuje istniejące repo (git pull --ff-only)
#   ./Infra/docker/start.sh --clone-only tylko pobiera repozytoria
#   ./Infra/docker/start.sh --no-build   start bez przebudowy obrazów
#   ./Infra/docker/start.sh --no-scrape  bez pierwszego pobrania źródeł
#
# Gałęzie można nadpisać zmiennymi, np.:
#   VERIFICATIONPIPELINE_BRANCH=feat/docker-stack-integration ./Infra/docker/start.sh

set -euo pipefail

# ------------------------------------------------------------------ konfiguracja
GITHUB_ORG_URL="${GITHUB_ORG_URL:-https://github.com/ornitolodzy}"
ADMIN_API_KEY="${ADMIN_API_KEY:-change-me-admin}"   # musi być zgodny z docker-compose.yml
GATEWAY_URL="${GATEWAY_URL:-http://localhost:8000}"
APP_URL="${APP_URL:-http://localhost:4200}"
WAIT_TIMEOUT="${WAIT_TIMEOUT:-900}"                 # sekundy na healthchecki (pierwszy build OCR jest długi)

# katalog repo -> gałąź (pusta = domyślna gałąź repo)
REPOS=(
  "APIGateway|${APIGATEWAY_BRANCH:-}"
  "Frontend|${FRONTEND_BRANCH:-}"
  "ImageRecognitionServcie|${IMAGERECOGNITIONSERVCIE_BRANCH:-develop}"
  "PageScrapper|${PAGESCRAPPER_BRANCH:-}"
  "VerificationPipeline|${VERIFICATIONPIPELINE_BRANCH:-}"
)

DO_PULL=false
CLONE_ONLY=false
BUILD=true
SCRAPE=true
for arg in "$@"; do
  case "$arg" in
    --pull) DO_PULL=true ;;
    --clone-only) CLONE_ONLY=true ;;
    --no-build) BUILD=false ;;
    --no-scrape) SCRAPE=false ;;
    -h|--help) sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
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
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"    # .../Infra/docker
WORKSPACE="$(cd "$SCRIPT_DIR/../.." && pwd)"                   # katalog z wszystkimi repo
cd "$SCRIPT_DIR"

# ------------------------------------------------------------------ 1. wymagania
step "Sprawdzam wymagania"
command -v git >/dev/null 2>&1 || die "Brak git. Zainstaluj: https://git-scm.com/downloads"
ok "git $(git --version | awk '{print $3}')"
if ! $CLONE_ONLY; then
  command -v docker >/dev/null 2>&1 || die "Brak Dockera. Zainstaluj Docker Desktop: https://docs.docker.com/get-docker/"
  docker compose version >/dev/null 2>&1 || die "Brak 'docker compose' (v2). Zaktualizuj Docker Desktop."
  docker info >/dev/null 2>&1 || die "Silnik Dockera nie działa. Uruchom Docker Desktop i spróbuj ponownie."
  ok "docker $(docker version --format '{{.Server.Version}}'), compose $(docker compose version --short)"
  command -v curl >/dev/null 2>&1 || die "Brak curl (potrzebny do sprawdzenia serwisów)."
fi

# ------------------------------------------------------------------ 2. repozytoria
step "Repozytoria w $WORKSPACE"
for entry in "${REPOS[@]}"; do
  name="${entry%%|*}"
  branch="${entry#*|}"
  dir="$WORKSPACE/$name"
  url="$GITHUB_ORG_URL/$name.git"

  if [ -d "$dir/.git" ]; then
    current="$(git -C "$dir" branch --show-current 2>/dev/null || echo '?')"
    if $DO_PULL; then
      if [ -n "$(git -C "$dir" status --porcelain)" ]; then
        warn "$name: są niezacommitowane zmiany, pomijam pull"
      elif git -C "$dir" pull --ff-only --quiet 2>/dev/null; then
        ok "$name: zaktualizowane ($current)"
      else
        warn "$name: pull się nie udał (brak upstreamu albo rozjazd historii), zostawiam jak jest"
      fi
    else
      ok "$name: już jest ($current), pomijam"
    fi
  elif [ -e "$dir" ] && [ -n "$(ls -A "$dir" 2>/dev/null)" ]; then
    die "$name: katalog $dir istnieje, ale nie jest repozytorium git. Usuń go lub przenieś i uruchom ponownie."
  else
    printf '  … %s: klonuję %s%s\n' "$name" "$url" "${branch:+ (gałąź $branch)}"
    if [ -n "$branch" ]; then
      git clone --quiet --branch "$branch" "$url" "$dir" || die "Nie udało się sklonować $url (gałąź $branch). Sprawdź dostęp do repo."
    else
      git clone --quiet "$url" "$dir" || die "Nie udało się sklonować $url. Sprawdź dostęp do repo (prywatne repo wymaga zalogowania w git)."
    fi
    ok "$name: sklonowane ($(git -C "$dir" branch --show-current))"
  fi
done

# Integracja pipeline'u z gatewayem (/verify, /ingest) musi być w kodzie, inaczej stack nie połączy się.
if ! grep -q '"/verify"' "$WORKSPACE/VerificationPipeline/src/prawdowid/api/gateway.py" 2>/dev/null; then
  warn "VerificationPipeline nie ma integracji ze stackiem (brak src/prawdowid/api/gateway.py)."
  warn "Przełącz gałąź, np.: git -C \"$WORKSPACE/VerificationPipeline\" switch feat/docker-stack-integration"
  $CLONE_ONLY || die "Przerwano: bez tej integracji gateway nie połączy się z pipeline'em."
fi

if $CLONE_ONLY; then
  step "Gotowe (--clone-only). Pełny start: $0"
  exit 0
fi

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
  warn "Ustaw PRAWDOWID_LLM_PROVIDER, PRAWDOWID_LLM_MODEL i klucz w $SCRIPT_DIR/.env"
fi

# ------------------------------------------------------------------ 4. build + start
step "Uruchamiam stack (docker compose, projekt 'wrobel')"
$BUILD && echo "  Pierwszy build trwa kilkanaście minut (modele OCR, Chromium, zależności Pythona)."
up_args=(up -d --wait --wait-timeout "$WAIT_TIMEOUT")
$BUILD && up_args+=(--build)
if ! docker compose "${up_args[@]}"; then
  echo
  docker compose ps
  for svc in $(docker compose ps --format '{{.Service}} {{.Status}}' | awk '/unhealthy|Exited|Restarting/ {print $1}'); do
    printf '\n%s--- ostatnie logi: %s ---%s\n' "$C_ERR" "$svc" "$C_END"
    docker compose logs --tail 40 "$svc"
  done
  die "Nie wszystkie serwisy wystartowały. Szczegóły: docker compose logs <serwis>"
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
    *) warn "gateway -> $svc: ${status:-brak} (docker compose logs)" ;;
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

  Logi:       docker compose -f "$SCRIPT_DIR/docker-compose.yml" logs -f
  Zatrzymaj:  docker compose -f "$SCRIPT_DIR/docker-compose.yml" down
  Więcej:     $SCRIPT_DIR/README.md
EOF
