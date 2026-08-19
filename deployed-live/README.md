# deployed-live — Production Mirror

This folder is a **snapshot of what is actually running on AWS**, captured
server → GitHub. It is the source of truth for production.

**Sync direction policy:** changes are made on the live servers first, then
captured back into this folder. Do **not** treat this as code to deploy *to*
the servers — it mirrors *from* them.

> The repo's `lyo-bot/app/` modular refactor is a separate, **undeployed**
> work-in-progress. It is NOT what serves production. This folder is.

Captured: 2026-06-23

---

## Live topology

### Box 1 — Chatwoot  (EC2 `52.21.97.220`)
Public: `inbox.lyovirtualassistant.com` → ALB (HTTPS) → container `:3000`

| Container | Image | Port |
|-----------|-------|------|
| chatwoot-rails-1 | chatwoot-lyo (see IMAGE_STATE) | 3000 |
| chatwoot-sidekiq-1 | same | jobs |
| chatwoot-redis-1 | redis:alpine | 6379 |

DB = RDS (no Postgres container). Customizations baked into the Docker image
+ host bind-mounts.

### Box 2 — Bot + Dashboard  (EC2 `98.89.13.25`, Elastic IP)
Native systemd + nginx, no docker.

| systemd service | process | port | route |
|-----------------|---------|------|-------|
| lyo-bot.service | `python3 salon_bot_with_booking.py` | 8001 | nginx `/`, `/webhook`, `/health` |
| lyo-mgmt.service | `uvicorn management.app:mgmt_app` | 127.0.0.1:8002 | nginx `/manage/` |
| nginx | — | 80, 8000 | reverse proxy |

DB = RDS Postgres `lyo-enterprise-database-v2.cixc4kiw6r00.us-east-1.rds.amazonaws.com`

---

## Folder map

```
bot/                       Live WhatsApp bot (Box 2, /home/ec2-user/)
  salon_bot_with_booking.py   <- the actual running bot (monolith)
  business_context.py         <- import dep
  chatwoot_bridge.py          <- import dep (bot -> Chatwoot)
  .env.example                <- key names only, values stripped

dashboard/                 Live management dashboard (Box 2, /home/ec2-user/lyo-bot/)
                              runs as uvicorn management.app:mgmt_app

chatwoot/                  Chatwoot host files (Box 1, /home/ec2-user/chatwoot/)
  docker-compose.yaml
  docker-compose.override.yml  <- SMTP (password redacted -> ${SMTP_PASSWORD})
  sessions_controller.rb       <- bind-mounted override
  apply-lyo-brand.sh
  brand-assets/                <- logos (PNG; .svg symlinks not preserved)
  image/                       <- files baked INTO the running image
    installation_config.yml      branding defaults (Lyo)
    vueapp.html.erb              dark/light toggle + theme init
  .env.example
  IMAGE_STATE.txt              <- which image SHA is running vs tagged

infra/
  nginx/                     /etc/nginx/conf.d + nginx.conf (Box 2)
  systemd/                   lyo-bot.service, lyo-mgmt.service (Box 2)
```

---

## Known production facts / landmines

- **Chatwoot image drift:** running container = older image SHA; tag
  `chatwoot-lyo:latest` points to a newer image not yet deployed. See
  `chatwoot/IMAGE_STATE.txt`. A `docker compose up -d` would swap it in.
- **Secrets** are NOT in this folder. Real values live in `/home/ec2-user/.env`
  (Box 2) and `/home/ec2-user/chatwoot/.env` (Box 1). Only key names captured.
- **nginx Box 2** serves plain HTTP on `:80`/`:8000` (no TLS on the box).
