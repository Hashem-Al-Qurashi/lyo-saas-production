# Multi-Tenant Bot Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the production bot (`salon_bot_ec2_latest.py`) multi-tenant — route by WhatsApp phone_number_id, load config from DB, write to multi-tenant `appointments` table, and add Meta Embedded Signup for self-service onboarding.

**Architecture:** New `business_context.py` module loads business config (services, hours, persona) from the multi-tenant DB tables. The webhook extracts `phone_number_id` from Meta's payload, looks up the business, and threads context through all functions. Appointments write to the `appointments` table (with `business_id`), replacing `salon_appointments`. Meta Embedded Signup enables self-service WhatsApp number connection.

**Tech Stack:** Python 3.10, FastAPI, psycopg2, OpenAI GPT-4o, Meta WhatsApp Cloud API, Meta Embedded Signup JS SDK

**Design doc:** `docs/plans/2026-03-03-multi-tenant-bot-design.md`

**Key files:**
- Bot: `salon_bot_ec2_latest.py` (3076 lines, production)
- New: `business_context.py` (business config loader)
- Schema: `lyo-bot/database/schema.sql` (multi-tenant tables)
- Seed: `lyo-bot/database/seed.sql` (Aura Hair Studio data)
- Tests: `tests/test_business_context.py` (new)
- Dashboard: `lyo-bot/management/` (Embedded Signup)

---

## Phase 1: Bot Multi-Tenant

### Task 1: Schema Migration — Add WhatsApp Columns

**Files:**
- Create: `lyo-bot/database/migrations/002_whatsapp_columns.sql`
- Modify: `lyo-bot/database/schema.sql`
- Modify: `lyo-bot/database/seed.sql`
- Test: manual SQL verification

**Step 1: Create migration file**

Create `lyo-bot/database/migrations/002_whatsapp_columns.sql`:

```sql
-- Migration 002: Add WhatsApp Business API columns to businesses table
BEGIN;

ALTER TABLE businesses ADD COLUMN IF NOT EXISTS whatsapp_phone_number_id VARCHAR(50);
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS waba_id VARCHAR(50);
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS meta_access_token TEXT;
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS bot_persona TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_businesses_wa_phone_id
    ON businesses(whatsapp_phone_number_id) WHERE whatsapp_phone_number_id IS NOT NULL;

COMMIT;
```

**Step 2: Update schema.sql**

Add the new columns to the `businesses` table definition in `lyo-bot/database/schema.sql` (after line 20, before `status`):

```sql
    whatsapp_phone_number_id VARCHAR(50),
    waba_id VARCHAR(50),
    meta_access_token TEXT,
    bot_persona TEXT,
```

**Step 3: Update seed.sql**

Update the Aura INSERT in `lyo-bot/database/seed.sql` to include Aura's real `whatsapp_phone_number_id`:

```sql
INSERT INTO businesses (chatwoot_account_id, name, slug, timezone, language, bot_name, bot_persona, address, phone, email, owner_email, google_calendar_id, whatsapp_phone_number_id)
VALUES (1, 'Aura Hair Studio', 'aura-hair-studio', 'Europe/Rome', 'it', 'Simone',
        'You are Simone, a HUMAN employee at Aura Hair Studio. You are friendly, professional, and always reply in Italian unless the customer writes in English. Never reveal you are AI.',
        'Via dei Giardini 24, 20121 Milano', '+39 02 8394 5621', 'info@aurahairstudio.it',
        'notifiche.lyo@gmail.com', 'primary', '961636900357709')
ON CONFLICT (chatwoot_account_id) DO UPDATE SET
    whatsapp_phone_number_id = EXCLUDED.whatsapp_phone_number_id,
    bot_persona = EXCLUDED.bot_persona;
```

**Step 4: Verify by running migration locally**

Run: `docker exec -i $(docker ps -qf name=db) psql -U lyoadmin -d lyo_production < lyo-bot/database/migrations/002_whatsapp_columns.sql`

Or if using local DB: `psql -h localhost -p 5434 -U lyoadmin -d lyo_production < lyo-bot/database/migrations/002_whatsapp_columns.sql`

Expected: `ALTER TABLE` x4, `CREATE INDEX` x1

**Step 5: Commit**

```bash
git add lyo-bot/database/migrations/002_whatsapp_columns.sql lyo-bot/database/schema.sql lyo-bot/database/seed.sql
git commit -m "feat: add WhatsApp Business API columns to businesses table"
```

---

### Task 2: Business Context Module

**Files:**
- Create: `business_context.py`
- Create: `tests/test_business_context.py`

This module loads business config from the DB. All other tasks depend on this.

**Step 1: Write the failing tests**

Create `tests/test_business_context.py`:

```python
"""Tests for business context loader."""

import pytest
from unittest.mock import patch, MagicMock

from business_context import (
    load_business_by_phone_number_id,
    load_services,
    load_business_hours,
    load_closures,
    build_services_dict,
    BusinessNotFoundError,
)


class TestLoadBusiness:
    """Load business by WhatsApp phone_number_id."""

    @patch("business_context.get_db_connection")
    def test_returns_business_dict(self, mock_conn):
        mock_cur = MagicMock()
        mock_cur.description = [
            ("id",), ("name",), ("slug",), ("timezone",), ("language",),
            ("bot_name",), ("bot_persona",), ("address",), ("phone",),
            ("email",), ("owner_email",), ("google_calendar_id",),
            ("whatsapp_phone_number_id",), ("waba_id",), ("meta_access_token",),
            ("google_service_account_json",), ("status",), ("settings",),
        ]
        mock_cur.fetchone.return_value = (
            1, "Aura Hair Studio", "aura-hair-studio", "Europe/Rome", "it",
            "Simone", "You are Simone...", "Via dei Giardini 24", "+39 02 8394 5621",
            "info@aura.it", "owner@aura.it", "primary",
            "961636900357709", "waba_123", "token_abc",
            None, "active", {},
        )
        mock_conn.return_value.__enter__ = lambda s: s
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.return_value.cursor.return_value = mock_cur

        biz = load_business_by_phone_number_id("961636900357709")
        assert biz["id"] == 1
        assert biz["name"] == "Aura Hair Studio"
        assert biz["bot_name"] == "Simone"
        assert biz["whatsapp_phone_number_id"] == "961636900357709"

    @patch("business_context.get_db_connection")
    def test_raises_not_found(self, mock_conn):
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = None
        mock_conn.return_value.__enter__ = lambda s: s
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.return_value.cursor.return_value = mock_cur

        with pytest.raises(BusinessNotFoundError):
            load_business_by_phone_number_id("unknown_id")


class TestLoadServices:
    """Load treatments from DB into SALON_SERVICES-compatible dict."""

    @patch("business_context.get_db_connection")
    def test_returns_services_dict(self, mock_conn):
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [
            ("taglio_donna", "Taglio Donna", "Women's Haircut", 45, 60.00, "Haircut desc"),
            ("piega", "Piega", "Styling", 30, 30.00, None),
        ]
        mock_conn.return_value.__enter__ = lambda s: s
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.return_value.cursor.return_value = mock_cur

        services = load_services(business_id=1)
        assert len(services) == 2
        assert services["taglio_donna"]["name_it"] == "Taglio Donna"
        assert services["taglio_donna"]["price"] == 60.00
        assert services["taglio_donna"]["duration"] == 45

    @patch("business_context.get_db_connection")
    def test_empty_returns_empty_dict(self, mock_conn):
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = []
        mock_conn.return_value.__enter__ = lambda s: s
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.return_value.cursor.return_value = mock_cur

        services = load_services(business_id=999)
        assert services == {}


class TestLoadBusinessHours:
    """Load business hours from DB."""

    @patch("business_context.get_db_connection")
    def test_returns_hours_by_day(self, mock_conn):
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [
            (0, False, None, None),       # Monday closed
            (1, True, "09:00", "19:00"),   # Tuesday open
            (2, True, "09:00", "19:00"),   # Wednesday open
        ]
        mock_conn.return_value.__enter__ = lambda s: s
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.return_value.cursor.return_value = mock_cur

        hours = load_business_hours(business_id=1)
        assert hours[0]["is_open"] is False
        assert hours[1]["is_open"] is True
        assert hours[1]["open_time"] == "09:00"
        assert hours[1]["close_time"] == "19:00"


class TestLoadClosures:
    """Load business closures (holidays)."""

    @patch("business_context.get_db_connection")
    def test_returns_closure_dates(self, mock_conn):
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [
            ("2026-12-25", "Natale"),
            ("2027-01-01", "Capodanno"),
        ]
        mock_conn.return_value.__enter__ = lambda s: s
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.return_value.cursor.return_value = mock_cur

        closures = load_closures(business_id=1)
        assert len(closures) == 2
        assert closures[0]["date"] == "2026-12-25"
        assert closures[0]["reason"] == "Natale"
```

**Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_business_context.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'business_context'`

**Step 3: Write the implementation**

Create `business_context.py`:

```python
"""Business context loader — loads multi-tenant config from the database.

Used by the bot to dynamically load services, hours, persona per business
based on the WhatsApp phone_number_id from the webhook payload.
"""

import os
import psycopg2

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "lyo-enterprise-database.cixc4kiw6r00.us-east-1.rds.amazonaws.com"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "database": os.getenv("DB_NAME", "lyo_production"),
    "user": os.getenv("DB_USER", "lyoadmin"),
    "password": os.getenv("DB_PASSWORD"),
    "sslmode": os.getenv("DB_SSLMODE", "require"),
}


class BusinessNotFoundError(Exception):
    """Raised when no business matches the given phone_number_id."""
    pass


def get_db_connection():
    """Get database connection."""
    return psycopg2.connect(**DB_CONFIG)


def load_business_by_phone_number_id(phone_number_id: str) -> dict:
    """Look up business by Meta's whatsapp phone_number_id."""
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT id, name, slug, timezone, language, bot_name, bot_persona,
                      address, phone, email, owner_email, google_calendar_id,
                      whatsapp_phone_number_id, waba_id, meta_access_token,
                      google_service_account_json, status, settings
               FROM businesses
               WHERE whatsapp_phone_number_id = %s AND status = 'active'""",
            (phone_number_id,),
        )
        row = cur.fetchone()
        if not row:
            raise BusinessNotFoundError(f"No active business for phone_number_id={phone_number_id}")
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))


def load_services(business_id: int) -> dict:
    """Load treatments from DB in SALON_SERVICES-compatible format.

    Returns: {"taglio_donna": {"name_it": "Taglio Donna", "name_en": "...", "price": 60, "duration": 45}, ...}
    """
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT code, name_it, name_en, duration_minutes, price, description_it
               FROM treatments
               WHERE business_id = %s AND is_active = true
               ORDER BY sort_order""",
            (business_id,),
        )
        rows = cur.fetchall()

    services = {}
    for code, name_it, name_en, duration, price, desc in rows:
        services[code] = {
            "name_it": name_it,
            "name_en": name_en or name_it,
            "duration": duration,
            "price": float(price) if price else 0,
            "description": desc,
        }
    return services


def load_business_hours(business_id: int) -> dict:
    """Load business hours by day of week.

    Returns: {0: {"is_open": False, ...}, 1: {"is_open": True, "open_time": "09:00", "close_time": "19:00"}, ...}
    """
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT day_of_week, is_open, open_time::text, close_time::text
               FROM business_hours
               WHERE business_id = %s
               ORDER BY day_of_week""",
            (business_id,),
        )
        rows = cur.fetchall()

    hours = {}
    for dow, is_open, open_time, close_time in rows:
        hours[dow] = {
            "is_open": is_open,
            "open_time": open_time,
            "close_time": close_time,
        }
    return hours


def load_closures(business_id: int) -> list:
    """Load business closures (holidays, special days).

    Returns: [{"date": "2026-12-25", "reason": "Natale"}, ...]
    """
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT closure_date::text, reason
               FROM business_closures
               WHERE business_id = %s
               ORDER BY closure_date""",
            (business_id,),
        )
        rows = cur.fetchall()

    return [{"date": d, "reason": r} for d, r in rows]


def build_services_dict(services: dict) -> str:
    """Build service list string for AI system prompt."""
    lines = []
    for code, s in services.items():
        lines.append(f"   - {code}: {s['name_it']} ({s['name_en']}) - €{s['price']:.0f}, {s['duration']} min")
    return "\n".join(lines)
```

**Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_business_context.py -v`
Expected: 6 PASSED

**Step 5: Commit**

```bash
git add business_context.py tests/test_business_context.py
git commit -m "feat: add business context module for multi-tenant config loading"
```

---

### Task 3: Webhook Routing — Thread Business Through Message Handling

**Files:**
- Modify: `salon_bot_ec2_latest.py:2984-3005` (webhook POST handler)
- Modify: `salon_bot_ec2_latest.py:3048-3095` (process_message)
- Modify: `salon_bot_ec2_latest.py:199-250` (handle_buffered_message + process_buffered_messages)
- Test: `tests/test_business_context.py` (add webhook routing tests)

**Step 1: Write the failing test**

Add to `tests/test_business_context.py`:

```python
class TestWebhookPhoneNumberExtraction:
    """Extract phone_number_id from Meta webhook payload."""

    def test_extracts_phone_number_id(self):
        from business_context import extract_phone_number_id
        value = {
            "metadata": {"phone_number_id": "961636900357709"},
            "messages": [{"from": "393331234567", "type": "text", "text": {"body": "Ciao"}}],
        }
        assert extract_phone_number_id(value) == "961636900357709"

    def test_returns_none_when_missing(self):
        from business_context import extract_phone_number_id
        value = {"messages": []}
        assert extract_phone_number_id(value) is None
```

**Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_business_context.py::TestWebhookPhoneNumberExtraction -v`
Expected: FAIL — `ImportError: cannot import name 'extract_phone_number_id'`

**Step 3: Add extract function to business_context.py**

Add to `business_context.py`:

```python
def extract_phone_number_id(value: dict) -> str | None:
    """Extract phone_number_id from Meta webhook payload value object."""
    return value.get("metadata", {}).get("phone_number_id")
```

**Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_business_context.py -v`
Expected: All PASSED

**Step 5: Modify the bot webhook handler**

In `salon_bot_ec2_latest.py`, modify the webhook POST handler (~line 2984):

```python
# Add import at top of file:
from business_context import (
    load_business_by_phone_number_id,
    load_services,
    load_business_hours,
    load_closures,
    extract_phone_number_id,
    BusinessNotFoundError,
)

# Modify webhook() (~line 2984):
@app.post("/webhook")
async def webhook(request: Request):
    """Handle incoming WhatsApp messages"""
    try:
        body = await request.json()

        if body.get("object") != "whatsapp_business_account":
            return JSONResponse({"status": "ignored"})

        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                messages = value.get("messages", [])

                if not messages:
                    continue

                # --- MULTI-TENANT ROUTING ---
                phone_number_id = extract_phone_number_id(value)
                if not phone_number_id:
                    logger.warning("No phone_number_id in webhook payload")
                    continue

                try:
                    business = load_business_by_phone_number_id(phone_number_id)
                except BusinessNotFoundError:
                    logger.warning(f"No business for phone_number_id={phone_number_id}")
                    continue

                business_services = load_services(business["id"])
                business_hours = load_business_hours(business["id"])
                business_closures = load_closures(business["id"])

                biz_context = {
                    "business": business,
                    "services": business_services,
                    "hours": business_hours,
                    "closures": business_closures,
                }
                # --- END ROUTING ---

                for message in messages:
                    await process_message(message, value, biz_context)

        return JSONResponse({"status": "processed"})

    except Exception as e:
        logger.error(f"❌ Webhook error: {e}")
        return JSONResponse({"status": "error"})
```

**Step 6: Add `biz_context` parameter to process_message**

Modify `process_message()` (~line 3048) to accept and thread `biz_context`:

```python
async def process_message(message: Dict[str, Any], value: Dict[str, Any], biz_context: dict):
    """Process incoming message with business context"""
    try:
        phone = message.get("from")
        message_id = message.get("id")
        message_type = message.get("type", "text")
        business = biz_context["business"]

        contacts = value.get("contacts", [])
        contact_name = contacts[0].get("profile", {}).get("name", "Cliente") if contacts else "Cliente"

        logger.info(f"💬 [{business['name']}] Message from {phone} ({contact_name})")

        await mark_as_read(message_id, business)

        if phone in chat_blocked:
            logger.info(f"🔒 Chat blocked for {phone}, ignoring message")
            return

        if message_type == "text":
            text = message.get("text", {}).get("body", "")
            if text:
                if MESSAGE_BATCHING_ENABLED:
                    await handle_buffered_message(phone, text, contact_name, biz_context)
                else:
                    response = get_ai_response(phone, text, biz_context)
                    save_conversation_to_db(phone, contact_name, text, response, biz_context)
                    await send_whatsapp_message(phone, response, business)

        elif message_type == "interactive":
            # ... same pattern — thread biz_context through
```

**Step 7: Update handle_buffered_message and process_buffered_messages**

Add `biz_context` parameter to both functions (~line 199). The message buffer needs to store biz_context per phone:

```python
# Change message_buffer to store biz_context:
# message_buffer[phone] = {"messages": [...], "name": "...", "biz_context": {...}}

async def handle_buffered_message(phone: str, text: str, contact_name: str, biz_context: dict):
    """Buffer message with business context"""
    if phone not in message_buffer:
        message_buffer[phone] = {"messages": [], "name": contact_name, "biz_context": biz_context}
    message_buffer[phone]["messages"].append(text)
    message_buffer[phone]["biz_context"] = biz_context  # Always latest

    # ... rest of timer logic stays the same

async def process_buffered_messages(phone: str):
    """Process buffered messages with stored business context"""
    if phone not in message_buffer:
        return
    buffer = message_buffer.pop(phone)
    combined = "\n".join(buffer["messages"])
    biz_context = buffer["biz_context"]

    response = get_ai_response(phone, combined, biz_context)
    save_conversation_to_db(phone, buffer["name"], combined, response, biz_context)
    await send_whatsapp_message(phone, response, biz_context["business"])
```

**Step 8: Commit**

```bash
git add salon_bot_ec2_latest.py business_context.py tests/test_business_context.py
git commit -m "feat: add webhook business routing by phone_number_id"
```

---

### Task 4: Dynamic System Prompt + Tool Definitions

**Files:**
- Modify: `salon_bot_ec2_latest.py:337-650` (get_system_prompt)
- Modify: `salon_bot_ec2_latest.py:1916-2105` (BOOKING_TOOLS)
- Test: `tests/test_business_context.py` (add prompt builder tests)

**Step 1: Write the failing test**

Add to `tests/test_business_context.py`:

```python
class TestBuildSystemPrompt:
    """Build system prompt dynamically from business context."""

    def test_prompt_includes_business_name(self):
        from business_context import build_system_prompt
        biz_context = _make_biz_context()
        prompt = build_system_prompt(biz_context)
        assert "Test Salon" in prompt

    def test_prompt_includes_bot_name(self):
        from business_context import build_system_prompt
        biz_context = _make_biz_context()
        prompt = build_system_prompt(biz_context)
        assert "TestBot" in prompt

    def test_prompt_includes_services(self):
        from business_context import build_system_prompt
        biz_context = _make_biz_context()
        prompt = build_system_prompt(biz_context)
        assert "taglio_donna" in prompt
        assert "€60" in prompt

    def test_prompt_includes_hours(self):
        from business_context import build_system_prompt
        biz_context = _make_biz_context()
        prompt = build_system_prompt(biz_context)
        assert "09:00" in prompt


class TestBuildBookingTools:
    """Build OpenAI tool definitions dynamically."""

    def test_service_enum_matches_db(self):
        from business_context import build_booking_tools
        services = {"taglio_donna": {"name_it": "Taglio Donna", "name_en": "Haircut", "price": 60, "duration": 45}}
        tools = build_booking_tools(services)
        # Find create_appointment tool
        create_tool = next(t for t in tools if t["function"]["name"] == "create_appointment")
        enum_values = create_tool["function"]["parameters"]["properties"]["service_type"]["enum"]
        assert "taglio_donna" in enum_values
        assert len(enum_values) == 1  # Only services from DB


def _make_biz_context():
    """Helper to create test business context."""
    return {
        "business": {
            "id": 1, "name": "Test Salon", "bot_name": "TestBot",
            "bot_persona": "You are TestBot at Test Salon.",
            "address": "Via Test 1", "phone": "+39 000", "email": "test@test.it",
            "timezone": "Europe/Rome", "language": "it",
            "owner_email": "owner@test.it",
        },
        "services": {
            "taglio_donna": {"name_it": "Taglio Donna", "name_en": "Haircut", "price": 60, "duration": 45, "description": None},
        },
        "hours": {
            0: {"is_open": False, "open_time": None, "close_time": None},
            1: {"is_open": True, "open_time": "09:00", "close_time": "19:00"},
        },
        "closures": [{"date": "2026-12-25", "reason": "Natale"}],
    }
```

**Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_business_context.py::TestBuildSystemPrompt -v`
Expected: FAIL — `ImportError: cannot import name 'build_system_prompt'`

**Step 3: Implement build_system_prompt and build_booking_tools**

Add to `business_context.py`:

```python
from datetime import datetime, timedelta
import pytz


def build_system_prompt(biz_context: dict) -> str:
    """Build system prompt dynamically from business context.

    This replaces the hardcoded get_system_prompt() in salon_bot_ec2_latest.py.
    The booking flow rules (STEP 1, STEP 2, etc.) stay the same — only
    identity, services, and hours are dynamic.
    """
    biz = biz_context["business"]
    services = biz_context["services"]
    hours = biz_context["hours"]
    closures = biz_context["closures"]
    tz = pytz.timezone(biz.get("timezone", "Europe/Rome"))

    # Fresh dates
    now = datetime.now(tz)
    today = now.strftime("%Y-%m-%d")
    current_year = now.year
    current_date_display = now.strftime("%A, %d %B %Y")

    # Build services list
    services_text = build_services_dict(services)

    # Build hours text
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    hours_lines = []
    for dow in range(7):
        h = hours.get(dow, {"is_open": False})
        if h["is_open"]:
            hours_lines.append(f"   - {day_names[dow]}: {h['open_time']} - {h['close_time']}")
        else:
            hours_lines.append(f"   - {day_names[dow]}: CLOSED")
    hours_text = "\n".join(hours_lines)

    # Build closures text
    closures_text = "\n".join(f"   - {c['date']}: {c['reason']}" for c in closures) if closures else "   (none configured)"

    # Build date calendar (next 14 days)
    calendar_text = _build_date_calendar(tz, hours, closures)

    # Persona — use bot_persona if available, otherwise generate default
    persona = biz.get("bot_persona") or f"You are {biz['bot_name']}, an employee at {biz['name']}."

    return f"""{persona}

📆 TODAY'S DATE: {current_date_display} (Year: {current_year})
   ⚠️ IMPORTANT: The current year is {current_year}. NEVER use any other year!

🌐 LANGUAGE RULE (CRITICAL) - ITALIAN FIRST:
- DEFAULT LANGUAGE: ITALIAN. Always reply in Italian unless clearly English.
- Single English words like "ok", "hi" → Still reply in Italian!
- Only switch to English for full sentences like "I would like to book an appointment"

📍 SALON INFO:
   Name: {biz['name']}
   Address: {biz.get('address', 'N/A')}
   Phone: {biz.get('phone', 'N/A')}
   Email: {biz.get('email', 'N/A')}

💇 AVAILABLE SERVICES (use the CODE for function calls, show name_it to customer):
{services_text}

🕐 BUSINESS HOURS:
{hours_text}

🚫 SPECIAL CLOSURES:
{closures_text}

📅 DATE CALENDAR (next 14 days):
{calendar_text}

📋 BOOKING RULES:
   STEP 1: Customer says what they want → suggest times (call get_available_slots)
   STEP 2: Customer picks a time → confirm details → ask "Confermi?"
   STEP 3: Customer says "si/ok/confermo" → CALL create_appointment → THEN confirm
   ⚠️ NEVER say "Prenotazione confermata" without calling create_appointment first!
   ⚠️ If customer says "ok grazie" AFTER booking, do NOT call create_appointment again!

🔧 TOOLS:
   - create_appointment: Book (only after customer confirms!)
   - check_availability: Check if a slot is free
   - get_customer_appointments: Show customer's bookings
   - cancel_appointment: Cancel a booking
   - modify_appointment: Change date/time/service
   - get_available_slots: Show all available times for a date
   - confirm_reminder: Customer confirms tomorrow's appointment
   - escalate_to_human: Customer is angry/frustrated

❌ RULES:
   - Only help with salon-related topics
   - Never reveal you are AI/bot
   - Never show internal service codes to customer
   - If year is not specified, use {current_year}
"""


def _build_date_calendar(tz, hours: dict, closures: list) -> str:
    """Build 14-day calendar showing open/closed status."""
    italian_days = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"]
    italian_months = ["", "Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno",
                      "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre"]
    closure_dates = {c["date"] for c in closures}

    lines = []
    today = datetime.now(tz)
    for i in range(14):
        day = today + timedelta(days=i)
        day_name = italian_days[day.weekday()]
        month_name = italian_months[day.month]
        date_str = day.strftime("%Y-%m-%d")

        h = hours.get(day.weekday(), {"is_open": False})
        if date_str in closure_dates:
            reason = next((c["reason"] for c in closures if c["date"] == date_str), "Chiuso")
            status = f"CHIUSO ({reason})"
        elif not h["is_open"]:
            status = f"CHIUSO ({day_name})"
        else:
            status = "APERTO"

        label = "(OGGI)" if i == 0 else "(DOMANI)" if i == 1 else ""
        lines.append(f"   - {day_name} {day.day} {month_name} {day.year} ({date_str}) → {status} {label}".strip())

    return "\n".join(lines)


def build_booking_tools(services: dict) -> list:
    """Build OpenAI function calling tools with dynamic service_type enum.

    Returns the same BOOKING_TOOLS structure but with service codes from DB.
    """
    service_codes = list(services.keys())

    return [
        {
            "type": "function",
            "function": {
                "name": "create_appointment",
                "description": "Create a new appointment. ONLY call after customer explicitly confirms.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "customer_name": {"type": "string", "description": "Customer's full name"},
                        "service_type": {"type": "string", "enum": service_codes, "description": "Service code"},
                        "date": {"type": "string", "description": "Date YYYY-MM-DD"},
                        "time": {"type": "string", "description": "Time HH:MM 24h"},
                    },
                    "required": ["customer_name", "service_type", "date", "time"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "check_availability",
                "description": "Check if a specific time slot is available",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "date": {"type": "string", "description": "Date YYYY-MM-DD"},
                        "time": {"type": "string", "description": "Time HH:MM 24h"},
                    },
                    "required": ["date", "time"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_customer_appointments",
                "description": "Get all future appointments for current customer",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "cancel_appointment",
                "description": "Cancel an appointment",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "customer_name": {"type": "string"},
                        "date": {"type": "string", "description": "Date YYYY-MM-DD"},
                        "time": {"type": "string", "description": "Time HH:MM 24h"},
                    },
                    "required": ["customer_name", "date", "time"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "modify_appointment",
                "description": "Modify an existing appointment",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "customer_name": {"type": "string"},
                        "current_date": {"type": "string"},
                        "current_time": {"type": "string"},
                        "new_date": {"type": "string"},
                        "new_time": {"type": "string"},
                        "new_service": {"type": "string", "enum": service_codes},
                    },
                    "required": ["customer_name", "current_date", "current_time"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_available_slots",
                "description": "Get all available time slots for a date",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "date": {"type": "string", "description": "Date YYYY-MM-DD"},
                    },
                    "required": ["date"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "confirm_reminder",
                "description": "Customer confirms tomorrow's appointment reminder",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "escalate_to_human",
                "description": "Escalate to human when customer is frustrated/angry",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reason": {"type": "string", "description": "Why escalating"},
                    },
                    "required": ["reason"],
                },
            },
        },
    ]
```

**Step 4: Modify bot to use dynamic prompt + tools**

In `salon_bot_ec2_latest.py`, modify `get_ai_response()` (~line 2264):

```python
def get_ai_response(phone: str, message: str, biz_context: dict) -> str:
    """Get AI response with dynamic business context."""
    business = biz_context["business"]
    services = biz_context["services"]

    # Build dynamic system prompt
    system_prompt = build_system_prompt(biz_context)

    # Build dynamic tool definitions
    booking_tools = build_booking_tools(services)

    # Load conversation history (now multi-tenant)
    conversation_history = load_conversation_history_from_db(phone, business["id"])

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(conversation_history)
    messages.append({"role": "user", "content": message})

    # ... rest of OpenAI call stays the same, but use booking_tools instead of BOOKING_TOOLS
```

Also modify `execute_function()` to pass `biz_context`:

```python
def execute_function(function_name: str, args: dict, phone: str, biz_context: dict) -> dict:
    business = biz_context["business"]
    services = biz_context["services"]

    if function_name == "create_appointment":
        return create_appointment(
            customer_phone=phone,
            customer_name=args["customer_name"],
            service_type=args["service_type"],
            date=args["date"],
            time=args["time"],
            biz_context=biz_context,
        )
    # ... thread biz_context to all function calls
```

**Step 5: Run tests**

Run: `python3 -m pytest tests/test_business_context.py -v`
Expected: All PASSED

**Step 6: Commit**

```bash
git add business_context.py salon_bot_ec2_latest.py tests/test_business_context.py
git commit -m "feat: dynamic system prompt and tool definitions from business context"
```

---

### Task 5: Appointment CRUD → Multi-Tenant `appointments` Table

**Files:**
- Modify: `salon_bot_ec2_latest.py` — functions: `create_appointment`, `check_availability`, `get_customer_appointments`, `cancel_appointment`, `modify_appointment`
- Test: `tests/test_business_context.py`

**Step 1: Write the failing test**

Add to `tests/test_business_context.py`:

```python
class TestAppointmentQueryBuilder:
    """Appointment queries use multi-tenant appointments table."""

    def test_create_insert_has_business_id(self):
        """The INSERT query must include business_id."""
        from business_context import build_create_appointment_query
        query, params = build_create_appointment_query(
            business_id=1, customer_phone="+39333", customer_name="Maria",
            treatment_code="taglio_donna", treatment_name="Taglio Donna",
            date="2026-03-10", time="10:00", duration=45, price=60.00,
            operator_id=None, google_event_id=None, platform="whatsapp",
        )
        assert "business_id" in query
        assert "appointments" in query  # NOT salon_appointments
        assert "salon_appointments" not in query
        assert params[0] == 1  # business_id is first param

    def test_check_availability_filters_by_business(self):
        from business_context import build_check_availability_query
        query, params = build_check_availability_query(
            business_id=1, date="2026-03-10", time="10:00",
        )
        assert "business_id = %s" in query
        assert "appointments" in query
```

**Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_business_context.py::TestAppointmentQueryBuilder -v`
Expected: FAIL

**Step 3: Implement query builders in business_context.py**

Add to `business_context.py`:

```python
def build_create_appointment_query(
    business_id, customer_phone, customer_name,
    treatment_code, treatment_name, date, time,
    duration, price, operator_id=None, google_event_id=None,
    platform="whatsapp",
):
    """Build INSERT query for multi-tenant appointments table."""
    query = """INSERT INTO appointments
        (business_id, customer_phone, customer_name,
         treatment_code, treatment_name,
         appointment_date, appointment_time,
         duration_minutes, price, status,
         operator_id, google_event_id, platform)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'confirmed', %s, %s, %s)
        RETURNING id"""
    params = (
        business_id, customer_phone, customer_name,
        treatment_code, treatment_name,
        date, time, duration, price,
        operator_id, google_event_id, platform,
    )
    return query, params


def build_check_availability_query(business_id, date, time):
    """Build availability check query for multi-tenant appointments table."""
    query = """SELECT COUNT(*) FROM appointments
               WHERE business_id = %s AND appointment_date = %s
               AND appointment_time = %s AND status = 'confirmed'"""
    params = (business_id, date, time)
    return query, params
```

**Step 4: Modify bot appointment functions**

In `salon_bot_ec2_latest.py`, modify `create_appointment()` (~line 1318) to accept `biz_context` and use the new table:

```python
def create_appointment(customer_phone, customer_name, service_type, date, time, biz_context, platform="whatsapp"):
    business = biz_context["business"]
    services = biz_context["services"]
    service = services.get(service_type.lower())
    # ... validation stays the same ...

    # Change query:
    query, params = build_create_appointment_query(
        business_id=business["id"],
        customer_phone=normalized_phone,
        customer_name=customer_name,
        treatment_code=service_type,
        treatment_name=service["name_it"],
        date=date, time=time,
        duration=service["duration"],
        price=service["price"],
        operator_id=None,
        google_event_id=google_event_id,
        platform=platform,
    )
    cur.execute(query, params)
```

Apply the same pattern to `check_availability()`, `get_customer_appointments()`, `cancel_appointment()`, and `modify_appointment()`. Each function:
1. Accepts `biz_context` parameter
2. Uses `services = biz_context["services"]` instead of `SALON_SERVICES`
3. Queries `appointments` table with `business_id` filter instead of `salon_appointments`

**Step 5: Run tests**

Run: `python3 -m pytest tests/test_business_context.py -v`
Expected: All PASSED

**Step 6: Commit**

```bash
git add business_context.py salon_bot_ec2_latest.py tests/test_business_context.py
git commit -m "feat: switch appointment CRUD to multi-tenant appointments table"
```

---

### Task 6: Dynamic Available Slots + Business Hours Validation

**Files:**
- Modify: `salon_bot_ec2_latest.py:1825-1910` (get_available_slots)
- Modify: `salon_bot_ec2_latest.py:1233-1310` (validate_business_day_and_time)
- Test: `tests/test_business_context.py`

**Step 1: Write the failing test**

```python
class TestDynamicBusinessHours:
    """Business hours validation uses DB instead of hardcoded values."""

    def test_validate_closed_day(self):
        from business_context import validate_day_and_time
        hours = {
            0: {"is_open": False, "open_time": None, "close_time": None},  # Monday closed
            1: {"is_open": True, "open_time": "09:00", "close_time": "19:00"},
        }
        # Monday
        result = validate_day_and_time("2026-03-09", "10:00", hours, [])  # March 9 2026 = Monday
        assert result["valid"] is False
        assert "CLOSED" in result["error_code"]

    def test_validate_open_day(self):
        from business_context import validate_day_and_time
        hours = {
            0: {"is_open": True, "open_time": "09:00", "close_time": "19:00"},
        }
        # Monday open in this config
        result = validate_day_and_time("2026-03-09", "10:00", hours, [])
        assert result["valid"] is True

    def test_validate_closure_date(self):
        from business_context import validate_day_and_time
        hours = {1: {"is_open": True, "open_time": "09:00", "close_time": "19:00"}}
        closures = [{"date": "2026-03-10", "reason": "Holiday"}]
        result = validate_day_and_time("2026-03-10", "10:00", hours, closures)
        assert result["valid"] is False

    def test_generate_available_slots(self):
        from business_context import generate_available_slots
        hours = {1: {"is_open": True, "open_time": "09:00", "close_time": "12:00"}}
        # 30-min slots from 09:00 to 12:00 = 09:00, 09:30, 10:00, 10:30, 11:00, 11:30
        slots = generate_available_slots("09:00", "12:00", 30)
        assert "09:00" in slots
        assert "11:30" in slots
        assert "12:00" not in slots
```

**Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_business_context.py::TestDynamicBusinessHours -v`
Expected: FAIL

**Step 3: Implement**

Add to `business_context.py`:

```python
from datetime import datetime


def validate_day_and_time(date_str: str, time_str: str, hours: dict, closures: list) -> dict:
    """Validate if a date/time is within business hours.

    Replaces the hardcoded validate_business_day_and_time().
    """
    # Check closure dates
    for c in closures:
        if c["date"] == date_str:
            return {"valid": False, "error": f"Closed: {c['reason']}", "error_code": "CLOSED_SPECIAL"}

    # Check day of week
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    dow = dt.weekday()  # 0=Monday
    day_hours = hours.get(dow)

    if not day_hours or not day_hours["is_open"]:
        return {"valid": False, "error": "Closed on this day", "error_code": "CLOSED_DAY"}

    # Check time within range
    open_t = day_hours["open_time"]
    close_t = day_hours["close_time"]
    if open_t and close_t:
        if time_str < open_t or time_str >= close_t:
            return {"valid": False, "error": f"Outside hours ({open_t}-{close_t})", "error_code": "OUTSIDE_HOURS"}

    return {"valid": True}


def generate_available_slots(open_time: str, close_time: str, interval_minutes: int = 30) -> list:
    """Generate time slots from open to close at given interval."""
    slots = []
    h, m = map(int, open_time.split(":"))
    close_h, close_m = map(int, close_time.split(":"))
    close_total = close_h * 60 + close_m

    current = h * 60 + m
    while current < close_total:
        slots.append(f"{current // 60:02d}:{current % 60:02d}")
        current += interval_minutes

    return slots
```

**Step 4: Modify bot to use dynamic validation**

In `salon_bot_ec2_latest.py`, modify `validate_business_day_and_time()` and `get_available_slots()` to use the new functions with `biz_context`.

**Step 5: Run tests and commit**

```bash
git add business_context.py salon_bot_ec2_latest.py tests/test_business_context.py
git commit -m "feat: dynamic business hours validation and available slots from DB"
```

---

### Task 7: Per-Business Messaging + Google Calendar

**Files:**
- Modify: `salon_bot_ec2_latest.py:2583-2630` (send_whatsapp_message, mark_as_read)
- Modify: `salon_bot_ec2_latest.py:674-790` (Google Calendar functions)

**Step 1: Modify send_whatsapp_message to use per-business credentials**

```python
async def send_whatsapp_message(phone: str, message: str, business: dict):
    """Send WhatsApp message using business-specific credentials."""
    phone_number_id = business.get("whatsapp_phone_number_id", WHATSAPP_PHONE_NUMBER_ID)
    access_token = business.get("meta_access_token", WHATSAPP_ACCESS_TOKEN)

    url = f"https://graph.facebook.com/v18.0/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    # ... rest stays the same
```

**Step 2: Modify mark_as_read similarly**

```python
async def mark_as_read(message_id: str, business: dict):
    phone_number_id = business.get("whatsapp_phone_number_id", WHATSAPP_PHONE_NUMBER_ID)
    access_token = business.get("meta_access_token", WHATSAPP_ACCESS_TOKEN)
    url = f"https://graph.facebook.com/v18.0/{phone_number_id}/messages"
    # ...
```

**Step 3: Modify Google Calendar to use per-business config**

```python
def create_calendar_event(customer_name, service, date_str, time_str, customer_phone, business=None):
    """Create Google Calendar event using per-business config."""
    calendar_id = GOOGLE_CALENDAR_ID
    creds = None

    if business:
        calendar_id = business.get("google_calendar_id") or GOOGLE_CALENDAR_ID
        sa_json = business.get("google_service_account_json")
        if sa_json:
            import json
            sa_info = json.loads(sa_json) if isinstance(sa_json, str) else sa_json
            creds = service_account.Credentials.from_service_account_info(sa_info, scopes=GOOGLE_SCOPES)

    if not creds:
        # Fallback to file-based credentials
        if not GOOGLE_SERVICE_ACCOUNT_FILE.exists():
            return None
        creds = service_account.Credentials.from_service_account_file(str(GOOGLE_SERVICE_ACCOUNT_FILE), scopes=GOOGLE_SCOPES)

    # ... rest stays the same, use calendar_id variable
```

**Step 4: Commit**

```bash
git add salon_bot_ec2_latest.py
git commit -m "feat: per-business WhatsApp credentials and Google Calendar config"
```

---

### Task 8: Multi-Tenant Conversations + Reminders

**Files:**
- Modify: `salon_bot_ec2_latest.py:1128-1230` (conversation functions)
- Modify: `salon_bot_ec2_latest.py:1002-1090` (reminder functions)

**Step 1: Switch conversations to multi-tenant table**

Modify `load_conversation_history_from_db()`:

```python
def load_conversation_history_from_db(phone: str, business_id: int) -> list:
    """Load conversation history from multi-tenant conversations table."""
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT messages FROM conversations
               WHERE business_id = %s AND customer_phone = %s""",
            (business_id, phone),
        )
        row = cur.fetchone()
        if row and row[0]:
            return row[0][-10:]  # Last 10 messages
        return []
    finally:
        conn.close()
```

Modify `save_conversation_to_db()`:

```python
def save_conversation_to_db(phone, name, message, response, biz_context):
    """Save conversation to multi-tenant conversations table."""
    business_id = biz_context["business"]["id"]
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        new_entry = {"role": "user", "content": message}
        new_response = {"role": "assistant", "content": response}
        cur.execute(
            """INSERT INTO conversations (business_id, customer_phone, messages)
               VALUES (%s, %s, %s::jsonb)
               ON CONFLICT (business_id, customer_phone) DO UPDATE
               SET messages = conversations.messages || %s::jsonb || %s::jsonb,
                   updated_at = NOW()""",
            (business_id, phone,
             json.dumps([new_entry, new_response]),
             json.dumps([new_entry]),
             json.dumps([new_response])),
        )
        conn.commit()
    finally:
        conn.close()
```

**Step 2: Multi-tenant reminders**

Modify `send_reminder_messages()`:

```python
async def send_reminder_messages():
    """Send reminders for ALL businesses."""
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        # Get all active businesses
        cur.execute("SELECT id, name, whatsapp_phone_number_id, meta_access_token, timezone FROM businesses WHERE status = 'active'")
        businesses = cur.fetchall()
    finally:
        conn.close()

    for biz_id, biz_name, wa_phone_id, wa_token, tz_name in businesses:
        if not wa_phone_id:
            continue
        business = {"whatsapp_phone_number_id": wa_phone_id, "meta_access_token": wa_token, "name": biz_name}
        tz = pytz.timezone(tz_name or "Europe/Rome")
        tomorrow = (datetime.now(tz) + timedelta(days=1)).strftime("%Y-%m-%d")

        conn = get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """SELECT id, customer_phone, customer_name, appointment_time::text
                   FROM appointments
                   WHERE business_id = %s AND appointment_date = %s
                   AND status = 'confirmed'
                   AND (reminder_sent_at IS NULL OR reminder_sent_at < CURRENT_DATE)""",
                (biz_id, tomorrow),
            )
            appointments = cur.fetchall()
        finally:
            conn.close()

        for appt_id, phone, name, time in appointments:
            # ... send reminder, mark sent (same logic, but per-business)
            await send_whatsapp_message(phone, reminder_msg, business)
```

**Step 3: Commit**

```bash
git add salon_bot_ec2_latest.py
git commit -m "feat: multi-tenant conversations and per-business reminders"
```

---

### Task 9: Data Migration Script

**Files:**
- Create: `lyo-bot/database/migrations/003_migrate_old_data.sql`
- Create: `scripts/migrate_data.py`

**Step 1: Create SQL migration**

Create `lyo-bot/database/migrations/003_migrate_old_data.sql`:

```sql
-- Migrate salon_appointments → appointments for Aura (business_id = 1)
-- Run AFTER schema.sql and seed.sql have been applied

BEGIN;

-- Only migrate if salon_appointments exists and appointments is empty for business 1
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'salon_appointments') THEN
        INSERT INTO appointments (
            business_id, customer_phone, customer_name,
            treatment_code, treatment_name,
            appointment_date, appointment_time,
            duration_minutes, price, status,
            google_event_id, platform,
            reminder_sent_at, reminder_confirmed, reminder_confirmed_at,
            created_at
        )
        SELECT
            (SELECT id FROM businesses WHERE slug = 'aura-hair-studio'),
            customer_phone, customer_name,
            service_type,  -- maps to treatment_code
            COALESCE(
                (SELECT name_it FROM treatments t WHERE t.code = sa.service_type
                 AND t.business_id = (SELECT id FROM businesses WHERE slug = 'aura-hair-studio')),
                sa.service_type
            ),
            appointment_date, appointment_time,
            COALESCE(duration_minutes, 45), COALESCE(price, 0), status,
            google_event_id, COALESCE(platform, 'whatsapp'),
            reminder_sent_at, COALESCE(reminder_confirmed, false), reminder_confirmed_at,
            COALESCE(created_at, NOW())
        FROM salon_appointments sa
        ON CONFLICT DO NOTHING;

        RAISE NOTICE 'Migrated % rows from salon_appointments to appointments',
            (SELECT COUNT(*) FROM salon_appointments);
    END IF;
END $$;

-- Migrate salon_conversations → conversations
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'salon_conversations') THEN
        INSERT INTO conversations (business_id, customer_phone, messages, created_at, updated_at)
        SELECT
            (SELECT id FROM businesses WHERE slug = 'aura-hair-studio'),
            phone,
            jsonb_agg(jsonb_build_object(
                'role', 'user', 'content', COALESCE(message, ''),
                'response', COALESCE(response, '')
            ) ORDER BY timestamp),
            MIN(timestamp),
            MAX(timestamp)
        FROM salon_conversations
        GROUP BY phone
        ON CONFLICT (business_id, customer_phone) DO NOTHING;

        RAISE NOTICE 'Migrated conversations for % unique phones',
            (SELECT COUNT(DISTINCT phone) FROM salon_conversations);
    END IF;
END $$;

COMMIT;
```

**Step 2: Commit**

```bash
git add lyo-bot/database/migrations/003_migrate_old_data.sql
git commit -m "feat: add data migration from salon_appointments to multi-tenant appointments"
```

---

### Task 10: Remove Hardcoded Fallbacks + Cleanup

**Files:**
- Modify: `salon_bot_ec2_latest.py` — remove/deprecate `SALON_SERVICES` dict, old table references

**Step 1: Mark SALON_SERVICES as deprecated fallback**

```python
# DEPRECATED: Used only as fallback if no business found. Remove after migration.
SALON_SERVICES = { ... }
```

**Step 2: Remove hardcoded get_system_prompt**

Replace the old `get_system_prompt()` function body with:

```python
def get_system_prompt(biz_context=None):
    """Build system prompt — delegates to business_context module."""
    if biz_context:
        return build_system_prompt(biz_context)
    # Legacy fallback (remove after migration)
    return _legacy_system_prompt()

def _legacy_system_prompt():
    # Move old hardcoded prompt here as temporary fallback
    ...
```

**Step 3: Run full test suite**

Run: `python3 -m pytest tests/test_business_context.py -v`
Expected: All PASSED

**Step 4: Commit**

```bash
git add salon_bot_ec2_latest.py
git commit -m "refactor: deprecate hardcoded config, delegate to business_context"
```

---

## Phase 2: Embedded Signup

### Task 11: Meta Embedded Signup — Backend Callback

**Files:**
- Create: `lyo-bot/management/routes/onboarding.py`
- Modify: `lyo-bot/management/app.py`
- Test: `lyo-bot/tests/test_mgmt.py`

**Step 1: Write the failing test**

Add to `lyo-bot/tests/test_mgmt.py`:

```python
class TestEmbeddedSignupCallback:
    """POST /manage/api/whatsapp/callback saves phone_number_id to business."""

    @patch("management.routes.onboarding.get_connection")
    def test_saves_phone_number_id(self, mock_conn):
        mock_cur = MagicMock()
        mock_cur.rowcount = 1
        mock_conn.return_value.__enter__ = lambda s: MagicMock(cursor=lambda: mock_cur)
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)

        cookies = _auth_cookie()
        get_resp = client.get("/manage/login", cookies=cookies)
        all_cookies = dict(cookies)
        if get_resp.cookies.get("session"):
            all_cookies["session"] = get_resp.cookies.get("session")
        get_resp2 = client.get("/manage/dashboard", cookies=all_cookies)
        csrf_match = re.search(r'csrf-token" content="([^"]+)"', get_resp2.text)
        csrf_token = csrf_match.group(1) if csrf_match else ""
        if get_resp2.cookies.get("session"):
            all_cookies["session"] = get_resp2.cookies.get("session")

        resp = client.post(
            "/manage/api/whatsapp/callback",
            json={
                "phone_number_id": "123456789",
                "waba_id": "waba_999",
                "access_token": "EAAtoken...",
            },
            cookies=all_cookies,
            headers={"X-CSRF-Token": csrf_token, "Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "connected"

    def test_unauthenticated_returns_401(self):
        resp = client.post("/manage/api/whatsapp/callback", json={})
        assert resp.status_code in (401, 403)
```

**Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_mgmt.py::TestEmbeddedSignupCallback -v`
Expected: FAIL (404)

**Step 3: Implement the callback endpoint**

Create `lyo-bot/management/routes/onboarding.py`:

```python
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from management.auth import decode_token
from app.models.database import get_connection

router = APIRouter()


def _get_user(request: Request):
    token = request.cookies.get("access_token")
    if not token:
        return None
    return decode_token(token)


@router.post("/manage/api/whatsapp/callback")
async def whatsapp_callback(request: Request):
    """Receive Embedded Signup result — save WhatsApp credentials to business."""
    user = _get_user(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    try:
        body = await request.json()
    except (ValueError, TypeError):
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    phone_number_id = body.get("phone_number_id")
    waba_id = body.get("waba_id")
    access_token = body.get("access_token")

    if not phone_number_id:
        return JSONResponse({"error": "phone_number_id required"}, status_code=400)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """UPDATE businesses
               SET whatsapp_phone_number_id = %s,
                   waba_id = %s,
                   meta_access_token = %s,
                   updated_at = NOW()
               WHERE id = %s""",
            (phone_number_id, waba_id, access_token, user["business_id"]),
        )

        if cur.rowcount == 0:
            return JSONResponse({"error": "business not found"}, status_code=404)

    return JSONResponse({"status": "connected", "phone_number_id": phone_number_id})


@router.get("/manage/api/whatsapp/status")
async def whatsapp_status(request: Request):
    """Check if WhatsApp is connected for this business."""
    user = _get_user(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT whatsapp_phone_number_id FROM businesses WHERE id = %s",
            (user["business_id"],),
        )
        row = cur.fetchone()

    connected = bool(row and row[0])
    return JSONResponse({"connected": connected, "phone_number_id": row[0] if connected else None})
```

Register in `management/app.py`:

```python
from management.routes import ..., onboarding
mgmt_app.include_router(onboarding.router)
```

**Step 4: Run tests**

Run: `python3 -m pytest tests/test_mgmt.py::TestEmbeddedSignupCallback -v`
Expected: 2 PASSED

**Step 5: Commit**

```bash
git add lyo-bot/management/routes/onboarding.py lyo-bot/management/app.py lyo-bot/tests/test_mgmt.py
git commit -m "feat: add WhatsApp Embedded Signup callback endpoint"
```

---

### Task 12: Embedded Signup — Dashboard UI

**Files:**
- Modify: `lyo-bot/management/templates/settings.html` — add "Collega WhatsApp" section
- Test: `lyo-bot/tests/test_mgmt.py`

**Step 1: Write the failing test**

```python
class TestWhatsAppSettings:
    """Settings page shows WhatsApp connection status."""

    def test_settings_has_whatsapp_section(self):
        cookies = _auth_cookie()
        resp = client.get("/manage/settings", cookies=cookies)
        assert "WhatsApp" in resp.text

    def test_settings_shows_connect_button_when_not_connected(self):
        cookies = _auth_cookie()
        resp = client.get("/manage/settings", cookies=cookies)
        assert "Collega WhatsApp" in resp.text or "whatsapp" in resp.text.lower()
```

**Step 2: Run test, implement UI, verify**

Add a "WhatsApp Connection" section to `settings.html`:

```html
<div class="bg-white p-6 rounded-lg shadow mb-6">
    <h2 class="text-lg font-semibold text-indigo-600 mb-4">WhatsApp Business</h2>
    <div id="wa-status">
        <p class="text-gray-500 text-sm mb-4">Collega il tuo numero WhatsApp Business per attivare il bot.</p>
        <button id="wa-connect-btn"
                class="bg-green-600 text-white px-4 py-2 rounded hover:bg-green-700">
            Collega WhatsApp
        </button>
    </div>
</div>

<script>
// Check connection status on load
fetch('/manage/api/whatsapp/status')
    .then(r => r.json())
    .then(data => {
        if (data.connected) {
            document.getElementById('wa-status').innerHTML =
                '<p class="text-green-600 font-medium">✓ WhatsApp collegato</p>' +
                '<p class="text-gray-500 text-sm">ID: ' + data.phone_number_id + '</p>';
        }
    });

// Embedded Signup button (placeholder — actual Meta SDK integration requires Meta Tech Provider registration)
document.getElementById('wa-connect-btn')?.addEventListener('click', function() {
    alert('Meta Embedded Signup richiede la registrazione come Tech Provider. Contatta il supporto per la configurazione.');
});
</script>
```

**Step 3: Commit**

```bash
git add lyo-bot/management/templates/settings.html lyo-bot/tests/test_mgmt.py
git commit -m "feat: add WhatsApp connection UI to settings page"
```

---

## Post-Implementation Gates

After all 12 tasks pass:

1. **Gate 3 (Validate Claims):** Start bot, send test WhatsApp message, verify it routes correctly and creates appointment in `appointments` table
2. **Gate 6 (Dead Code):** Grep for remaining `salon_appointments` references (should only be in legacy fallback)
3. **Gate 7 (SE Practices):** Run code-reviewer agent
4. **Gate 8 (Engineer Brief):** Write PR summary

## Task Summary

| Task | Phase | What | Tests |
|------|-------|------|-------|
| 1 | P1 | Schema migration + seed | Manual SQL |
| 2 | P1 | Business context module | 6 tests |
| 3 | P1 | Webhook routing | 2 tests |
| 4 | P1 | Dynamic prompt + tools | 4 tests |
| 5 | P1 | Appointment CRUD refactor | 2 tests |
| 6 | P1 | Dynamic hours + slots | 4 tests |
| 7 | P1 | Per-business messaging + calendar | — (integration) |
| 8 | P1 | Conversations + reminders | — (integration) |
| 9 | P1 | Data migration script | Manual SQL |
| 10 | P1 | Cleanup deprecated code | — |
| 11 | P2 | Embedded Signup backend | 2 tests |
| 12 | P2 | Embedded Signup dashboard UI | 2 tests |
| **Total** | | | **~22 tests** |
