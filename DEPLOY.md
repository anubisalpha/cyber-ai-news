# Deployment Guide

The container image is the deployable unit. Run it anywhere — bare Docker, Docker
Compose, Proxmox LXC, AWS ECS — using the same image. Auth and SMTP are driven by
env vars, so nothing is baked in.

---

## Option 1 — Bare `docker run`

The simplest path: single container, built-in auth, no compose needed.

```bash
git clone https://github.com/anubisalpha/cyber-ai-news.git
cd cyber-ai-news
docker build -t cyber-ai-news:latest .
```

```bash
docker run -d \
  --name cyber-ai-news \
  -p 8000:8000 \
  -e AUTH_USER=admin \
  -e AUTH_PASS=yourpassword \
  -e SMTP_HOST=172.16.2.25 \
  -e SMTP_PORT=25 \
  -e SMTP_FROM=claude_ai@welfarecall.com \
  -e MAIL_TO=you@example.com \
  -v cyber-ai-news-data:/app/data \
  -v "$PWD/config:/app/config:ro" \
  --read-only --tmpfs /tmp \
  --cap-drop ALL --security-opt no-new-privileges \
  --restart unless-stopped \
  cyber-ai-news:latest
```

Open `http://<host>:8000/` and log in with the credentials you set.
`/api/health` is always open (no credentials required) for probes.

Trigger a first fetch:

```bash
curl -u admin:yourpassword -X POST http://localhost:8000/api/refresh
```

---

## Option 2 — Docker Compose with Caddy (TLS + reverse proxy)

Use this when you want automatic HTTPS for a real domain, or prefer Caddy to handle
auth in front of the app (the app's `AUTH_USER`/`AUTH_PASS` can then be left blank).

```bash
cp .env.example .env
chmod 600 .env
```

Generate a bcrypt hash for the Caddy proxy (the `$$` doubling is required by compose):

```bash
docker run --rm caddy caddy hash-password --plaintext 'yourpassword' | sed 's/\$/\$\$/g'
```

Set in `.env`:

```ini
BASIC_AUTH_USER=admin
BASIC_AUTH_HASH=$$2a$$14$$...the-escaped-hash...
SITE_ADDRESS=http://:8080    # or a domain for HTTPS
```

For HTTPS with a real domain, also open ports 80 and 443 in `compose.yml` (caddy service).

```bash
docker compose up -d
docker compose logs -f
```

Dashboard at `http://<host>:8080/`. First fetch:

```bash
curl -u admin:yourpassword -X POST http://localhost:8080/api/refresh
```

**Update:**

```bash
git pull && docker compose up -d --build
```

**Back up the database:**

```bash
docker run --rm \
  -v cyber-ai-news_news-data:/data \
  -v "$PWD":/backup alpine \
  tar czf /backup/news-db-backup.tgz -C /data .
```

---

## Option 3 — Proxmox LXC

Run the container inside a Proxmox LXC guest. Docker runs inside the LXC, not on
the Proxmox host.

### Create the LXC (on the Proxmox host)

```bash
pct create <VMID> local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst \
  --hostname cyber-ai-news \
  --cores 2 --memory 1024 --swap 512 \
  --rootfs local-lvm:8 \
  --net0 name=eth0,bridge=vmbr0,ip=dhcp \
  --unprivileged 1 --features nesting=1,keyctl=1 \
  --onboot 1

pct start <VMID>
pct enter <VMID>
```

### Install Docker (inside the LXC)

```bash
apt update && apt install -y ca-certificates curl git
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg \
  -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
  https://download.docker.com/linux/debian \
  $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
  > /etc/apt/sources.list.d/docker.list
apt update && apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
```

### Deploy (inside the LXC)

Follow **Option 1** (bare `docker run`) or **Option 2** (compose with Caddy) from above.
For internal-only use with no public domain, Option 1 is the simpler path.

---

## Option 4 — AWS ECS / Fargate

Push the image to ECR, then run as an ECS service. Auth is handled by your ALB (not
the app's built-in auth, unless you want defence-in-depth).

### Push to ECR

```bash
aws ecr create-repository --repository-name cyber-ai-news --region eu-west-2
IMAGE=<account>.dkr.ecr.eu-west-2.amazonaws.com/cyber-ai-news:latest
aws ecr get-login-password --region eu-west-2 | docker login --username AWS --password-stdin <account>.dkr.ecr.eu-west-2.amazonaws.com
docker build -t $IMAGE .
docker push $IMAGE
```

### ECS Task Definition (key settings)

| Setting | Value |
|---|---|
| Container port | 8000 |
| `AUTH_USER` / `AUTH_PASS` | via ECS Secrets (SSM Parameter Store) or ALB handles auth |
| `SMTP_HOST` | your internal relay or SES |
| Data volume | EFS mount at `/app/data` for persistent SQLite |
| Health check | `GET /api/health` (always open, no credentials required) |

Put a private ALB + HTTPS listener in front. The container itself has no TLS.

---

## Automation (any deployment)

Edit `config/settings.yaml` (bind-mounted, no rebuild needed) to enable auto-refresh
and the daily digest email:

```yaml
schedule:
  enabled: true
  interval_minutes: 60
  refresh_on_start: true
  digest_daily_at: "08:00"    # sends real email — opt in deliberately

alerts:
  enabled: true
  channel: email
```

Restart the container after changing settings.

---

## Security guidance

### Authentication
- **Built-in auth** (`AUTH_USER` + `AUTH_PASS`): plain-password comparison using
  `secrets.compare_digest` (constant-time). `/api/health` is always open.
- **Caddy proxy** (`BASIC_AUTH_USER` + `BASIC_AUTH_HASH`): bcrypt cost 14.
  The app is `expose`-only (never published directly).
- **ALB / other proxy**: use HTTPS + your existing auth layer; omit `AUTH_USER`/`AUTH_PASS`
  or keep them as a second layer.
- Basic auth is credentials-over-the-wire — **always run behind TLS on untrusted networks**.
  LAN/VPN-only deployments may use plain HTTP internally.

### Secrets
- `.env` holds `AUTH_PASS`, `SMTP_PASS`, and optionally `GOOGLE_API_KEY`.
  Keep it `chmod 600` and never commit it (git-ignored).
- For ECS, store secrets in SSM Parameter Store; do not bake them into the image.

### Container hardening (already in compose.yml and the docker run example)
- Non-root user; read-only rootfs; `no-new-privileges`; all capabilities dropped;
  memory and PID limits.

### Feed content
- All feed text is escaped before rendering; only `http(s)` links are allowed in the
  dashboard (hostile `javascript:`/`data:` URLs are blocked).
- SQL filters use parameterised queries.
- The optional LLM classifier only ever receives titles/summaries and validates its
  output against the fixed taxonomy — injected or hallucinated tags are dropped.

### Updates
Rebuild periodically to pick up base-image and dependency patches:
```bash
docker compose up -d --build   # compose
docker build -t cyber-ai-news:latest . && docker run ...   # bare docker
```
