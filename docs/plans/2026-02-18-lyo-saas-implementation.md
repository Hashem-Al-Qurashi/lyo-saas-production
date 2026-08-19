# Lyo SaaS Multi-Tenant Bot - Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Transform the single-tenant Lyo booking bot into a multi-tenant SaaS platform where any salon can self-onboard via Chatwoot dashboard, configure their business via a management page, and have an AI booking bot running automatically.

**Architecture:** Single Global Agent Bot (account_id: NULL) in Chatwoot v4.10.1 (unmodified). Bot receives webhooks from Chatwoot, identifies tenant from `payload.account.id`, loads config from external PostgreSQL config DB. Management page is a separate FastAPI app writing to the same DB. Chatwoot handles all Meta API communication.

**Tech Stack:** Python 3.11+, FastAPI, psycopg2 (connection pooling), OpenAI GPT-4o (function calling with strict mode), Google Calendar API (service accounts), Chatwoot v4.10.1 (Docker), Jinja2 + Tailwind CSS (management page), pytest + pytest-asyncio (tests)

**Source Reference:** Current production bot at `salon_bot_ec2_latest.py` (3,444 lines). Port logic, don't refactor in-place.

---

## Architecture Diagram

```
Customer (WhatsApp/Instagram)
        |
        v
    Meta APIs
        |
        v
    Chatwoot v4.10.1 (unmodified, Docker)
    - Receives Meta webhooks
    - Stores conversations/contacts
    - Agent inbox UI for human takeover
    - Embedded Signup for self-service WA/IG connection
        |
        v (Agent Bot webhook: POST to bot's outgoing_url)
    Lyo Bot (FastAPI) ← NEW, clean rewrite
    - POST /webhook/chatwoot receives payload
    - Returns 200 immediately (5s Chatwoot timeout)
    - Background: resolve tenant → load config → AI → book → reply
    - Replies via Chatwoot API (not Meta directly)
        |
        v
    Config DB (PostgreSQL on RDS)
    - businesses (linked to chatwoot_account_id)
    - operators, treatments, operator_treatments
    - business_hours, business_closures
    - appointments (with operator_id)
    - customers (mandatory name DB)
        ^
        |
    Management Page (FastAPI + Jinja2)
    - Standalone web app, JWT auth
    - Salon owner configures treatments/operators/hours
    - Writes to config DB, bot reads automatically
```

## Chatwoot Webhook Payload (what bot receives)

```json
{
  "event": "message_created",
  "id": 123,
  "content": "Vorrei prenotare un taglio",
  "message_type": "incoming",
  "content_type": "text",
  "account": { "id": 1, "name": "Aura Hair Studio" },
  "conversation": {
    "id": 456, "inbox_id": 789, "status": "open",
    "meta": { "sender": { "id": 101, "name": "Maria", "phone_number": "+39..." } }
  },
  "inbox": { "id": 789, "name": "WhatsApp Aura" },
  "sender": { "id": 101, "name": "Maria", "phone_number": "+39...", "type": "contact" }
}
```

## Bot Reply API (how bot responds)

```
POST /api/v1/accounts/{account_id}/conversations/{conversation_id}/messages
Header: api_access_token: {agent_bot_access_token}
Body: { "content": "reply text", "message_type": "outgoing" }
```

Agent Bot can ONLY access (verified in `access_token_auth_helper.rb:2-6`):
- `conversations` - toggle_status, toggle_priority, create, update, custom_attributes
- `conversations/messages` - create
- `conversations/assignments` - create

## Dependency Graph

```
Task 1 (Scaffold) → all
Task 2 (Schema) → Task 3
Task 3 (Models) → Tasks 4, 7, 8
Task 4 (Tenant) ─┐
Task 7 (Customer)─┤→ parallel after Task 3
Task 8 (Avail.) ──┘
Task 5 (Webhook) ─┐
Task 6 (Chatwoot)─┤→ parallel after Task 4
Task 9 (Booking) ─┘
Task 10 (Tools) → Task 11 (AI)
Task 11 (AI) → Task 12 (Pipeline)
Task 12 (Pipeline) → Task 13, 20
Task 14 (Mgmt Auth) → Tasks 15-18 (parallel)
Task 19 (Docker) → after Tasks 12 + 18
Task 20 (Integration) → after Task 19
```

---

## Task 1: Project Scaffolding

**Files:**
- Create: `lyo-bot/requirements.txt`
- Create: `lyo-bot/app/__init__.py`
- Create: `lyo-bot/app/config.py`
- Create: `lyo-bot/app/main.py`
- Create: `lyo-bot/app/models/__init__.py`
- Create: `lyo-bot/app/services/__init__.py`
- Create: `lyo-bot/app/tools/__init__.py`
- Create: `lyo-bot/app/utils/__init__.py`
- Create: `lyo-bot/tests/__init__.py`
- Create: `lyo-bot/.env.example`

**Step 1: Create directory structure**

```bash
mkdir -p lyo-bot/app/{models,services,tools,utils}
mkdir -p lyo-bot/tests
mkdir -p lyo-bot/management/{templates,static}
mkdir -p lyo-bot/database
touch lyo-bot/app/__init__.py
touch lyo-bot/app/models/__init__.py
touch lyo-bot/app/services/__init__.py
touch lyo-bot/app/tools/__init__.py
touch lyo-bot/app/utils/__init__.py
touch lyo-bot/tests/__init__.py
touch lyo-bot/management/__init__.py
```

**Step 2: Create requirements.txt**

```
# lyo-bot/requirements.txt
fastapi==0.109.2
uvicorn[standard]==0.27.1
psycopg2-binary==2.9.9
httpx==0.27.0
openai==1.12.0
pydantic==2.6.1
pydantic-settings==2.1.0
python-jose[cryptography]==3.3.0
passlib[bcrypt]==1.7.4
jinja2==3.1.3
google-api-python-client==2.116.0
google-auth==2.27.0
apscheduler==3.10.4
pytz==2024.1

# Testing
pytest==8.0.0
pytest-asyncio==0.23.4
pytest-cov==4.1.0
httpx  # already above, used for TestClient
```

**Step 3: Create config.py**

```python
# lyo-bot/app/config.py
import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "lyo_production"
    db_user: str = "lyoadmin"
    db_password: str = ""
    db_sslmode: str = "require"

    # OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"

    # Chatwoot
    chatwoot_base_url: str = "http://localhost:3000"
    chatwoot_bot_token: str = ""

    # Google Calendar (default service account path)
    google_service_account_file: str = ""

    # Management Page
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480

    # Bot defaults
    default_timezone: str = "Europe/Rome"
    message_batch_delay_seconds: int = 15

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
```

**Step 4: Create main.py skeleton**

```python
# lyo-bot/app/main.py
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Lyo Bot", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok", "version": "2.0.0"}
```

**Step 5: Create .env.example**

```bash
# lyo-bot/.env.example
DB_HOST=localhost
DB_PORT=5432
DB_NAME=lyo_production
DB_USER=lyoadmin
DB_PASSWORD=
DB_SSLMODE=require

OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o

CHATWOOT_BASE_URL=http://localhost:3000
CHATWOOT_BOT_TOKEN=

GOOGLE_SERVICE_ACCOUNT_FILE=

JWT_SECRET=change-me-in-production
```

**Step 6: Verify project runs**

```bash
cd lyo-bot && pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8001
# Expected: INFO: Uvicorn running on http://0.0.0.0:8001
# GET /health → {"status": "ok", "version": "2.0.0"}
```

**Step 7: Commit**

```bash
git add lyo-bot/
git commit -m "feat: scaffold lyo-bot multi-tenant project structure"
```

---

## Task 2: Multi-Tenant Database Schema

**Files:**
- Create: `lyo-bot/database/schema.sql`
- Create: `lyo-bot/database/seed.sql`

**Step 1: Write the test — verify schema creates cleanly**

```python
# lyo-bot/tests/test_schema.py
import subprocess

def test_schema_syntax():
    """Verify SQL file has no syntax errors by running through psql --echo-all"""
    result = subprocess.run(
        ["python", "-c", "open('database/schema.sql').read()"],
        capture_output=True, text=True, cwd="lyo-bot"
    )
    assert result.returncode == 0

def test_schema_file_exists():
    from pathlib import Path
    assert Path("lyo-bot/database/schema.sql").exists()
```

Run: `pytest lyo-bot/tests/test_schema.py -v`
Expected: FAIL (file doesn't exist yet)

**Step 2: Write schema.sql**

```sql
-- lyo-bot/database/schema.sql
-- Lyo SaaS Multi-Tenant Schema
-- Each business is linked to a Chatwoot account via chatwoot_account_id

BEGIN;

-- 1. Businesses (core tenant table)
CREATE TABLE IF NOT EXISTS businesses (
    id SERIAL PRIMARY KEY,
    chatwoot_account_id INTEGER UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    slug VARCHAR(100) UNIQUE,
    timezone VARCHAR(50) NOT NULL DEFAULT 'Europe/Rome',
    language VARCHAR(10) NOT NULL DEFAULT 'it',
    bot_name VARCHAR(100) NOT NULL DEFAULT 'Assistente',
    bot_persona TEXT,
    address TEXT,
    phone VARCHAR(50),
    email VARCHAR(255),
    google_calendar_id VARCHAR(255) DEFAULT 'primary',
    google_service_account_json TEXT,
    owner_email VARCHAR(255),
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    settings JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2. Operators (stylists/staff per business)
CREATE TABLE IF NOT EXISTS operators (
    id SERIAL PRIMARY KEY,
    business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    technical_id VARCHAR(50) NOT NULL,
    display_name VARCHAR(100) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT true,
    sort_order INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(business_id, technical_id)
);

-- 3. Treatments (services offered per business)
CREATE TABLE IF NOT EXISTS treatments (
    id SERIAL PRIMARY KEY,
    business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    code VARCHAR(50) NOT NULL,
    name_it VARCHAR(100) NOT NULL,
    name_en VARCHAR(100),
    description_it TEXT,
    description_en TEXT,
    duration_minutes INTEGER NOT NULL,
    price DECIMAL(10,2),
    is_active BOOLEAN NOT NULL DEFAULT true,
    sort_order INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(business_id, code)
);

-- 4. Operator-Treatment mapping (which operators do which treatments)
CREATE TABLE IF NOT EXISTS operator_treatments (
    id SERIAL PRIMARY KEY,
    operator_id INTEGER NOT NULL REFERENCES operators(id) ON DELETE CASCADE,
    treatment_id INTEGER NOT NULL REFERENCES treatments(id) ON DELETE CASCADE,
    UNIQUE(operator_id, treatment_id)
);

-- 5. Business hours (per day of week)
CREATE TABLE IF NOT EXISTS business_hours (
    id SERIAL PRIMARY KEY,
    business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    day_of_week INTEGER NOT NULL CHECK (day_of_week BETWEEN 0 AND 6),
    is_open BOOLEAN NOT NULL DEFAULT true,
    open_time TIME,
    close_time TIME,
    UNIQUE(business_id, day_of_week)
);

-- 6. Business closures (holidays, special days)
CREATE TABLE IF NOT EXISTS business_closures (
    id SERIAL PRIMARY KEY,
    business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    closure_date DATE NOT NULL,
    reason VARCHAR(255),
    UNIQUE(business_id, closure_date)
);

-- 7. Customers (mandatory name DB per business)
CREATE TABLE IF NOT EXISTS customers (
    id SERIAL PRIMARY KEY,
    business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    phone VARCHAR(50) NOT NULL,
    first_name VARCHAR(100),
    last_name VARCHAR(100),
    platform VARCHAR(20),
    chatwoot_contact_id INTEGER,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(business_id, phone)
);

-- 8. Appointments (with operator assignment)
CREATE TABLE IF NOT EXISTS appointments (
    id SERIAL PRIMARY KEY,
    business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    operator_id INTEGER REFERENCES operators(id) ON DELETE SET NULL,
    customer_id INTEGER REFERENCES customers(id) ON DELETE SET NULL,
    customer_phone VARCHAR(50) NOT NULL,
    customer_name VARCHAR(255) NOT NULL,
    treatment_id INTEGER REFERENCES treatments(id) ON DELETE SET NULL,
    treatment_code VARCHAR(50) NOT NULL,
    appointment_date DATE NOT NULL,
    appointment_time TIME NOT NULL,
    duration_minutes INTEGER NOT NULL,
    price DECIMAL(10,2),
    status VARCHAR(20) NOT NULL DEFAULT 'confirmed',
    google_event_id VARCHAR(255),
    platform VARCHAR(20),
    chatwoot_conversation_id INTEGER,
    reminder_sent_at TIMESTAMPTZ,
    reminder_confirmed BOOLEAN NOT NULL DEFAULT false,
    reminder_confirmed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Prevent double-booking: same operator, same date+time
    UNIQUE(business_id, operator_id, appointment_date, appointment_time)
);

-- 9. Conversations (bot context per customer per business)
CREATE TABLE IF NOT EXISTS conversations (
    id SERIAL PRIMARY KEY,
    business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    customer_id INTEGER REFERENCES customers(id) ON DELETE SET NULL,
    customer_phone VARCHAR(50) NOT NULL,
    chatwoot_conversation_id INTEGER,
    messages JSONB NOT NULL DEFAULT '[]',
    booking_state JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(business_id, customer_phone)
);

-- 10. Management users (for management page login)
CREATE TABLE IF NOT EXISTS management_users (
    id SERIAL PRIMARY KEY,
    business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    name VARCHAR(255),
    role VARCHAR(20) NOT NULL DEFAULT 'owner',
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_operators_business ON operators(business_id);
CREATE INDEX IF NOT EXISTS idx_treatments_business ON treatments(business_id);
CREATE INDEX IF NOT EXISTS idx_customers_business_phone ON customers(business_id, phone);
CREATE INDEX IF NOT EXISTS idx_appointments_business_date ON appointments(business_id, appointment_date);
CREATE INDEX IF NOT EXISTS idx_appointments_operator_date ON appointments(operator_id, appointment_date);
CREATE INDEX IF NOT EXISTS idx_appointments_customer_phone ON appointments(customer_phone);
CREATE INDEX IF NOT EXISTS idx_appointments_status ON appointments(status);
CREATE INDEX IF NOT EXISTS idx_conversations_business_phone ON conversations(business_id, customer_phone);
CREATE INDEX IF NOT EXISTS idx_business_hours_business ON business_hours(business_id);
CREATE INDEX IF NOT EXISTS idx_business_closures_date ON business_closures(business_id, closure_date);

COMMIT;
```

**Step 3: Write seed.sql (test data for Aura Hair Studio)**

```sql
-- lyo-bot/database/seed.sql
-- Seed data: Aura Hair Studio as first business (chatwoot_account_id = 1)

BEGIN;

-- Business
INSERT INTO businesses (chatwoot_account_id, name, slug, timezone, language, bot_name, address, phone, email, owner_email, google_calendar_id)
VALUES (1, 'Aura Hair Studio', 'aura-hair-studio', 'Europe/Rome', 'it', 'Simone',
        'Via Example 123, 20121 Milano', '+39 02 1234567', 'info@aurahair.it',
        'notifiche.lyo@gmail.com', 'primary')
ON CONFLICT (chatwoot_account_id) DO NOTHING;

-- Operators
INSERT INTO operators (business_id, technical_id, display_name, sort_order) VALUES
    ((SELECT id FROM businesses WHERE slug='aura-hair-studio'), 'operatore_1', 'Giulia', 1),
    ((SELECT id FROM businesses WHERE slug='aura-hair-studio'), 'operatore_2', 'Martina', 2),
    ((SELECT id FROM businesses WHERE slug='aura-hair-studio'), 'operatore_3', 'Sara', 3),
    ((SELECT id FROM businesses WHERE slug='aura-hair-studio'), 'operatore_4', 'Luca', 4)
ON CONFLICT (business_id, technical_id) DO NOTHING;

-- Treatments
INSERT INTO treatments (business_id, code, name_it, name_en, duration_minutes, price, sort_order) VALUES
    ((SELECT id FROM businesses WHERE slug='aura-hair-studio'), 'taglio_donna', 'Taglio Donna', 'Women''s Haircut', 45, 60, 1),
    ((SELECT id FROM businesses WHERE slug='aura-hair-studio'), 'taglio_uomo', 'Taglio Uomo', 'Men''s Haircut', 45, 40, 2),
    ((SELECT id FROM businesses WHERE slug='aura-hair-studio'), 'piega', 'Piega', 'Styling/Blow-dry', 30, 30, 3),
    ((SELECT id FROM businesses WHERE slug='aura-hair-studio'), 'colore_base', 'Colore Base', 'Basic Color', 90, 70, 4),
    ((SELECT id FROM businesses WHERE slug='aura-hair-studio'), 'balayage', 'Balayage/Schiariture', 'Balayage/Highlights', 150, 130, 5),
    ((SELECT id FROM businesses WHERE slug='aura-hair-studio'), 'trattamento_ristrutturante', 'Trattamento Ristrutturante', 'Restructuring Treatment', 45, 45, 6),
    ((SELECT id FROM businesses WHERE slug='aura-hair-studio'), 'trattamento_cute', 'Trattamento Cute', 'Scalp Treatment', 30, 40, 7)
ON CONFLICT (business_id, code) DO NOTHING;

-- Operator-Treatment mappings (all operators do all treatments except balayage = only Giulia, Martina)
-- Giulia: all treatments
INSERT INTO operator_treatments (operator_id, treatment_id)
SELECT o.id, t.id FROM operators o, treatments t
WHERE o.technical_id = 'operatore_1' AND o.business_id = t.business_id
ON CONFLICT DO NOTHING;

-- Martina: all treatments
INSERT INTO operator_treatments (operator_id, treatment_id)
SELECT o.id, t.id FROM operators o, treatments t
WHERE o.technical_id = 'operatore_2' AND o.business_id = t.business_id
ON CONFLICT DO NOTHING;

-- Sara: taglio_donna, taglio_uomo, piega, trattamento_ristrutturante, trattamento_cute
INSERT INTO operator_treatments (operator_id, treatment_id)
SELECT o.id, t.id FROM operators o, treatments t
WHERE o.technical_id = 'operatore_3' AND o.business_id = t.business_id
AND t.code IN ('taglio_donna', 'taglio_uomo', 'piega', 'trattamento_ristrutturante', 'trattamento_cute')
ON CONFLICT DO NOTHING;

-- Luca: taglio_donna, taglio_uomo, piega
INSERT INTO operator_treatments (operator_id, treatment_id)
SELECT o.id, t.id FROM operators o, treatments t
WHERE o.technical_id = 'operatore_4' AND o.business_id = t.business_id
AND t.code IN ('taglio_donna', 'taglio_uomo', 'piega')
ON CONFLICT DO NOTHING;

-- Business hours (Tue-Sat open, Mon+Sun closed)
INSERT INTO business_hours (business_id, day_of_week, is_open, open_time, close_time) VALUES
    ((SELECT id FROM businesses WHERE slug='aura-hair-studio'), 0, false, NULL, NULL),          -- Monday: closed
    ((SELECT id FROM businesses WHERE slug='aura-hair-studio'), 1, true, '09:00', '19:00'),     -- Tuesday
    ((SELECT id FROM businesses WHERE slug='aura-hair-studio'), 2, true, '09:00', '19:00'),     -- Wednesday
    ((SELECT id FROM businesses WHERE slug='aura-hair-studio'), 3, true, '09:00', '19:00'),     -- Thursday
    ((SELECT id FROM businesses WHERE slug='aura-hair-studio'), 4, true, '09:00', '20:00'),     -- Friday
    ((SELECT id FROM businesses WHERE slug='aura-hair-studio'), 5, true, '09:00', '18:00'),     -- Saturday
    ((SELECT id FROM businesses WHERE slug='aura-hair-studio'), 6, false, NULL, NULL)            -- Sunday: closed
ON CONFLICT (business_id, day_of_week) DO NOTHING;

COMMIT;
```

**Step 4: Run tests**

```bash
pytest lyo-bot/tests/test_schema.py -v
```
Expected: PASS

**Step 5: Commit**

```bash
git add lyo-bot/database/
git commit -m "feat: add multi-tenant database schema with operators and treatments"
```

---

## Task 3: Database Connection + Pydantic Models

**Files:**
- Create: `lyo-bot/app/models/database.py`
- Create: `lyo-bot/app/models/schemas.py`
- Create: `lyo-bot/tests/test_models.py`

**Step 1: Write failing test**

```python
# lyo-bot/tests/test_models.py
from app.models.schemas import (
    Business, Operator, Treatment, OperatorTreatment,
    BusinessHours, Customer, Appointment, WebhookPayload,
)


def test_business_schema():
    biz = Business(
        id=1, chatwoot_account_id=1, name="Test Salon",
        timezone="Europe/Rome", language="it", bot_name="Simone", status="active",
    )
    assert biz.chatwoot_account_id == 1
    assert biz.bot_name == "Simone"


def test_operator_schema():
    op = Operator(id=1, business_id=1, technical_id="op_1", display_name="Giulia", is_active=True)
    assert op.display_name == "Giulia"


def test_treatment_schema():
    t = Treatment(id=1, business_id=1, code="taglio_donna", name_it="Taglio Donna", duration_minutes=45, price=60.0)
    assert t.duration_minutes == 45


def test_webhook_payload_parsing():
    raw = {
        "event": "message_created",
        "id": 123,
        "content": "Ciao vorrei prenotare",
        "message_type": "incoming",
        "content_type": "text",
        "account": {"id": 1, "name": "Aura Hair Studio"},
        "conversation": {"id": 456, "inbox_id": 789},
        "inbox": {"id": 789, "name": "WhatsApp"},
        "sender": {"id": 101, "name": "Maria", "phone_number": "+393331234567", "type": "contact"},
    }
    payload = WebhookPayload(**raw)
    assert payload.account.id == 1
    assert payload.content == "Ciao vorrei prenotare"
    assert payload.sender.phone_number == "+393331234567"


def test_webhook_payload_ignores_outgoing():
    raw = {
        "event": "message_created",
        "id": 124,
        "content": "Bot reply",
        "message_type": "outgoing",
        "account": {"id": 1, "name": "Test"},
        "conversation": {"id": 456},
        "inbox": {"id": 789, "name": "WA"},
        "sender": {"id": 1, "name": "Bot", "type": "agent_bot"},
    }
    payload = WebhookPayload(**raw)
    assert payload.is_incoming() is False
```

Run: `pytest lyo-bot/tests/test_models.py -v`
Expected: FAIL (modules don't exist)

**Step 2: Implement schemas.py**

```python
# lyo-bot/app/models/schemas.py
from __future__ import annotations
from typing import Optional, List
from decimal import Decimal
from datetime import date, time, datetime
from pydantic import BaseModel, Field


# --- Database models ---

class Business(BaseModel):
    id: int
    chatwoot_account_id: int
    name: str
    slug: Optional[str] = None
    timezone: str = "Europe/Rome"
    language: str = "it"
    bot_name: str = "Assistente"
    bot_persona: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    google_calendar_id: str = "primary"
    google_service_account_json: Optional[str] = None
    owner_email: Optional[str] = None
    status: str = "active"
    settings: dict = Field(default_factory=dict)
    # Loaded relations
    operators: List[Operator] = Field(default_factory=list)
    treatments: List[Treatment] = Field(default_factory=list)
    hours: List[BusinessHours] = Field(default_factory=list)


class Operator(BaseModel):
    id: int
    business_id: int
    technical_id: str
    display_name: str
    is_active: bool = True
    sort_order: int = 0
    notes: Optional[str] = None
    treatment_ids: List[int] = Field(default_factory=list)


class Treatment(BaseModel):
    id: int
    business_id: int
    code: str
    name_it: str
    name_en: Optional[str] = None
    description_it: Optional[str] = None
    description_en: Optional[str] = None
    duration_minutes: int
    price: Optional[Decimal] = None
    is_active: bool = True
    sort_order: int = 0
    notes: Optional[str] = None
    operator_ids: List[int] = Field(default_factory=list)


class OperatorTreatment(BaseModel):
    operator_id: int
    treatment_id: int


class BusinessHours(BaseModel):
    business_id: int
    day_of_week: int  # 0=Monday, 6=Sunday
    is_open: bool = True
    open_time: Optional[time] = None
    close_time: Optional[time] = None


class Customer(BaseModel):
    id: int
    business_id: int
    phone: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    platform: Optional[str] = None
    chatwoot_contact_id: Optional[int] = None

    @property
    def full_name(self) -> Optional[str]:
        parts = [p for p in [self.first_name, self.last_name] if p]
        return " ".join(parts) if parts else None

    @property
    def has_name(self) -> bool:
        return bool(self.first_name)


class Appointment(BaseModel):
    id: int
    business_id: int
    operator_id: Optional[int] = None
    operator_name: Optional[str] = None
    customer_phone: str
    customer_name: str
    treatment_code: str
    treatment_name: Optional[str] = None
    appointment_date: date
    appointment_time: time
    duration_minutes: int
    price: Optional[Decimal] = None
    status: str = "confirmed"
    google_event_id: Optional[str] = None
    platform: Optional[str] = None


# --- Webhook payload models ---

class WebhookAccount(BaseModel):
    id: int
    name: Optional[str] = None


class WebhookConversation(BaseModel):
    id: int
    inbox_id: Optional[int] = None
    status: Optional[str] = None
    meta: Optional[dict] = None


class WebhookInbox(BaseModel):
    id: int
    name: Optional[str] = None


class WebhookSender(BaseModel):
    id: int
    name: Optional[str] = None
    phone_number: Optional[str] = None
    type: Optional[str] = None
    email: Optional[str] = None


class WebhookPayload(BaseModel):
    event: str
    id: Optional[int] = None
    content: Optional[str] = None
    message_type: Optional[str] = None
    content_type: Optional[str] = None
    account: Optional[WebhookAccount] = None
    conversation: Optional[WebhookConversation] = None
    inbox: Optional[WebhookInbox] = None
    sender: Optional[WebhookSender] = None

    def is_incoming(self) -> bool:
        return self.message_type == "incoming"

    def is_message_created(self) -> bool:
        return self.event == "message_created"


# Forward refs
Business.model_rebuild()
```

**Step 3: Implement database.py**

```python
# lyo-bot/app/models/database.py
import logging
from contextlib import contextmanager
from psycopg2.pool import ThreadedConnectionPool
from app.config import settings

logger = logging.getLogger(__name__)

_pool: ThreadedConnectionPool | None = None


def get_pool() -> ThreadedConnectionPool:
    global _pool
    if _pool is None or _pool.closed:
        _pool = ThreadedConnectionPool(
            minconn=2,
            maxconn=10,
            host=settings.db_host,
            port=settings.db_port,
            database=settings.db_name,
            user=settings.db_user,
            password=settings.db_password,
            sslmode=settings.db_sslmode,
        )
        logger.info("Database connection pool created")
    return _pool


@contextmanager
def get_connection():
    pool = get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def close_pool():
    global _pool
    if _pool and not _pool.closed:
        _pool.closeall()
        logger.info("Database connection pool closed")
```

**Step 4: Run tests**

```bash
pytest lyo-bot/tests/test_models.py -v
```
Expected: PASS

**Step 5: Commit**

```bash
git add lyo-bot/app/models/ lyo-bot/tests/test_models.py
git commit -m "feat: add Pydantic schemas and database connection pool"
```

---

## Task 4: Tenant Resolution Service

**Files:**
- Create: `lyo-bot/app/services/tenant.py`
- Create: `lyo-bot/tests/test_tenant.py`

**Step 1: Write failing test**

```python
# lyo-bot/tests/test_tenant.py
import pytest
from unittest.mock import patch, MagicMock
from app.services.tenant import TenantService


@pytest.fixture
def tenant_service():
    return TenantService()


def _mock_business_row():
    return (1, 1, "Aura Hair Studio", "aura", "Europe/Rome", "it", "Simone", None,
            "Via Roma 123", "+39 123", "info@aura.it", "primary", None,
            "owner@aura.it", "active", "{}")


def _mock_treatment_rows():
    return [
        (1, 1, "taglio_donna", "Taglio Donna", "Women's Haircut", None, None, 45, 60.0, True, 1, None),
        (2, 1, "piega", "Piega", "Blow-dry", None, None, 30, 30.0, True, 2, None),
    ]


def _mock_operator_rows():
    return [
        (1, 1, "operatore_1", "Giulia", True, 1, None),
        (2, 1, "operatore_2", "Martina", True, 2, None),
    ]


def _mock_operator_treatment_rows():
    return [(1, 1), (1, 2), (2, 1)]  # Giulia: both, Martina: taglio only


def _mock_hours_rows():
    return [
        (1, 0, False, None, None),  # Mon closed
        (1, 1, True, "09:00", "19:00"),  # Tue open
    ]


class TestTenantService:
    @patch("app.services.tenant.get_connection")
    def test_load_business_by_account_id(self, mock_conn, tenant_service):
        mock_cur = MagicMock()
        mock_cur.fetchone.side_effect = [_mock_business_row()]
        mock_cur.fetchall.side_effect = [
            _mock_treatment_rows(),
            _mock_operator_rows(),
            _mock_operator_treatment_rows(),
            _mock_hours_rows(),
        ]
        mock_conn.return_value.__enter__ = lambda s: MagicMock(cursor=lambda: mock_cur)
        mock_conn.return_value.__exit__ = lambda s, *a: None

        biz = tenant_service.get_business(chatwoot_account_id=1)
        assert biz is not None
        assert biz.name == "Aura Hair Studio"
        assert biz.bot_name == "Simone"

    def test_cache_hit(self, tenant_service):
        from app.models.schemas import Business
        cached = Business(id=1, chatwoot_account_id=1, name="Cached", bot_name="Bot")
        tenant_service._cache[1] = (cached, 9999999999)  # far future expiry
        result = tenant_service.get_business(1)
        assert result.name == "Cached"

    def test_unknown_account_returns_none(self, tenant_service):
        with patch("app.services.tenant.get_connection") as mock_conn:
            mock_cur = MagicMock()
            mock_cur.fetchone.return_value = None
            mock_conn.return_value.__enter__ = lambda s: MagicMock(cursor=lambda: mock_cur)
            mock_conn.return_value.__exit__ = lambda s, *a: None
            result = tenant_service.get_business(999)
            assert result is None
```

Run: `pytest lyo-bot/tests/test_tenant.py -v`
Expected: FAIL

**Step 2: Implement tenant.py**

```python
# lyo-bot/app/services/tenant.py
import time
import logging
from typing import Optional
from app.models.database import get_connection
from app.models.schemas import Business, Operator, Treatment, BusinessHours

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 300  # 5 minutes


class TenantService:
    def __init__(self):
        self._cache: dict[int, tuple[Business, float]] = {}

    def get_business(self, chatwoot_account_id: int) -> Optional[Business]:
        # Check cache
        if chatwoot_account_id in self._cache:
            biz, expiry = self._cache[chatwoot_account_id]
            if time.time() < expiry:
                return biz

        # Load from DB
        biz = self._load_from_db(chatwoot_account_id)
        if biz:
            self._cache[chatwoot_account_id] = (biz, time.time() + CACHE_TTL_SECONDS)
        return biz

    def invalidate(self, chatwoot_account_id: int):
        self._cache.pop(chatwoot_account_id, None)

    def invalidate_all(self):
        self._cache.clear()

    def _load_from_db(self, chatwoot_account_id: int) -> Optional[Business]:
        try:
            with get_connection() as conn:
                cur = conn.cursor()

                # Load business
                cur.execute(
                    """SELECT id, chatwoot_account_id, name, slug, timezone, language,
                              bot_name, bot_persona, address, phone, email,
                              google_calendar_id, google_service_account_json,
                              owner_email, status, settings::text
                       FROM businesses WHERE chatwoot_account_id = %s AND status = 'active'""",
                    (chatwoot_account_id,),
                )
                row = cur.fetchone()
                if not row:
                    logger.warning(f"No business found for chatwoot_account_id={chatwoot_account_id}")
                    return None

                import json
                biz = Business(
                    id=row[0], chatwoot_account_id=row[1], name=row[2], slug=row[3],
                    timezone=row[4], language=row[5], bot_name=row[6], bot_persona=row[7],
                    address=row[8], phone=row[9], email=row[10],
                    google_calendar_id=row[11], google_service_account_json=row[12],
                    owner_email=row[13], status=row[14],
                    settings=json.loads(row[15]) if row[15] else {},
                )
                business_id = biz.id

                # Load treatments
                cur.execute(
                    """SELECT id, business_id, code, name_it, name_en, description_it,
                              description_en, duration_minutes, price, is_active, sort_order, notes
                       FROM treatments WHERE business_id = %s AND is_active = true
                       ORDER BY sort_order""",
                    (business_id,),
                )
                treatments = []
                for r in cur.fetchall():
                    treatments.append(Treatment(
                        id=r[0], business_id=r[1], code=r[2], name_it=r[3], name_en=r[4],
                        description_it=r[5], description_en=r[6], duration_minutes=r[7],
                        price=r[8], is_active=r[9], sort_order=r[10], notes=r[11],
                    ))

                # Load operators
                cur.execute(
                    """SELECT id, business_id, technical_id, display_name, is_active, sort_order, notes
                       FROM operators WHERE business_id = %s AND is_active = true
                       ORDER BY sort_order""",
                    (business_id,),
                )
                operators = []
                for r in cur.fetchall():
                    operators.append(Operator(
                        id=r[0], business_id=r[1], technical_id=r[2], display_name=r[3],
                        is_active=r[4], sort_order=r[5], notes=r[6],
                    ))

                # Load operator-treatment mappings
                cur.execute(
                    """SELECT ot.operator_id, ot.treatment_id
                       FROM operator_treatments ot
                       JOIN operators o ON ot.operator_id = o.id
                       WHERE o.business_id = %s""",
                    (business_id,),
                )
                for op_id, tr_id in cur.fetchall():
                    for op in operators:
                        if op.id == op_id:
                            op.treatment_ids.append(tr_id)
                    for tr in treatments:
                        if tr.id == tr_id:
                            tr.operator_ids.append(op_id)

                # Load business hours
                cur.execute(
                    """SELECT business_id, day_of_week, is_open, open_time::text, close_time::text
                       FROM business_hours WHERE business_id = %s ORDER BY day_of_week""",
                    (business_id,),
                )
                hours = []
                for r in cur.fetchall():
                    hours.append(BusinessHours(
                        business_id=r[0], day_of_week=r[1], is_open=r[2],
                        open_time=r[3], close_time=r[4],
                    ))

                biz.operators = operators
                biz.treatments = treatments
                biz.hours = hours

                logger.info(
                    f"Loaded business '{biz.name}': {len(treatments)} treatments, "
                    f"{len(operators)} operators, {len(hours)} hour rules"
                )
                return biz

        except Exception as e:
            logger.error(f"Error loading business for account {chatwoot_account_id}: {e}")
            return None


# Singleton
tenant_service = TenantService()
```

**Step 3: Run tests**

```bash
pytest lyo-bot/tests/test_tenant.py -v
```
Expected: PASS

**Step 4: Commit**

```bash
git add lyo-bot/app/services/tenant.py lyo-bot/tests/test_tenant.py
git commit -m "feat: add tenant resolution service with caching"
```

---

## Task 5: Chatwoot Webhook Endpoint

**Files:**
- Modify: `lyo-bot/app/main.py`
- Create: `lyo-bot/tests/test_webhook.py`

**Step 1: Write failing test**

```python
# lyo-bot/tests/test_webhook.py
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def _make_incoming_payload(content="Ciao", account_id=1, conv_id=456):
    return {
        "event": "message_created",
        "id": 123,
        "content": content,
        "message_type": "incoming",
        "content_type": "text",
        "account": {"id": account_id, "name": "Test Salon"},
        "conversation": {"id": conv_id, "inbox_id": 789},
        "inbox": {"id": 789, "name": "WhatsApp"},
        "sender": {"id": 101, "name": "Maria", "phone_number": "+393331234567", "type": "contact"},
    }


def test_webhook_returns_200_immediately():
    response = client.post("/webhook/chatwoot", json=_make_incoming_payload())
    assert response.status_code == 200
    assert response.json()["status"] == "received"


def test_webhook_ignores_outgoing():
    payload = _make_incoming_payload()
    payload["message_type"] = "outgoing"
    response = client.post("/webhook/chatwoot", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


def test_webhook_ignores_non_message_events():
    payload = _make_incoming_payload()
    payload["event"] = "conversation_resolved"
    response = client.post("/webhook/chatwoot", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


def test_webhook_rejects_empty_content():
    payload = _make_incoming_payload(content="")
    response = client.post("/webhook/chatwoot", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
```

Run: `pytest lyo-bot/tests/test_webhook.py -v`
Expected: FAIL

**Step 2: Implement webhook endpoint in main.py**

```python
# lyo-bot/app/main.py
import asyncio
import logging
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from app.models.schemas import WebhookPayload

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Lyo Bot", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok", "version": "2.0.0"}


@app.post("/webhook/chatwoot")
async def chatwoot_webhook(request: Request):
    """Receive webhook from Chatwoot Agent Bot system.
    Returns 200 immediately (Chatwoot has 5s timeout).
    Processes message in background."""
    try:
        raw = await request.json()
        payload = WebhookPayload(**raw)

        # Only process incoming message_created events with content
        if not payload.is_message_created() or not payload.is_incoming():
            return {"status": "ignored", "reason": "not incoming message"}

        if not payload.content or not payload.content.strip():
            return {"status": "ignored", "reason": "empty content"}

        if not payload.account or not payload.conversation:
            return {"status": "ignored", "reason": "missing account or conversation"}

        logger.info(
            f"Webhook received: account={payload.account.id} "
            f"conv={payload.conversation.id} "
            f"from={payload.sender.phone_number if payload.sender else 'unknown'} "
            f"content={payload.content[:50]}..."
        )

        # Process in background (don't block the 200 response)
        asyncio.create_task(_process_message(payload))

        return {"status": "received"}

    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return {"status": "error", "message": str(e)}


async def _process_message(payload: WebhookPayload):
    """Background task: resolve tenant, process AI, send reply."""
    try:
        # TODO: Wire up in Task 12 (Message Processing Pipeline)
        logger.info(f"Processing message for account {payload.account.id}: {payload.content[:50]}")
    except Exception as e:
        logger.error(f"Error processing message: {e}")
```

**Step 3: Run tests**

```bash
pytest lyo-bot/tests/test_webhook.py -v
```
Expected: PASS

**Step 4: Commit**

```bash
git add lyo-bot/app/main.py lyo-bot/tests/test_webhook.py
git commit -m "feat: add Chatwoot webhook endpoint with async background processing"
```

---

## Task 6: Chatwoot API Client

**Files:**
- Create: `lyo-bot/app/services/chatwoot.py`
- Create: `lyo-bot/tests/test_chatwoot.py`

**Step 1: Write failing test**

```python
# lyo-bot/tests/test_chatwoot.py
import pytest
from unittest.mock import AsyncMock, patch
from app.services.chatwoot import ChatwootClient


@pytest.fixture
def client():
    return ChatwootClient(base_url="http://localhost:3000", bot_token="test-token")


@pytest.mark.asyncio
async def test_send_message(client):
    with patch.object(client, "_http", new_callable=lambda: type("", (), {"post": AsyncMock(return_value=type("", (), {"status_code": 200, "json": lambda: {"id": 1}})())})()) as mock_http:
        result = await client.send_message(account_id=1, conversation_id=456, content="Ciao!")
        mock_http.post.assert_called_once()
        call_args = mock_http.post.call_args
        assert "/api/v1/accounts/1/conversations/456/messages" in call_args[0][0]


@pytest.mark.asyncio
async def test_send_message_includes_auth_header(client):
    with patch.object(client, "_http") as mock_http:
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": 1}
        mock_http.post = AsyncMock(return_value=mock_response)

        await client.send_message(1, 456, "Test")
        call_kwargs = mock_http.post.call_args[1]
        assert call_kwargs["headers"]["api_access_token"] == "test-token"
```

Run: `pytest lyo-bot/tests/test_chatwoot.py -v`
Expected: FAIL

**Step 2: Implement chatwoot.py**

```python
# lyo-bot/app/services/chatwoot.py
import logging
import httpx
from app.config import settings

logger = logging.getLogger(__name__)


class ChatwootClient:
    def __init__(self, base_url: str = None, bot_token: str = None):
        self.base_url = (base_url or settings.chatwoot_base_url).rstrip("/")
        self.bot_token = bot_token or settings.chatwoot_bot_token
        self._http = httpx.AsyncClient(timeout=30.0)

    async def send_message(
        self, account_id: int, conversation_id: int, content: str,
        message_type: str = "outgoing", private: bool = False,
    ) -> dict:
        url = f"{self.base_url}/api/v1/accounts/{account_id}/conversations/{conversation_id}/messages"
        headers = {"api_access_token": self.bot_token}
        body = {
            "content": content,
            "message_type": message_type,
            "private": private,
        }

        try:
            response = await self._http.post(url, json=body, headers=headers)
            if response.status_code in (200, 201):
                logger.info(f"Message sent to conv {conversation_id} in account {account_id}")
                return response.json()
            else:
                logger.error(
                    f"Chatwoot API error {response.status_code}: {response.text}"
                )
                return {"error": response.text, "status_code": response.status_code}
        except Exception as e:
            logger.error(f"Failed to send message via Chatwoot: {e}")
            return {"error": str(e)}

    async def toggle_conversation_status(
        self, account_id: int, conversation_id: int, status: str = "open",
    ) -> dict:
        url = f"{self.base_url}/api/v1/accounts/{account_id}/conversations/{conversation_id}/toggle_status"
        headers = {"api_access_token": self.bot_token}
        body = {"status": status}

        try:
            response = await self._http.post(url, json=body, headers=headers)
            return response.json()
        except Exception as e:
            logger.error(f"Failed to toggle conversation status: {e}")
            return {"error": str(e)}

    async def assign_conversation(
        self, account_id: int, conversation_id: int, assignee_id: int = None,
    ) -> dict:
        url = f"{self.base_url}/api/v1/accounts/{account_id}/conversations/{conversation_id}/assignments"
        headers = {"api_access_token": self.bot_token}
        body = {"assignee_id": assignee_id}

        try:
            response = await self._http.post(url, json=body, headers=headers)
            return response.json()
        except Exception as e:
            logger.error(f"Failed to assign conversation: {e}")
            return {"error": str(e)}

    async def close(self):
        await self._http.aclose()


# Singleton
chatwoot_client = ChatwootClient()
```

**Step 3: Run tests**

```bash
pytest lyo-bot/tests/test_chatwoot.py -v
```
Expected: PASS

**Step 4: Commit**

```bash
git add lyo-bot/app/services/chatwoot.py lyo-bot/tests/test_chatwoot.py
git commit -m "feat: add Chatwoot API client for sending messages"
```

---

## Task 7: Customer Name Database Service

**Files:**
- Create: `lyo-bot/app/services/customer.py`
- Create: `lyo-bot/tests/test_customer.py`

**Step 1: Write failing test**

```python
# lyo-bot/tests/test_customer.py
import pytest
from unittest.mock import patch, MagicMock
from app.services.customer import CustomerService

service = CustomerService()


class TestCustomerService:
    @patch("app.services.customer.get_connection")
    def test_get_existing_customer(self, mock_conn):
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = (1, 1, "+393331234567", "Maria", "Rossi", "whatsapp", None)
        mock_conn.return_value.__enter__ = lambda s: MagicMock(cursor=lambda: mock_cur)
        mock_conn.return_value.__exit__ = lambda s, *a: None

        customer = service.get_customer(business_id=1, phone="+393331234567")
        assert customer is not None
        assert customer.first_name == "Maria"
        assert customer.has_name is True

    @patch("app.services.customer.get_connection")
    def test_create_customer_without_name(self, mock_conn):
        mock_cur = MagicMock()
        mock_cur.fetchone.side_effect = [None, (2, 1, "+393339999999", None, None, "whatsapp", None)]
        mock_conn.return_value.__enter__ = lambda s: MagicMock(cursor=lambda: mock_cur)
        mock_conn.return_value.__exit__ = lambda s, *a: None

        customer = service.get_or_create(business_id=1, phone="+393339999999", platform="whatsapp")
        assert customer is not None
        assert customer.has_name is False

    @patch("app.services.customer.get_connection")
    def test_update_customer_name(self, mock_conn):
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = (1, 1, "+393331234567", "Maria", "Rossi", "whatsapp", None)
        mock_conn.return_value.__enter__ = lambda s: MagicMock(cursor=lambda: mock_cur)
        mock_conn.return_value.__exit__ = lambda s, *a: None

        customer = service.update_name(business_id=1, phone="+393331234567", first_name="Maria", last_name="Rossi")
        assert customer.full_name == "Maria Rossi"
```

Run: `pytest lyo-bot/tests/test_customer.py -v`
Expected: FAIL

**Step 2: Implement customer.py**

```python
# lyo-bot/app/services/customer.py
import logging
from typing import Optional
from app.models.database import get_connection
from app.models.schemas import Customer

logger = logging.getLogger(__name__)


class CustomerService:
    def get_customer(self, business_id: int, phone: str) -> Optional[Customer]:
        phone = self._normalize_phone(phone)
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """SELECT id, business_id, phone, first_name, last_name, platform, chatwoot_contact_id
                   FROM customers WHERE business_id = %s AND phone = %s""",
                (business_id, phone),
            )
            row = cur.fetchone()
            if not row:
                return None
            return Customer(
                id=row[0], business_id=row[1], phone=row[2],
                first_name=row[3], last_name=row[4], platform=row[5],
                chatwoot_contact_id=row[6],
            )

    def get_or_create(
        self, business_id: int, phone: str, platform: str = None,
        chatwoot_contact_id: int = None,
    ) -> Customer:
        phone = self._normalize_phone(phone)
        existing = self.get_customer(business_id, phone)
        if existing:
            return existing

        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO customers (business_id, phone, platform, chatwoot_contact_id)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (business_id, phone) DO NOTHING
                   RETURNING id, business_id, phone, first_name, last_name, platform, chatwoot_contact_id""",
                (business_id, phone, platform, chatwoot_contact_id),
            )
            row = cur.fetchone()
            if row:
                return Customer(
                    id=row[0], business_id=row[1], phone=row[2],
                    first_name=row[3], last_name=row[4], platform=row[5],
                    chatwoot_contact_id=row[6],
                )
            # Race condition: another request created it between our check and insert
            return self.get_customer(business_id, phone)

    def update_name(
        self, business_id: int, phone: str, first_name: str, last_name: str = None,
    ) -> Optional[Customer]:
        phone = self._normalize_phone(phone)
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """UPDATE customers SET first_name = %s, last_name = %s, updated_at = NOW()
                   WHERE business_id = %s AND phone = %s
                   RETURNING id, business_id, phone, first_name, last_name, platform, chatwoot_contact_id""",
                (first_name.strip(), (last_name or "").strip() or None, business_id, phone),
            )
            row = cur.fetchone()
            if not row:
                return None
            return Customer(
                id=row[0], business_id=row[1], phone=row[2],
                first_name=row[3], last_name=row[4], platform=row[5],
                chatwoot_contact_id=row[6],
            )

    @staticmethod
    def _normalize_phone(phone: str) -> str:
        if not phone:
            return phone
        cleaned = phone.strip().replace(" ", "").replace("-", "")
        if not cleaned.startswith("+"):
            cleaned = "+" + cleaned
        return cleaned


customer_service = CustomerService()
```

**Step 3: Run tests**

```bash
pytest lyo-bot/tests/test_customer.py -v
```
Expected: PASS

**Step 4: Commit**

```bash
git add lyo-bot/app/services/customer.py lyo-bot/tests/test_customer.py
git commit -m "feat: add customer name database service"
```

---

## Task 8: Multi-Operator Availability Service

**Files:**
- Create: `lyo-bot/app/services/availability.py`
- Create: `lyo-bot/tests/test_availability.py`

**This is the most critical task — implements the parallel booking and occupancy logic from the project PDF.**

**Step 1: Write failing tests**

```python
# lyo-bot/tests/test_availability.py
import pytest
from unittest.mock import patch, MagicMock
from datetime import date, time
from app.services.availability import AvailabilityService
from app.models.schemas import Business, Operator, Treatment, BusinessHours

# Test fixtures
def _make_business():
    ops = [
        Operator(id=1, business_id=1, technical_id="op_1", display_name="Giulia", treatment_ids=[1, 2]),
        Operator(id=2, business_id=1, technical_id="op_2", display_name="Martina", treatment_ids=[1, 2]),
        Operator(id=3, business_id=1, technical_id="op_3", display_name="Sara", treatment_ids=[1]),
    ]
    treatments = [
        Treatment(id=1, business_id=1, code="taglio_donna", name_it="Taglio Donna", duration_minutes=45, price=60, operator_ids=[1, 2, 3]),
        Treatment(id=2, business_id=1, code="balayage", name_it="Balayage", duration_minutes=150, price=130, operator_ids=[1, 2]),
    ]
    hours = [
        BusinessHours(business_id=1, day_of_week=0, is_open=False),  # Monday closed
        BusinessHours(business_id=1, day_of_week=1, is_open=True, open_time=time(9, 0), close_time=time(19, 0)),
    ]
    return Business(
        id=1, chatwoot_account_id=1, name="Test Salon",
        operators=ops, treatments=treatments, hours=hours,
    )


service = AvailabilityService()


class TestOperatorLookup:
    def test_get_operators_for_treatment(self):
        biz = _make_business()
        ops = service.get_operators_for_treatment(biz, "taglio_donna")
        assert len(ops) == 3
        names = [o.display_name for o in ops]
        assert "Giulia" in names
        assert "Sara" in names

    def test_get_operators_for_treatment_limited(self):
        biz = _make_business()
        ops = service.get_operators_for_treatment(biz, "balayage")
        assert len(ops) == 2
        names = [o.display_name for o in ops]
        assert "Sara" not in names


class TestSlotAvailability:
    @patch("app.services.availability.get_connection")
    def test_operator_free_when_no_appointments(self, mock_conn):
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = (0,)  # count = 0
        mock_conn.return_value.__enter__ = lambda s: MagicMock(cursor=lambda: mock_cur)
        mock_conn.return_value.__exit__ = lambda s, *a: None

        assert service.is_operator_free(1, 1, date(2026, 2, 20), time(9, 0), 45) is True

    @patch("app.services.availability.get_connection")
    def test_operator_busy_when_overlap(self, mock_conn):
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = (1,)  # count = 1 (overlap)
        mock_conn.return_value.__enter__ = lambda s: MagicMock(cursor=lambda: mock_cur)
        mock_conn.return_value.__exit__ = lambda s, *a: None

        assert service.is_operator_free(1, 1, date(2026, 2, 20), time(9, 0), 45) is False


class TestParallelBooking:
    @patch("app.services.availability.get_connection")
    def test_slot_available_when_one_operator_free(self, mock_conn):
        """If 3 operators do taglio and 2 are busy, slot still available (the 3rd is free)"""
        mock_cur = MagicMock()
        # is_operator_free called for each operator: Giulia=busy, Martina=busy, Sara=free
        mock_cur.fetchone.side_effect = [(1,), (1,), (0,)]
        mock_conn.return_value.__enter__ = lambda s: MagicMock(cursor=lambda: mock_cur)
        mock_conn.return_value.__exit__ = lambda s, *a: None

        biz = _make_business()
        result = service.check_slot(biz, "taglio_donna", date(2026, 2, 20), time(9, 0))
        assert result["available"] is True
        assert result["operator"] == "Sara"

    @patch("app.services.availability.get_connection")
    def test_slot_unavailable_when_all_operators_busy(self, mock_conn):
        """Slot unavailable only when ALL operators for that treatment are busy"""
        mock_cur = MagicMock()
        mock_cur.fetchone.side_effect = [(1,), (1,), (1,)]  # all 3 busy
        mock_conn.return_value.__enter__ = lambda s: MagicMock(cursor=lambda: mock_cur)
        mock_conn.return_value.__exit__ = lambda s, *a: None

        biz = _make_business()
        result = service.check_slot(biz, "taglio_donna", date(2026, 2, 20), time(9, 0))
        assert result["available"] is False


class TestOperatorPreference:
    @patch("app.services.availability.get_connection")
    def test_preferred_operator_free(self, mock_conn):
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = (0,)  # Giulia is free
        mock_conn.return_value.__enter__ = lambda s: MagicMock(cursor=lambda: mock_cur)
        mock_conn.return_value.__exit__ = lambda s, *a: None

        biz = _make_business()
        result = service.check_slot(biz, "taglio_donna", date(2026, 2, 20), time(9, 0), preferred_operator="Giulia")
        assert result["available"] is True
        assert result["operator"] == "Giulia"

    @patch("app.services.availability.get_connection")
    def test_preferred_operator_busy_suggests_their_alternatives_only(self, mock_conn):
        """When preferred operator is busy, suggest alternatives for THAT operator only, never auto-switch"""
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = (1,)  # Giulia is busy
        # For alternative slots query: return some available times
        mock_cur.fetchall.return_value = [("10:00",), ("11:00",), ("14:00",)]
        mock_conn.return_value.__enter__ = lambda s: MagicMock(cursor=lambda: mock_cur)
        mock_conn.return_value.__exit__ = lambda s, *a: None

        biz = _make_business()
        result = service.check_slot(biz, "taglio_donna", date(2026, 2, 20), time(9, 0), preferred_operator="Giulia")
        assert result["available"] is False
        assert result["operator"] == "Giulia"
        # Must not suggest other operators, only alternative times for Giulia
        assert "alternatives" in result


class TestBusinessHours:
    def test_closed_day_returns_closed(self):
        biz = _make_business()
        result = service.validate_business_hours(biz, date(2026, 2, 23), time(9, 0))  # Monday
        assert result["valid"] is False
        assert "closed" in result["reason"].lower()

    def test_outside_hours_returns_invalid(self):
        biz = _make_business()
        result = service.validate_business_hours(biz, date(2026, 2, 24), time(20, 0))  # Tuesday 8pm (closes 7pm)
        assert result["valid"] is False

    def test_within_hours_returns_valid(self):
        biz = _make_business()
        result = service.validate_business_hours(biz, date(2026, 2, 24), time(10, 0))  # Tuesday 10am
        assert result["valid"] is True
```

Run: `pytest lyo-bot/tests/test_availability.py -v`
Expected: FAIL

**Step 2: Implement availability.py**

```python
# lyo-bot/app/services/availability.py
import logging
from datetime import date, time, datetime, timedelta
from typing import Optional
from app.models.database import get_connection
from app.models.schemas import Business, Operator

logger = logging.getLogger(__name__)


class AvailabilityService:
    def get_operators_for_treatment(self, business: Business, treatment_code: str) -> list[Operator]:
        treatment = next((t for t in business.treatments if t.code == treatment_code), None)
        if not treatment:
            return []
        return [op for op in business.operators if op.id in treatment.operator_ids and op.is_active]

    def is_operator_free(
        self, business_id: int, operator_id: int, appt_date: date, appt_time: time, duration_minutes: int,
    ) -> bool:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """SELECT COUNT(*) FROM appointments
                   WHERE business_id = %s AND operator_id = %s
                     AND appointment_date = %s AND status = 'confirmed'
                     AND appointment_time < (%s::time + (%s || ' minutes')::interval)
                     AND (appointment_time + (duration_minutes || ' minutes')::interval) > %s::time""",
                (business_id, operator_id, appt_date, appt_time.strftime("%H:%M"),
                 str(duration_minutes), appt_time.strftime("%H:%M")),
            )
            count = cur.fetchone()[0]
            return count == 0

    def check_slot(
        self, business: Business, treatment_code: str, appt_date: date, appt_time: time,
        preferred_operator: str = None,
    ) -> dict:
        # Validate business hours first
        hours_check = self.validate_business_hours(business, appt_date, appt_time)
        if not hours_check["valid"]:
            return {"available": False, "reason": hours_check["reason"]}

        treatment = next((t for t in business.treatments if t.code == treatment_code), None)
        if not treatment:
            return {"available": False, "reason": f"Treatment '{treatment_code}' not found"}

        # Get candidate operators
        if preferred_operator:
            ops = [op for op in business.operators
                   if op.display_name.lower() == preferred_operator.lower()
                   and op.id in treatment.operator_ids and op.is_active]
            if not ops:
                return {"available": False, "reason": f"{preferred_operator} does not offer {treatment.name_it}"}
        else:
            ops = self.get_operators_for_treatment(business, treatment_code)

        if not ops:
            return {"available": False, "reason": "No operators available for this treatment"}

        # Check each operator's availability
        available_ops = []
        for op in ops:
            if self.is_operator_free(business.id, op.id, appt_date, appt_time, treatment.duration_minutes):
                available_ops.append(op)

        if available_ops:
            chosen = available_ops[0]
            return {
                "available": True,
                "operator": chosen.display_name,
                "operator_id": chosen.id,
                "treatment": treatment.name_it,
                "date": str(appt_date),
                "time": appt_time.strftime("%H:%M"),
            }

        # Not available — find alternatives
        if preferred_operator:
            # Only suggest alternatives for THIS operator
            alternatives = self._find_operator_alternatives(
                business, ops[0].id, treatment, appt_date,
            )
            return {
                "available": False,
                "operator": preferred_operator,
                "alternatives": alternatives,
                "reason": f"{preferred_operator} is busy at {appt_time.strftime('%H:%M')}",
            }
        else:
            # Suggest next available across any operator
            alternatives = self._find_any_operator_alternatives(
                business, treatment, appt_date, appt_time,
            )
            return {
                "available": False,
                "alternatives": alternatives,
                "reason": f"All operators busy at {appt_time.strftime('%H:%M')}",
            }

    def _find_operator_alternatives(
        self, business: Business, operator_id: int, treatment, appt_date: date,
    ) -> list[dict]:
        hours_rule = self._get_hours_for_date(business, appt_date)
        if not hours_rule or not hours_rule.is_open:
            return []

        slots = self._generate_time_slots(hours_rule.open_time, hours_rule.close_time, 30)
        alternatives = []
        for slot_time in slots:
            if self.is_operator_free(business.id, operator_id, appt_date, slot_time, treatment.duration_minutes):
                alternatives.append({"time": slot_time.strftime("%H:%M"), "date": str(appt_date)})
            if len(alternatives) >= 4:
                break
        return alternatives

    def _find_any_operator_alternatives(
        self, business: Business, treatment, appt_date: date, requested_time: time,
    ) -> list[dict]:
        hours_rule = self._get_hours_for_date(business, appt_date)
        if not hours_rule or not hours_rule.is_open:
            return []

        ops = self.get_operators_for_treatment(business, treatment.code)
        slots = self._generate_time_slots(hours_rule.open_time, hours_rule.close_time, 30)

        # Sort by proximity to requested time
        req_minutes = requested_time.hour * 60 + requested_time.minute
        slots.sort(key=lambda t: abs((t.hour * 60 + t.minute) - req_minutes))

        alternatives = []
        for slot_time in slots:
            for op in ops:
                if self.is_operator_free(business.id, op.id, appt_date, slot_time, treatment.duration_minutes):
                    alternatives.append({
                        "time": slot_time.strftime("%H:%M"),
                        "date": str(appt_date),
                        "operator": op.display_name,
                    })
                    break  # one operator per slot is enough
            if len(alternatives) >= 4:
                break
        return alternatives

    def get_available_slots(
        self, business: Business, treatment_code: str, appt_date: date,
        preferred_operator: str = None,
    ) -> list[dict]:
        hours_check = self.validate_business_hours(business, appt_date, time(0, 0))
        hours_rule = self._get_hours_for_date(business, appt_date)
        if not hours_rule or not hours_rule.is_open:
            return []

        treatment = next((t for t in business.treatments if t.code == treatment_code), None)
        if not treatment:
            return []

        if preferred_operator:
            ops = [op for op in business.operators
                   if op.display_name.lower() == preferred_operator.lower()
                   and op.id in treatment.operator_ids]
        else:
            ops = self.get_operators_for_treatment(business, treatment_code)

        slots = self._generate_time_slots(hours_rule.open_time, hours_rule.close_time, 30)
        available = []
        for slot_time in slots:
            for op in ops:
                if self.is_operator_free(business.id, op.id, appt_date, slot_time, treatment.duration_minutes):
                    available.append({
                        "time": slot_time.strftime("%H:%M"),
                        "operator": op.display_name,
                        "operator_id": op.id,
                    })
                    if not preferred_operator:
                        break  # one operator per slot unless asking about specific one
        return available

    def validate_business_hours(self, business: Business, appt_date: date, appt_time: time) -> dict:
        hours_rule = self._get_hours_for_date(business, appt_date)

        if not hours_rule:
            return {"valid": True}  # No rule = assume open

        if not hours_rule.is_open:
            day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            return {"valid": False, "reason": f"Closed on {day_names[hours_rule.day_of_week]}"}

        if hours_rule.open_time and appt_time < hours_rule.open_time:
            return {"valid": False, "reason": f"Opens at {hours_rule.open_time.strftime('%H:%M')}"}

        if hours_rule.close_time and appt_time >= hours_rule.close_time:
            return {"valid": False, "reason": f"Closes at {hours_rule.close_time.strftime('%H:%M')}"}

        return {"valid": True}

    def _get_hours_for_date(self, business: Business, appt_date: date):
        dow = appt_date.weekday()  # 0=Monday
        return next((h for h in business.hours if h.day_of_week == dow), None)

    @staticmethod
    def _generate_time_slots(open_time: time, close_time: time, interval_minutes: int) -> list[time]:
        slots = []
        current = datetime.combine(date.today(), open_time)
        end = datetime.combine(date.today(), close_time)
        while current < end:
            slots.append(current.time())
            current += timedelta(minutes=interval_minutes)
        return slots


availability_service = AvailabilityService()
```

**Step 3: Run tests**

```bash
pytest lyo-bot/tests/test_availability.py -v
```
Expected: PASS

**Step 4: Commit**

```bash
git add lyo-bot/app/services/availability.py lyo-bot/tests/test_availability.py
git commit -m "feat: add multi-operator availability service with parallel booking logic"
```

---

## Task 9: Booking Service (Multi-Operator)

**Files:**
- Create: `lyo-bot/app/services/booking.py`
- Create: `lyo-bot/app/services/calendar.py`
- Create: `lyo-bot/tests/test_booking.py`

**Step 1: Write failing test**

```python
# lyo-bot/tests/test_booking.py
import pytest
from unittest.mock import patch, MagicMock
from datetime import date, time
from app.services.booking import BookingService
from app.models.schemas import Business, Operator, Treatment, BusinessHours
from app.services.availability import AvailabilityService


def _make_business():
    ops = [
        Operator(id=1, business_id=1, technical_id="op_1", display_name="Giulia", treatment_ids=[1]),
        Operator(id=2, business_id=1, technical_id="op_2", display_name="Martina", treatment_ids=[1]),
    ]
    treatments = [
        Treatment(id=1, business_id=1, code="taglio_donna", name_it="Taglio Donna", duration_minutes=45, price=60, operator_ids=[1, 2]),
    ]
    hours = [
        BusinessHours(business_id=1, day_of_week=1, is_open=True, open_time=time(9, 0), close_time=time(19, 0)),
    ]
    return Business(id=1, chatwoot_account_id=1, name="Test Salon", operators=ops, treatments=treatments, hours=hours)


service = BookingService()


class TestCreateAppointment:
    @patch("app.services.booking.create_calendar_event", return_value="gcal_123")
    @patch("app.services.booking.get_connection")
    @patch.object(AvailabilityService, "check_slot")
    def test_create_with_auto_assign(self, mock_check, mock_conn, mock_cal):
        mock_check.return_value = {"available": True, "operator": "Giulia", "operator_id": 1}
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = (42,)  # appointment id
        mock_conn.return_value.__enter__ = lambda s: MagicMock(cursor=lambda: mock_cur)
        mock_conn.return_value.__exit__ = lambda s, *a: None

        biz = _make_business()
        result = service.create_appointment(
            business=biz, customer_phone="+393331234567", customer_name="Maria Rossi",
            treatment_code="taglio_donna", appt_date=date(2026, 2, 20), appt_time=time(9, 0),
        )
        assert result["success"] is True
        assert result["operator"] == "Giulia"
        assert result["appointment_id"] == 42

    @patch.object(AvailabilityService, "check_slot")
    def test_create_fails_when_slot_busy(self, mock_check):
        mock_check.return_value = {
            "available": False, "reason": "All operators busy",
            "alternatives": [{"time": "10:00", "operator": "Giulia"}],
        }
        biz = _make_business()
        result = service.create_appointment(
            business=biz, customer_phone="+393331234567", customer_name="Maria",
            treatment_code="taglio_donna", appt_date=date(2026, 2, 20), appt_time=time(9, 0),
        )
        assert result["success"] is False
        assert "alternatives" in result

    def test_create_fails_without_customer_name(self):
        biz = _make_business()
        result = service.create_appointment(
            business=biz, customer_phone="+393331234567", customer_name="",
            treatment_code="taglio_donna", appt_date=date(2026, 2, 20), appt_time=time(9, 0),
        )
        assert result["success"] is False
        assert result["error"] == "CUSTOMER_NAME_REQUIRED"
```

Run: `pytest lyo-bot/tests/test_booking.py -v`
Expected: FAIL

**Step 2: Implement calendar.py**

```python
# lyo-bot/app/services/calendar.py
import logging
from datetime import datetime
from pathlib import Path
from google.oauth2 import service_account
from googleapiclient.discovery import build
from app.models.schemas import Business

logger = logging.getLogger(__name__)

_calendar_services: dict[int, object] = {}  # business_id -> service


def get_calendar_service(business: Business):
    if business.id in _calendar_services:
        return _calendar_services[business.id]

    if not business.google_service_account_json:
        logger.warning(f"No Google service account for business {business.id}")
        return None

    try:
        import json
        import tempfile
        # Write service account JSON to temp file
        creds_data = json.loads(business.google_service_account_json)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(creds_data, f)
            creds_path = f.name

        creds = service_account.Credentials.from_service_account_file(
            creds_path, scopes=["https://www.googleapis.com/auth/calendar"],
        )
        svc = build("calendar", "v3", credentials=creds)
        _calendar_services[business.id] = svc
        logger.info(f"Google Calendar initialized for business {business.id}")
        return svc
    except Exception as e:
        logger.error(f"Failed to init Google Calendar for business {business.id}: {e}")
        return None


def create_calendar_event(
    business: Business, customer_name: str, treatment_name: str,
    date_str: str, time_str: str, duration_minutes: int,
    operator_name: str = None, customer_phone: str = None,
) -> str | None:
    svc = get_calendar_service(business)
    if not svc:
        return None

    try:
        import pytz
        tz = pytz.timezone(business.timezone)
        start = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        start = tz.localize(start)
        end = start + __import__("datetime").timedelta(minutes=duration_minutes)

        summary = f"{treatment_name} - {customer_name}"
        if operator_name:
            summary += f" (con {operator_name})"

        description = f"Cliente: {customer_name}"
        if customer_phone:
            description += f"\nTel: {customer_phone}"
        if operator_name:
            description += f"\nOperatore: {operator_name}"

        event = {
            "summary": summary,
            "description": description,
            "start": {"dateTime": start.isoformat(), "timeZone": business.timezone},
            "end": {"dateTime": end.isoformat(), "timeZone": business.timezone},
        }

        result = svc.events().insert(calendarId=business.google_calendar_id, body=event).execute()
        logger.info(f"Calendar event created: {result.get('id')}")
        return result.get("id")
    except Exception as e:
        logger.error(f"Failed to create calendar event: {e}")
        return None


def delete_calendar_event(business: Business, event_id: str) -> bool:
    svc = get_calendar_service(business)
    if not svc or not event_id:
        return False
    try:
        svc.events().delete(calendarId=business.google_calendar_id, eventId=event_id).execute()
        return True
    except Exception as e:
        logger.error(f"Failed to delete calendar event {event_id}: {e}")
        return False
```

**Step 3: Implement booking.py**

```python
# lyo-bot/app/services/booking.py
import logging
from datetime import date, time
from typing import Optional
from app.models.database import get_connection
from app.models.schemas import Business, Appointment
from app.services.availability import availability_service
from app.services.calendar import create_calendar_event, delete_calendar_event

logger = logging.getLogger(__name__)


class BookingService:
    def create_appointment(
        self, business: Business, customer_phone: str, customer_name: str,
        treatment_code: str, appt_date: date, appt_time: time,
        preferred_operator: str = None, platform: str = None,
        chatwoot_conversation_id: int = None,
    ) -> dict:
        # Validate name
        if not customer_name or not customer_name.strip():
            return {"success": False, "error": "CUSTOMER_NAME_REQUIRED"}

        # Check availability (handles operator preference logic)
        slot = availability_service.check_slot(
            business, treatment_code, appt_date, appt_time, preferred_operator,
        )

        if not slot.get("available"):
            return {
                "success": False,
                "error": "SLOT_NOT_AVAILABLE",
                "reason": slot.get("reason", ""),
                "alternatives": slot.get("alternatives", []),
            }

        operator_name = slot["operator"]
        operator_id = slot["operator_id"]

        # Get treatment info
        treatment = next((t for t in business.treatments if t.code == treatment_code), None)
        if not treatment:
            return {"success": False, "error": "INVALID_TREATMENT"}

        # Create Google Calendar event
        google_event_id = create_calendar_event(
            business=business, customer_name=customer_name,
            treatment_name=treatment.name_it,
            date_str=str(appt_date), time_str=appt_time.strftime("%H:%M"),
            duration_minutes=treatment.duration_minutes,
            operator_name=operator_name, customer_phone=customer_phone,
        )

        # Insert into DB
        try:
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    """INSERT INTO appointments
                       (business_id, operator_id, customer_phone, customer_name,
                        treatment_id, treatment_code, appointment_date, appointment_time,
                        duration_minutes, price, status, google_event_id, platform,
                        chatwoot_conversation_id)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'confirmed', %s, %s, %s)
                       RETURNING id""",
                    (business.id, operator_id, customer_phone, customer_name.strip(),
                     treatment.id, treatment_code, appt_date, appt_time,
                     treatment.duration_minutes, float(treatment.price) if treatment.price else None,
                     google_event_id, platform, chatwoot_conversation_id),
                )
                appointment_id = cur.fetchone()[0]

            logger.info(f"Appointment #{appointment_id} created: {customer_name} with {operator_name}")
            return {
                "success": True,
                "appointment_id": appointment_id,
                "customer_name": customer_name.strip(),
                "operator": operator_name,
                "treatment": treatment.name_it,
                "date": str(appt_date),
                "time": appt_time.strftime("%H:%M"),
                "duration": treatment.duration_minutes,
                "price": str(treatment.price) if treatment.price else None,
                "calendar_synced": bool(google_event_id),
            }
        except Exception as e:
            logger.error(f"Booking error: {e}")
            return {"success": False, "error": "BOOKING_ERROR", "details": str(e)}

    def cancel_appointment(
        self, business: Business, customer_name: str, appt_date: date, appt_time: time,
    ) -> dict:
        try:
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    """UPDATE appointments SET status = 'cancelled', updated_at = NOW()
                       WHERE business_id = %s AND LOWER(customer_name) = LOWER(%s)
                         AND appointment_date = %s AND appointment_time = %s
                         AND status = 'confirmed'
                       RETURNING id, google_event_id, treatment_code""",
                    (business.id, customer_name.strip(), appt_date, appt_time),
                )
                row = cur.fetchone()
                if not row:
                    return {"success": False, "error": "APPOINTMENT_NOT_FOUND"}

                # Delete calendar event
                if row[1]:
                    delete_calendar_event(business, row[1])

                return {"success": True, "appointment_id": row[0], "treatment": row[2]}
        except Exception as e:
            logger.error(f"Cancel error: {e}")
            return {"success": False, "error": str(e)}

    def modify_appointment(
        self, business: Business, customer_name: str,
        current_date: date, current_time: time,
        new_date: date = None, new_time: time = None,
        new_treatment: str = None, new_operator: str = None,
    ) -> dict:
        # Find existing appointment
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """SELECT id, operator_id, treatment_code, google_event_id
                   FROM appointments
                   WHERE business_id = %s AND LOWER(customer_name) = LOWER(%s)
                     AND appointment_date = %s AND appointment_time = %s
                     AND status = 'confirmed'""",
                (business.id, customer_name.strip(), current_date, current_time),
            )
            row = cur.fetchone()
            if not row:
                return {"success": False, "error": "APPOINTMENT_NOT_FOUND"}

        old_id, old_operator_id, old_treatment_code, old_event_id = row
        target_date = new_date or current_date
        target_time = new_time or current_time
        target_treatment = new_treatment or old_treatment_code

        # Cancel old
        cancel_result = self.cancel_appointment(business, customer_name, current_date, current_time)
        if not cancel_result["success"]:
            return cancel_result

        # Create new
        create_result = self.create_appointment(
            business=business, customer_phone="",  # Will be looked up
            customer_name=customer_name, treatment_code=target_treatment,
            appt_date=target_date, appt_time=target_time,
            preferred_operator=new_operator,
        )
        if not create_result["success"]:
            # TODO: Roll back cancellation if new booking fails
            return create_result

        create_result["modified_from"] = {
            "date": str(current_date), "time": current_time.strftime("%H:%M"),
        }
        return create_result

    def get_customer_appointments(self, business: Business, customer_phone: str) -> list[dict]:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """SELECT a.id, a.customer_name, a.treatment_code, t.name_it,
                          a.appointment_date, a.appointment_time, a.duration_minutes,
                          a.price, a.status, o.display_name
                   FROM appointments a
                   LEFT JOIN treatments t ON a.treatment_id = t.id
                   LEFT JOIN operators o ON a.operator_id = o.id
                   WHERE a.business_id = %s AND a.customer_phone = %s
                     AND a.status = 'confirmed'
                     AND a.appointment_date >= CURRENT_DATE
                   ORDER BY a.appointment_date, a.appointment_time""",
                (business.id, customer_phone),
            )
            results = []
            for r in cur.fetchall():
                results.append({
                    "id": r[0], "customer_name": r[1], "treatment_code": r[2],
                    "treatment_name": r[3], "date": str(r[4]),
                    "time": str(r[5])[:5], "duration": r[6],
                    "price": str(r[7]) if r[7] else None, "status": r[8],
                    "operator": r[9],
                })
            return results


booking_service = BookingService()
```

**Step 4: Run tests**

```bash
pytest lyo-bot/tests/test_booking.py -v
```
Expected: PASS

**Step 5: Commit**

```bash
git add lyo-bot/app/services/booking.py lyo-bot/app/services/calendar.py lyo-bot/tests/test_booking.py
git commit -m "feat: add multi-operator booking service with calendar integration"
```

---

## Task 10: Dynamic OpenAI Tool Definitions

**Files:**
- Create: `lyo-bot/app/tools/definitions.py`
- Create: `lyo-bot/tests/test_tools.py`

**Step 1: Write failing test**

```python
# lyo-bot/tests/test_tools.py
from datetime import time
from app.tools.definitions import build_tools_for_business
from app.models.schemas import Business, Operator, Treatment, BusinessHours


def _make_business():
    treatments = [
        Treatment(id=1, business_id=1, code="taglio_donna", name_it="Taglio Donna", duration_minutes=45, price=60, operator_ids=[1, 2]),
        Treatment(id=2, business_id=1, code="piega", name_it="Piega", duration_minutes=30, price=30, operator_ids=[1]),
    ]
    operators = [
        Operator(id=1, business_id=1, technical_id="op_1", display_name="Giulia", treatment_ids=[1, 2]),
        Operator(id=2, business_id=1, technical_id="op_2", display_name="Martina", treatment_ids=[1]),
    ]
    return Business(id=1, chatwoot_account_id=1, name="Test", treatments=treatments, operators=operators)


def test_tools_have_dynamic_treatment_enum():
    biz = _make_business()
    tools = build_tools_for_business(biz)
    create_tool = next(t for t in tools if t["function"]["name"] == "create_appointment")
    treatment_enum = create_tool["function"]["parameters"]["properties"]["treatment_code"]["enum"]
    assert "taglio_donna" in treatment_enum
    assert "piega" in treatment_enum


def test_tools_include_operator_names():
    biz = _make_business()
    tools = build_tools_for_business(biz)
    create_tool = next(t for t in tools if t["function"]["name"] == "create_appointment")
    op_enum = create_tool["function"]["parameters"]["properties"]["operator_name"]["enum"]
    assert "Giulia" in op_enum
    assert "Martina" in op_enum


def test_tools_count():
    biz = _make_business()
    tools = build_tools_for_business(biz)
    names = [t["function"]["name"] for t in tools]
    assert "create_appointment" in names
    assert "check_availability" in names
    assert "get_available_slots" in names
    assert "get_operators_for_treatment" in names
    assert "get_customer_appointments" in names
    assert "cancel_appointment" in names
    assert "modify_appointment" in names
    assert "confirm_reminder" in names
    assert "escalate_to_human" in names
```

**Step 2: Implement definitions.py**

```python
# lyo-bot/app/tools/definitions.py
from app.models.schemas import Business


def build_tools_for_business(business: Business) -> list[dict]:
    treatment_codes = [t.code for t in business.treatments if t.is_active]
    operator_names = [o.display_name for o in business.operators if o.is_active]

    return [
        {
            "type": "function",
            "function": {
                "name": "create_appointment",
                "description": "Create a booking appointment. Call IMMEDIATELY when customer confirms (says yes/ok/confirm). REQUIRES customer name — if unknown, ask first.",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "customer_name": {"type": "string", "description": "Customer's full name (REQUIRED before booking)"},
                        "treatment_code": {"type": "string", "description": "Treatment code", "enum": treatment_codes},
                        "date": {"type": "string", "description": "Date in YYYY-MM-DD format"},
                        "time": {"type": "string", "description": "Time in HH:MM 24h format"},
                        "operator_name": {"type": ["string", "null"], "description": "Preferred operator name, or null for auto-assign", "enum": operator_names + [None]},
                    },
                    "required": ["customer_name", "treatment_code", "date", "time", "operator_name"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "check_availability",
                "description": "Check if a specific date/time slot is available for a treatment.",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "treatment_code": {"type": "string", "enum": treatment_codes},
                        "date": {"type": "string", "description": "Date YYYY-MM-DD"},
                        "time": {"type": "string", "description": "Time HH:MM"},
                        "operator_name": {"type": ["string", "null"], "description": "Check specific operator only", "enum": operator_names + [None]},
                    },
                    "required": ["treatment_code", "date", "time", "operator_name"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_available_slots",
                "description": "Show all available time slots for a treatment on a specific date.",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "treatment_code": {"type": "string", "enum": treatment_codes},
                        "date": {"type": "string", "description": "Date YYYY-MM-DD"},
                        "operator_name": {"type": ["string", "null"], "description": "Filter by operator", "enum": operator_names + [None]},
                    },
                    "required": ["treatment_code", "date", "operator_name"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_operators_for_treatment",
                "description": "List available operators for a specific treatment. Call when customer asks 'who is available?' or 'chi e disponibile?'",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "treatment_code": {"type": "string", "enum": treatment_codes},
                    },
                    "required": ["treatment_code"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_customer_appointments",
                "description": "Get customer's upcoming appointments. Call FIRST before modify or cancel.",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "cancel_appointment",
                "description": "Cancel an appointment by name, date, and time.",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "customer_name": {"type": "string"},
                        "date": {"type": "string", "description": "Date YYYY-MM-DD"},
                        "time": {"type": "string", "description": "Time HH:MM"},
                    },
                    "required": ["customer_name", "date", "time"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "modify_appointment",
                "description": "Modify/reschedule an existing appointment.",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "customer_name": {"type": "string"},
                        "current_date": {"type": "string", "description": "Current date YYYY-MM-DD"},
                        "current_time": {"type": "string", "description": "Current time HH:MM"},
                        "new_date": {"type": ["string", "null"], "description": "New date or null"},
                        "new_time": {"type": ["string", "null"], "description": "New time or null"},
                        "new_treatment": {"type": ["string", "null"], "enum": treatment_codes + [None]},
                        "new_operator": {"type": ["string", "null"], "enum": operator_names + [None]},
                    },
                    "required": ["customer_name", "current_date", "current_time", "new_date", "new_time", "new_treatment", "new_operator"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "confirm_reminder",
                "description": "Confirm customer's upcoming appointment. Call when customer says 'confermo', 'ci saro', 'vengo', etc.",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "escalate_to_human",
                "description": "Escalate to human agent. Call when customer has complaint, is angry, or asks to speak with a person.",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reason": {"type": "string", "description": "Brief reason for escalation"},
                    },
                    "required": ["reason"],
                    "additionalProperties": False,
                },
            },
        },
    ]
```

**Step 3: Run tests**

```bash
pytest lyo-bot/tests/test_tools.py -v
```
Expected: PASS

**Step 4: Commit**

```bash
git add lyo-bot/app/tools/definitions.py lyo-bot/tests/test_tools.py
git commit -m "feat: add dynamic OpenAI tool definitions from business config"
```

---

## Task 11: AI Conversation Handler

**Files:**
- Create: `lyo-bot/app/services/ai.py`
- Create: `lyo-bot/app/utils/time_helpers.py`
- Create: `lyo-bot/tests/test_ai.py`

**Step 1: Write time_helpers.py (ported from salon_bot_ec2_latest.py:283-332)**

```python
# lyo-bot/app/utils/time_helpers.py
from datetime import datetime, timedelta
import pytz
from app.models.schemas import Business


def get_date_context(business: Business) -> dict:
    tz = pytz.timezone(business.timezone)
    now = datetime.now(tz)
    return {
        "today": now.strftime("%Y-%m-%d"),
        "tomorrow": (now + timedelta(days=1)).strftime("%Y-%m-%d"),
        "year": now.year,
        "display": now.strftime("%A, %d %B %Y"),
        "calendar": generate_date_calendar(business),
    }


def generate_date_calendar(business: Business) -> str:
    tz = pytz.timezone(business.timezone)
    today = datetime.now(tz)

    italian_days = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"]
    italian_months = ["", "Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno",
                      "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre"]

    # Build closed-days set from business hours
    closed_days = set()
    for h in business.hours:
        if not h.is_open:
            closed_days.add(h.day_of_week)

    lines = []
    for i in range(14):
        day = today + timedelta(days=i)
        day_name = italian_days[day.weekday()]
        month_name = italian_months[day.month]
        date_str = day.strftime("%Y-%m-%d")

        if day.weekday() in closed_days:
            status = f"CHIUSO ({day_name})"
        else:
            status = "APERTO"

        label = "(OGGI)" if i == 0 else "(DOMANI)" if i == 1 else ""
        lines.append(f"   - {day_name} {day.day} {month_name} {day.year} ({date_str}) → {status} {label}".strip())

    return "\n".join(lines)
```

**Step 2: Write failing test**

```python
# lyo-bot/tests/test_ai.py
import pytest
import json
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import time
from app.services.ai import AIService, build_system_prompt
from app.models.schemas import Business, Operator, Treatment, BusinessHours


def _make_business():
    return Business(
        id=1, chatwoot_account_id=1, name="Aura Hair Studio",
        bot_name="Simone", language="it", timezone="Europe/Rome",
        address="Via Roma 123, Milano", phone="+39 02 1234567",
        operators=[
            Operator(id=1, business_id=1, technical_id="op_1", display_name="Giulia", treatment_ids=[1]),
        ],
        treatments=[
            Treatment(id=1, business_id=1, code="taglio_donna", name_it="Taglio Donna",
                      duration_minutes=45, price=60, operator_ids=[1]),
        ],
        hours=[
            BusinessHours(business_id=1, day_of_week=1, is_open=True,
                          open_time=time(9, 0), close_time=time(19, 0)),
        ],
    )


class TestSystemPrompt:
    def test_prompt_includes_bot_name(self):
        biz = _make_business()
        prompt = build_system_prompt(biz)
        assert "Simone" in prompt

    def test_prompt_includes_treatments(self):
        biz = _make_business()
        prompt = build_system_prompt(biz)
        assert "Taglio Donna" in prompt

    def test_prompt_includes_operators(self):
        biz = _make_business()
        prompt = build_system_prompt(biz)
        assert "Giulia" in prompt

    def test_prompt_includes_address(self):
        biz = _make_business()
        prompt = build_system_prompt(biz)
        assert "Via Roma 123" in prompt


class TestAIService:
    @pytest.mark.asyncio
    @patch("app.services.ai.openai_client")
    async def test_process_returns_response(self, mock_openai):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Ciao! Come posso aiutarti?"
        mock_response.choices[0].message.tool_calls = None
        mock_openai.chat.completions.create.return_value = mock_response

        service = AIService()
        biz = _make_business()
        result = await service.process_message(
            business=biz, customer_phone="+393331234567",
            message="Ciao", conversation_history=[],
        )
        assert "Ciao" in result or "aiutarti" in result.lower() or len(result) > 0

    @pytest.mark.asyncio
    @patch("app.services.ai.openai_client")
    async def test_handles_tool_call(self, mock_openai):
        # First call returns tool_call, second returns final response
        tool_call = MagicMock()
        tool_call.id = "call_123"
        tool_call.function.name = "get_customer_appointments"
        tool_call.function.arguments = "{}"

        first_response = MagicMock()
        first_response.choices = [MagicMock()]
        first_response.choices[0].message.content = None
        first_response.choices[0].message.tool_calls = [tool_call]

        second_response = MagicMock()
        second_response.choices = [MagicMock()]
        second_response.choices[0].message.content = "Non hai appuntamenti."
        second_response.choices[0].message.tool_calls = None

        mock_openai.chat.completions.create.side_effect = [first_response, second_response]

        service = AIService()
        biz = _make_business()
        with patch.object(service, "_execute_tool", return_value=json.dumps([])):
            result = await service.process_message(
                business=biz, customer_phone="+393331234567",
                message="Ho appuntamenti?", conversation_history=[],
            )
        assert len(result) > 0
```

Run: `pytest lyo-bot/tests/test_ai.py -v`
Expected: FAIL

**Step 3: Implement ai.py**

```python
# lyo-bot/app/services/ai.py
import json
import logging
import asyncio
from typing import Optional
import openai
from app.config import settings
from app.models.schemas import Business
from app.tools.definitions import build_tools_for_business
from app.utils.time_helpers import get_date_context
from app.services.availability import availability_service
from app.services.booking import booking_service
from datetime import date, time

logger = logging.getLogger(__name__)

openai_client = openai.OpenAI(api_key=settings.openai_api_key)


def build_system_prompt(business: Business) -> str:
    dates = get_date_context(business)

    # Build services list
    services_text = ""
    for t in business.treatments:
        if t.is_active:
            ops = [o.display_name for o in business.operators if o.id in t.operator_ids]
            services_text += f"   - {t.code}: {t.name_it} ({t.duration_minutes} min, €{t.price}) — Operatori: {', '.join(ops)}\n"

    # Build operators list
    operators_text = ""
    for o in business.operators:
        if o.is_active:
            treats = [t.name_it for t in business.treatments if t.id in o.treatment_ids]
            operators_text += f"   - {o.display_name}: {', '.join(treats)}\n"

    # Build hours
    day_names = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"]
    hours_text = ""
    for h in sorted(business.hours, key=lambda x: x.day_of_week):
        day = day_names[h.day_of_week]
        if h.is_open and h.open_time and h.close_time:
            hours_text += f"   - {day}: {h.open_time.strftime('%H:%M')}-{h.close_time.strftime('%H:%M')}\n"
        else:
            hours_text += f"   - {day}: CHIUSO\n"

    lang = business.language
    if lang == "it":
        lang_instruction = """LANGUAGE: ITALIAN. Always reply in Italian unless customer writes full English sentences.
Single English words (ok, hi, yes) → still reply in Italian.
Tool results are in English — YOU translate to Italian for the customer."""
    else:
        lang_instruction = f"LANGUAGE: {lang.upper()}. Reply in the customer's language."

    return f"""You are {business.bot_name}, an employee at {business.name}.
You present yourself as a real person (not a bot). You are helpful, professional, and friendly.

📆 TODAY: {dates['display']} (Year: {dates['year']})

🌐 {lang_instruction}

👋 "CIAO" CONTEXT RULE:
"Ciao" means both hello and goodbye. Use context:
- After booking confirmation or "grazie" → treat as goodbye, reply briefly
- First message or with request → treat as greeting, offer help

📍 SALON INFO:
   Name: {business.name}
   Address: {business.address or 'N/A'}
   Phone: {business.phone or 'N/A'}

🕐 HOURS:
{hours_text}

💇 SERVICES:
{services_text}

👩‍💼 OPERATORS:
{operators_text}

📋 OPERATOR RULES:
1. If customer has NO preference → auto-assign first available operator
2. If customer asks "chi è disponibile?" → list available operators for that treatment
3. If customer requests specific operator → check ONLY their calendar
   - If busy, suggest alternative times for THAT operator only
   - NEVER auto-switch to another operator
4. ALWAYS show operator name in availability response:
   "Alle 9:00 è disponibile con Giulia. Va bene?"

📛 NAME REQUIREMENT:
BEFORE booking, you MUST know the customer's name. If unknown, ask:
"Per procedere con la prenotazione, mi servirebbe il tuo nome e cognome."
NEVER book without a name.

📅 CALENDAR (next 14 days):
{dates['calendar']}

RULES:
- Be concise. No walls of text.
- Confirm details before booking: treatment, date, time, operator
- After booking, summarize: "Perfetto! Ho prenotato [treatment] per [date] alle [time] con [operator]."
- For complaints/frustration/request for human → call escalate_to_human immediately
"""


class AIService:
    MAX_TOOL_ROUNDS = 5

    async def process_message(
        self, business: Business, customer_phone: str, message: str,
        conversation_history: list[dict], customer_name: str = None,
    ) -> str:
        system_prompt = build_system_prompt(business)
        tools = build_tools_for_business(business)

        # Build messages
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(conversation_history)
        messages.append({"role": "user", "content": message})

        # Function calling loop
        for _ in range(self.MAX_TOOL_ROUNDS):
            response = await asyncio.to_thread(
                openai_client.chat.completions.create,
                model=settings.openai_model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=0,
            )

            choice = response.choices[0]

            if not choice.message.tool_calls:
                return choice.message.content or ""

            # Process tool calls
            messages.append({
                "role": "assistant",
                "content": choice.message.content,
                "tool_calls": [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in choice.message.tool_calls
                ],
            })

            for tc in choice.message.tool_calls:
                result = self._execute_tool(
                    business, customer_phone, customer_name,
                    tc.function.name, tc.function.arguments,
                )
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })

        return "Mi scusi, c'è stato un problema. Può riprovare?"

    def _execute_tool(
        self, business: Business, customer_phone: str, customer_name: str,
        tool_name: str, arguments_json: str,
    ) -> str:
        try:
            args = json.loads(arguments_json)
        except json.JSONDecodeError:
            return json.dumps({"error": "Invalid arguments"})

        try:
            if tool_name == "create_appointment":
                result = booking_service.create_appointment(
                    business=business,
                    customer_phone=customer_phone,
                    customer_name=args["customer_name"],
                    treatment_code=args["treatment_code"],
                    appt_date=date.fromisoformat(args["date"]),
                    appt_time=time.fromisoformat(args["time"]),
                    preferred_operator=args.get("operator_name"),
                )
            elif tool_name == "check_availability":
                result = availability_service.check_slot(
                    business, args["treatment_code"],
                    date.fromisoformat(args["date"]),
                    time.fromisoformat(args["time"]),
                    args.get("operator_name"),
                )
            elif tool_name == "get_available_slots":
                result = availability_service.get_available_slots(
                    business, args["treatment_code"],
                    date.fromisoformat(args["date"]),
                    args.get("operator_name"),
                )
            elif tool_name == "get_operators_for_treatment":
                ops = availability_service.get_operators_for_treatment(business, args["treatment_code"])
                result = [{"name": o.display_name, "id": o.id} for o in ops]
            elif tool_name == "get_customer_appointments":
                result = booking_service.get_customer_appointments(business, customer_phone)
            elif tool_name == "cancel_appointment":
                result = booking_service.cancel_appointment(
                    business, args["customer_name"],
                    date.fromisoformat(args["date"]),
                    time.fromisoformat(args["time"]),
                )
            elif tool_name == "modify_appointment":
                result = booking_service.modify_appointment(
                    business, args["customer_name"],
                    date.fromisoformat(args["current_date"]),
                    time.fromisoformat(args["current_time"]),
                    date.fromisoformat(args["new_date"]) if args.get("new_date") else None,
                    time.fromisoformat(args["new_time"]) if args.get("new_time") else None,
                    args.get("new_treatment"),
                    args.get("new_operator"),
                )
            elif tool_name == "confirm_reminder":
                result = self._confirm_reminder(business, customer_phone)
            elif tool_name == "escalate_to_human":
                result = {"escalated": True, "reason": args.get("reason", "")}
            else:
                result = {"error": f"Unknown tool: {tool_name}"}

            return json.dumps(result, default=str)
        except Exception as e:
            logger.error(f"Tool execution error ({tool_name}): {e}")
            return json.dumps({"error": str(e)})

    def _confirm_reminder(self, business: Business, customer_phone: str) -> dict:
        from app.models.database import get_connection
        try:
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    """UPDATE appointments SET reminder_confirmed = true, reminder_confirmed_at = NOW()
                       WHERE business_id = %s AND customer_phone = %s
                         AND status = 'confirmed' AND appointment_date >= CURRENT_DATE
                         AND reminder_confirmed = false
                       RETURNING id, appointment_date, appointment_time""",
                    (business.id, customer_phone),
                )
                rows = cur.fetchall()
                if rows:
                    return {"confirmed": True, "appointments": [
                        {"id": r[0], "date": str(r[1]), "time": str(r[2])[:5]} for r in rows
                    ]}
                return {"confirmed": False, "message": "No unconfirmed appointments found"}
        except Exception as e:
            return {"error": str(e)}


ai_service = AIService()
```

**Step 4: Run tests**

```bash
pytest lyo-bot/tests/test_ai.py -v
```
Expected: PASS

**Step 5: Commit**

```bash
git add lyo-bot/app/services/ai.py lyo-bot/app/utils/time_helpers.py lyo-bot/tests/test_ai.py
git commit -m "feat: add AI conversation handler with dynamic system prompt and tool dispatch"
```

---

## Task 12: Message Processing Pipeline

**Files:**
- Modify: `lyo-bot/app/main.py`
- Create: `lyo-bot/app/services/pipeline.py`
- Create: `lyo-bot/tests/test_pipeline.py`

**This task wires everything together: webhook → tenant → customer → AI → Chatwoot reply.**

**Step 1: Write failing test**

```python
# lyo-bot/tests/test_pipeline.py
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from app.services.pipeline import MessagePipeline
from app.models.schemas import Business, Customer, WebhookPayload


def _make_payload(content="Ciao", account_id=1, conv_id=456):
    return WebhookPayload(
        event="message_created", id=123, content=content,
        message_type="incoming", content_type="text",
        account={"id": account_id, "name": "Test"},
        conversation={"id": conv_id, "inbox_id": 789},
        inbox={"id": 789, "name": "WA"},
        sender={"id": 101, "name": "Maria", "phone_number": "+393331234567", "type": "contact"},
    )


class TestPipeline:
    @pytest.mark.asyncio
    @patch("app.services.pipeline.chatwoot_client")
    @patch("app.services.pipeline.ai_service")
    @patch("app.services.pipeline.customer_service")
    @patch("app.services.pipeline.tenant_service")
    async def test_full_pipeline(self, mock_tenant, mock_customer, mock_ai, mock_chatwoot):
        biz = Business(id=1, chatwoot_account_id=1, name="Test", bot_name="Simone")
        mock_tenant.get_business.return_value = biz
        mock_customer.get_or_create.return_value = Customer(
            id=1, business_id=1, phone="+393331234567", first_name="Maria",
        )
        mock_ai.process_message = AsyncMock(return_value="Ciao Maria! Come posso aiutarti?")
        mock_chatwoot.send_message = AsyncMock(return_value={"id": 1})

        pipeline = MessagePipeline()
        await pipeline.process(_make_payload())

        mock_tenant.get_business.assert_called_once_with(1)
        mock_chatwoot.send_message.assert_called_once()
        call_args = mock_chatwoot.send_message.call_args
        assert call_args[1]["account_id"] == 1
        assert call_args[1]["conversation_id"] == 456

    @pytest.mark.asyncio
    @patch("app.services.pipeline.chatwoot_client")
    @patch("app.services.pipeline.tenant_service")
    async def test_unknown_tenant_ignored(self, mock_tenant, mock_chatwoot):
        mock_tenant.get_business.return_value = None
        pipeline = MessagePipeline()
        await pipeline.process(_make_payload(account_id=999))
        mock_chatwoot.send_message.assert_not_called()
```

Run: `pytest lyo-bot/tests/test_pipeline.py -v`
Expected: FAIL

**Step 2: Implement pipeline.py**

```python
# lyo-bot/app/services/pipeline.py
import asyncio
import logging
import json
from typing import Dict, List
from app.models.database import get_connection
from app.models.schemas import WebhookPayload
from app.services.tenant import tenant_service
from app.services.customer import customer_service
from app.services.chatwoot import chatwoot_client
from app.services.ai import ai_service
from app.config import settings

logger = logging.getLogger(__name__)


class MessagePipeline:
    def __init__(self):
        self._buffers: Dict[str, List[dict]] = {}  # key -> messages
        self._timers: Dict[str, asyncio.Task] = {}  # key -> timer task

    async def process(self, payload: WebhookPayload):
        account_id = payload.account.id
        conversation_id = payload.conversation.id

        # 1. Resolve tenant
        business = tenant_service.get_business(account_id)
        if not business:
            logger.warning(f"No business for account {account_id}")
            return

        # 2. Extract sender info
        phone = payload.sender.phone_number if payload.sender else None
        sender_name = payload.sender.name if payload.sender else None

        if not phone:
            logger.warning(f"No phone in payload for conv {conversation_id}")
            return

        # 3. Buffer messages (combine rapid messages)
        buffer_key = f"{business.id}:{phone}"
        await self._buffer_message(
            buffer_key, payload.content, business, phone,
            sender_name, account_id, conversation_id,
        )

    async def _buffer_message(
        self, key: str, content: str, business, phone: str,
        sender_name: str, account_id: int, conversation_id: int,
    ):
        if key not in self._buffers:
            self._buffers[key] = []

        self._buffers[key].append({
            "content": content,
            "sender_name": sender_name,
        })

        # Cancel existing timer
        if key in self._timers:
            self._timers[key].cancel()

        # Start new timer
        delay = settings.message_batch_delay_seconds

        async def timer():
            await asyncio.sleep(delay)
            await self._process_buffered(key, business, phone, account_id, conversation_id)

        self._timers[key] = asyncio.create_task(timer())

    async def _process_buffered(
        self, key: str, business, phone: str,
        account_id: int, conversation_id: int,
    ):
        messages = self._buffers.pop(key, [])
        self._timers.pop(key, None)

        if not messages:
            return

        # Combine messages
        combined = "\n".join(m["content"] for m in messages if m["content"])
        sender_name = messages[0].get("sender_name", "")

        try:
            # 3. Get/create customer
            customer = await asyncio.to_thread(
                customer_service.get_or_create, business.id, phone,
            )

            # 4. Load conversation history
            history = await asyncio.to_thread(
                self._load_conversation_history, business.id, phone,
            )

            # 5. Process with AI
            response = await ai_service.process_message(
                business=business,
                customer_phone=phone,
                message=combined,
                conversation_history=history,
                customer_name=customer.full_name if customer else None,
            )

            # 6. Save conversation
            await asyncio.to_thread(
                self._save_conversation, business.id, phone,
                conversation_id, combined, response, history,
            )

            # 7. Send reply via Chatwoot
            await chatwoot_client.send_message(
                account_id=account_id,
                conversation_id=conversation_id,
                content=response,
            )

            logger.info(f"Reply sent to {phone} in account {account_id}: {response[:80]}...")

        except Exception as e:
            logger.error(f"Pipeline error for {phone}: {e}")

    def _load_conversation_history(self, business_id: int, phone: str) -> list[dict]:
        try:
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT messages FROM conversations WHERE business_id = %s AND customer_phone = %s",
                    (business_id, phone),
                )
                row = cur.fetchone()
                if row and row[0]:
                    msgs = row[0] if isinstance(row[0], list) else json.loads(row[0])
                    # Keep last 20 messages for context
                    return msgs[-20:]
                return []
        except Exception as e:
            logger.error(f"Error loading history: {e}")
            return []

    def _save_conversation(
        self, business_id: int, phone: str, conversation_id: int,
        user_message: str, bot_response: str, history: list,
    ):
        try:
            new_history = history + [
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": bot_response},
            ]
            # Keep last 40 messages
            new_history = new_history[-40:]

            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    """INSERT INTO conversations (business_id, customer_phone, chatwoot_conversation_id, messages)
                       VALUES (%s, %s, %s, %s::jsonb)
                       ON CONFLICT (business_id, customer_phone) DO UPDATE SET
                           messages = %s::jsonb,
                           chatwoot_conversation_id = COALESCE(EXCLUDED.chatwoot_conversation_id, conversations.chatwoot_conversation_id),
                           updated_at = NOW()""",
                    (business_id, phone, conversation_id, json.dumps(new_history),
                     json.dumps(new_history)),
                )
        except Exception as e:
            logger.error(f"Error saving conversation: {e}")


pipeline = MessagePipeline()
```

**Step 3: Update main.py to use pipeline**

Replace the `_process_message` function in `lyo-bot/app/main.py`:

```python
# In lyo-bot/app/main.py, update the _process_message function:

from app.services.pipeline import pipeline

async def _process_message(payload: WebhookPayload):
    """Background task: full message processing pipeline."""
    try:
        await pipeline.process(payload)
    except Exception as e:
        logger.error(f"Error processing message: {e}")
```

**Step 4: Run tests**

```bash
pytest lyo-bot/tests/test_pipeline.py -v
```
Expected: PASS

**Step 5: Commit**

```bash
git add lyo-bot/app/services/pipeline.py lyo-bot/app/main.py lyo-bot/tests/test_pipeline.py
git commit -m "feat: wire message processing pipeline (webhook → tenant → AI → Chatwoot)"
```

---

## Task 13: Reminders System

**Files:**
- Create: `lyo-bot/app/services/reminders.py`
- Create: `lyo-bot/tests/test_reminders.py`

**Step 1: Write failing test**

```python
# lyo-bot/tests/test_reminders.py
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from app.services.reminders import ReminderService


class TestReminderService:
    @patch("app.services.reminders.chatwoot_client")
    @patch("app.services.reminders.get_connection")
    @pytest.mark.asyncio
    async def test_send_reminders_for_tomorrow(self, mock_conn, mock_chatwoot):
        mock_cur = MagicMock()
        # Tomorrow's appointments with chatwoot conversation IDs
        mock_cur.fetchall.return_value = [
            (1, 1, "+393331234567", "Maria", "taglio_donna", "Taglio Donna",
             "2026-02-19", "09:00", "Giulia", 456, 1),
        ]
        mock_conn.return_value.__enter__ = lambda s: MagicMock(cursor=lambda: mock_cur)
        mock_conn.return_value.__exit__ = lambda s, *a: None
        mock_chatwoot.send_message = AsyncMock(return_value={"id": 1})

        service = ReminderService()
        await service.send_daily_reminders()

        mock_chatwoot.send_message.assert_called_once()
```

**Step 2: Implement reminders.py**

```python
# lyo-bot/app/services/reminders.py
import logging
from datetime import datetime, timedelta
import pytz
from app.models.database import get_connection
from app.services.chatwoot import chatwoot_client
from app.services.tenant import tenant_service

logger = logging.getLogger(__name__)


class ReminderService:
    async def send_daily_reminders(self):
        """Send reminders for tomorrow's appointments across all businesses."""
        try:
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    """SELECT a.id, a.business_id, a.customer_phone, a.customer_name,
                              a.treatment_code, t.name_it, a.appointment_date::text,
                              a.appointment_time::text, o.display_name,
                              a.chatwoot_conversation_id, b.chatwoot_account_id
                       FROM appointments a
                       JOIN businesses b ON a.business_id = b.id
                       LEFT JOIN treatments t ON a.treatment_id = t.id
                       LEFT JOIN operators o ON a.operator_id = o.id
                       WHERE a.appointment_date = CURRENT_DATE + INTERVAL '1 day'
                         AND a.status = 'confirmed'
                         AND a.reminder_sent_at IS NULL
                         AND a.chatwoot_conversation_id IS NOT NULL""",
                )
                appointments = cur.fetchall()

            for appt in appointments:
                (appt_id, biz_id, phone, name, treatment_code, treatment_name,
                 appt_date, appt_time, operator_name, conv_id, account_id) = appt

                message = (
                    f"Ciao {name}! Ti ricordiamo il tuo appuntamento di domani:\n"
                    f"📋 {treatment_name or treatment_code}\n"
                    f"🕐 Ore {appt_time[:5]}\n"
                )
                if operator_name:
                    message += f"👩‍💼 Con {operator_name}\n"
                message += "\nPuoi confermare rispondendo 'Confermo' o contattarci per modificare."

                try:
                    await chatwoot_client.send_message(
                        account_id=account_id,
                        conversation_id=conv_id,
                        content=message,
                    )
                    # Mark reminder as sent
                    with get_connection() as conn:
                        cur = conn.cursor()
                        cur.execute(
                            "UPDATE appointments SET reminder_sent_at = NOW() WHERE id = %s",
                            (appt_id,),
                        )
                    logger.info(f"Reminder sent for appointment #{appt_id} to {phone}")
                except Exception as e:
                    logger.error(f"Failed to send reminder for #{appt_id}: {e}")

        except Exception as e:
            logger.error(f"Error in daily reminders: {e}")

    async def check_unconfirmed(self):
        """Send email to owner about unconfirmed appointments for tomorrow."""
        # Port from salon_bot_ec2_latest.py reminder logic
        # Uses owner_email from business config
        pass


reminder_service = ReminderService()
```

**Step 3: Add scheduler to main.py**

Add to `lyo-bot/app/main.py` startup:

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from app.services.reminders import reminder_service

@app.on_event("startup")
async def startup():
    scheduler = AsyncIOScheduler(timezone="Europe/Rome")
    scheduler.add_job(reminder_service.send_daily_reminders, CronTrigger(hour=10, minute=0))
    scheduler.add_job(reminder_service.check_unconfirmed, CronTrigger(hour=18, minute=0))
    scheduler.start()
    logger.info("Scheduler started: reminders at 10:00, unconfirmed check at 18:00")
```

**Step 4: Run tests and commit**

```bash
pytest lyo-bot/tests/test_reminders.py -v
git add lyo-bot/app/services/reminders.py lyo-bot/tests/test_reminders.py lyo-bot/app/main.py
git commit -m "feat: add per-business reminder system via Chatwoot API"
```

---

## Task 14: Management Page - Authentication

**Files:**
- Create: `lyo-bot/management/app.py`
- Create: `lyo-bot/management/auth.py`
- Create: `lyo-bot/management/templates/login.html`
- Create: `lyo-bot/management/templates/base.html`
- Create: `lyo-bot/tests/test_mgmt_auth.py`

**Step 1: Implement auth.py**

```python
# lyo-bot/management/auth.py
from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from app.config import settings
from app.models.database import get_connection

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def create_token(email: str, business_id: int) -> str:
    expire = datetime.utcnow() + timedelta(minutes=settings.jwt_expire_minutes)
    return jwt.encode(
        {"sub": email, "business_id": business_id, "exp": expire},
        settings.jwt_secret, algorithm=settings.jwt_algorithm,
    )


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None


def authenticate_user(email: str, password: str) -> Optional[dict]:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, business_id, email, password_hash, name, role FROM management_users WHERE email = %s AND is_active = true",
            (email,),
        )
        row = cur.fetchone()
        if not row or not verify_password(password, row[3]):
            return None
        return {"id": row[0], "business_id": row[1], "email": row[2], "name": row[4], "role": row[5]}
```

**Step 2: Implement management app.py**

```python
# lyo-bot/management/app.py
from fastapi import FastAPI, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from management.auth import authenticate_user, create_token, decode_token
from app.config import settings
import os

mgmt_app = FastAPI(title="Lyo Management")
mgmt_app.add_middleware(SessionMiddleware, secret_key=settings.jwt_secret)

templates_dir = os.path.join(os.path.dirname(__file__), "templates")
static_dir = os.path.join(os.path.dirname(__file__), "static")
templates = Jinja2Templates(directory=templates_dir)

if os.path.exists(static_dir):
    mgmt_app.mount("/static", StaticFiles(directory=static_dir), name="static")


def get_current_user(request: Request) -> dict:
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(status_code=302, headers={"Location": "/manage/login"})
    data = decode_token(token)
    if not data:
        raise HTTPException(status_code=302, headers={"Location": "/manage/login"})
    return data


@mgmt_app.get("/manage/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@mgmt_app.post("/manage/login")
async def login(request: Request, email: str = Form(...), password: str = Form(...)):
    user = authenticate_user(email, password)
    if not user:
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid credentials"})
    token = create_token(user["email"], user["business_id"])
    response = RedirectResponse(url="/manage/dashboard", status_code=302)
    response.set_cookie("access_token", token, httponly=True, max_age=settings.jwt_expire_minutes * 60)
    return response


@mgmt_app.get("/manage/logout")
async def logout():
    response = RedirectResponse(url="/manage/login", status_code=302)
    response.delete_cookie("access_token")
    return response


@mgmt_app.get("/manage/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, user: dict = Depends(get_current_user)):
    return templates.TemplateResponse("dashboard.html", {"request": request, "user": user})
```

**Step 3: Create base.html template**

```html
<!-- lyo-bot/management/templates/base.html -->
<!DOCTYPE html>
<html lang="it">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{% block title %}Lyo Management{% endblock %}</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-50 min-h-screen">
    {% block nav %}
    <nav class="bg-white shadow-sm border-b">
        <div class="max-w-7xl mx-auto px-4 py-3 flex justify-between items-center">
            <span class="text-xl font-bold text-indigo-600">Lyo Management</span>
            <a href="/manage/logout" class="text-sm text-gray-600 hover:text-red-600">Logout</a>
        </div>
    </nav>
    {% endblock %}
    <main class="max-w-7xl mx-auto px-4 py-8">
        {% block content %}{% endblock %}
    </main>
</body>
</html>
```

**Step 4: Create login.html**

```html
<!-- lyo-bot/management/templates/login.html -->
<!DOCTYPE html>
<html lang="it">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Login - Lyo Management</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-50 min-h-screen flex items-center justify-center">
    <div class="bg-white p-8 rounded-lg shadow-md w-full max-w-md">
        <h1 class="text-2xl font-bold text-center mb-6">Lyo Management</h1>
        {% if error %}
        <div class="bg-red-50 text-red-700 p-3 rounded mb-4">{{ error }}</div>
        {% endif %}
        <form method="post" action="/manage/login">
            <div class="mb-4">
                <label class="block text-sm font-medium text-gray-700 mb-1">Email</label>
                <input type="email" name="email" required
                       class="w-full border rounded-lg px-3 py-2 focus:ring-2 focus:ring-indigo-500">
            </div>
            <div class="mb-6">
                <label class="block text-sm font-medium text-gray-700 mb-1">Password</label>
                <input type="password" name="password" required
                       class="w-full border rounded-lg px-3 py-2 focus:ring-2 focus:ring-indigo-500">
            </div>
            <button type="submit"
                    class="w-full bg-indigo-600 text-white py-2 rounded-lg hover:bg-indigo-700">
                Accedi
            </button>
        </form>
    </div>
</body>
</html>
```

**Step 5: Commit**

```bash
git add lyo-bot/management/
git commit -m "feat: add management page with JWT authentication"
```

---

## Task 15: Management Page - Treatments CRUD

**Files:**
- Create: `lyo-bot/management/routes/treatments.py`
- Create: `lyo-bot/management/templates/treatments.html`

**Implementation:** Standard CRUD routes reading/writing to `treatments` table. Form with fields: code, name_it, name_en, description, duration_minutes, price. List view with edit/delete buttons.

**Key route pattern:**

```python
# lyo-bot/management/routes/treatments.py
from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from management.app import templates, get_current_user
from app.models.database import get_connection

router = APIRouter(prefix="/manage/treatments")

@router.get("/", response_class=HTMLResponse)
async def list_treatments(request: Request, user: dict = Depends(get_current_user)):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM treatments WHERE business_id = %s ORDER BY sort_order", (user["business_id"],))
        treatments = cur.fetchall()
    return templates.TemplateResponse("treatments.html", {"request": request, "treatments": treatments, "user": user})

@router.post("/add")
async def add_treatment(request: Request, user: dict = Depends(get_current_user),
                        code: str = Form(...), name_it: str = Form(...), name_en: str = Form(""),
                        duration_minutes: int = Form(...), price: float = Form(...)):
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO treatments (business_id, code, name_it, name_en, duration_minutes, price)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (user["business_id"], code, name_it, name_en or None, duration_minutes, price),
        )
    return RedirectResponse(url="/manage/treatments/", status_code=302)

# Similar for edit, delete
```

**Commit:**
```bash
git add lyo-bot/management/routes/treatments.py lyo-bot/management/templates/treatments.html
git commit -m "feat: add treatments CRUD management page"
```

---

## Task 16: Management Page - Operators CRUD

**Files:**
- Create: `lyo-bot/management/routes/operators.py`
- Create: `lyo-bot/management/templates/operators.html`

**Implementation:** CRUD for operators with treatment assignment checkboxes. Form fields: technical_id, display_name, treatment checkboxes. On save, updates both `operators` and `operator_treatments` tables.

**Commit:**
```bash
git add lyo-bot/management/routes/operators.py lyo-bot/management/templates/operators.html
git commit -m "feat: add operators CRUD with treatment assignment"
```

---

## Task 17: Management Page - Business Hours & Info

**Files:**
- Create: `lyo-bot/management/routes/hours.py`
- Create: `lyo-bot/management/templates/hours.html`
- Create: `lyo-bot/management/routes/info.py`
- Create: `lyo-bot/management/templates/info.html`

**Implementation:** Hours page shows 7 rows (Mon-Sun) with toggle + open/close time inputs. Closures section for adding holiday dates. Info page has form for address, phone, email, policies text.

**Commit:**
```bash
git add lyo-bot/management/routes/ lyo-bot/management/templates/
git commit -m "feat: add business hours and salon info management pages"
```

---

## Task 18: Management Page - Settings

**Files:**
- Create: `lyo-bot/management/routes/settings.py`
- Create: `lyo-bot/management/templates/settings.html`

**Implementation:** Settings form: bot_name, timezone dropdown, language dropdown, owner_email, google_calendar_id. Saves to `businesses` table. Invalidates tenant cache on save.

**Key pattern for cache invalidation:**

```python
from app.services.tenant import tenant_service

# After saving settings:
tenant_service.invalidate(user["business_id"])  # Force reload on next request
```

**Commit:**
```bash
git add lyo-bot/management/routes/settings.py lyo-bot/management/templates/settings.html
git commit -m "feat: add settings management page with cache invalidation"
```

---

## Task 19: Docker Deployment Setup

**Files:**
- Create: `lyo-bot/Dockerfile`
- Create: `lyo-bot/docker-compose.yml`
- Create: `lyo-bot/deploy.sh`

**Step 1: Dockerfile**

```dockerfile
# lyo-bot/Dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
```

**Step 2: docker-compose.yml**

```yaml
# lyo-bot/docker-compose.yml
version: "3.8"

services:
  lyo-bot:
    build: .
    ports:
      - "8001:8001"
    env_file: .env
    restart: unless-stopped
    depends_on:
      - db

  management:
    build: .
    command: uvicorn management.app:mgmt_app --host 0.0.0.0 --port 8002
    ports:
      - "8002:8002"
    env_file: .env
    restart: unless-stopped
    depends_on:
      - db

  db:
    image: postgres:15
    environment:
      POSTGRES_DB: lyo_production
      POSTGRES_USER: lyoadmin
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./database/schema.sql:/docker-entrypoint-initdb.d/01-schema.sql
      - ./database/seed.sql:/docker-entrypoint-initdb.d/02-seed.sql
    ports:
      - "5432:5432"

volumes:
  pgdata:
```

**Step 3: Commit**

```bash
git add lyo-bot/Dockerfile lyo-bot/docker-compose.yml
git commit -m "feat: add Docker deployment configuration"
```

---

## Task 20: Integration Testing

**Files:**
- Create: `lyo-bot/tests/test_integration.py`

**Step 1: Write integration test**

```python
# lyo-bot/tests/test_integration.py
"""
Integration tests — require running database.
Run with: pytest lyo-bot/tests/test_integration.py -v --integration
Skip in CI without DB: pytest -m "not integration"
"""
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


@pytest.mark.integration
class TestFullFlow:
    def test_webhook_to_response(self):
        """Full flow: incoming webhook → 200 response"""
        payload = {
            "event": "message_created",
            "id": 1, "content": "Ciao",
            "message_type": "incoming", "content_type": "text",
            "account": {"id": 1, "name": "Test"},
            "conversation": {"id": 1, "inbox_id": 1},
            "inbox": {"id": 1, "name": "WA"},
            "sender": {"id": 1, "name": "Test", "phone_number": "+393331234567", "type": "contact"},
        }
        response = client.post("/webhook/chatwoot", json=payload)
        assert response.status_code == 200
        assert response.json()["status"] == "received"

    def test_health(self):
        response = client.get("/health")
        assert response.status_code == 200

    def test_unknown_event_ignored(self):
        payload = {"event": "conversation_resolved", "id": 1}
        response = client.post("/webhook/chatwoot", json=payload)
        assert response.json()["status"] == "ignored"
```

**Step 2: Commit**

```bash
git add lyo-bot/tests/test_integration.py
git commit -m "feat: add integration test suite"
```

---

## Execution Notes

### Parallel Task Groups (for subagent execution)
- **Group A** (after Task 3): Tasks 4, 7, 8 — independent services
- **Group B** (after Group A): Tasks 5, 6, 9 — endpoint + client + booking
- **Group C** (after Task 10): Tasks 10, 11 — tools + AI
- **Group D** (after Task 14): Tasks 15, 16, 17, 18 — management pages

### Migration from Production Bot
- Port system prompt logic from `salon_bot_ec2_latest.py:337-500`
- Port message batching from `salon_bot_ec2_latest.py:120-218`
- Port calendar integration from `salon_bot_ec2_latest.py:240-267`
- Port Italian language handling and "ciao" context detection
- Port date calendar generation from `salon_bot_ec2_latest.py:302-332`
- **Do NOT port:** direct Meta API calls, in-memory state, hardcoded services

### Key Chatwoot Source References
- Agent Bot model: `chatwoot/app/models/agent_bot.rb` (outgoing_url, bot_config, account_id nullable)
- Webhook listener: `chatwoot/app/listeners/agent_bot_listener.rb:18-24` (message_created dispatch)
- Bot API access: `chatwoot/app/controllers/concerns/access_token_auth_helper.rb:2-6` (allowed endpoints)
- Message webhook data: `chatwoot/app/models/message.rb:171-189` (payload structure)
