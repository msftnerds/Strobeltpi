from __future__ import annotations

import types

from strobeltpi.docker_metrics import DockerMetricsCollector

class DummyContainer:
    name = "dummy"
    attrs = {"State": {"Status": "running", "StartedAt": "2025-01-01T00:00:00Z"}}

    def stats(self, stream=False):  # noqa: D401, ANN001
        return {
            "cpu_stats": {"cpu_usage": {"total_usage": 200000000}, "system_cpu_usage": 400000000, "online_cpus": 4},
            "precpu_stats": {"cpu_usage": {"total_usage": 100000000}, "system_cpu_usage": 300000000},
            "memory_stats": {"usage": 1024, "limit": 2048},
            "blkio_stats": {"io_service_bytes_recursive": [
                {"op": "Read", "value": 100},
                {"op": "Write", "value": 50},
            ]},
        }

class DummyClient:
    def containers(self):  # noqa: D401
        return self

    def list(self, all=True):  # noqa: D401, ANN001
        return [DummyContainer()]

def test_collect_monkeypatch(monkeypatch):  # noqa: D401
    dummy_client = types.SimpleNamespace(containers=types.SimpleNamespace(list=lambda all=True: [DummyContainer()]))
    monkeypatch.setattr("strobeltpi.docker_metrics.docker.from_env", lambda: dummy_client)
    collector = DockerMetricsCollector()
    metrics = collector.collect()
    assert metrics
    m = metrics[0]
    assert m.container_name == "dummy"
    assert m.status == "running"
