from __future__ import annotations

import os
import signal
import sys
import time
from contextlib import suppress

import structlog

from .config import RuntimeConfig
from .docker_metrics import DockerMetricsCollector
from .eventhub_sender import EventHubSender

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.dict_tracebacks,
        structlog.processors.JSONRenderer(),
    ],
)
logger = structlog.get_logger()

def main() -> int:
    key_vault_url = os.getenv("KEYVAULT_URL")
    if not key_vault_url:
        logger.error("missing_keyvault_url")
        return 2
    try:
        cfg = RuntimeConfig.from_key_vault(key_vault_url)
    except Exception as e:  # noqa: BLE001
        logger.error("config_init_failed", error=str(e))
        return 3

    collector = DockerMetricsCollector()
    sender = EventHubSender(cfg)

    refresh_interval = int(os.getenv("CONFIG_REFRESH_SECONDS", "900"))  # 15 min default
    last_refresh = time.time()

    shutdown = False

    def _stop(signum, frame):  # noqa: D401, ANN001, D403
        nonlocal shutdown
        shutdown = True
        logger.info("shutdown_signal", signum=signum)

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    interval = float(os.getenv("SCRAPE_INTERVAL_SECONDS", "15"))

    logger.info("agent_started", interval_seconds=interval, host_id=cfg.metrics_host_id)
    while not shutdown:
        start = time.time()
        try:
            # Periodic credential/config refresh
            if (time.time() - last_refresh) >= refresh_interval:
                try:
                    changed, new_cfg = RuntimeConfig.refresh_if_changed(cfg)
                    if changed:
                        logger.info("config_changed_detected", action="reinitialize_eventhub_client")
                        sender.close()
                        sender = EventHubSender(new_cfg)
                        cfg = new_cfg
                    last_refresh = time.time()
                except Exception as e:  # noqa: BLE001
                    logger.warning("config_refresh_failed", error=str(e))

            metrics = collector.collect()
            dicts = [m.to_dict() for m in metrics]
            if dicts:
                sender.send_metrics(cfg.metrics_host_id, dicts)
            else:
                logger.info("no_containers_found")
        except Exception as e:  # noqa: BLE001
            logger.error("collection_or_send_failed", error=str(e))
        # sleep remaining time
        elapsed = time.time() - start
        to_sleep = max(0.0, interval - elapsed)
        time.sleep(to_sleep)

    with suppress(Exception):
        sender.close()
    logger.info("agent_stopped")
    return 0

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
