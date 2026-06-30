#!/bin/bash
# Fail-then-succeed retry for active-passive → golddr-primary
#
# Mocks start at active-passive; Tekton runs on passive (golddr) via initiate_passive_primary.
# Phase PUTs go to mock-active (6664); Tekton mode goes to mock-passive (6665).
#
# Usage:
#   bin/retry-scenario-golddr-primary.sh
#   bin/retry-scenario-golddr-primary.sh --fresh
#
# Retry timing from switchover-agent-passive env (after stack is up), else docker-compose.yml.

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

# One retry cycle: initial fail + interval + retry success (+ tick/pipeline slack).
WAIT_MAX=$((PIPELINE_RETRY_INTERVAL_SECONDS * (PIPELINE_MAX_RETRIES + 1) + 90))

echo "Retry config: interval=${PIPELINE_RETRY_INTERVAL_SECONDS}s max_retries=${PIPELINE_MAX_RETRIES} cap=${PIPELINE_RETRY_TOTAL_CAP_SECONDS}s (wait up to ${WAIT_MAX}s)"

echo "==> Tekton mode on passive mock: fail-then-succeed (fail_count=1)"
curl -sf -X PUT "${MOCK_PASSIVE}/tekton/mode/fail-then-succeed?fail_count=1"

echo "==> Failover: transition-to-golddr-primary (active-passive → golddr-primary)"
curl -sf -X PUT "${MOCK_ACTIVE}/phase/transition-to-golddr-primary"

echo "==> Waiting for passive pipeline fail + retry success..."
for i in $(seq 1 "$WAIT_MAX"); do
  p=$(curl -sf "${MOCK_PASSIVE}/activity") || { sleep 1; continue; }
  echo "$p" | grep -q 'pipelineruns-failed.json' \
    && echo "$p" | grep 'pipelineruns_fixture' | tail -1 | grep -q 'pipelineruns-completed.json' \
    && break
  sleep 1
done
if [ "$i" -eq "$WAIT_MAX" ]; then
  echo "Timed out waiting for retry cycle — check logs below" >&2
fi

echo ""
echo "==> Passive mock activity (TEKTON)"
curl -sf "${MOCK_PASSIVE}/activity" | grep -E 'TEKTON/(MODE|TRIGGER)|trigger_num|pipelineruns_fixture' || true

echo ""
echo "==> Passive agent (pipeline / retry)"
docker logs switchover-agent-passive 2>&1 \
  | grep -iE 'initiate_primary|Pipeline|retry|Retry|Failed|Completed|scheduling|golddr-primary' \
  | tail -25 || true

echo ""
echo "==> Active agent (site down — no Tekton on this transition)"
docker logs switchover-agent-active 2>&1 \
  | grep -iE 'initiate_active_down|golddr-primary' | tail -10 || true

echo ""
echo "==> Passive metrics (switchover_pipeline)"
docker exec switchover-agent-passive curl -sf http://127.0.0.1:8000/metrics 2>/dev/null \
  | grep switchover_pipeline || echo "(no switchover_pipeline series yet)"

echo ""
echo "==> Passive metrics (transition failed — expect absent on success)"
docker exec switchover-agent-passive curl -sf http://127.0.0.1:8000/metrics 2>/dev/null \
  | grep switchover_transition || echo "(no switchover_transition series)"

if [ "$i" -lt "$WAIT_MAX" ]; then
  echo ""
  echo "golddr-primary retry scenario: OK (${i}s)"
else
  exit 1
fi
