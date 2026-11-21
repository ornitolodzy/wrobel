# PRAWDOWID monorepo. `make help` pokazuje cele.
# Działa z `docker compose` (plugin) i z samodzielnym `docker-compose` v2.
COMPOSE ?= $(shell docker compose version >/dev/null 2>&1 && echo "docker compose" || echo "docker-compose")
DC      := $(COMPOSE)
S       ?=

.PHONY: help up start down clean ps logs build config dev-backend dev-frontend \
        test test-gateway test-scrapper test-pipeline test-ocr test-discord test-frontend

help: ## lista celów
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

start: ## pełny start ze sprawdzeniem połączeń i pierwszym pobraniem źródeł
	./start.sh

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

test: test-gateway test-scrapper test-pipeline test-ocr test-discord ## testy wszystkich serwisów Pythona

test-gateway:
	cd APIGateway && python -m pytest

test-scrapper:
	cd PageScrapper && python -m pytest

test-pipeline:
	cd VerificationPipeline && python -m pytest -m "not live and not db"

test-ocr:
	cd ImageRecognitionServcie && uv run pytest

test-discord:
	cd DiscordBot && python -m pytest

test-frontend:
	cd Frontend && npm test -- --watch=false
