## Context

The agent is an event-driven, multi-process app: watchers/workers push events onto a single `logic_q`, and `Logic.handler` (`src/logic.py`) is the sole consumer that drives transitions. A transition triggers a Tekton deployment (`trigger_tekton_build`) and records the in-flight run on `Logic.pipeline = {event_id, start_ts, maintenance}`. The `tekton_watch` process (`kube_stream` over `pipelineruns`) feeds `PipelineRun` events back; `logic.py` matches on the Tekton `triggers-eventid` and, on a terminal state, decides maintenance on/off and clears `self.pipeline`.

Today a failed pipeline (`Failed` / `PipelineRunCancelled`) just keeps maintenance on and clears tracking — the transition stalls with no recovery. This matches the 2026-06-04 PIR. There is no timer/scheduler in the system and no automated test coverage of pipeline tracking.

Constraints: preserve the single-consumer model (all transition state mutated only in the logic process); avoid breaking existing deployments (new behavior must have safe, default-on values); make the change unit-testable locally, since manual testing in dev is slow and function is critical.

## Goals / Non-Goals

**Goals:**
- Automatically re-trigger a failed, *tracked* deployment pipeline after an interval, bounded by a max retry count and a total-duration cap.
- At the cap, stop starting new pipelines but let an in-flight run finish.
- Emit a distinct, alertable "transition failed" metric plus visibility into retry attempts.
- Make retry parameters configurable via `config.py` env-vars with the ticket defaults.
- Make the retry/tracking logic unit-testable with an injectable clock.

**Non-Goals:**
- AC #2 (alert condition update), AC #3 (only-increment-on-tracked-runs false-positive fix), AC #4 (maintenance cleanup by matching any successful env pipeline). These are separate follow-ups on APS-4631.
- Persisting retry state across logic-process restarts (current design already loses in-flight `pipeline` state on restart; unchanged here).
- Redoing full transition side-effects on retry (see Decisions).

## Decisions

### 1. Time-based retries via a periodic "tick" event (not a new blocking timer)
Retries are time-based (5-min interval, 15-min cap) but the logic loop is event-driven and may be idle. Add a lightweight heartbeat producer that enqueues `{"event": "tick"}` onto `logic_q` on a **30-second** cadence (coarsest value that gives acceptable retry-timing precision). `Logic.handler` evaluates whether a scheduled retry is due on each tick (and also re-checks the cap on relevant events).
- **Why:** keeps all state mutation in the single logic consumer (no locking), fits the existing producer/queue pattern, and is trivially testable (tests inject synthetic ticks + a fake clock).
- **Alternatives:** (a) rely on incidental events from `patroni_worker`/`tekton_watch` re-lists — rejected as fragile/non-deterministic; (b) a `threading.Timer` inside the logic process — workable but adds concurrency to the consumer; the tick producer keeps the consumer single-threaded.

### 2. Injectable clock for deterministic tests
Replace direct `datetime.datetime.now()` calls in the pipeline/retry path with `self._now()` (default `datetime.datetime.now`). Tests set `logic._now_fn` to a fake clock.
- **Why:** lets unit tests advance time to assert the 5-min interval and 15-min cap without real waits.

### 3. Retry helpers on `Logic` (handler stays inline)
Keep the PipelineRun branch inside `Logic.handler` as it was; terminal success/failure/retry scheduling live in `_on_pipeline_success`, `_on_pipeline_failure`, `_declare_transition_failed`, `_maybe_retry`, and `_fire_retry`. Unit tests call those helpers directly.
- **Why:** reviewers see PipelineRun changes in context; helpers hold the new retry behaviour without a handler rewrite.

### 4. Explicit per-transition retry state
Introduce a retry/attempt record (extending or alongside `Logic.pipeline`), e.g. `{event_id, transition_started_ts, attempt_started_ts, attempts_made, retry_at, kind, maintenance, context}` capturing what's needed to re-trigger and to evaluate the cap. `transition_started_ts` is set at first attempt start and is the anchor for the total-duration cap. After each re-trigger, update `event_id` to the new Tekton event id so tracking follows the latest attempt.
- **Why:** deterministic, inspectable state; prevents untracked events from entering retry handling (matching the spec's "untracked ignored" requirement).

### 5. Retry re-triggers the deployment only, not the full transition
On retry, re-invoke `trigger_tekton_build` (the deployment) rather than re-running the whole transition (e.g. `set_primary_cluster`, env-var patches), which already ran on the first attempt and are effectively idempotent/complete.
- **Why:** the failure is in the deployment pipeline; redoing cluster mutations adds risk. A small `retry_deploy()` helper in `src/transitions/` wraps the re-trigger and updates tracked state.
- **Alternative:** re-run the full `initiate_*` path — rejected as higher-risk and unnecessary.

### 6. Metrics: distinct failed-transition signal + retry visibility
Use the existing `switchover_*` naming conventions:
- `switchover_logic_gauge{resource="transition"}` extended with a distinct failed value (e.g. `3`) to signal a failed transition (status/dashboard)
- `switchover_logic{resource="transition", state="failed"}` incremented once in `_declare_transition_failed()` for alerting
- `switchover_pipeline{state="Retry"}` incremented for each retry attempt started

These keep naming consistent with existing metrics and give AC #2 alerting a stable contract.
- **Why:** AC #1.a requires that "all retries done and failed" is easy to see and drives AC #2 alerting, distinct from single-pipeline failures (the false-positive concern in AC #3).

### 7. Configuration
Add to `config.py`: `pipeline_retry_interval_seconds` (default 300), `pipeline_max_retries` (default 2), `pipeline_retry_total_cap_seconds` (default 900), read from env-vars with defaults applied when unset/invalid.

### 8. Testing
- **Unit (pytest):** new `dev` dependency. Tests construct `Logic` with a fake clock, feed crafted `PipelineRun` ADDED/MODIFIED/Failed/Succeeded events and `tick` events, and assert: counters increment only for tracked runs, retry scheduled after interval, retry count bounded, cap stops new attempts but lets in-flight finish, "transition failed" emitted once on exhaustion, success cancels failure path.
- **Mock/integration:** add `mock/data/k8s/pipelineruns-failed.json`; add a trigger counter in `mock/main.py` so the first N `/tekton` triggers serve the failed fixture then flip to completed (to exercise retry→eventual-success end-to-end via docker-compose); also add a scenario where every trigger keeps returning the failed fixture and never flips to completed (so retries are exhausted and the transition fails). 

## Risks / Trade-offs

- **Idle queue delays retry evaluation** → tick producer guarantees periodic evaluation; cap is also re-checked on incoming PipelineRun events.
- **In-memory retry state lost if logic process restarts** mid-transition → accepted (pre-existing behavior for `pipeline`); maintenance stays on, so it fails safe. Documented.
- **Re-trigger storm if event-id tracking is wrong** → after each re-trigger, immediately adopt the new Tekton event id; only the tracked id can advance retry state.
- **Metric name churn vs. existing dashboards/alerts** → coordinate exact names with AC #2; treat names as the contract for the alerting change.
- **Refactor scope** limited to retry helper methods — PipelineRun handling stays inline in `handler()`.

## Migration Plan

- Ship behind safe defaults (retry enabled with 5 min / 2 / 15 min). No new required env-vars; existing deployments behave the same except failed pipelines now auto-retry.
- Add the tick producer to `PROCESS_LIST` wiring in `main.py` (enabled by default).
- Rollback: revert the change; or set `pipeline_max_retries=0` to disable retries while keeping the new failed-transition metric.

