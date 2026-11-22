# PRAWDOWID monorepo. `make help` pokazuje cele.
# Działa z `docker compose` (plugin) i z samodzielnym `docker-compose` v2.
COMPOSE ?= $(shell docker compose version >/dev/null 2>&1 && echo "docker compose" || echo "docker-compose")
DC      := $(COMPOSE)
S       ?=

.PHONY: help up start down clean ps logs build config dev-backend dev-frontend graph-up graph-sync \
        test test-gateway test-scrapper test-pipeline test-ocr test-graph test-discord test-frontend

help: ## lista celów
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

start: ## pełny start ze sprawdzeniem połączeń (make start ARGS="--graph --no-build")
	./start.sh $(ARGS)

up: ## build + start całego stacku
	$(DC) up -d --build

down: ## zatrzymanie stacku (baza zostaje)
	$(DC) down

clean: ## zatrzymanie + usunięcie bazy
	$(DC) down -v

ps: ## stan kontenerów
	$(DC) ps

logs: ## logi (make logs S=api-gateway)
	$(DC) logs -f $(S)

build: ## przebudowa obrazów (make build S=page-scrapper)
	$(DC) build $(S)

config: ## walidacja docker-compose.yml
	$(DC) config --quiet && echo "compose OK"

dev-backend: ## backend w Dockerze bez frontendu (do pracy z `make dev-frontend`)
	$(DC) up -d --build api-gateway

dev-frontend: ## Angular dev server :4200, /api i /ws -> gateway :8000
	cd Frontend && npm ci && npm start

graph-up: ## ContextGraph + Neo4j (profil graph), API :8100, Browser :7474
	$(DC) --profile graph up -d --build neo4j context-graph

graph-sync: ## dokumenty z PageScrappera :8002 do grafu :8100
	cd ContextGraph && python scripts/sync_from_scraper.py --scraper http://127.0.0.1:8002 --graph http://127.0.0.1:8100

test: test-gateway test-scrapper test-pipeline test-ocr test-graph test-discord ## testy wszystkich serwisów Pythona

test-gateway:
	cd APIGateway && python -m pytest

test-scrapper:
	cd PageScrapper && python -m pytest

test-pipeline:
	cd VerificationPipeline && python -m pytest -m "not live and not db"

test-ocr:
	cd ImageRecognitionServcie && uv run pytest

test-graph: ## bez NEO4J_TEST_URI tylko testy jednostkowe (integracyjne czyszczą bazę)
	cd ContextGraph && python -m pytest

test-discord:
	cd DiscordBot && python -m pytest

test-frontend:
	cd Frontend && npm test -- --watch=false
