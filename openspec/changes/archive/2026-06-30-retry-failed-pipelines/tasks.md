## 1. Configuration

- [x] 1.1 Add `pipeline_retry_interval_seconds` (default 300), `pipeline_max_retries` (default 2), and `pipeline_retry_total_cap_seconds` (default 900) to `src/config.py`, read from env-vars with safe default/parse fallback.
- [x] 1.2 Document the new env-vars in `README.md` (Configuration table) and note retry behavior in `AGENTS.md`.

## 2. Testability (retry helpers only)

- [x] 2.1 Keep PipelineRun handling inline in `handler()`; add `_on_pipeline_success/failure`, `_declare_transition_failed`, `_maybe_retry`, `_fire_retry` for retry behaviour.
- [x] 2.2 Introduce an injectable clock (`self._now()` defaulting to `datetime.datetime.now`; tests set `logic._now_fn`) and replace direct `now()` calls in the pipeline/retry path.
- [~] 2.3 ~~Extract full handler body into `handle_item()`~~ — skipped; not required for retry or tests.

## 3. Retry state & scheduling

- [x] 3.1 Add a per-transition retry/attempt state record (event_id, transition_started_ts, attempt_started_ts, attempts_made, retry_at, kind, maintenance, context); set `transition_started_ts` at the first attempt start and use it for the total-duration cap.
- [x] 3.2 Add a heartbeat producer that enqueues `{"event": "tick"}` onto `logic_q` on a 30-second cadence; wire it into `main.py` process startup (enabled by default).
- [x] 3.3 Handle `tick` events in `handler()` to evaluate whether a due retry should start (interval elapsed AND cap not exceeded AND retries remaining).

## 4. Retry behavior

- [x] 4.1 On a tracked PipelineRun reaching `Failed`/`PipelineRunCancelled`, schedule a retry (set `retry_at = now + interval`) instead of clearing state, when retries remain and the cap has not elapsed.
- [x] 4.2 Add a `retry_deploy()` helper in `src/transitions/` that re-triggers the Tekton build and updates tracked `event_id` to the new attempt; do NOT re-run full transition side-effects.
- [x] 4.3 Ensure untracked/unmatched PipelineRun events never alter retry state or trigger a retry.
- [x] 4.4 Enforce the total-duration cap measured from first attempt start: stop starting new attempts after the cap, but allow an in-flight attempt to finish and be evaluated.
- [x] 4.5 On a successful attempt, cancel the failure path, apply normal post-success maintenance handling, and clear retry state.
- [x] 4.6 When the cap has elapsed and the in-flight attempt subsequently fails, declare the transition failed (do not wait indefinitely).

## 5. Failed-transition declaration & metrics

- [x] 5.1 Declare the transition failed when retries are exhausted or when the cap has elapsed, no success occurred, and no attempt remains in flight: keep maintenance on and clear tracked state.
- [x] 5.2 Set `switchover_logic_gauge{resource="transition"}` to a distinct failed-transition value when a transition is declared failed (consistent with existing `switchover_*` metric naming).
- [x] 5.3 Increment `switchover_pipeline{state="Retry"}` for each retry attempt started so retry progress is visible.

## 6. Unit tests (pytest)

- [x] 6.1 Add `pytest` as a dev dependency in `pyproject.toml` and a `src/tests/` (or `tests/`) test package with shared fixtures (Logic with fake clock, crafted PipelineRun event builders).
- [x] 6.2 Test: tracked failure schedules a retry only after the interval; untracked failure is ignored.
- [x] 6.3 Test: retries are bounded by max count; "transition failed" emitted exactly once on exhaustion.
- [x] 6.4 Test: cap stops new attempts but lets an in-flight attempt finish and be evaluated.
- [x] 6.5 Test: cap is measured from first attempt start, not first observed failure.
- [x] 6.6 Test: in-flight attempt fails after cap → transition declared failed.
- [x] 6.7 Test: a successful retry cancels the failure path and clears state.
- [x] 6.8 Test: defaults applied when env-vars unset; overrides honored when set.

## 7. Mock / integration coverage

- [x] 7.1 Add `mock/data/k8s/pipelineruns-failed.json` (a Failed terminal PipelineRun fixture).
- [x] 7.2 Add a trigger counter to `mock/main.py` so the first N `/tekton` triggers serve the failed fixture, then flip to completed — exercising retry → eventual success via docker-compose.
- [x] 7.3 Add a mock mode where every `/tekton` trigger returns the failed fixture (never flips to completed) to exercise retry exhaustion and transition-failed declaration end-to-end.
- [ ] 7.4 (Optional) Add a hook to emit an untracked MODIFIED PipelineRun event, to support future AC #3 work.
- [x] 7.5 Verify both mock paths manually via docker-compose (retry-then-success and always-fail exhaustion) and capture an updated activity baseline if appropriate.

## 8. Validation

- [x] 8.1 Run `openspec validate retry-failed-pipelines --strict` and resolve any issues.
- [x] 8.2 Run the pytest suite green; confirm no regression in existing transitions.
