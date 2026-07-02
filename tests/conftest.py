"""Shared fixtures for Logic unit tests."""
import sys
import os
import datetime
import pytest

# Make src/ importable without installing the package.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

# Minimal env so config.py doesn't blow up on missing vars.
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("KUBE_HEALTH_NAMESPACE", "test-tools")
os.environ.setdefault("KUBE_NAMESPACE", "test-ns")
os.environ.setdefault("KUBE_TEKTON_NAMESPACE", "test-tools")
os.environ.setdefault("TEKTON_URL", "http://mock/tekton")
os.environ.setdefault("TEKTON_GITHUB_REPO", "https://github.com/example/repo.git")
os.environ.setdefault("TEKTON_GITHUB_REF", "refs/heads/deploy/dev")
os.environ.setdefault("TEKTON_HMAC_SECRET", "")
os.environ.setdefault("TERRAFORM_TFVARS", "tfvars-test")
os.environ.setdefault("PROMETHEUS_MULTIPROC_DIR", "/tmp")
os.environ.setdefault("PY_ENV", "test")


class FakeClock:
    """Monotonically advanceable fake clock for tests."""

    def __init__(self, start: datetime.datetime = None):
        self._now = start or datetime.datetime(2026, 1, 1, 0, 0, 0)

    def __call__(self) -> datetime.datetime:
        return self._now

    def advance(self, seconds: float):
        self._now += datetime.timedelta(seconds=seconds)


def make_pipeline_run_event(event_id: str, event_type: str = "MODIFIED", reason: str = "Running") -> dict:
    """Build a kube_stream PipelineRun event dict."""
    obj = {
        "kind": "PipelineRun",
        "apiVersion": "tekton.dev/v1",
        "spec": {
            "params": [
                {"name": "git-release-branch", "value": "deploy/test"},
                {"name": "release-namespace", "value": "test-ns"},
            ]
        },
        "metadata": {
            "name": "pipeline-run-test",
            "labels": {
                "triggers.tekton.dev/triggers-eventid": event_id,
            },
        },
    }
    if reason != "Running":
        obj["status"] = {
            "conditions": [
                {"reason": reason, "status": "True" if reason in ("Completed", "Succeeded") else "False", "message": ""}
            ]
        }
    return {"event": "kube_stream", "kind": "tekton", "data": {"type": event_type, "object": obj}}


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def logic(clock):
    """A Logic instance wired with an injectable clock and prometheus disabled."""
    # Reset prometheus registry to avoid duplicate metric errors between tests.
    from prometheus_client import REGISTRY
    collectors = list(REGISTRY._names_to_collectors.values())
    for c in set(collectors):
        try:
            REGISTRY.unregister(c)
        except Exception:
            pass

    from logic import Logic
    logic = Logic()
    logic._now_fn = clock
    return logic
