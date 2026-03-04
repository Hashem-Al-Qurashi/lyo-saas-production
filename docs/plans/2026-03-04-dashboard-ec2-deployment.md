# Dashboard EC2 Deployment Design

**Date:** 2026-03-04
**Status:** Approved
**Approach:** Same EC2 + Nginx reverse proxy + systemd

## Context

The management dashboard (`lyo-bot/management/`) exists but has never been deployed. The bot runs on EC2 (t3.small, 3.239.106.181:8000) via `nohup`. No reverse proxy, no process management, no SSL. Targeting 10+ salon clients at <$30/mo.

## Architecture

```
Internet → :80 (nginx)
               ├── /manage/*  → localhost:8002 (dashboard, py3.11)
               └── /*         → localhost:8000 (bot, py3.7)

Both services → RDS PostgreSQL (external)
Both managed by systemd (auto-restart, boot-on-startup)
```

## Key Decisions

1. **Two separate processes**: Bot (py3.7) and dashboard (py3.11) run independently. Dashboard crash cannot affect bot.
2. **Nginx as reverse proxy**: Handles routing, security headers, rate limiting, gzip. Ready for SSL when domain is acquired.
3. **systemd over nohup**: Auto-restart on crash, start on boot, proper logging via journalctl.
4. **Port 80 only**: Close port 8000 from public access. Nginx is the single entry point.

## Dependencies

### Dashboard (Python 3.11) — needs pip install:
- `pydantic-settings`
- `python-jose[cryptography]`
- `passlib[bcrypt]`
- `python-multipart`
- `itsdangerous`
- `jinja2`

### Already installed on Python 3.11:
- fastapi, uvicorn, psycopg2-binary, pydantic, httpx, openai

### Files to upload:
- `lyo-bot/app/__init__.py`, `config.py`
- `lyo-bot/app/models/__init__.py`, `database.py`, `schemas.py`
- `lyo-bot/app/services/__init__.py`, `tenant.py`
- `lyo-bot/management/` (entire directory)

## Security

- nginx blocks direct access to 8000/8002
- Dashboard auth: JWT in HttpOnly cookies + CSRF tokens
- Default login: admin@aura.it / admin123
- No SSL until domain acquired (documented risk)
- JWT_SECRET must be set in .env (currently defaults to "change-me-in-production")

## Upgrade Path

1. Now: EC2 + nginx + systemd (1-5 clients)
2. ~5 clients: Upgrade to t3.medium ($30/mo)
3. ~15 clients: Containerize with Docker Compose
4. ~50 clients: Migrate to ECS Fargate + ALB

## Risks

| Risk | Mitigation |
|------|-----------|
| 1.9GB RAM tight for 10+ clients | Upgrade instance when hitting ~5 clients |
| No SSL (passwords in plaintext) | Add Let's Encrypt when domain acquired |
| Single point of failure | Acceptable at this scale; ECS later |
| Python 3.7 EOL for bot | Separate process; upgrade bot later |
| Security group needs updating | Open port 80, close port 8000 |
