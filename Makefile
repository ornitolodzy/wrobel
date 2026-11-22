# PRAWDOWID monorepo. `make help` pokazuje cele.
# Działa z `docker compose` (plugin) i z samodzielnym `docker-compose` v2.
COMPOSE ?= $(shell docker compose version >/dev/null 2>&1 && echo "docker compose" || echo "docker-compose")
DC      := $(COMPOSE)
S       ?=

.PHONY: help up start down clean ps logs build config dev-backend dev-frontend seed-dump seed-dump-all graph-up graph-seed-dump graph-sync \
        test test-gateway test-scrapper test-pipeline test-ocr test-graph test-discord test-frontend eval-e2e \
        aws-init aws-up aws-deploy aws-config aws-ps aws-logs aws-stop aws-start aws-down

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

seed-dump: ## zrzut korpusu z działającego Postgresa do db/seed (bez checks/audit_events/check/case)
	$(DC) exec -T postgres pg_dump -U prawdowid -d prawdowid --no-owner --no-privileges \
	  --exclude-table-data=checks --exclude-table-data=audit_events \
	  --exclude-table-data='"check"' --exclude-table-data='"case"' \
	  | gzip -9 > db/seed/10-prawdowid.sql.gz
	@ls -lh db/seed/10-prawdowid.sql.gz

graph-up: ## ContextGraph + Neo4j (profil graph), API :8100, Browser :7474
	$(DC) --profile graph up -d --build neo4j context-graph

graph-seed-dump: ## zrzut grafu Neo4j do db/neo4j-seed/neo4j.dump (zatrzymuje neo4j na czas zrzutu)
	$(DC) --profile graph stop context-graph neo4j
	$(DC) --profile graph run --rm --no-deps -v "$(CURDIR)/db/neo4j-seed:/dumps" --entrypoint /bin/bash neo4j -c \
	  'neo4j-admin database dump neo4j --to-path=/dumps --overwrite-destination=true && chown $(shell id -u):$(shell id -g) /dumps/neo4j.dump'
	$(DC) --profile graph up -d neo4j context-graph
	@ls -lh db/neo4j-seed/neo4j.dump

graph-sync: ## rekoncyliacja grafu :8100 z PageScrapperem (brakujące / nieaktualne dokumenty, brakujące wektory)
	python3 ContextGraph/scripts/sync_from_scraper.py --graph http://127.0.0.1:8100

seed-dump-all: ## spójne seedy: najpierw graf zgodny ze scraperem (lag = 0), potem zrzut Postgresa i Neo4j
	python3 ContextGraph/scripts/sync_from_scraper.py --graph http://127.0.0.1:8100
	$(MAKE) seed-dump
	$(MAKE) graph-seed-dump

eval-e2e: ## mierniki z sekcji 6 zadania na działającym stacku (WWW + Discord), raport w eval/results/
	python eval/run_e2e.py --project "$(or $(COMPOSE_PROJECT_NAME),wrobel)" $(ARGS)

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

# --- AWS (deploy/aws): 3x EC2 (edge / app / data), publiczne API na Elastic IP ---
TF  := terraform -chdir=deploy/aws/terraform
H   ?= app
AWS_IDS = $$($(TF) output -raw edge_instance_id) $$($(TF) output -raw app_instance_id) $$($(TF) output -raw data_instance_id)

aws-init: ## AWS: terraform init (raz)
	$(TF) init

aws-up: ## AWS: infrastruktura (terraform apply)
	$(TF) apply

aws-deploy: ## AWS: build + push obrazów, paczka do S3, restart hostów (HOSTS="edge")
	./deploy/aws/deploy.sh

aws-config: ## AWS: tylko .env / nginx.conf / compose, bez przebudowy obrazów
	SKIP_BUILD=1 ./deploy/aws/deploy.sh

aws-ps: ## AWS: stan kontenerów na hoście (make aws-ps H=edge)
	./deploy/aws/remote.sh $(H) 'docker compose -f $(H).yml ps'

aws-logs: ## AWS: logi serwisu (make aws-logs H=app S=verification-pipeline)
	./deploy/aws/remote.sh $(H) 'docker compose -f $(H).yml logs --tail 150 $(S)'

aws-stop: ## AWS: zatrzymanie maszyn (płacisz tylko za dyski i IP)
	aws ec2 stop-instances --region $$($(TF) output -raw region) --instance-ids $(AWS_IDS) --output text

aws-start: ## AWS: start maszyn (kontenery wstają same)
	aws ec2 start-instances --region $$($(TF) output -raw region) --instance-ids $(AWS_IDS) --output text

aws-down: ## AWS: usunięcie wszystkiego (terraform destroy, baza też znika)
	$(TF) destroy
