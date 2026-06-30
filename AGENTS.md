# AGENTS.md

Guidance for AI agents and contributors working in this repo. Keep it short; update it when conventions change.

## What this is

Switchover Agent: a Python service that, alongside an F5 GSLB, manages failover of a Patroni Postgres cluster (and Keycloak/Kong) between the `gold` and `golddr` data centres. It runs on both the active and passive sites and they coordinate over a secure websocket. See `README.md` for the full domain description and deployment details.

## Architecture (the mental model)

- The app is a set of **independent processes** spawned from `src/main.py` via `multiprocessing.Process`. Which ones start is controlled by the `PROCESS_LIST` env var (see `is_enabled.py`).
- Every watcher/worker pushes events onto a single **`logic_q` queue**. `Logic.handler` in `src/logic.py` is the single consumer and the brain: it reacts to `kube_stream` (configmap + tekton PipelineRun), `patroni`, `dns`, `peer`, and `switchover_state` events and drives transitions.
- Transitions live in `src/transitions/` (`initiate_*`, `shared.py`, `wait_for.py`). External I/O is isolated in `src/clients/` (`kube`, `kube_stream`, `tekton`, `patroni`, `dns`, `maintenance`, `keycloak`, `prom`).
- Config is **env-var driven**, centralized in `src/config.py`. Don't read `os.environ` ad hoc in new code; add to `config.py`.
- Pipeline retry behaviour is configurable via `PIPELINE_RETRY_INTERVAL_SECONDS` (default 300), `PIPELINE_MAX_RETRIES` (default 2), and `PIPELINE_RETRY_TOTAL_CAP_SECONDS` (default 900). Failed pipelines are automatically retried up to the configured count; after the cap, no new attempts start but any in-flight run finishes. A failed transition (all retries exhausted) keeps maintenance on and emits a dedicated metric.

### Pipeline / Tekton tracking and retry

- A transition triggers a Tekton build (`trigger_tekton_build`) and records the in-flight run via `Logic.set_pipeline()`, which sets both `Logic.pipeline = {event_id, start_ts, maintenance}` and `Logic.retry_state = {event_id, transition_started_ts, attempts_made, retry_at, maintenance, release}`.
- `tekton_watch` feeds `PipelineRun` events into the inline PipelineRun branch of `Logic.handler()`, which matches on `triggers.tekton.dev/triggers-eventid`.
- On a tracked terminal state:
  - **Success** (`Completed`/`Succeeded`): normal maintenance handling, clear pipeline/retry state.
  - **Failure** (`Failed`/`PipelineRunCancelled`): schedule a retry (`retry_at = now + interval`) if attempts remain and the cap (from first attempt start) has not elapsed; otherwise call `_declare_transition_failed()`.
- Retries are driven by `tick` events (30s heartbeat from `clients/tick.py`). `_maybe_retry()` fires `retry_deploy()` when `retry_at` is due; re-triggers Tekton only (no full transition side-effects).
- Cap behaviour: stop starting new attempts after `PIPELINE_RETRY_TOTAL_CAP_SECONDS`; any in-flight run may finish. Declare failed when retries exhausted or cap elapsed with no success/in-flight attempt.
- `release` on retry/failed metrics comes from Tekton param `release-namespace` (updated on PipelineRun events), seeded from `config.solution_namespace` (`KUBE_NAMESPACE`) at first trigger.

**Metrics** (class attrs on `Logic`):

| Metric | Labels | Notes |
| --- | --- | --- |
| `switchover_pipeline` | `release`, `state` | Incremented for MODIFIED events matching `retry_state.event_id` (excludes untracked runs; may count duplicate watch events; terminal success often not counted) |
| `switchover_transition_failed` | `release` | Counter; incremented **once** per failed transition — use for alerting |
| `switchover_transition` | `release` | Gauge; `3` = failed, `0` = success/cleared — per-release status |
| `switchover_logic_gauge` | `resource` | Legacy aggregate; `transition` = `0` idle, `1` in progress, `3` failed (no `release` label) |
| `switchover_logic` | `resource`, `state` | General logic events |

**Structure** (for tests): retry logic lives in `_on_pipeline_success/failure`, `_declare_transition_failed`, `_maybe_retry`, `_fire_retry`. Tests call those directly (with the same event_id guard the handler uses); inject a fake clock via `logic._now_fn`.

## Build / run / dev

- Python managed by **Poetry**; deps in `pyproject.toml`. Containerized via `Dockerfile` (agent) and `Dockerfile.mock` (mock).
- Local run: `poetry install` then `poetry run python src/main.py` (needs the full env-var set — see `README.md`).
- Full local stack: `docker compose build && docker compose up` (two agents + two mocks).

## Testing (read before changing behavior)

Current reality:

- **Unit tests** in `tests/test_pipeline_retry.py` (pytest): feed crafted PipelineRun/tick events into `Logic` with a fake clock; assert retry scheduling, bounds, cap, and metrics. Run: `pytest tests/`.
- `src/tests/*.json` are stale Patroni fixtures, not loaded by any code.
- The de-facto integration harness is **`docker-compose` + the FastAPI mock** in `mock/`, which emulates k8s/Tekton/Patroni/DNS/maintenance APIs from canned JSON in `mock/data/`.
- Manual integration flow: bring up the stack, drive transitions with `curl .../phase/<name>` and `.../initiate/<event>` (see `README.md`), then inspect `GET /activity`.
- `local/bin/cycle-through-transitions.sh` is a **snapshot test**: it runs the transition cycle and `diff`s the captured activity log against `local/baseline/activity_{a,p}.json`. This test is run in CI via `.github/workflows/test.yml`.
- `local/bin/retry-scenario-golddr-primary.sh` and `retry-scenario-golddr-primary-exhaust.sh` exercise fail-then-succeed and always-fail retry paths manually (use `--fresh` for a clean stack).

Mock retry support (added):

- `mock/data/k8s/pipelineruns-failed.json` — failed terminal PipelineRun fixture.
- `PUT /tekton/mode/{normal|fail-then-succeed|always-fail}` — control failure simulation; `fail_count` query param for fail-then-succeed.

Remaining gaps:

- No mock hook yet for untracked ("Track none") MODIFIED PipelineRun events (AC #3 follow-up).
- Snapshot/CI path does not exercise retry/failure scenarios by default.

## Conventions

- Match the existing style; this is a small, pragmatic codebase. Logging via the stdlib `logging` module with module-level `logger`.
- Do not add narration comments that restate the code. Comment only non-obvious intent/constraints.
- New external calls go in `src/clients/`; new env/config in `src/config.py`.

## Workflow

- Changes are planned with **OpenSpec** (`openspec/`). Use the `/opsx-*` commands / `.cursor/skills/openspec-*` to propose, apply, and archive changes.
