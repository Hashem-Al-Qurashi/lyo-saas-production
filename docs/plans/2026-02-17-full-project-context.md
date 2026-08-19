# Lyo Assistant - Full Project Context

**Date:** February 17, 2026
**Purpose:** Complete context for any future session. Covers what exists, what we're building, and how it all connects.

---

## THE VISION

Onboard any new salon client with their phone number and minimal work via the dashboard. The flow:

1. Super admin creates a new account in Chatwoot dashboard
2. Connects the client's WhatsApp number + Instagram to the account
3. Attaches the Lyo bot (Agent Bot) to that inbox
4. Configures treatments, operators, hours via the management page (Step 3)
5. Bot starts handling bookings for that client automatically

---

## THE PROJECT (from "PROJECT LYO ASSISTANT 2" PDF, dated 16.02.26)

Three steps, each a separate delivery with its own payment.

### STEP 1 -- Multi-Operator Structure + Booking Logic

**What it means:** The current bot is hardcoded for a single salon with no concept of individual operators/stylists. Step 1 adds:

- **Operators:** Each has a technical ID (operatore_1, operatore_2...) and visible name (Giulia, Martina, etc.)
- **Treatment-to-operator mapping:** Each treatment is linked to specific operators (e.g., Blow-dry -> 4 operators, Colour -> 2)
- **Occupancy logic:** If Martina has Balayage 9:00-12:00, she can't be assigned a 9:30 Haircut
- **Parallel booking:** If 3 operators do Haircut and all free at 9:00, there are 3 slots -- unavailable only when ALL enabled operators are busy
- **Operator preference handling:**
  - No preference -> auto-assign a free operator
  - Client asks "who is available?" -> list available operators for that treatment
  - Client requests specific operator -> check only THEIR calendar, never auto-switch. If busy, propose alternatives for that operator only
- **Availability response must show operator:** "At 9:00 it is available with Martina. Does that work for you?"
- **Mandatory name database:** DB linked to user_id/phone with first_name + last_name. Check before every booking, ask if missing, never book without a name

### STEP 2 -- Dashboard Setup + Meta Connection + Bot Integration

**What it means:** Deploy Chatwoot (the dashboard repo at `github.com/agsolutions29/dashboard`) and rewire the message flow.

- **Current flow:** User -> Meta -> Bot (direct) -> Meta -> User
- **New flow:** User -> Meta -> **Chatwoot Dashboard** -> Bot -> **Chatwoot Dashboard** -> Meta -> User
- The bot NO LONGER connects directly to Meta. Chatwoot handles all Meta webhooks.
- Messages arrive in Chatwoot, get forwarded to the bot via Agent Bot webhook, bot responds back through Chatwoot API
- **Users:**
  - Super Admin (Greta, Antonio): full control
  - Normal Users (clients): each client has their own Chatwoot account, bot connected to that account's inbox
- **Pause/activate bot button:** Already exists in Chatwoot (AgentBotInbox has active/inactive status). Pause bot on a single chat, human takes over, reactivate when done
- **Branding:** New logos, icons, colors for the Chatwoot instance

### STEP 3 -- HTML Management Page (No Code Modifications)

**What it means:** A standalone web page (accessible via link, login-protected) where the salon owner can configure:

- **Treatments/Services:** list, description, duration, price, notes
- **Salon Information:** address, hours, closing days, contacts, rules
- **Operators:** list, enabled treatments per operator, preferences/notes
- Changes saved persistently. Bot reads updated data automatically -- no deploy, no manual reload, no file editing. Save -> bot immediately uses new data.

---

## WHAT EXISTS TODAY

### Production Bot (`salon_bot_ec2_latest.py`, 3,444 lines)

Single monolithic FastAPI app on EC2. Hardcoded for "Aura Hair Studio". Handles WhatsApp + Instagram.

**AWS Resources (live):**

| Resource | Details |
|----------|---------|
| EC2 | `3.239.106.181:8000` |
| RDS PostgreSQL | `lyo-enterprise-database.cixc4kiw6r00.us-east-1.rds.amazonaws.com`, db `lyo_production`, user `lyoadmin` |
| Google Calendar | Service account at `/home/ec2-user/google_creds/service_account_key.json` |

**Integrations:**

| Service | Version | Auth |
|---------|---------|------|
| WhatsApp Cloud API | v18.0 | Bearer token, phone ID `961636900357709` |
| Instagram Graph API | v21.0 | Bearer token |
| OpenAI | GPT-4o | API key, Tools API with strict mode, temp 0 |
| Google Calendar | - | Service account (permanent) |
| Gmail SMTP | smtp.gmail.com:587 | App password |

**Endpoints (16):**
- Webhooks: `GET/POST /webhook` (WA), `GET/POST /webhook/instagram` (IG)
- Operations: `/health`, `/`, `/sblocca_chat/{phone}`, `/blocked_chats`
- Reminders: `/reminders/send-now`, `/reminders/check-unconfirmed`, `/reminders/status`, `/reminders/test-email`
- Test: `/test/conversations/{phone}`, `/test/conversations-by-date/{date}`, `/test/appointments/{phone}`, `/test/buffer`, `/test/buffer/clear`, `/test/simulate-reminder/{phone}`

**Scheduler (APScheduler, Europe/Rome):**
- 10:00 AM: WhatsApp reminders
- 10:05 AM: Instagram reminders
- 6:00 PM: Email owner about unconfirmed appointments

**OpenAI Tools (8):** create_appointment, check_availability, get_customer_appointments, cancel_appointment, modify_appointment, get_available_slots, confirm_reminder, escalate_to_human

**Bot persona:** "Simone" (presents as human employee), Italian-first, 7 hardcoded services

**Database tables (RDS):**
- `salon_appointments` -- id, customer_phone, customer_name, service_type, appointment_date, appointment_time, duration_minutes, price, status, google_event_id, created_at, reminder_sent_at, reminder_confirmed, reminder_confirmed_at, platform
- `salon_conversations` -- id, phone, name, message, response, timestamp, platform

**No operators table. No configurable services table. No business config table. Everything hardcoded in Python.**

**Env vars on EC2:** OPENAI_API_KEY, WHATSAPP_ACCESS_TOKEN, WHATSAPP_PHONE_NUMBER_ID, INSTAGRAM_ACCESS_TOKEN, INSTAGRAM_PAGE_ID, INSTAGRAM_APP_SECRET, DB_HOST/PORT/NAME/USER/PASSWORD, GOOGLE_SERVICE_ACCOUNT_FILE, GOOGLE_CALENDAR_ID, EMAIL_ADDRESS, EMAIL_PASSWORD, BOT_BASE_URL

---

### Dashboard (`github.com/agsolutions29/dashboard`)

This is a **fork of Chatwoot v3.13.0** -- the open-source customer support platform.

**Tech stack:** Ruby on Rails backend, Vue 3 frontend (Composition API), Tailwind CSS, PostgreSQL (pgvector), Redis, Sidekiq (background jobs), Vite (frontend build)

**Key Chatwoot concepts for this project:**

| Concept | What it is | How we use it |
|---------|-----------|---------------|
| **Account** | A tenant/organization in Chatwoot | Each salon client = one Account |
| **Inbox** | A communication channel connected to an Account | WhatsApp number or Instagram page = one Inbox |
| **Channel::Whatsapp** | WhatsApp Cloud API integration | `phone_number`, `provider_config` (API key, phone_number_id, webhook_verify_token). Provider: `whatsapp_cloud` |
| **Channel::Instagram** | Instagram DM integration | `access_token`, `instagram_id`, auto-subscribes to webhooks on create |
| **Agent Bot** | External bot connected via webhook | Has `outgoing_url` (our Lyo bot endpoint), `bot_config` (JSONB), `account_id` |
| **AgentBotInbox** | Links an Agent Bot to an Inbox | Has `status` (active/inactive = the pause button!), connects bot to specific WhatsApp/IG inbox |
| **Contact** | A customer who messages | Auto-created from WhatsApp/IG, has phone, name, profile |
| **Conversation** | A thread between contact and business | Created per contact per inbox, holds all messages |
| **Message** | A single message in a conversation | Has content, sender, message_type (incoming/outgoing) |

**Agent Bot webhook flow (how Lyo connects):**

1. Customer sends WhatsApp/IG message -> Meta delivers to Chatwoot webhook
2. Chatwoot creates/updates Conversation + Message
3. `AgentBotListener.message_created` fires
4. Checks if inbox has an active AgentBotInbox
5. If yes, dispatches `AgentBots::WebhookJob` with message payload to bot's `outgoing_url`
6. Lyo bot receives webhook, processes message, calls Chatwoot API to send response
7. Chatwoot sends response back through the channel (WA/IG)

**This means:** The bot doesn't talk to Meta directly anymore. Chatwoot is the middleware. The bot just receives webhooks from Chatwoot and responds via Chatwoot's API.

**Multi-tenant is built-in:** Chatwoot already isolates everything by Account. Each account has its own inboxes, contacts, conversations, agent bots. Adding a new client = creating a new Account + Inbox + linking the Agent Bot.

**Dashboard pages (Vue frontend):**
- Inbox (conversations list + chat interface)
- Contacts management
- Reports/Analytics
- Settings: Account, Agents, Inboxes, Agent Bots, Labels, Canned Responses, Automation, Teams, Custom Roles, Integrations, Billing, Macros, SLAs, Audit Logs, Attributes

**Docker setup:** docker-compose with Rails, Sidekiq, Vite, PostgreSQL (pgvector), Redis, Mailhog (dev email)

---

## HOW IT ALL CONNECTS (Target Architecture)

```
Customer (WhatsApp/Instagram)
        |
        v
    Meta APIs (WhatsApp Cloud / Instagram Graph)
        |
        v
    Chatwoot Dashboard (Ruby on Rails)
    - Receives webhooks from Meta
    - Stores conversations, contacts
    - Provides agent inbox UI
    - Super admins see all accounts
    - Each client sees only their conversations
        |
        v (Agent Bot webhook)
    Lyo Bot (Python/FastAPI)
    - Receives message payload from Chatwoot
    - Identifies which business (from account/inbox context)
    - Loads business config (treatments, operators, hours) from DB or management page
    - Processes with OpenAI (GPT-4o + tools)
    - Books appointments, checks availability, manages operators
    - Responds via Chatwoot API (not Meta directly)
        |
        v
    RDS PostgreSQL
    - Appointments (with operator assignment)
    - Conversations
    - Business config (treatments, operators, hours)
    - Customer name database
        |
        v
    Google Calendar
    - Per-operator or per-business calendar sync
```

---

## KNOWN ISSUES (Not Action Items -- Just Awareness)

### Security
- Credentials in git history (DB password, API keys from earlier commits)
- `connect_server.sh` has Hetzner root password in plaintext (tracked)
- Admin/test endpoints have no authentication
- No webhook signature verification
- CORS allows all origins with credentials

### Architecture
- All state in-memory (lost on restart)
- No DB connection pooling
- Synchronous OpenAI calls block event loop
- No unique constraint on appointment slots
- Business hours time validation commented out
- WhatsApp API v18.0 deprecated (should be v21.0+)

### Economics
- OpenAI GPT-4o costs ~$150/mo per active salon at current token usage

---

## REPO LOCATIONS

- **Production bot repo:** `/home/sakr_quraish/Projects/italian/` (git: `Hashem-Al-Qurashi/lyo-saas-production`, branch: `dev`)
- **Dashboard repo:** `github.com/agsolutions29/dashboard` (Chatwoot v3.13.0 fork, cloned to `/tmp/dashboard-explore/`)
- **Backup of old files:** `/home/sakr_quraish/Projects/italian/lyo-backup-2026-02-17/`
- **Project PDF:** `/home/sakr_quraish/Projects/italian/docs/plans/PROJECT LYO ASSISTANT 2.pdf`
- **Old SaaS design doc:** `/home/sakr_quraish/Projects/italian/docs/plans/2026-01-30-multi-tenant-saas-design.md`
