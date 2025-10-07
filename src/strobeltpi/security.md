# Security Considerations

- Transport Security: All Azure SDK calls use HTTPS/TLS 1.2+; Event Hubs uses AMQP over TLS. No plaintext protocols.
- Secrets: Never stored locally; retrieved from Azure Key Vault at startup. Environment variables only provide bootstrap Service Principal credentials.
- Rotation: Update SP secret in AAD then update Key Vault secret versions; restart agent (or implement periodic refresh in future).
- Least Privilege Roles:
  - Key Vault: `get` permission for required secrets only.
  - Event Hub: `Azure Event Hubs Data Sender` on the specific Event Hub or Event Hub namespace scope.
- Logging: Structured JSON logs; excludes secret values.
- Resilience: Exponential backoff retries for Event Hub send; prevents tight loops on outage.
- Future Hardening: Add local disk queue for offline buffering; implement certificate-based auth (client assertion) to remove client secret usage.
