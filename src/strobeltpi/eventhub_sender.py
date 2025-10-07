from __future__ import annotations

import json
from typing import Iterable

import orjson
import structlog
from azure.eventhub import EventData
from azure.eventhub import EventHubProducerClient
from tenacity import retry, stop_after_attempt, wait_exponential_jitter

from .config import RuntimeConfig

logger = structlog.get_logger()

class EventHubSender:
    def __init__(self, cfg: RuntimeConfig) -> None:
        self._cfg = cfg
        self._producer = EventHubProducerClient(
            fully_qualified_namespace=cfg.event_hub_fqns,
            eventhub_name=cfg.event_hub_name,
            credential=cfg.build_eventhub_credential(),
        )

    def close(self) -> None:
        try:
            self._producer.close()
        except Exception:  # noqa: BLE001
            pass

    @retry(wait=wait_exponential_jitter(initial=1, max=30), stop=stop_after_attempt(5))
    def send_metrics(self, host_id: str, records: Iterable[dict]) -> None:
        batch = self._producer.create_batch()
        for r in records:
            body = {"host_id": host_id, **r}
            payload = orjson.dumps(body)
            try:
                batch.add(EventData(body=payload))
            except ValueError:
                # batch full - send current and start new
                self._producer.send_batch(batch)
                batch = self._producer.create_batch()
                batch.add(EventData(body=payload))
        if batch.count:  # type: ignore[attr-defined]
            self._producer.send_batch(batch)
        logger.info("eventhub_send_success", count=getattr(batch, "count", None))
