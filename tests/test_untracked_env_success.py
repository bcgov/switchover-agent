"""Unit tests for untracked same-env pipeline success clears stuck maintenance."""
import pytest
from unittest.mock import patch

# solution_namespace in conftest is "test-ns" (KUBE_NAMESPACE env var).
RELEASE = "test-ns"
WRONG_RELEASE = "other-ns"


def _declare_failed(logic, clock, event_id="evt-001"):
    """Drive logic to a stuck failed-transition state."""
    logic.set_pipeline(dict(event_id=event_id, start_ts=clock(), maintenance=False))
    logic._declare_transition_failed()


# ---------------------------------------------------------------------------
# transition_failed flag lifecycle
# ---------------------------------------------------------------------------

class TestTransitionFailedFlag:
    def test_starts_false(self, logic):
        assert logic.transition_failed is False

    def test_set_on_declare(self, logic, clock):
        _declare_failed(logic, clock)
        assert logic.transition_failed is True

    def test_cleared_on_tracked_success(self, logic, clock):
        _declare_failed(logic, clock)
        logic.set_pipeline(dict(event_id="evt-002", start_ts=clock(), maintenance=False))
        with patch("logic.maintenance_off"):
            logic._on_pipeline_success("test")
        assert logic.transition_failed is False

    def test_cleared_on_untracked_env_success(self, logic, clock):
        _declare_failed(logic, clock)
        with patch("logic.maintenance_off"):
            logic._on_untracked_env_success("test")
        assert logic.transition_failed is False


# ---------------------------------------------------------------------------
# _on_untracked_env_success helper behaviour
# ---------------------------------------------------------------------------

class TestUntrackedEnvSuccessHelper:
    def test_calls_maintenance_off(self, logic, clock):
        _declare_failed(logic, clock)
        with patch("logic.maintenance_off") as mock_off:
            logic._on_untracked_env_success("test")
        mock_off.assert_called_once()

    def test_resets_legacy_transition_gauge(self, logic, clock):
        _declare_failed(logic, clock)
        assert logic.GAUGE.labels(resource="transition")._value.get() == 3.0
        with patch("logic.maintenance_off"):
            logic._on_untracked_env_success("test")
        assert logic.GAUGE.labels(resource="transition")._value.get() == 0.0

    def test_resets_per_release_transition_gauge(self, logic, clock):
        _declare_failed(logic, clock)
        assert logic.TRANSITION_GAUGE.labels(release=RELEASE)._value.get() == 3.0
        with patch("logic.maintenance_off"):
            logic._on_untracked_env_success("test")
        assert logic.TRANSITION_GAUGE.labels(release=RELEASE)._value.get() == 0.0

    def test_transition_failed_counter_unchanged(self, logic, clock):
        """does not increment the failed counter — that already fired on declare."""
        _declare_failed(logic, clock)
        count_before = logic.TRANSITION_FAILED.labels(release=RELEASE)._value.get()
        with patch("logic.maintenance_off"):
            logic._on_untracked_env_success("test")
        assert logic.TRANSITION_FAILED.labels(release=RELEASE)._value.get() == count_before
