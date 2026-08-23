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

The stack runs the app **behind a Caddy reverse proxy that enforces HTTP basic
auth** — the app itself is never published directly. So the first step is setting
up credentials.

```bash
git clone https://github.com/anubisalpha/cyber-ai-news.git
cd cyber-ai-news

cp .env.example .env
chmod 600 .env                # secrets: owner-only
```

Set a username and password hash in `.env`. Generate the hash with this one-liner
(it also doubles every `$` to `$$`, which docker compose requires):

```bash
docker run --rm caddy caddy hash-password --plaintext 'choose-a-password' | sed 's/\$/\$\$/g'
```

Put the output in `.env`:

```ini
BASIC_AUTH_USER=admin
BASIC_AUTH_HASH=$$2a$$14$$...the-escaped-hash...
SITE_ADDRESS=http://:8080
```

Then start it:

```bash
docker compose up -d          # builds the app image + starts app and caddy
docker compose logs -f        # watch both boot
```

Open the dashboard at `http://<lxc-ip>:8080/` and log in with your credentials.
Populate the database with a first fetch (the browser will have your session; from
the shell, pass the creds):

```bash
curl -u admin:choose-a-password -X POST http://localhost:8080/api/refresh
```

### Enabling HTTPS (optional)
Point a DNS name at the LXC, then in `.env` set `SITE_ADDRESS=news.example.com`
and publish 80/443 by adding to the `caddy` service in `compose.yml`:

```yaml
    ports:
      - "80:80"
      - "443:443"
      - "8080:8080"
```

Caddy will obtain and renew a Let's Encrypt certificate automatically.

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

### Authentication (built into the stack)
The compose stack puts the app **behind a Caddy reverse proxy that enforces HTTP
basic auth** — the app is never published directly (it's `expose`-only on the
internal network). So `POST /api/refresh` and everything else require credentials.

- **Set a strong password.** Basic auth is only as good as the password; the hash
  is bcrypt (cost 14). Never commit `.env`.
- **Add TLS before using it over any untrusted network.** Basic auth sends
  credentials base64-encoded, not encrypted — on plain HTTP they're exposed in
  transit. Enable HTTPS (set `SITE_ADDRESS` to a domain, see §3) or keep it on a
  LAN/VPN. Prefer not to expose it to the public internet at all.
- The app has no auth of its own — the proxy is the gate. If you front it with a
  different proxy, keep an equivalent auth layer, and don't also publish port 8000.

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
