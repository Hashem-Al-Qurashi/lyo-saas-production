# Lyo Current State Findings

**Date:** February 17, 2026
**Purpose:** Context document for future sessions. Understand what exists before building.

---

## What We're Aiming For

See: `docs/plans/2026-01-30-multi-tenant-saas-design.md` (in backup at `lyo-backup-2026-02-17/docs/plans/`)

**Goal:** Transform single-tenant salon bot into multi-tenant SaaS serving 100+ clients. Each client gets their own WhatsApp number, services, prices, bot personality, Google Calendar -- all managed via self-service dashboard.

---

## What's Actually Running on AWS

A **single monolithic Python file** (`salon_bot_ec2_latest.py`, 3,444 lines) running on one EC2 instance. Hardcoded for "Aura Hair Studio". No Docker, no Nginx, no Redis, no multi-tenancy.

### AWS Resources (Live)

| Resource | Details |
|----------|---------|
| EC2 | IP `3.239.106.181`, uvicorn on port 8000 |
| RDS PostgreSQL | `lyo-enterprise-database.cixc4kiw6r00.us-east-1.rds.amazonaws.com`, db `lyo_production`, user `lyoadmin`, SSL required |
| ALB | `lyo-enterprise-alb-558118620.us-east-1.elb.amazonaws.com` (unclear if active) |
| Route53 | `api.lyo-webhook.click` was configured |

### AWS Resources (NOT deployed, templates only)

- CloudFormation templates in `aws/cloudformation/` (VPC, subnets, NAT, ALB, ECS Fargate, RDS Multi-AZ, Redis, CloudFront, S3, Secrets Manager)
- Terraform stub in `terraform/terraform/main.tf` (VPC module only, references non-existent modules)
- Docker/compose/nginx configs exist as files but are not running

---

## Production Bot Components (`salon_bot_ec2_latest.py`)

### External Integrations

| Service | API Version | Auth Method | Notes |
|---------|-------------|-------------|-------|
| WhatsApp Cloud API | v18.0 | Bearer token from env | Phone ID: `961636900357709`, verify token: `lyosaas2024` |
| Instagram Graph API | v21.0 | Bearer token from env | Verify token: `lyosaas2024_ig` |
| OpenAI | GPT-4o | API key from env | Tools API with strict mode, temperature 0 |
| Google Calendar | - | Service account key at `/home/ec2-user/google_creds/service_account_key.json` | Permanent, no refresh needed |
| Gmail SMTP | smtp.gmail.com:587 | App password from env | Sender: `notifiche.lyo@gmail.com` |

### All Endpoints (16)

**Webhooks:**
- `GET /webhook` -- WhatsApp verification
- `POST /webhook` -- WhatsApp incoming messages
- `GET /webhook/instagram` -- Instagram verification
- `POST /webhook/instagram` -- Instagram incoming DMs

**Operations:**
- `GET /health` -- status, version, features
- `GET /` -- root info
- `GET /sblocca_chat/{phone}` -- unblock escalated chat
- `GET /blocked_chats` -- list blocked chats

**Reminders:**
- `POST /reminders/send-now` -- trigger WA reminders manually
- `POST /reminders/check-unconfirmed` -- trigger unconfirmed check
- `GET /reminders/status` -- scheduler status
- `POST /reminders/test-email` -- send test email

**Test/Debug:**
- `GET /test/conversations/{phone}` -- conversation history
- `GET /test/conversations-by-date/{date}` -- conversations by date
- `GET /test/appointments/{phone}` -- appointments for phone
- `GET /test/buffer` -- message batching buffer state
- `POST /test/buffer/clear` -- clear buffers
- `POST /test/simulate-reminder/{phone}` -- simulate reminder

### Scheduler Jobs (APScheduler, Europe/Rome timezone)

| Job | Time | Function |
|-----|------|----------|
| WhatsApp reminders | 10:00 AM daily | `send_reminder_messages()` |
| Instagram reminders | 10:05 AM daily | `send_ig_reminder_messages()` |
| Unconfirmed check | 6:00 PM daily | `check_unconfirmed_and_notify()` |

### OpenAI Tools (8 functions with strict mode)

1. `create_appointment` -- book with calendar sync
2. `check_availability` -- check slot + nearest alternatives
3. `get_customer_appointments` -- future appointments
4. `cancel_appointment` -- fuzzy name match + calendar delete
5. `modify_appointment` -- fuzzy name match + calendar update
6. `get_available_slots` -- 30-min intervals
7. `confirm_reminder` -- confirm appointment reminder
8. `escalate_to_human` -- block chat + email owner

### Bot Persona

- Name: "Simone" (presents as human employee, never reveals AI)
- Business: Aura Hair Studio, Milan
- 7 hardcoded services with Italian/English names and prices
- Hours: Tue-Fri 9-18, Sat 9-17, Mon/Sun closed
- Language: Italian-first, switches to English if detected

---

## Instagram Setup (Detailed)

Fully integrated into the same monolith. Separate from WhatsApp but shares AI engine, DB, and calendar.

**Message flow:** Instagram DM -> `POST /webhook/instagram` -> `process_instagram_event()` -> 15s message batching -> `get_ai_response()` (same OpenAI engine as WhatsApp) -> `send_instagram_message()` via Graph API v21.0

**Handles:** text (with batching), images/video/audio (email alert + human handoff), stickers/reactions (ignored), story mentions (thank you), story replies (AI response)

**Platform tracking:** `platform` column in `salon_appointments` and `salon_conversations` tables, value `'instagram'` or `'whatsapp'`

**Separate reminder system:** Queries `WHERE platform = 'instagram'` at 10:05 AM, sends DM reminders

---

## Database (What's Actually Deployed)

Two tables in RDS `lyo_production`. Old single-tenant `salon_*` schema. **NOT** the multi-tenant `lyo_tenants` schema from the design doc.

### `salon_appointments`

| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL PK | |
| customer_phone | VARCHAR(20) NOT NULL | |
| customer_name | VARCHAR(100) NOT NULL | |
| service_type | VARCHAR(50) NOT NULL | Internal code |
| appointment_date | DATE NOT NULL | |
| appointment_time | TIME NOT NULL | |
| duration_minutes | INTEGER DEFAULT 60 | |
| price | DECIMAL(10,2) | |
| status | VARCHAR(20) DEFAULT 'confirmed' | confirmed, cancelled |
| google_event_id | VARCHAR(255) | Calendar sync |
| created_at | TIMESTAMP | |
| reminder_sent_at | TIMESTAMP | |
| reminder_confirmed | BOOLEAN DEFAULT FALSE | |
| reminder_confirmed_at | TIMESTAMP | |
| platform | VARCHAR(20) DEFAULT 'whatsapp' | whatsapp or instagram |

### `salon_conversations`

| Column | Type | Notes |
|--------|------|-------|
| id | SERIAL PK | |
| phone | VARCHAR(20) NOT NULL | |
| name | VARCHAR(100) | |
| message | TEXT | Customer message |
| response | TEXT | Bot response |
| timestamp | TIMESTAMP | |
| platform | VARCHAR(20) DEFAULT 'whatsapp' | Added via ALTER TABLE |

---

## Env Vars Needed on EC2

| Variable | Required | Default |
|----------|----------|---------|
| OPENAI_API_KEY | YES (crashes without) | None |
| WHATSAPP_ACCESS_TOKEN | YES for WA | None |
| WHATSAPP_PHONE_NUMBER_ID | Has default | `961636900357709` |
| WHATSAPP_WEBHOOK_VERIFY_TOKEN | Has default | `lyosaas2024` |
| INSTAGRAM_ACCESS_TOKEN | YES for IG | None |
| INSTAGRAM_PAGE_ID | YES for IG | None |
| INSTAGRAM_APP_SECRET | Optional | None |
| INSTAGRAM_WEBHOOK_VERIFY_TOKEN | Has default | `lyosaas2024_ig` |
| DB_HOST | Has default | RDS endpoint |
| DB_PORT | Has default | 5432 |
| DB_NAME | Has default | lyo_production |
| DB_USER | Has default | lyoadmin |
| DB_PASSWORD | YES | None |
| GOOGLE_SERVICE_ACCOUNT_FILE | Has default | `/home/ec2-user/google_creds/service_account_key.json` |
| GOOGLE_CALENDAR_ID | Has default | primary |
| EMAIL_ADDRESS | Has default | `notifiche.lyo@gmail.com` |
| EMAIL_PASSWORD | YES for email | None |
| EMAIL_TO | Has default | `notifiche.lyo@gmail.com` |
| BOT_BASE_URL | Has default | `http://3.239.106.181:8000` |

---

## Known Issues (For Awareness, Not Action)

### Security
- Credentials in git history (DB password, API keys, tokens from earlier commits)
- Hetzner root password in `connect_server.sh` (tracked file)
- 12 admin/test endpoints with zero authentication
- No webhook signature verification (WA or IG)
- CORS `allow_origins=["*"]` with `allow_credentials=True`
- No HTTPS on the EC2 app itself

### Architecture
- All state in-memory (conversations, blocked chats, message buffers) -- lost on restart
- No DB connection pooling (new psycopg2 connection per query)
- OpenAI calls are synchronous, block the event loop
- No unique constraint on appointment slots (double-booking possible)
- Business hours time validation commented out
- WhatsApp API v18.0 is deprecated (should be v21.0+)

### Code
- 3,444-line monolith, no tests
- WA/IG message batching logic duplicated
- OpenAI tool-call handling duplicated 3x (one per round) with SDK version branching
- `services/`, `app/`, `config/`, `api/` directories contain dead code never imported by the monolith
- `PRODUCTION_ARCHITECTURE.md` describes Docker/Redis/Nginx stack that doesn't exist
- `database/schema.sql` uses `lyo_*` table names; production uses `salon_*` tables

### Economics
- OpenAI cost ~$150/mo per active salon (GPT-4o, ~5,500 tokens/request)
- Starter plan at EUR 49/mo doesn't cover OpenAI costs alone

---

## Repo Structure (Tracked Files Only)

```
/home/sakr_quraish/Projects/italian/    (git root, branch: dev)
├── salon_bot_ec2_latest.py             ← THE production file running on EC2
├── salon_bot_with_booking.py           ← older version
├── lyo_production.py                   ← dead code (not used)
├── main_production.py                  ← dead code (not used)
├── conversational_server.py            ← dead code (not used)
├── connect_server.sh                   ← SSH to Hetzner (has plaintext password!)
├── deploy.sh                           ← deploy script
├── requirements.txt                    ← main deps
├── requirements_minimal.txt            ← minimal deps
├── requirements.webhook.txt            ← webhook proxy deps
├── .env.example / .env.template        ← env templates
├── .gitignore
├── README.md
├── PRODUCTION_ARCHITECTURE.md          ← describes non-existent infra
├── WEBHOOK_ARCHITECTURE.md
├── DEPLOYMENT_GUIDE.md
├── WEBHOOK_DEPLOYMENT_GUIDE.md
├── Procfile / railway.toml / vercel.json  ← old platform configs
├── api/                                ← Vercel webhook proxy (dead code)
│   ├── webhook.py
│   ├── webhook_production.py
│   ├── health.py
│   └── metrics.py
├── app/                                ← planned modular app (dead code)
│   ├── main.py
│   ├── api/webhooks.py
│   └── core/config.py
├── config/settings.py                  ← dead code
├── database/
│   └── schema.sql                      ← lyo_* schema (not deployed)
├── services/                           ← dead code (not imported by monolith)
│   ├── memory_manager.py
│   ├── lyo_memory_service.py
│   ├── postgresql_memory_service.py
│   ├── production_calendar_service.py
│   └── v1_calendar_service.py
├── scripts/
│   ├── backup.sh
│   └── setup-ssl.sh
├── nginx/nginx.conf                    ← not deployed
└── docs/
    ├── index.html
    └── webhook.html
```

**Backup location:** `/home/sakr_quraish/Projects/italian/lyo-backup-2026-02-17/` (7.6MB)
- Contains the multi-tenant SaaS design doc, old sub-projects (lyo-instagram, lyo-production, lyo-saas-clean), loose scripts, and planning docs.

---

*This document is context for future sessions. The goal is the multi-tenant SaaS platform described in `2026-01-30-multi-tenant-saas-design.md`. This document captures what actually exists today.*
