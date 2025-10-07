from __future__ import annotations

import orjson

from strobeltpi.eventhub_sender import EventHubSender
from strobeltpi.config import RuntimeConfig

class DummyProducer:
    def __init__(self):
        self.sent_batches = []
        self._current_batch = None
    def create_batch(self):  # noqa: D401
        class B(list):
            def add(self_inner, event):  # noqa: D401, ANN001
                # simple size limit of 2 for testing
                if len(self_inner) >= 2:
                    raise ValueError("full")
                self_inner.append(event)
        return B()
    def send_batch(self, batch):  # noqa: D401, ANN001
        self.sent_batches.append(list(batch))
    def close(self):  # noqa: D401
        pass

class DummyCfg(RuntimeConfig):  # noqa: D401
    def build_eventhub_credential(self):  # noqa: D401
        return object()

class DummyEventHubSender(EventHubSender):  # noqa: D401
    def __init__(self, cfg, dummy_producer):  # noqa: D401, ANN001
        self._cfg = cfg
        self._producer = dummy_producer

def test_send_metrics_batching(monkeypatch):  # noqa: D401
    cfg = DummyCfg(
        event_hub_fqns="f",
        event_hub_name="n",
        tenant_id="t",
        client_id="c",
        client_secret="s",
        metrics_host_id="h",
        key_vault_url="u",
        fingerprint="fp",
        secret_values={},
        secret_versions={},
    )
    prod = DummyProducer()
    sender = DummyEventHubSender(cfg, prod)
    records = [{"a": 1}, {"b": 2}, {"c": 3}]
    sender.send_metrics("h", records)
    # With size limit of 2 we expect 2 batches: [0,1] and [2]
    assert len(prod.sent_batches) == 2
    total_events = sum(len(b) for b in prod.sent_batches)
    assert total_events == 3
