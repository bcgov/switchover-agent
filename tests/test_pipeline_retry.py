"""Unit tests for pipeline retry helpers in Logic (_on_pipeline_*, _maybe_retry)."""
import datetime
import os
import pytest
from unittest.mock import patch

# Defaults mirrored from config.py / conftest pipeline fixtures
INTERVAL = 30    # post-failure retry backoff
ATTEMPT_TIMEOUT = 360
MAX_RETRIES = 2
CAP = 900        # 15 min
RELEASE = "test-ns"


def _failed_counter(logic):
    return logic.TRANSITION_FAILED.labels(release=RELEASE)


def _transition_gauge(logic):
    return logic.TRANSITION_GAUGE.labels(release=RELEASE)


def _seed_pipeline(logic, clock, event_id="evt-001", maintenance=False):
    """Set up an in-progress pipeline as if initiate_primary/standby just ran."""
    logic.set_pipeline(dict(event_id=event_id, start_ts=clock(), maintenance=maintenance))


def _fail_pipeline(logic, event_id, py_env="test"):
    """Drive a tracked pipeline to Failed; no-op when event_id does not match pipeline."""
    if logic.pipeline['event_id'] != event_id:
        return
    logic._on_pipeline_failure(py_env)


def _succeed_pipeline(logic, event_id, py_env="test"):
    """Drive a tracked pipeline to Completed; no-op when event_id does not match pipeline."""
    if logic.pipeline['event_id'] != event_id:
        return
    with patch("logic.maintenance_off"):
        logic._on_pipeline_success(py_env)


def _tick(logic):
    """Drive a tick event."""
    logic._maybe_retry()


# ---------------------------------------------------------------------------
# 6.2 — tracked failure schedules a retry only after the interval;
#         untracked failure is ignored
# ---------------------------------------------------------------------------

class TestRetryScheduling:
    def test_tracked_failure_schedules_retry(self, logic, clock):
        _seed_pipeline(logic, clock, event_id="evt-001")
        _fail_pipeline(logic, "evt-001")

        rs = logic.retry_state
        assert rs is not None, "retry_state should remain after failure"
        assert rs['retry_at'] is not None, "retry_at should be set"
        assert rs['release'] == RELEASE
        expected_retry_at = clock() + datetime.timedelta(seconds=INTERVAL)
        assert rs['retry_at'] == expected_retry_at

    def test_untracked_failure_does_not_schedule_retry(self, logic, clock):
        _seed_pipeline(logic, clock, event_id="evt-001")
        _fail_pipeline(logic, "evt-UNTRACKED")

        # retry_state event_id must not change and retry_at must stay None
        rs = logic.retry_state
        assert rs['event_id'] == "evt-001"
        assert rs.get('retry_at') is None

    def test_retry_not_fired_before_interval(self, logic, clock):
        _seed_pipeline(logic, clock, event_id="evt-001")
        _fail_pipeline(logic, "evt-001")

        clock.advance(INTERVAL - 1)
        with patch("logic.Logic._fire_retry") as mock_fire:
            _tick(logic)
            mock_fire.assert_not_called()

    def test_retry_fired_after_interval(self, logic, clock):
        _seed_pipeline(logic, clock, event_id="evt-001")
        _fail_pipeline(logic, "evt-001")

        clock.advance(INTERVAL)
        with patch("logic.Logic._fire_retry") as mock_fire:
            _tick(logic)
            mock_fire.assert_called_once()


# ---------------------------------------------------------------------------
# 6.3 — retries are bounded by max count;
#         "transition failed" emitted exactly once on exhaustion
# ---------------------------------------------------------------------------

class TestRetryBounds:
    def _simulate_retry_fired(self, logic, clock, new_event_id):
        """Simulate _fire_retry: bump attempts_made, set new event_id, clear retry_at."""
        rs = logic.retry_state
        rs['attempts_made'] += 1
        rs['retry_at'] = None
        rs['event_id'] = new_event_id
        logic.pipeline = dict(event_id=new_event_id, start_ts=clock(), maintenance=rs['maintenance'],
                              name=None, cancelling=False)

    def test_retry_count_bounded(self, logic, clock):
        _seed_pipeline(logic, clock, event_id="evt-001")
        assert logic.retry_state['attempts_made'] == 0

        # Initial failure → schedules retry
        _fail_pipeline(logic, "evt-001")
        assert logic.retry_state is not None
        assert logic.retry_state['retry_at'] is not None

        # Fire retry 1 and fail it
        self._simulate_retry_fired(logic, clock, "evt-002")
        _fail_pipeline(logic, "evt-002")
        assert logic.retry_state is not None
        assert logic.retry_state['attempts_made'] == 1

        # Fire retry 2 (max) and fail it → exhausted
        self._simulate_retry_fired(logic, clock, "evt-003")
        _fail_pipeline(logic, "evt-003")
        assert logic.retry_state is None, "retry_state should be cleared after exhaustion"
        assert logic.pipeline['event_id'] is None

    def test_transition_failed_emitted_once_on_exhaustion(self, logic, clock):
        _seed_pipeline(logic, clock, event_id="evt-001")
        failed_counter = _failed_counter(logic)
        count_before = failed_counter._value.get()

        # Initial failure schedules retry — counter must not fire yet
        _fail_pipeline(logic, "evt-001")
        assert failed_counter._value.get() == count_before

        for i in range(MAX_RETRIES):
            new_id = f"evt-{i+2:03d}"
            logic.retry_state['attempts_made'] += 1
            logic.retry_state['retry_at'] = None
            logic.retry_state['event_id'] = new_id
            logic.pipeline = dict(event_id=new_id, start_ts=clock(), maintenance=False,
                                  name=None, cancelling=False)
            _fail_pipeline(logic, new_id)
            if i < MAX_RETRIES - 1:
                assert failed_counter._value.get() == count_before

        assert failed_counter._value.get() == count_before + 1.0
        assert _transition_gauge(logic)._value.get() == 3.0


# ---------------------------------------------------------------------------
# 6.4 — cap stops new attempts but lets in-flight attempt finish
# ---------------------------------------------------------------------------

class TestCapBehavior:
    def test_cap_prevents_new_retry_when_nothing_in_flight(self, logic, clock):
        _seed_pipeline(logic, clock, event_id="evt-001")
        _fail_pipeline(logic, "evt-001")

        # Jump past the cap
        clock.advance(CAP + 1)
        with patch("logic.Logic._fire_retry") as mock_fire:
            _tick(logic)
            mock_fire.assert_not_called()

        assert logic.retry_state is None, "Transition should be declared failed"

    def test_in_flight_run_allowed_to_finish_after_cap(self, logic, clock):
        """An attempt started before the cap may still be in-flight at cap time;
        it should be evaluated, not abandoned."""
        _seed_pipeline(logic, clock, event_id="evt-001")

        # Stay within attempt timeout and cap; tick must not cancel a healthy run.
        with patch("logic.config") as mock_config:
            mock_config.get = lambda key: (
                99999 if key == 'pipeline_attempt_timeout_seconds'
                else __import__('config').config.get(key))
            clock.advance(CAP - 1)
            _tick(logic)

        assert logic.pipeline['event_id'] == "evt-001"

        clock.advance(60)
        _succeed_pipeline(logic, "evt-001")

        assert logic.retry_state is None
        assert logic.pipeline['event_id'] is None


# ---------------------------------------------------------------------------
# 6.5 — cap is measured from first attempt start, not first observed failure
# ---------------------------------------------------------------------------

class TestCapMeasurement:
    def test_cap_measured_from_first_attempt_start(self, logic, clock):
        start = clock()
        _seed_pipeline(logic, clock, event_id="evt-001")

        # Fail 30 min in (well past cap if measured from failure, but transition
        # started at clock() so transition_started_ts == start)
        clock.advance(30)  # 30s first, still within cap
        _fail_pipeline(logic, "evt-001")

        rs = logic.retry_state
        assert rs is not None
        assert rs['transition_started_ts'] == start, \
            "transition_started_ts must be set at first trigger, not at failure"


# ---------------------------------------------------------------------------
# 6.6 — in-flight attempt fails after cap → transition declared failed
# ---------------------------------------------------------------------------

class TestInFlightFailAfterCap:
    def test_in_flight_fail_after_cap_declares_failed(self, logic, clock):
        _seed_pipeline(logic, clock, event_id="evt-001")
        count_before = _failed_counter(logic)._value.get()

        # Advance past cap while pipeline is still in flight (no failure yet)
        clock.advance(CAP + 1)

        # Pipeline finishes with failure
        _fail_pipeline(logic, "evt-001")

        assert logic.retry_state is None
        assert logic.pipeline['event_id'] is None
        assert _failed_counter(logic)._value.get() == count_before + 1.0
        assert _transition_gauge(logic)._value.get() == 3.0


# ---------------------------------------------------------------------------
# 6.7 — a successful retry cancels the failure path and clears state
# ---------------------------------------------------------------------------

class TestRetrySuccess:
    def test_success_clears_retry_state(self, logic, clock):
        _seed_pipeline(logic, clock, event_id="evt-001")
        _fail_pipeline(logic, "evt-001")

        assert logic.retry_state is not None

        # Simulate retry being fired
        logic.pipeline['event_id'] = "evt-002"
        logic.retry_state['event_id'] = "evt-002"

        _succeed_pipeline(logic, "evt-002")

        assert logic.retry_state is None
        assert logic.pipeline['event_id'] is None

    def test_success_does_not_set_failed_signals(self, logic, clock):
        _seed_pipeline(logic, clock, event_id="evt-001")
        count_before = _failed_counter(logic)._value.get()
        _succeed_pipeline(logic, "evt-001")

        assert _failed_counter(logic)._value.get() == count_before
        assert _transition_gauge(logic)._value.get() == 0.0


# ---------------------------------------------------------------------------
# Attempt timeout — hung pipeline cancelled once, then fast retry on terminal Cancelled
# ---------------------------------------------------------------------------

class TestAttemptTimeout:
    def _seed_in_flight(self, logic, clock, event_id="evt-001", name="pipeline-run-test"):
        logic.set_pipeline(dict(event_id=event_id, start_ts=clock(), maintenance=False, name=name))

    def test_timeout_issues_cancel_once(self, logic, clock):
        self._seed_in_flight(logic, clock)
        clock.advance(ATTEMPT_TIMEOUT)

        with patch("logic.Logic._cancel_timed_out_pipeline") as mock_cancel:
            _tick(logic)
            mock_cancel.assert_called_once()

    def test_cancel_sets_cancelling_flag(self, logic, clock):
        self._seed_in_flight(logic, clock)
        clock.advance(ATTEMPT_TIMEOUT)

        with patch("clients.tekton.cancel_pipeline_run"):
            _tick(logic)

        assert logic.pipeline['cancelling'] is True
        assert logic.pipeline['event_id'] == "evt-001"

    def test_no_duplicate_cancel_on_subsequent_ticks(self, logic, clock):
        self._seed_in_flight(logic, clock)
        clock.advance(ATTEMPT_TIMEOUT)

        with patch("clients.tekton.cancel_pipeline_run") as mock_cancel:
            _tick(logic)
            _tick(logic)
            mock_cancel.assert_called_once()

    def test_terminal_cancelled_schedules_fast_retry(self, logic, clock):
        self._seed_in_flight(logic, clock)
        clock.advance(ATTEMPT_TIMEOUT)

        with patch("clients.tekton.cancel_pipeline_run"):
            _tick(logic)

        _fail_pipeline(logic, "evt-001")

        expected_retry_at = clock() + datetime.timedelta(seconds=INTERVAL)
        assert logic.retry_state['retry_at'] == expected_retry_at


# ---------------------------------------------------------------------------
# 6.8 — defaults applied when env-vars unset; overrides honored when set
# ---------------------------------------------------------------------------

class TestConfigDefaults:
    def test_defaults_when_env_unset(self):
        for var in ("PIPELINE_RETRY_INTERVAL_SECONDS", "PIPELINE_MAX_RETRIES",
                    "PIPELINE_RETRY_TOTAL_CAP_SECONDS", "PIPELINE_ATTEMPT_TIMEOUT_SECONDS"):
            os.environ.pop(var, None)

        # Re-import config to pick up cleared env
        import importlib
        import config as cfg_module
        importlib.reload(cfg_module)
        from config import config as cfg

        assert cfg['pipeline_retry_interval_seconds'] == 30
        assert cfg['pipeline_max_retries'] == 2
        assert cfg['pipeline_retry_total_cap_seconds'] == 900
        assert cfg['pipeline_attempt_timeout_seconds'] == 360

    def test_overrides_honored(self):
        os.environ["PIPELINE_RETRY_INTERVAL_SECONDS"] = "60"
        os.environ["PIPELINE_MAX_RETRIES"] = "5"
        os.environ["PIPELINE_RETRY_TOTAL_CAP_SECONDS"] = "600"
        os.environ["PIPELINE_ATTEMPT_TIMEOUT_SECONDS"] = "120"

        import importlib
        import config as cfg_module
        importlib.reload(cfg_module)
        from config import config as cfg

        assert cfg['pipeline_retry_interval_seconds'] == 60
        assert cfg['pipeline_max_retries'] == 5
        assert cfg['pipeline_retry_total_cap_seconds'] == 600
        assert cfg['pipeline_attempt_timeout_seconds'] == 120

        # Clean up
        del os.environ["PIPELINE_RETRY_INTERVAL_SECONDS"]
        del os.environ["PIPELINE_MAX_RETRIES"]
        del os.environ["PIPELINE_RETRY_TOTAL_CAP_SECONDS"]
        del os.environ["PIPELINE_ATTEMPT_TIMEOUT_SECONDS"]
        importlib.reload(cfg_module)
