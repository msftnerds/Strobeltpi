# Raspberry Pi 5 Docker Metrics -> Fabric Eventstream 

Collect Docker container metrics (name, status, uptime, cpu %, memory, disk IO read/write) on a Raspberry Pi 5 and stream them securely to Fabric Eventstream using Azure AD (Service Principal) and Azure Key Vault for secret management.

## Security Principles (Microsoft Security Foundation)
- Least privilege: Service Principal granted only `get` on required Key Vault secrets and `Fabric Eventstream s Data Sender` on the target Event Hub.
- No credentials in code or repo. All sensitive values stored as Key Vault secrets.
- Encrypted transport only (HTTPS / AMQP over TLS). SDK defaults enforce TLS.
- Rotatable secrets: Rotate SP client secret/cert; update Key Vault and restart service.
- Measured, structured logging without secrets.

## Architecture
1. Scheduler loop collects metrics from local Docker Engine (Docker SDK + psutil / cgroups).
2. Metrics serialized to compact JSON (orjson) with timestamp + host identity.
3. Event batch sent with `EventHubProducerClient` using `ClientSecretCredential` (or certificate) obtained from Key Vault.
4. Retry (tenacity) with exponential backoff and jitter for transient failures.
5. Read data through Event Stream and store them in Fabric Eventhouse
6. Set Data Activator for status change if docker container is not running for more than 10 minutes (inform user through teams message)
7. Show results in Fabric Real-Time Dashboard

<img width="1261" height="497" alt="image" src="https://github.com/user-attachments/assets/742dc735-06b2-46c3-8335-fae9c50963dd" />


## Key Vault Secrets (expected names)
- `event-hub-fully-qualified-namespace` (e.g. `mynamespace.servicebus.windows.net`)
- `event-hub-name`
- `tenant-id`
- `client-id`
- `client-secret` (or use certificate flow; then store `client-cert` + `client-cert-password` if PFX)
- Optional: `metrics-host-id` (override hostname)

You may also supply these via environment variables to bootstrap initial Key Vault access: `KEYVAULT_URL`.

## Deployment on Raspberry Pi 5
Install Python 3.11+, docker engine, and enable user permissions.

```bash
sudo apt update && sudo apt install -y python3-full python3-venv python3-pip
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install .

# Run
python -m strobeltpi.metrics_agent
```

Systemd unit example (`/etc/systemd/system/docker-metrics-agent.service`):
```
[Unit]
Description=Docker Metrics -> Event Hub Agent
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/strobeltpi
Environment=KEYVAULT_URL=https://<your-vault>.vault.azure.net/
ExecStart=/home/pi/strobeltpi/.venv/bin/python -m strobeltpi.metrics_agent
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

## Run Locally (Dev)
Set env vars for Key Vault dev override (OPTIONAL):
```
export KEYVAULT_URL=https://<vault>.vault.azure.net/
```
Ensure the Service Principal has appropriate RBAC.

### Dynamic Credential / Secret Rotation
The agent now performs a periodic Key Vault re-fetch (default every 900s) controlled by `CONFIG_REFRESH_SECONDS` environment variable. If any of the tracked secrets change (detected via fingerprint hash), the Event Hub client is reinitialized seamlessly without restarting the container or process. Set a lower value (e.g. 300) if you rotate secrets more frequently; balance against extra Key Vault read cost.

## Testing
```
pytest -q
```

## Future Enhancements
- Add Prometheus exporter option.
- Add edge caching (persist to disk when offline and replay).
- Add certificate-based auth (X.509) instead of client secret.

## License
MIT

## Container Image

### Build (multi-arch example via buildx)
```bash
docker buildx build --platform linux/arm64,linux/amd64 -t yourrepo/strobeltpi-metrics:0.1.0 .
```

For local Pi build (on the Pi itself):
```bash
docker build -t strobeltpi-metrics:local .
```

### Run
```bash
docker run -d \
	--name docker-metrics-agent \
	-e KEYVAULT_URL=https://<your-vault>.vault.azure.net/ \
	-e AZURE_TENANT_ID=<tenant> \
	-e AZURE_CLIENT_ID=<client-id> \
	-e AZURE_CLIENT_SECRET=<client-secret> \
	-e SCRAPE_INTERVAL_SECONDS=15 \
	-e LOG_LEVEL=INFO \
	-v /var/run/docker.sock:/var/run/docker.sock:ro \
	--restart=unless-stopped \
	strobeltpi-metrics:local
```

### Notes
- Mounting `/var/run/docker.sock` read-only allows metrics collection without bundling Docker-in-Docker.
- Provide secrets via Docker/Kubernetes secrets or Azure Container Apps secret references; never bake them into the image.
- To use a certificate instead of client secret, extend the image to copy the cert and set env vars accordingly.
- If the container shows as `unhealthy`, inspect with `docker inspect --format='{{json .State.Health}}' docker-metrics-agent`.
	- Common reasons: process crashed (check `docker logs`), no Key Vault access (missing env), or healthcheck timing out before startup (increase start-period).
	- You can temporarily disable the healthcheck by adding `--no-healthcheck` at run for diagnostics.
- Healthcheck implementation: the agent writes a heartbeat timestamp to `$HEARTBEAT_FILE` (default `/tmp/agent_heartbeat`) each loop. The container is considered healthy if the file's mtime is newer than `2 * SCRAPE_INTERVAL_SECONDS`.
