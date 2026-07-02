#!/bin/bash
# Scenario B: always-fresh — active-passive → golddr-primary with all retries failing.
#
# Exercises retry exhaustion: initial attempt + max_retries retries, then
# switchover_transition_failed / switchover_transition=3 on passive agent.
#
# Tekton runs on passive (golddr); mode on mock-passive (6665), phase on mock-active (6664).
#
# Usage:
#   bin/retry-scenario-golddr-primary-exhaust.sh
#   bin/retry-scenario-golddr-primary-exhaust.sh --fresh
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

passive_metrics() {
  docker exec switchover-agent-passive curl -sf http://127.0.0.1:8000/metrics 2>/dev/null || true
}

transition_failed_seen() {
  passive_metrics | grep -E 'switchover_transition_failed' | grep -qvE ' 0\.0$'
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

EXPECTED_TRIGGERS=$((PIPELINE_MAX_RETRIES + 1))
# Initial fail + max_retries intervals between retries (+ tick/pipeline slack).
WAIT_MAX=$((PIPELINE_RETRY_INTERVAL_SECONDS * PIPELINE_MAX_RETRIES + 90))

echo "Retry config: interval=${PIPELINE_RETRY_INTERVAL_SECONDS}s max_retries=${PIPELINE_MAX_RETRIES} cap=${PIPELINE_RETRY_TOTAL_CAP_SECONDS}s"
echo "Expect ${EXPECTED_TRIGGERS} failed Tekton triggers; wait up to ${WAIT_MAX}s for transition failed"

echo "==> Tekton mode on passive mock: always-fail"
curl -sf -X PUT "${MOCK_PASSIVE}/tekton/mode/always-fail"

echo "==> Failover: transition-to-golddr-primary (active-passive → golddr-primary)"
curl -sf -X PUT "${MOCK_ACTIVE}/phase/transition-to-golddr-primary"

echo "==> Waiting for retry exhaustion (transition failed)..."
for i in $(seq 1 "$WAIT_MAX"); do
  if transition_failed_seen; then
    break
  fi
  sleep 1
done
if [ "$i" -eq "$WAIT_MAX" ]; then
  echo "Timed out waiting for transition failed — check logs below" >&2
fi

echo ""
echo "==> Passive mock activity (TEKTON)"
curl -sf "${MOCK_PASSIVE}/activity" | grep -E 'TEKTON/(MODE|TRIGGER)|trigger_num|pipelineruns_fixture' || true

trigger_count=$(curl -sf "${MOCK_PASSIVE}/activity" | grep -c 'TEKTON/TRIGGER' || true)
failed_only=$(curl -sf "${MOCK_PASSIVE}/activity" \
  | grep 'pipelineruns_fixture' | grep -cv 'pipelineruns-failed.json' || true)

echo ""
echo "==> Passive agent (pipeline / retry / exhaustion)"
docker logs switchover-agent-passive 2>&1 \
  | grep -iE 'initiate_primary|Pipeline|retry|Retry|Failed|FAILED|exhausted|scheduling|golddr-primary|Transition FAILED' \
  | tail -30 || true

echo ""
echo "==> Active agent (site down — no Tekton on this transition)"
docker logs switchover-agent-active 2>&1 \
  | grep -iE 'initiate_active_down|golddr-primary|retry|FAILED' | tail -10 || true

echo ""
echo "==> Passive metrics (switchover_pipeline + transition)"
passive_metrics | grep -E 'switchover_transition|switchover_pipeline' || echo "(no matching series)"

ok=1
if [ "$i" -ge "$WAIT_MAX" ]; then ok=0; fi
if [ "$trigger_count" -ne "$EXPECTED_TRIGGERS" ]; then
  echo "WARN: expected ${EXPECTED_TRIGGERS} Tekton triggers, got ${trigger_count}" >&2
  ok=0
fi
if [ "$failed_only" -ne 0 ]; then
  echo "WARN: not all triggers used failed fixture" >&2
  ok=0
fi
if ! transition_failed_seen; then
  echo "WARN: switchover_transition_failed not seen" >&2
  ok=0
fi

if [ "$ok" -eq 1 ]; then
  echo ""
  echo "golddr-primary exhaust scenario (B): OK (${i}s, ${trigger_count} triggers)"
else
  exit 1
fi
