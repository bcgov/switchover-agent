## ADDED Requirements

### Requirement: Automatic retry of a failed deployment pipeline

The agent SHALL automatically re-trigger the switchover deployment pipeline when the tracked PipelineRun for the current transition reaches a failed terminal state (`Failed` or `PipelineRunCancelled`). A re-trigger SHALL only occur for the PipelineRun whose event id matches the transition the agent is currently tracking; events for untracked or unrelated PipelineRuns SHALL NOT cause a retry.

The agent SHALL wait a configurable retry interval (default 5 minutes) after observing a failure before starting the next attempt, and SHALL retry at most a configurable maximum number of times (default 2 retries, i.e. up to 3 total attempts).

The agent SHALL evaluate scheduled retries and the total-duration cap on a periodic heartbeat (`tick` event) enqueued at a 30-second cadence.

#### Scenario: Tracked pipeline fails and is retried

- **WHEN** the agent is tracking a transition and observes the tracked PipelineRun reach `Failed`
- **AND** the maximum retry count has not been reached and the total-duration cap has not elapsed
- **THEN** the agent SHALL schedule a re-trigger of the deployment pipeline after the configured retry interval
- **AND** SHALL begin tracking the new PipelineRun as the current attempt

#### Scenario: Untracked pipeline failure is ignored

- **WHEN** the agent observes a `Failed` PipelineRun whose event id does not match the transition currently being tracked
- **THEN** the agent SHALL NOT trigger a retry and SHALL NOT alter the current transition's retry state

#### Scenario: Retry interval is honored

- **WHEN** a tracked pipeline failure is observed
- **THEN** the agent SHALL NOT start the next attempt before the configured retry interval has elapsed

### Requirement: Bounded total retry duration

The agent SHALL stop starting new pipeline attempts once a configurable total-duration cap (default 15 minutes), measured from the start of the first attempt for the transition, has elapsed. Any pipeline attempt already in flight when the cap is reached SHALL be allowed to finish and SHALL still be evaluated for success or failure.

#### Scenario: Cap reached with an in-flight run

- **WHEN** the total-duration cap elapses while a pipeline attempt is still running
- **THEN** the agent SHALL NOT start any further attempts
- **AND** SHALL continue to track the in-flight attempt to its terminal state

#### Scenario: Cap reached prevents a scheduled retry

- **WHEN** a retry is due but the total-duration cap has already elapsed
- **THEN** the agent SHALL NOT start the retry

#### Scenario: Cap measured from first attempt start

- **WHEN** the agent is tracking a transition
- **THEN** the total-duration cap SHALL be measured from the start of the first attempt for that transition, not from the first observed failure

### Requirement: Declare transition failed when retries are exhausted

The agent SHALL declare the transition failed when no attempt succeeds and either the maximum retry count is reached or the total-duration cap elapses with no successful and no in-flight attempt. When a transition is declared failed, the agent SHALL keep maintenance mode on and SHALL clear the tracked transition state so subsequent unrelated pipeline events do not re-enter retry handling.

#### Scenario: All attempts fail

- **WHEN** the final permitted attempt reaches a failed terminal state
- **THEN** the agent SHALL declare the transition failed
- **AND** SHALL keep maintenance mode on
- **AND** SHALL stop retrying

#### Scenario: A retry succeeds before exhaustion

- **WHEN** a retry attempt reaches a successful terminal state (`Completed` / `Succeeded`) before retries are exhausted
- **THEN** the agent SHALL NOT declare the transition failed
- **AND** SHALL apply the normal post-success behavior (maintenance handled per the original transition) and clear retry state

#### Scenario: In-flight attempt fails after cap

- **WHEN** the total-duration cap has elapsed, no further attempts are started, and the in-flight attempt subsequently reaches a failed terminal state
- **THEN** the agent SHALL declare the transition failed
- **AND** SHALL keep maintenance mode on
- **AND** SHALL stop retrying

### Requirement: Metrics expose retry progress and failed transitions

The agent SHALL emit metrics that make a failed transition unambiguous for alerting, distinct from the count of individual failed pipeline runs. Metrics SHALL follow the existing `switchover_*` naming conventions used elsewhere in the agent. Metrics SHALL allow an operator to see retry attempts as they happen and to detect when all retries for a transition have completed and failed.

The agent SHALL use:
- `switchover_transition_failed{release="<release-namespace>"}` incremented once when a transition is declared failed (alerting)
- `switchover_transition{release="<release-namespace>"}` gauge set to `3` when a transition is declared failed (status/dashboard)
- `switchover_logic_gauge{resource="transition"}` with a distinct value indicating a failed transition (legacy aggregate status)
- `switchover_pipeline{state="Retry"}` incremented for each retry attempt started

The `release` label SHALL match the Tekton pipeline `release-namespace` parameter (same source as `switchover_pipeline`).

#### Scenario: Failed transition is observable

- **WHEN** a transition is declared failed
- **THEN** the agent SHALL increment `switchover_transition_failed{release="<release-namespace>"}` exactly once
- **AND** the agent SHALL set `switchover_transition{release="<release-namespace>"}` to `3`

#### Scenario: Retry attempts are observable

- **WHEN** the agent starts a retry attempt
- **THEN** the agent SHALL increment `switchover_pipeline{state="Retry"}` so retry progress is visible

### Requirement: Configurable retry parameters

The retry interval, maximum retry count, and total-duration cap SHALL be configurable via environment variables surfaced through `config.py`, each with the documented defaults (5 minutes, 2 retries, 15 minutes). Absent configuration SHALL fall back to the defaults without error.

#### Scenario: Defaults applied when unset

- **WHEN** the retry environment variables are not set
- **THEN** the agent SHALL use the default interval (5 min), default max retries (2), and default cap (15 min)

#### Scenario: Overrides honored when set

- **WHEN** an operator sets a retry environment variable to a valid value
- **THEN** the agent SHALL use the configured value instead of the default
