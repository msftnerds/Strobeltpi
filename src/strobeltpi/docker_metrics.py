from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List

import docker
import psutil
import structlog

logger = structlog.get_logger()

def _safe_div(n: float, d: float) -> float:
    return round(n / d, 4) if d else 0.0

@dataclass
class ContainerMetrics:
    container_name: str
    status: str
    uptime_seconds: float
    cpu_percent: float
    mem_usage_bytes: int
    mem_limit_bytes: int | None
    mem_percent: float
    blk_read_bytes: int
    blk_write_bytes: int
    collected_utc: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "container_name": self.container_name,
            "status": self.status,
            "uptime_seconds": self.uptime_seconds,
            "cpu_percent": self.cpu_percent,
            "mem_usage_bytes": self.mem_usage_bytes,
            "mem_limit_bytes": self.mem_limit_bytes,
            "mem_percent": self.mem_percent,
            "blk_read_bytes": self.blk_read_bytes,
            "blk_write_bytes": self.blk_write_bytes,
            "collected_utc": self.collected_utc,
        }

class DockerMetricsCollector:
    def __init__(self) -> None:
        self._client = None  # lazy to avoid failing when docker not accessible during tests

    @property
    def client(self):  # type: ignore[override]
        if self._client is None:
            try:
                self._client = docker.from_env()
            except Exception as e:  # noqa: BLE001
                # Surface permission guidance if likely a socket permission issue inside container
                perm_hint = None
                if isinstance(e, PermissionError) or "permission" in str(e).lower():
                    perm_hint = (
                        "Docker socket permission denied. If running in a container, add: --group-add $(stat -c '%g' /var/run/docker.sock) "
                        "or run as a user with access to the docker socket. For quick test only: run with --user root (not recommended for prod)."
                    )
                logger.error(
                    "docker_client_init_failed",
                    error=str(e),
                    hint=perm_hint,
                )
                raise
        return self._client

    def collect(self) -> List[ContainerMetrics]:
        now = dt.datetime.utcnow()
        metrics: List[ContainerMetrics] = []
        for c in self.client.containers.list(all=True):
            try:
                inspect = c.attrs
                state = inspect.get("State", {})
                started_at = state.get("StartedAt")
                uptime_seconds = 0.0
                if started_at and started_at != "0001-01-01T00:00:00Z":
                    try:
                        started_dt = dt.datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                        uptime_seconds = (now - started_dt.replace(tzinfo=None)).total_seconds()
                    except Exception:  # noqa: BLE001
                        uptime_seconds = 0.0
                stats = c.stats(stream=False)
                # CPU calculation per docker docs
                cpu_stats = stats.get("cpu_stats", {})
                precpu_stats = stats.get("precpu_stats", {})
                cpu_delta = cpu_stats.get("cpu_usage", {}).get("total_usage", 0) - precpu_stats.get("cpu_usage", {}).get("total_usage", 0)
                system_delta = cpu_stats.get("system_cpu_usage", 0) - precpu_stats.get("system_cpu_usage", 0)
                online_cpus = cpu_stats.get("online_cpus") or cpu_stats.get("cpu_usage", {}).get("percpu_usage")
                if isinstance(online_cpus, list):
                    cpu_count = len(online_cpus)
                else:
                    cpu_count = online_cpus or psutil.cpu_count(logical=True) or 1
                cpu_percent = 0.0
                if system_delta > 0 and cpu_delta > 0:
                    cpu_percent = (cpu_delta / system_delta) * cpu_count * 100.0
                mem_stats = stats.get("memory_stats", {})
                mem_usage = mem_stats.get("usage") or 0
                mem_limit = mem_stats.get("limit")
                mem_percent = _safe_div(mem_usage, mem_limit) * 100 if mem_limit else 0.0
                blkio = stats.get("blkio_stats", {}).get("io_service_bytes_recursive", []) or []
                read_bytes = 0
                write_bytes = 0
                for op in blkio:
                    if op.get("op") == "Read":
                        read_bytes += op.get("value", 0)
                    elif op.get("op") == "Write":
                        write_bytes += op.get("value", 0)
                metrics.append(
                    ContainerMetrics(
                        container_name=c.name,
                        status=state.get("Status", "unknown"),
                        uptime_seconds=uptime_seconds,
                        cpu_percent=round(cpu_percent, 2),
                        mem_usage_bytes=mem_usage,
                        mem_limit_bytes=mem_limit,
                        mem_percent=round(mem_percent, 2),
                        blk_read_bytes=read_bytes,
                        blk_write_bytes=write_bytes,
                        collected_utc=now.isoformat() + "Z",
                    )
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("container_metrics_error", container=c.name, error=str(e))
        return metrics
