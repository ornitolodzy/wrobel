#!/usr/bin/env bash
# Wdrożenie na AWS (po terraform apply): build obrazów -> ECR, paczka -> S3, restart na EC2 przez SSM.
#   ./deploy/aws/deploy.sh                 pełne wdrożenie
#   SKIP_BUILD=1 ./deploy/aws/deploy.sh    tylko konfiguracja (.env, nginx.conf, compose), te same obrazy
#   HOSTS="edge" ./deploy/aws/deploy.sh    restart wybranych hostów (domyślnie: data app edge)
set -euo pipefail
# shellcheck source=deploy/aws/lib.sh
source "$(dirname "$0")/lib.sh"

HOSTS=${HOSTS:-"data app edge"}
COMPOSE=$(docker compose version >/dev/null 2>&1 && echo "docker compose" || echo "docker-compose")
REGISTRY=$(tf_out registry)

[ -f "$ROOT/.env" ] || { echo "brak $ROOT/.env (cp .env.example .env i uzupełnij klucze)"; exit 1; }

STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT

if [ -z "${SKIP_BUILD:-}" ]; then
  TAG="$(git -C "$ROOT" rev-parse --short HEAD)$(git -C "$ROOT" diff --quiet HEAD || echo -dirty)-$(date +%Y%m%d%H%M%S)"
  echo "== build + push $TAG -> $REGISTRY"
  aws ecr get-login-password | docker login --username AWS --password-stdin "$REGISTRY"
  export REGISTRY IMAGE_TAG=$TAG
  $COMPOSE -f "$ROOT/deploy/aws/compose/build.yml" build
  $COMPOSE -f "$ROOT/deploy/aws/compose/build.yml" push
  echo "IMAGE_TAG=$TAG" > "$STAGE/release.env"
fi

echo "== paczka -> s3://$BUCKET/bundle/"
mkdir -p "$STAGE/host" "$STAGE/db" "$STAGE/PageScrapper/imports"
cp "$ROOT"/deploy/aws/compose/{edge,app,data}.yml "$STAGE/"
cp "$ROOT/deploy/aws/host/wrobel-deploy.sh" "$STAGE/host/"
cp -r "$ROOT/proxy" "$STAGE/proxy"
cp -r "$ROOT/db/seed" "$ROOT/db/neo4j-seed" "$STAGE/db/"
cp -r "$ROOT/PageScrapper/imports/." "$STAGE/PageScrapper/imports/" 2>/dev/null || true
cp "$ROOT/.env" "$STAGE/app.env"
aws s3 sync "$STAGE" "s3://$BUCKET/bundle/" --only-show-errors

for host in $HOSTS; do
  echo "== $host"
  run_remote "$host" "aws s3 cp s3://$BUCKET/bundle/host/wrobel-deploy.sh /opt/wrobel/wrobel-deploy.sh --region $REGION --only-show-errors && bash /opt/wrobel/wrobel-deploy.sh"
done

echo
echo "Gotowe: $(tf_out public_url)   (health: $(tf_out public_url)/health/services)"
