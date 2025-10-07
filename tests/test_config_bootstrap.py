from __future__ import annotations

import os
import types

import pytest

from strobeltpi.config import RuntimeConfig

class DummySecret:
    def __init__(self, value: str):
        self.value = value

class DummySecretClient:
    def __init__(self, values):
        self._values = values

    def get_secret(self, name):  # noqa: D401, ANN001
        return DummySecret(self._values[name])

class DummyCredential:
    pass

def test_runtime_config_from_key_vault(monkeypatch):  # noqa: D401
    vals = {
        "event-hub-fully-qualified-namespace": "ns.servicebus.windows.net",
        "event-hub-name": "hub",
        "tenant-id": "tenant",
        "client-id": "client",
        "client-secret": "secret",
        "metrics-host-id": "pi-device",
    }
    monkeypatch.setenv("AZURE_TENANT_ID", "boot-tenant")
    monkeypatch.setenv("AZURE_CLIENT_ID", "boot-client")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "boot-secret")

    def dummy_client_secret_credential(*a, **k):  # noqa: D401, ANN001
        return DummyCredential()

    monkeypatch.setattr("strobeltpi.config.ClientSecretCredential", dummy_client_secret_credential)
    # Minimal SecretClient double supporting get_secret only
    monkeypatch.setattr("strobeltpi.config.SecretClient", lambda vault_url, credential: DummySecretClient(vals))
    cfg = RuntimeConfig.from_key_vault("https://vault")
    assert cfg.event_hub_name == "hub"
    assert cfg.metrics_host_id == "pi-device"


def test_refresh_if_changed(monkeypatch):  # noqa: D401
    base_vals = {
        "event-hub-fully-qualified-namespace": "ns.servicebus.windows.net",
        "event-hub-name": "hub",
        "tenant-id": "tenant",
        "client-id": "client",
        "client-secret": "secret1",
        "metrics-host-id": "pi-device",
    }
    monkeypatch.setenv("AZURE_TENANT_ID", "boot-tenant")
    monkeypatch.setenv("AZURE_CLIENT_ID", "boot-client")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "boot-secret")

    class DummySecret:
        def __init__(self, value, version):
            self.value = value
            self.id = f"https://vault/secrets/name/{version}"

    class DummyProps:
        def __init__(self, version, updated_on):
            self.version = version
            self.updated_on = updated_on

    class DummyClient:
        def __init__(self, mapping, versions):
            self._m = mapping
            self._versions = versions  # dict[name] = list[(version, datetime)]
        def get_secret(self, name):  # noqa: D401, ANN001
            # return value for latest version
            return DummySecret(self._m[name], self._versions[name][-1][0])
        def list_properties_of_secret_versions(self, name):  # noqa: D401, ANN001
            for ver, ts in self._versions.get(name, []):
                yield DummyProps(ver, ts)

    monkeypatch.setattr("strobeltpi.config.ClientSecretCredential", lambda *a, **k: object())
    secret_map = dict(base_vals)
    from datetime import datetime, timezone, timedelta
    # Start with only v1 versions; we'll append v2 for client-secret after initial config load
    versions = {
        "event-hub-fully-qualified-namespace": [("v1", datetime.now(timezone.utc))],
        "event-hub-name": [("v1", datetime.now(timezone.utc))],
        "tenant-id": [("v1", datetime.now(timezone.utc))],
        "client-id": [("v1", datetime.now(timezone.utc))],
        "client-secret": [("v1", datetime.now(timezone.utc) - timedelta(seconds=10))],
        "metrics-host-id": [("v1", datetime.now(timezone.utc))],
    }
    monkeypatch.setattr("strobeltpi.config.SecretClient", lambda vault_url, credential: DummyClient(secret_map, versions))
    cfg1 = RuntimeConfig.from_key_vault("https://vault")
    # mutate secret map to simulate rotation
    # After initial load, simulate new version publish (v2) with new value
    versions["client-secret"].append(("v2", datetime.now(timezone.utc)))
    secret_map["client-secret"] = "secret2"
    changed, cfg2 = RuntimeConfig.refresh_if_changed(cfg1)
    assert changed is True
    assert cfg1.fingerprint != cfg2.fingerprint
