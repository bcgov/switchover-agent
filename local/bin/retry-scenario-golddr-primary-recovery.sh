#!/bin/bash
# Scenario C (AC #4): exhaust all retries, then recover via operator re-deploy.
#
# After all retries fail (maintenance stuck, switchover_transition=3), an operator
# manually triggers a new Tekton deploy for the same environment. The agent detects
# the untracked success and clears maintenance (AC #4 cleanup path).
#
# Tekton runs on passive (golddr); mode on mock-passive (6665), phase on mock-active (6664).
#
# Usage:
#   bin/retry-scenario-golddr-primary-recovery.sh
#   bin/retry-scenario-golddr-primary-recovery.sh --fresh

set -e

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-$ROOT/docker-compose.yml}"
MOCK_ACTIVE="${MOCK_ACTIVE:-http://127.0.0.1:6664}"
MOCK_PASSIVE="${MOCK_PASSIVE:-http://127.0.0.1:6665}"

read_compose_var() {
  local name=$1 default=$2
  local val
  val=$(grep -E "^  ${name}:" "$COMPOSE_FILE" 2>/dev/null | head -1 \
    | sed -E 's/^[^:]+:[[:space:]]*"?([^"#]*)"?.*/\1/' | tr -d ' ')
  echo "${val:-$default}"
}

passive_metrics() {
  docker exec switchover-agent-passive curl -sf http://127.0.0.1:8000/metrics 2>/dev/null || true
}

transition_failed_seen() {
  passive_metrics | grep -E 'switchover_transition_failed' | grep -qvE ' 0\.0$'
}

maintenance_cleared() {
  passive_metrics | grep -E 'switchover_transition\{' | grep -q '"000000-dev"} 0\.0'
}

if [ "${1:-}" = "--fresh" ]; then
  cd "$ROOT"
  docker compose down -v
  docker compose up -d
  echo "Waiting for stack warm-up..."
  sleep 20
  shift
fi

if ! docker ps --format '{{.Names}}' 2>/dev/null | grep -qx mock-passive; then
  echo "Stack is not running — use --fresh or docker compose up -d" >&2
  exit 1
fi

PIPELINE_RETRY_INTERVAL_SECONDS=$(read_compose_var PIPELINE_RETRY_INTERVAL_SECONDS 300)
PIPELINE_MAX_RETRIES=$(read_compose_var PIPELINE_MAX_RETRIES 2)
PIPELINE_RETRY_TOTAL_CAP_SECONDS=$(read_compose_var PIPELINE_RETRY_TOTAL_CAP_SECONDS 900)

if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx switchover-agent-passive; then
  PIPELINE_RETRY_INTERVAL_SECONDS=$(docker exec switchover-agent-passive \
    printenv PIPELINE_RETRY_INTERVAL_SECONDS 2>/dev/null || echo "$PIPELINE_RETRY_INTERVAL_SECONDS")
  PIPELINE_MAX_RETRIES=$(docker exec switchover-agent-passive \
    printenv PIPELINE_MAX_RETRIES 2>/dev/null || echo "$PIPELINE_MAX_RETRIES")
  PIPELINE_RETRY_TOTAL_CAP_SECONDS=$(docker exec switchover-agent-passive \
    printenv PIPELINE_RETRY_TOTAL_CAP_SECONDS 2>/dev/null || echo "$PIPELINE_RETRY_TOTAL_CAP_SECONDS")
fi

if [ "${1:-}" != "--fresh" ]; then
  prior=$(curl -sf "${MOCK_PASSIVE}/activity" 2>/dev/null | grep -c 'TEKTON/TRIGGER' || true)
  if [ "$prior" -gt 0 ]; then
    echo "Passive mock already has Tekton activity — re-run with --fresh" >&2
    exit 1
  fi
fi

EXHAUST_WAIT=$((PIPELINE_RETRY_INTERVAL_SECONDS * PIPELINE_MAX_RETRIES + 90))

echo "Retry config: interval=${PIPELINE_RETRY_INTERVAL_SECONDS}s max_retries=${PIPELINE_MAX_RETRIES} cap=${PIPELINE_RETRY_TOTAL_CAP_SECONDS}s"
echo "Phase 1: exhaust all retries (wait up to ${EXHAUST_WAIT}s)"

echo "==> Tekton mode on passive mock: always-fail"
curl -sf -X PUT "${MOCK_PASSIVE}/tekton/mode/always-fail"

echo "==> Failover: transition-to-golddr-primary (active-passive → golddr-primary)"
curl -sf -X PUT "${MOCK_ACTIVE}/phase/transition-to-golddr-primary"

echo "==> Waiting for retry exhaustion (transition failed)..."
for i in $(seq 1 "$EXHAUST_WAIT"); do
  if transition_failed_seen; then
    break
  fi
  sleep 1
done
if [ "$i" -eq "$EXHAUST_WAIT" ]; then
  echo "Timed out waiting for transition failed — aborting" >&2
  exit 1
fi
echo "    Transition declared failed (${i}s). Maintenance is stuck on."

echo ""
echo "Phase 2: operator re-deploy (AC #4 recovery)"

echo "==> Reset Tekton mode to normal on passive mock"
curl -sf -X PUT "${MOCK_PASSIVE}/tekton/mode/normal"

echo "==> Simulate operator-triggered deploy: POST /tekton on passive mock"
curl -sf -X POST "${MOCK_PASSIVE}/tekton" \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Event: push" \
  -d '{"ref":"refs/heads/deploy/dev","repository":{"url":"https://github.com/example/repo.git"},"head_commit":{"id":"","message":"","author":{"username":"operator"}}}'

echo "==> Waiting for maintenance to clear (AC #4)..."
for i in $(seq 1 60); do
  if maintenance_cleared; then
    break
  fi
  sleep 1
done

echo ""
echo "==> Passive mock activity (TEKTON)"
curl -sf "${MOCK_PASSIVE}/activity" | grep -E 'TEKTON/(MODE|TRIGGER)|trigger_num|pipelineruns_fixture' || true

echo ""
echo "==> Passive agent (recovery path)"
docker logs switchover-agent-passive 2>&1 \
  | grep -iE 'Untracked pipeline|clearing stuck|Transition FAILED|retry|Retry|scheduling' \
  | tail -20 || true

echo ""
echo "==> Passive metrics"
passive_metrics | grep -E 'switchover_transition|switchover_pipeline|switchover_maintenance' || true

if maintenance_cleared; then
  echo ""
  echo "golddr-primary recovery scenario (C / AC #4): OK (${i}s)"
else
  echo "Timed out waiting for maintenance to clear — check logs above" >&2
  exit 1
fi
