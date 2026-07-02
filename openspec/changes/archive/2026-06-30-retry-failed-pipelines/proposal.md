## Why

During a switchover, the agent triggers a Tekton deployment pipeline and waits for it to finish. If that single pipeline run fails, the transition stalls with maintenance mode left on and no automatic recovery — exactly the failure mode seen in the 2026-06-04 incomplete-failover PIR. The agent should automatically retry a failed pipeline a bounded number of times before declaring the transition failed, and expose metrics that make a failed transition unambiguous for alerting.

## What Changes

- When a tracked Tekton PipelineRun reaches a failed terminal state (`Failed` / `PipelineRunCancelled`), the agent automatically re-triggers the deployment after a fixed interval instead of leaving the transition stalled.
- Retries are bounded: a configurable per-attempt interval (default 5 min), a configurable max retry count (default 2), and a configurable total-duration cap (default 15 min). At the cap, the agent stops starting new pipelines but allows any in-flight run to finish.
- The agent declares the **transition failed** once no attempt has succeeded and there is no attempt still in flight — i.e. after retries are exhausted, or after the cap when any run still in progress at the cap has been allowed to finish and also failed. On a failed transition, maintenance mode stays on and a distinct metric is emitted so alerting can fire on a failed transition rather than on every single failed pipeline.
- Retry/transition state is tracked per transition attempt so behavior is deterministic and testable.
- Retry interval, max retries, and total cap are exposed as env-vars via `config.py`.

Out of scope (related, follow-up ACs on APS-4631): AC #2 alert-condition update, AC #3 only-increment-counters-on-tracked-runs, AC #4 maintenance-mode cleanup by matching any successful pipeline for the environment.

## Capabilities

### New Capabilities

- `pipeline-retry`: Automatic, bounded retrying of failed switchover deployment pipelines, including retry scheduling, the total-duration cap, terminal "transition failed" declaration, and the metrics that expose retry progress and failed transitions.

### Modified Capabilities

<!-- None: no existing specs in openspec/specs/ yet. -->

## Impact

- **Code**: `src/logic.py` (`Logic.handler` PipelineRun handling + retry/transition state), `src/transitions/initiate_primary.py` / `initiate_standby.py` (re-trigger path), `src/config.py` (new retry settings), `src/logic.py` metrics (new failed-transition metric/labels).
- **Tooling/tests**: new pytest unit layer around retry helpers (`_on_pipeline_*`, `_maybe_retry`) with an injectable clock; mock extensions (`mock/`) to simulate failed pipeline runs and the retry cycle.
- **Operational**: new metric(s) for alerting; new optional env-vars (with safe defaults) — no breaking change to existing deployments.
- **Dependencies**: add `pytest` as a dev dependency.
