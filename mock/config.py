import os

config = {
  "dns": "active.json",
  "k8s.configmaps": "configmaps.json",
  "k8s.pipelineruns": "pipelineruns.json",
  "k8s.services": "services.json",
  "k8s.statefulsets": "statefulsets.json",
  "patroni.cluster": "cluster.json",
  "patroni.config": "config.json",
  "activity": [],
  # Retry simulation controls.
  # "pipeline_fail_mode": None         — normal: always complete (default)
  # "pipeline_fail_mode": "fail-then-succeed" — first N triggers fail, then succeed
  # "pipeline_fail_mode": "always-fail"       — every trigger returns failed fixture
  "pipeline_fail_mode": None,
  "pipeline_fail_count": 1,   # used by fail-then-succeed: number of initial failures
  "pipeline_trigger_count": 0,
}
