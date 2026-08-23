# Deploying to Proxmox (LXC + Docker)

This runs the whole stack as a Docker container inside an **LXC container** on
Proxmox VE. The web service (dashboard, API, RSS, scheduler) is fully
self-contained; email (digest/alerts) works too once you set SMTP env vars.

> Docker is not run on the Proxmox host itself. It runs inside a guest — here an
> LXC container with nesting enabled.

## 1. Create the LXC container (on the Proxmox host)

Use an **unprivileged** container with a modern Debian/Ubuntu template. Docker in
LXC needs `nesting` (and `keyctl`):

```bash
# On the Proxmox host shell. Pick an unused VMID (e.g. 120) and your storage/bridge.
pct create 120 local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst \
  --hostname cyber-ai-news \
  --cores 2 --memory 1024 --swap 512 \
  --rootfs local-lvm:8 \
  --net0 name=eth0,bridge=vmbr0,ip=dhcp \
  --unprivileged 1 --features nesting=1,keyctl=1 \
  --onboot 1

pct start 120
pct enter 120
```

If you prefer a helper, the community `tteck` Proxmox scripts include a
"Docker LXC" that sets nesting up for you.

## 2. Install Docker (inside the LXC)

```bash
apt update && apt install -y ca-certificates curl git
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
  https://download.docker.com/linux/debian $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
  > /etc/apt/sources.list.d/docker.list
apt update && apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
```

## 3. Deploy the app (inside the LXC)

```bash
git clone https://github.com/anubisalpha/cyber-ai-news.git
cd cyber-ai-news

cp .env.example .env          # then edit — see Security below
chmod 600 .env                # secrets: owner-only

docker compose up -d          # builds the image and starts the container
docker compose logs -f news   # watch it boot
```

Then populate the database and open the dashboard:

```bash
# first fetch (or wait for the scheduler if you enabled it)
curl -X POST http://localhost:8000/api/refresh
```

Dashboard: `http://<lxc-ip>:8000/`

## 4. Turn on automation (optional)

Edit `config/settings.yaml` on the host (it's bind-mounted, no rebuild needed),
then `docker compose restart news`:

```yaml
schedule:
  enabled: true
  interval_minutes: 60
  refresh_on_start: true
  digest_daily_at: "08:00"     # emails the digest daily (needs SMTP + opt-in)

alerts:
  enabled: true
  channel: email               # ping on new critical / AI×Cyber items
```

## 5. Updating

```bash
cd cyber-ai-news && git pull
docker compose up -d --build   # rebuild + restart; SQLite history persists in the volume
```

## 6. Back up the history

The database lives in the `news-data` Docker volume:

```bash
docker run --rm -v cyber-ai-news_news-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/news-db-backup.tgz -C /data .
```

---

# Security guidance

Read this before exposing the service anywhere beyond your own machine.

### The service has NO built-in authentication
The API and dashboard are unauthenticated, and **`POST /api/refresh` can be
triggered by anyone who can reach it** (a mild resource-abuse / DoS vector).

- **Do not expose port 8000 to the public internet.** Keep it on your LAN/VPN,
  or put it behind a reverse proxy (Caddy/Nginx/Traefik) that adds **TLS + auth**
  (basic auth, an SSO forward-auth, or an allow-list).
- If you only ever use it locally, bind the published port to localhost in
  `compose.yml` (`"127.0.0.1:8000:8000"`).

### Secrets (`.env`)
- `.env` holds `SMTP_PASS` and optionally `GOOGLE_API_KEY`. Keep it `chmod 600`,
  never commit it (it's git-ignored), and it is **not** baked into the image.
- Use a **Gmail app password** (or a dedicated sending account), never your main
  password. Scope the `GOOGLE_API_KEY` to just the Generative Language API.

### Container hardening (already in `compose.yml`)
- Runs as a **non-root** user; **read-only root filesystem** (writes only to the
  data volume + a tmpfs `/tmp`); **`no-new-privileges`**; **all Linux capabilities
  dropped**; **memory and PID limits** so a runaway refresh can't starve the host.
- The config is bind-mounted **read-only**.

### LXC hardening (Proxmox)
- Use an **unprivileged** LXC; enable only the features you need (`nesting`,
  `keyctl`). Keep the Proxmox host and the container's packages patched.

### Feed content is untrusted input
Feeds are third-party HTML/JSON. The app treats them as data:
- The dashboard **escapes all feed text** and **only allows `http(s)` links**
  (a hostile feed cannot inject `javascript:`/`data:` URLs — verified).
- API filters use **parameterised SQL** (no injection); query params are
  pattern-validated.
- The optional LLM classifier only ever runs feed text through a prompt whose
  output is **validated against the fixed taxonomy** (hallucinated/injected tags
  are dropped), and it sees titles/summaries only.
- You control the source list in `config/sources.yaml` — only add feeds you trust
  to point at legitimate URLs.

### Updates & supply chain
- Rebuild periodically (`docker compose up -d --build`) to pick up base-image and
  dependency security fixes. Consider pinning the base image by digest and running
  an image scan (e.g. `docker scout cves cyber-ai-news:latest`).

### Data sensitivity
- The store holds **public news metadata only** — no personal data, credentials,
  or private content. Bookmarks/read-state live in the browser's localStorage, not
  on the server.
