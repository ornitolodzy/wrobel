#!/usr/bin/env bash
# Na EC2: pobiera paczkę z S3, składa .env, pull obrazów z ECR, docker compose up i czeka na zdrowie.
# Wywoływany przez SSM z make aws-deploy i przy starcie nowej instancji (user_data).
set -euo pipefail

# shellcheck disable=SC1091
source /etc/wrobel.conf # ROLE BUCKET REGION
export AWS_DEFAULT_REGION="$REGION"

exec 9>/opt/wrobel/deploy.lock
flock 9

# Komenda z SSM może przyjść, zanim user_data doinstaluje Dockera.
if [ -z "${WROBEL_FROM_BOOT:-}" ]; then
  cloud-init status --wait >/dev/null 2>&1 || true
fi

cd /opt/wrobel
# --exact-timestamps: bez tego sync z S3 pomija pliki o tej samej wielkości (np. release.env z nowym tagiem)
aws s3 sync "s3://$BUCKET/bundle/" bundle/ --only-show-errors --exclude .env --exact-timestamps
aws s3 cp "s3://$BUCKET/infra/infra.env" infra.env --only-show-errors

cd bundle
if [ ! -f release.env ] || [ ! -f app.env ]; then
  echo "brak release.env / app.env w s3://$BUCKET/bundle: najpierw make aws-deploy z laptopa"
  exit 0
fi

# .env = klucze z laptopa (bez kluczy zarządzanych przez Terraform) + infra.env + tag obrazów
OVERRIDDEN='^(REGISTRY|AWS_REGION|PUBLIC_URL|APP_HOST|DATA_HOST|PG_PASSWORD|NEO4J_PASSWORD|ADMIN_API_KEY|IMAGE_TAG)='
{ grep -vE "$OVERRIDDEN" app.env || true; echo; cat ../infra.env release.env; } > .env
chmod 600 .env

REGISTRY=$(grep -E '^REGISTRY=' ../infra.env | cut -d= -f2-)
aws ecr get-login-password | docker login --username AWS --password-stdin "$REGISTRY" >/dev/null

if [ "$ROLE" = "edge" ] && grep -qE '^DISCORD_TOKEN=.+' .env; then
  export COMPOSE_PROFILES=discord
fi

dc() { docker compose -f "$ROLE.yml" "$@"; }

echo "== $ROLE: $(grep -E '^IMAGE_TAG=' release.env)"
dc pull --quiet
dc up -d --remove-orphans

case "$ROLE" in
  data) WAIT_FOR="postgres neo4j" ;;
  app)  WAIT_FOR="verification-pipeline page-scrapper ocr" ;;
  edge) WAIT_FOR="api-gateway" ;;
esac

deadline=$((SECONDS + 900))
for svc in $WAIT_FOR; do
  while :; do
    cid=$(dc ps -q "$svc")
    state=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid" 2>/dev/null || echo missing)
    [ "$state" = "healthy" ] && { echo "$svc: healthy"; break; }
    if [ $SECONDS -ge $deadline ]; then
      echo "$svc: $state po 15 min, ostatnie logi:"
      dc logs --tail 60 "$svc"
      exit 1
    fi
    sleep 5
  done
done

docker image prune -af --filter "until=72h" >/dev/null || true
dc ps --format 'table {{.Service}}\t{{.Status}}'
