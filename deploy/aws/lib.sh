# Wspólne funkcje dla deploy.sh i remote.sh (source, nie uruchamiać).
ROOT=$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)
TF_DIR="$ROOT/deploy/aws/terraform"

tf_out() { terraform -chdir="$TF_DIR" output -raw "$1"; }

REGION=$(tf_out region)
BUCKET=$(tf_out bucket)
export AWS_DEFAULT_REGION="$REGION"

instance_id() { tf_out "${1}_instance_id"; }

wait_ssm_online() { # <instance-id>
  local id=$1 t=0
  until [ "$(aws ssm describe-instance-information --filters "Key=InstanceIds,Values=$id" \
      --query 'InstanceInformationList[0].PingStatus' --output text 2>/dev/null)" = "Online" ]; do
    [ $t -ge 600 ] && { echo "$id nie zgłosił się w SSM po 10 min"; return 1; }
    [ $t -eq 0 ] && echo "czekam, aż $id zgłosi się w SSM (świeża maszyna: 1-3 min)..."
    sleep 10; t=$((t + 10))
  done
}

# run_remote <host: edge|app|data> <komenda bash>: uruchamia przez SSM, czeka, wypisuje wyjście.
run_remote() {
  local host=$1 cmd=$2 id cmd_id status params
  id=$(instance_id "$host")
  wait_ssm_online "$id"
  params=$(python3 -c 'import json,sys; print(json.dumps({"commands": [sys.argv[1]], "executionTimeout": ["1800"]}))' "$cmd")
  cmd_id=$(aws ssm send-command --instance-ids "$id" --document-name AWS-RunShellScript \
    --comment "wrobel $host" --parameters "$params" --query Command.CommandId --output text)
  while :; do
    status=$(aws ssm get-command-invocation --command-id "$cmd_id" --instance-id "$id" \
      --query Status --output text 2>/dev/null || echo Pending)
    case "$status" in
      Pending|InProgress|Delayed) sleep 5 ;;
      *) break ;;
    esac
  done
  aws ssm get-command-invocation --command-id "$cmd_id" --instance-id "$id" \
    --query StandardOutputContent --output text
  if [ "$status" != "Success" ]; then
    aws ssm get-command-invocation --command-id "$cmd_id" --instance-id "$id" \
      --query StandardErrorContent --output text >&2
    echo "[$host] $status" >&2
    return 1
  fi
}
