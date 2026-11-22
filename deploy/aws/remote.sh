#!/usr/bin/env bash
# Komenda na hoście przez SSM (bez SSH i bez session-manager-plugin), w katalogu paczki.
#   ./deploy/aws/remote.sh app 'docker compose -f app.yml logs --tail 100 verification-pipeline'
#   ./deploy/aws/remote.sh app 'systemctl status wrobel-gpu-tunnel --no-pager'
set -euo pipefail
# shellcheck source=deploy/aws/lib.sh
source "$(dirname "$0")/lib.sh"

[ $# -ge 2 ] || { echo "użycie: $0 <edge|app|data> '<komenda>'"; exit 1; }
host=$1; shift
run_remote "$host" "cd /opt/wrobel/bundle 2>/dev/null || cd /opt/wrobel; $*"
