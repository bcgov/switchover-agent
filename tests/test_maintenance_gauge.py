"""Maintenance is recorded in one file and published by the metrics process."""
from transitions import shared


def _isolate_state_file(monkeypatch, tmp_path):
    monkeypatch.setattr(shared, 'maintenance_state_path', lambda: str(tmp_path / 'state'))


def _skip_cluster_calls(monkeypatch):
    monkeypatch.setattr(shared, 'restart_deployment', lambda *args, **kwargs: None)
    monkeypatch.setattr(shared, 'keycloak_service_block', lambda *args, **kwargs: None)
    monkeypatch.setattr(shared, 'keycloak_service_flow', lambda *args, **kwargs: None)
    monkeypatch.setattr(shared, 'set_maintenance', lambda *args, **kwargs: None)


def test_missing_state_file_reads_as_off(monkeypatch, tmp_path):
    _isolate_state_file(monkeypatch, tmp_path)
    assert shared.read_maintenance_state() == 0.0


def test_maintenance_toggle_updates_file_not_worker_gauge(monkeypatch, tmp_path):
    _isolate_state_file(monkeypatch, tmp_path)
    _skip_cluster_calls(monkeypatch)
    published = []
    monkeypatch.setattr(shared.MAINT, 'set', lambda value: published.append(value))

    shared.maintenance_on()
    assert published == []
    assert shared.read_maintenance_state() == 1.0

    shared.publish_maintenance_gauge()
    assert published == [1.0]

    shared.maintenance_off('test')
    assert published == [1.0]
    assert shared.read_maintenance_state() == 0.0

    shared.publish_maintenance_gauge()
    assert published == [1.0, 0.0]
