from __future__ import annotations

import os
from dataclasses import dataclass
import hashlib
from datetime import datetime
from typing import Dict, Tuple
from typing import Optional

import structlog
from azure.identity import ClientSecretCredential
from azure.keyvault.secrets import SecretClient

logger = structlog.get_logger()

REQUIRED_SECRETS = [
    "event-hub-fully-qualified-namespace",
    "event-hub-name",
    "tenant-id",
    "client-id",
    "client-secret",
]

@dataclass(frozen=True)
class RuntimeConfig:
    event_hub_fqns: str
    event_hub_name: str
    tenant_id: str
    client_id: str
    client_secret: str
    metrics_host_id: str
    key_vault_url: str
    fingerprint: str  # hash of sensitive values to detect rotation
    secret_values: Dict[str, str]
    secret_versions: Dict[str, str]

    @staticmethod
    def from_key_vault(key_vault_url: str) -> "RuntimeConfig":
        # Bootstrap: we need tenant, client id, client secret first so we fetch them individually via env for first credential OR assume Managed Identity (not on Pi). We'll use env bootstrap only if present.
        bootstrap_tenant = os.getenv("AZURE_TENANT_ID")
        bootstrap_client = os.getenv("AZURE_CLIENT_ID")
        bootstrap_secret = os.getenv("AZURE_CLIENT_SECRET")
        if not (bootstrap_tenant and bootstrap_client and bootstrap_secret):
            raise RuntimeError(
                "Bootstrap environment variables AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET must be set on Raspberry Pi to access Key Vault secrets via Service Principal." 
            )
        bootstrap_credential = ClientSecretCredential(
            tenant_id=bootstrap_tenant,
            client_id=bootstrap_client,
            client_secret=bootstrap_secret,
        )
        secret_client = SecretClient(vault_url=key_vault_url, credential=bootstrap_credential)
        secrets: dict[str,str] = {}
        secret_versions: dict[str, str] = {}
        for name in REQUIRED_SECRETS:
            s = secret_client.get_secret(name)
            secrets[name] = s.value  # network call returns SecretBundle
            # secret id format: https://{vault}.vault.azure.net/secrets/{name}/{version}
            if hasattr(s, "id") and s.id:  # type: ignore[attr-defined]
                secret_versions[name] = s.id.rsplit("/", 1)[-1]  # version segment
        # optional host id override
        try:
            metrics_host_id = secret_client.get_secret("metrics-host-id").value
        except Exception:  # noqa: BLE001 broad to avoid crash if not set
            metrics_host_id = os.uname().nodename  # type: ignore[attr-defined]
        fp_source = "|".join(
            [
                secrets["event-hub-fully-qualified-namespace"],
                secrets["event-hub-name"],
                secrets["tenant-id"],
                secrets["client-id"],
                secrets["client-secret"],
                metrics_host_id,
            ]
        )
        fingerprint = hashlib.sha256(fp_source.encode()).hexdigest()
        return RuntimeConfig(
            event_hub_fqns=secrets["event-hub-fully-qualified-namespace"],
            event_hub_name=secrets["event-hub-name"],
            tenant_id=secrets["tenant-id"],
            client_id=secrets["client-id"],
            client_secret=secrets["client-secret"],
            metrics_host_id=metrics_host_id,
            key_vault_url=key_vault_url,
            fingerprint=fingerprint,
            secret_values=secrets,
            secret_versions=secret_versions,
        )

    @staticmethod
    def refresh_if_changed(old: "RuntimeConfig") -> Tuple[bool, "RuntimeConfig"]:
        """Selective refresh using secret version metadata.

        For each required secret:
        1. List versions; determine latest (by created_on or updated_on timestamp).
        2. If version differs from cached, fetch value; else reuse cached value.
        3. Recompute fingerprint; if changed -> True.
        Returns (changed, new_config).
        """
        bootstrap_credential = ClientSecretCredential(
            tenant_id=os.getenv("AZURE_TENANT_ID", old.tenant_id),
            client_id=os.getenv("AZURE_CLIENT_ID", old.client_id),
            client_secret=os.getenv("AZURE_CLIENT_SECRET", old.client_secret),
        )
        secret_client = SecretClient(vault_url=old.key_vault_url, credential=bootstrap_credential)

        new_values: dict[str, str] = dict(old.secret_values)
        new_versions: dict[str, str] = dict(old.secret_versions)
        changed_any = False

        def latest_version(name: str) -> str | None:
            latest_v = None
            latest_time: datetime | None = None
            for props in secret_client.list_properties_of_secret_versions(name):  # network calls
                ts = getattr(props, "updated_on", None) or getattr(props, "created_on", None)
                if latest_time is None or (ts and ts > latest_time):
                    latest_time = ts
                    latest_v = props.version
            return latest_v

        names = list(REQUIRED_SECRETS)
        # metrics-host-id is optional if originally present
        if "metrics-host-id" in old.secret_values:
            names.append("metrics-host-id")

        for name in names:
            try:
                latest_v = latest_version(name)
            except Exception as e:  # noqa: BLE001
                logger.warning("list_secret_versions_failed", secret=name, error=str(e))
                latest_v = None
            cached_v = new_versions.get(name)
            if latest_v and latest_v != cached_v:
                # Fetch new secret value
                s = secret_client.get_secret(name)
                new_values[name] = s.value
                new_versions[name] = latest_v
                changed_any = True

        # Recompute fingerprint from possibly updated values
        fp_source = "|".join(
            [
                new_values.get("event-hub-fully-qualified-namespace", old.event_hub_fqns),
                new_values.get("event-hub-name", old.event_hub_name),
                new_values.get("tenant-id", old.tenant_id),
                new_values.get("client-id", old.client_id),
                new_values.get("client-secret", old.client_secret),
                new_values.get("metrics-host-id", old.metrics_host_id),
            ]
        )
        fingerprint = hashlib.sha256(fp_source.encode()).hexdigest()
        if fingerprint != old.fingerprint and not changed_any:
            # fallback safety: changed fingerprint implies modifications
            changed_any = True
        new_cfg = RuntimeConfig(
            event_hub_fqns=new_values.get("event-hub-fully-qualified-namespace", old.event_hub_fqns),
            event_hub_name=new_values.get("event-hub-name", old.event_hub_name),
            tenant_id=new_values.get("tenant-id", old.tenant_id),
            client_id=new_values.get("client-id", old.client_id),
            client_secret=new_values.get("client-secret", old.client_secret),
            metrics_host_id=new_values.get("metrics-host-id", old.metrics_host_id),
            key_vault_url=old.key_vault_url,
            fingerprint=fingerprint,
            secret_values=new_values,
            secret_versions=new_versions,
        )
        return changed_any, new_cfg

    def build_eventhub_credential(self) -> ClientSecretCredential:
        return ClientSecretCredential(
            tenant_id=self.tenant_id,
            client_id=self.client_id,
            client_secret=self.client_secret,
        )
