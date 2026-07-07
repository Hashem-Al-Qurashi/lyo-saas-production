# Lyo Multi-Tenant SaaS Platform - Design Document

**Date:** January 30, 2026
**Status:** Draft
**Author:** Planning Session

---

## Executive Summary

Transform the current single-tenant Lyo salon bot into a multi-tenant SaaS platform capable of serving 100+ clients. Each client (salon/business) will have their own WhatsApp number, customizable services, prices, and bot personality - all managed through a self-service dashboard.

---

## Table of Contents

1. [Current State](#1-current-state)
2. [Target State](#2-target-state)
3. [Architecture Overview](#3-architecture-overview)
4. [Client Onboarding Flow](#4-client-onboarding-flow)
5. [Dashboard Features](#5-dashboard-features)
6. [Database Schema](#6-database-schema)
7. [Bot Changes](#7-bot-changes)
8. [WhatsApp Integration](#8-whatsapp-integration)
9. [Google Calendar Integration](#9-google-calendar-integration)
10. [Infrastructure](#10-infrastructure)
11. [Implementation Phases](#11-implementation-phases)
12. [Pricing Model](#12-pricing-model)

---

## 1. Current State

### What Exists Today

- **Single-tenant bot** hardcoded for "Aura Hair Studio"
- **Hardcoded configuration:**
  - Business name, address, phone
  - 7 services with fixed prices
  - Business hours (Tue-Sat)
  - Bot personality ("Simone")
- **Single WhatsApp number** (961636900357709)
- **Single Google Calendar** connection
- **Single PostgreSQL database** on AWS RDS

### Current Architecture

```
One WhatsApp Number → One Bot Instance → One Database → One Calendar
```

### Limitations

- Cannot serve multiple businesses
- Every new client requires code changes
- No self-service configuration
- No client isolation

---

## 2. Target State

### Multi-Tenant SaaS Platform

```
┌─────────────────────────────────────────────────────────────────────┐
│                    MULTI-TENANT ARCHITECTURE                        │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  Client A          Client B          Client C          Client N    │
│  (Salon Roma)      (Aura Milano)     (Beauty Bar)      (...)       │
│      │                 │                 │                │        │
│      ▼                 ▼                 ▼                ▼        │
│  ┌────────┐        ┌────────┐        ┌────────┐        ┌────────┐ │
│  │WhatsApp│        │WhatsApp│        │WhatsApp│        │WhatsApp│ │
│  │+39 111 │        │+39 222 │        │+39 333 │        │+39 NNN │ │
│  └────┬───┘        └────┬───┘        └────┬───┘        └────┬───┘ │
│       │                 │                 │                 │      │
│       └────────────────┼─────────────────┼─────────────────┘      │
│                        │                 │                         │
│                        ▼                 ▼                         │
│              ┌─────────────────────────────────────┐               │
│              │         SINGLE BOT INSTANCE          │               │
│              │                                      │               │
│              │  • Routes by WhatsApp Phone ID      │               │
│              │  • Loads client config from DB       │               │
│              │  • Builds dynamic prompts            │               │
│              │  • Isolates data per client          │               │
│              └─────────────────────────────────────┘               │
│                                │                                    │
│                                ▼                                    │
│              ┌─────────────────────────────────────┐               │
│              │         SHARED DATABASE              │               │
│              │                                      │               │
│              │  • businesses (client configs)      │               │
│              │  • services (per business)          │               │
│              │  • appointments (per business)      │               │
│              │  • conversations (per business)     │               │
│              └─────────────────────────────────────┘               │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### Key Capabilities

| Feature | Description |
|---------|-------------|
| **Multi-tenant** | One codebase serves unlimited clients |
| **Self-service dashboard** | Clients configure everything themselves |
| **Client brings WhatsApp** | Each client connects their own number |
| **Custom services/prices** | Each client defines their menu |
| **Custom bot personality** | Bot name, tone, language per client |
| **Isolated data** | Each client only sees their data |
| **Shared infrastructure** | One AWS setup, scales horizontally |

---

## 3. Architecture Overview

### System Components

```
┌─────────────────────────────────────────────────────────────────────┐
│                         PLATFORM ARCHITECTURE                        │
└─────────────────────────────────────────────────────────────────────┘

                         ┌──────────────────┐
                         │   CLIENTS        │
                         │   (Browsers)     │
                         └────────┬─────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         DASHBOARD (Web App)                          │
│                                                                      │
│  • Client registration & login                                       │
│  • Business configuration                                            │
│  • Service/price management                                          │
│  • WhatsApp OAuth connection (Embedded Signup)                       │
│  • Google Calendar OAuth connection                                  │
│  • Analytics & reporting                                             │
│  • Appointment management                                            │
│                                                                      │
│  Tech: Next.js (React + API routes) or FastAPI + React              │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  │ Reads/Writes
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         DATABASE (PostgreSQL)                        │
│                                                                      │
│  • businesses (client accounts & config)                            │
│  • services (per-business service menu)                             │
│  • business_hours (per-business hours)                              │
│  • appointments (all bookings, filtered by business_id)             │
│  • conversations (chat history, filtered by business_id)            │
│  • integrations (WhatsApp & Calendar credentials per business)      │
└─────────────────────────────────────────────────────────────────────┘
                                  ▲
                                  │ Reads config, Writes appointments
                                  │
┌─────────────────────────────────────────────────────────────────────┐
│                         BOT ENGINE (FastAPI)                         │
│                                                                      │
│  Webhook: POST /webhook                                              │
│  1. Receive WhatsApp message                                         │
│  2. Extract phone_number_id → lookup business_id                    │
│  3. Load business config (name, bot personality, services, hours)   │
│  4. Build dynamic system prompt                                      │
│  5. Call OpenAI GPT-4o with business-specific context               │
│  6. Execute tools (create_appointment, etc.) with business_id       │
│  7. Send response via WhatsApp                                       │
│                                                                      │
│  Tech: Python FastAPI (existing bot, modified)                       │
└─────────────────────────────────────────────────────────────────────┘
                                  │
                                  │ API Calls
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         EXTERNAL SERVICES                            │
│                                                                      │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                 │
│  │   OpenAI    │  │  WhatsApp   │  │   Google    │                 │
│  │   GPT-4o    │  │  Cloud API  │  │  Calendar   │                 │
│  └─────────────┘  └─────────────┘  └─────────────┘                 │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 4. Client Onboarding Flow

### Step-by-Step Process

```
┌─────────────────────────────────────────────────────────────────────┐
│                    CLIENT ONBOARDING JOURNEY                         │
└─────────────────────────────────────────────────────────────────────┘

STEP 1: REGISTRATION
────────────────────
Client visits: dashboard.lyobot.com/signup
        │
        ▼
┌─────────────────────────────────┐
│ Create Account                  │
│                                 │
│ Email: [________________]       │
│ Password: [________________]    │
│ Business Name: [____________]   │
│                                 │
│ [Create Account]                │
└─────────────────────────────────┘
        │
        ▼
Account created, email verified


STEP 2: BUSINESS SETUP
──────────────────────
Client configures their business:
        │
        ▼
┌─────────────────────────────────┐
│ Business Details                │
│                                 │
│ Name: [Salon Roma           ]   │
│ Address: [Via Roma 45, Milano]  │
│ Phone: [+39 02 1234567      ]   │
│ Email: [info@salonroma.it   ]   │
│                                 │
│ [Save & Continue]               │
└─────────────────────────────────┘


STEP 3: BOT PERSONALITY
───────────────────────
Client customizes their bot:
        │
        ▼
┌─────────────────────────────────┐
│ Bot Configuration               │
│                                 │
│ Bot Name: [Sofia            ]   │
│                                 │
│ Tone: ○ Professional            │
│       ● Friendly                │
│       ○ Casual                  │
│                                 │
│ Primary Language:               │
│       ● Italian                 │
│       ○ English                 │
│       ○ Both                    │
│                                 │
│ [Save & Continue]               │
└─────────────────────────────────┘


STEP 4: SERVICES
────────────────
Client adds their services:
        │
        ▼
┌─────────────────────────────────────────────────────┐
│ Your Services                              [+ Add]  │
│                                                     │
│ ┌─────────────────┬─────────┬──────────┬────────┐ │
│ │ Service         │ Price   │ Duration │ Action │ │
│ ├─────────────────┼─────────┼──────────┼────────┤ │
│ │ Taglio Donna    │ €55     │ 45 min   │ ✏️ 🗑️  │ │
│ │ Taglio Uomo     │ €35     │ 30 min   │ ✏️ 🗑️  │ │
│ │ Colore          │ €80     │ 90 min   │ ✏️ 🗑️  │ │
│ └─────────────────┴─────────┴──────────┴────────┘ │
│                                                     │
│ [Save & Continue]                                   │
└─────────────────────────────────────────────────────┘


STEP 5: BUSINESS HOURS
──────────────────────
Client sets their hours:
        │
        ▼
┌─────────────────────────────────────────────────────┐
│ Business Hours                                      │
│                                                     │
│ Monday:    [Closed           ▼]                     │
│ Tuesday:   [09:00 ▼] - [18:00 ▼]                   │
│ Wednesday: [09:00 ▼] - [18:00 ▼]                   │
│ Thursday:  [09:00 ▼] - [18:00 ▼]                   │
│ Friday:    [09:00 ▼] - [18:00 ▼]                   │
│ Saturday:  [09:00 ▼] - [17:00 ▼]                   │
│ Sunday:    [Closed           ▼]                     │
│                                                     │
│ [Save & Continue]                                   │
└─────────────────────────────────────────────────────┘


STEP 6: CONNECT WHATSAPP
────────────────────────
Client connects their WhatsApp Business:
        │
        ▼
┌─────────────────────────────────────────────────────┐
│ Connect WhatsApp                                    │
│                                                     │
│ Click below to connect your WhatsApp Business      │
│ account. You'll be redirected to Facebook to       │
│ authorize the connection.                          │
│                                                     │
│ ┌─────────────────────────────────────────────┐   │
│ │  🟢 Connect WhatsApp Business               │   │
│ └─────────────────────────────────────────────┘   │
│                                                     │
│ Don't have WhatsApp Business API?                  │
│ [Learn how to set it up →]                         │
└─────────────────────────────────────────────────────┘
        │
        ▼
Meta OAuth popup → Client logs in → Selects phone → Credentials returned
        │
        ▼
┌─────────────────────────────────────────────────────┐
│ WhatsApp Connected! ✅                              │
│                                                     │
│ Phone: +39 333 111 2222                            │
│ Status: Active                                      │
│                                                     │
│ [Continue to Calendar →]                            │
└─────────────────────────────────────────────────────┘


STEP 7: CONNECT CALENDAR (Optional)
───────────────────────────────────
Client connects Google Calendar:
        │
        ▼
┌─────────────────────────────────────────────────────┐
│ Connect Calendar (Optional)                         │
│                                                     │
│ Sync appointments automatically to Google Calendar  │
│                                                     │
│ ┌─────────────────────────────────────────────┐   │
│ │  📅 Connect Google Calendar                 │   │
│ └─────────────────────────────────────────────┘   │
│                                                     │
│ [Skip for now]                                      │
└─────────────────────────────────────────────────────┘


STEP 8: GO LIVE!
────────────────
        │
        ▼
┌─────────────────────────────────────────────────────┐
│ 🎉 You're All Set!                                  │
│                                                     │
│ Your WhatsApp bot is now live!                     │
│                                                     │
│ Phone: +39 333 111 2222                            │
│ Bot Name: Sofia                                     │
│ Services: 3 configured                              │
│                                                     │
│ Test it now: Send "Ciao" to your WhatsApp number   │
│                                                     │
│ [Go to Dashboard]                                   │
└─────────────────────────────────────────────────────┘
```

---

## 5. Dashboard Features

### Main Dashboard Sections

#### 5.1 Overview / Home

```
┌─────────────────────────────────────────────────────────────────────┐
│ 📊 Dashboard                                    Salon Roma          │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌───────────┐ │
│  │ Today       │  │ This Week   │  │ This Month  │  │ Revenue   │ │
│  │ 5 bookings  │  │ 23 bookings │  │ 87 bookings │  │ €3,450    │ │
│  │ ↑ 2 vs yday │  │ ↑ 5 vs last │  │ ↑ 12% vs    │  │ ↑ 8%      │ │
│  └─────────────┘  └─────────────┘  └─────────────┘  └───────────┘ │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ Today's Appointments                                         │   │
│  ├──────────┬────────────────┬────────────────┬────────────────┤   │
│  │ Time     │ Customer       │ Service        │ Status         │   │
│  ├──────────┼────────────────┼────────────────┼────────────────┤   │
│  │ 09:00    │ Maria Rossi    │ Taglio Donna   │ ✅ Confirmed   │   │
│  │ 10:00    │ Giovanni B.    │ Taglio Uomo    │ ⏳ Pending     │   │
│  │ 11:30    │ Sara Verdi     │ Colore         │ ✅ Confirmed   │   │
│  └──────────┴────────────────┴────────────────┴────────────────┘   │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

#### 5.2 Appointments

- Calendar view (day/week/month)
- List view with filters
- Create manual appointment
- Edit/cancel appointments
- Export to CSV

#### 5.3 Services

- Add/edit/delete services
- Set prices and durations
- Enable/disable services
- Reorder display order

#### 5.4 Business Hours

- Set hours per day
- Holiday closures
- Special hours

#### 5.5 Bot Settings

- Bot name and personality
- Language settings
- Custom greetings
- Auto-replies when closed

#### 5.6 Integrations

- WhatsApp connection status
- Google Calendar connection
- Disconnect/reconnect options

#### 5.7 Analytics

- Booking trends
- Popular services
- Peak hours
- Customer retention

#### 5.8 Settings

- Business profile
- Account settings
- Billing (if applicable)
- Team members (future)

---

## 6. Database Schema

### Multi-Tenant Schema

```sql
-- =====================================================
-- BUSINESSES (Client accounts)
-- =====================================================
CREATE TABLE businesses (
    id SERIAL PRIMARY KEY,

    -- Account
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    email_verified BOOLEAN DEFAULT FALSE,

    -- Business Info
    name VARCHAR(255) NOT NULL,
    address TEXT,
    phone VARCHAR(50),
    contact_email VARCHAR(255),
    timezone VARCHAR(50) DEFAULT 'Europe/Rome',

    -- Bot Personality
    bot_name VARCHAR(100) DEFAULT 'Assistant',
    bot_tone VARCHAR(50) DEFAULT 'friendly',  -- friendly, professional, casual
    primary_language VARCHAR(10) DEFAULT 'it',  -- it, en, both

    -- Status
    status VARCHAR(20) DEFAULT 'active',  -- active, suspended, trial
    plan VARCHAR(50) DEFAULT 'standard',

    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- =====================================================
-- SERVICES (Per-business service menu)
-- =====================================================
CREATE TABLE services (
    id SERIAL PRIMARY KEY,
    business_id INTEGER REFERENCES businesses(id) ON DELETE CASCADE,

    -- Service Details
    name VARCHAR(255) NOT NULL,
    name_en VARCHAR(255),  -- English name (optional)
    description TEXT,
    price DECIMAL(10,2) NOT NULL,
    duration INTEGER NOT NULL,  -- in minutes

    -- Settings
    is_active BOOLEAN DEFAULT TRUE,
    display_order INTEGER DEFAULT 0,

    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_services_business ON services(business_id);

-- =====================================================
-- BUSINESS HOURS
-- =====================================================
CREATE TABLE business_hours (
    id SERIAL PRIMARY KEY,
    business_id INTEGER REFERENCES businesses(id) ON DELETE CASCADE,

    day_of_week INTEGER NOT NULL,  -- 0=Monday, 6=Sunday
    is_closed BOOLEAN DEFAULT FALSE,
    open_time TIME,
    close_time TIME,

    UNIQUE(business_id, day_of_week)
);

CREATE INDEX idx_hours_business ON business_hours(business_id);

-- =====================================================
-- HOLIDAY CLOSURES
-- =====================================================
CREATE TABLE holiday_closures (
    id SERIAL PRIMARY KEY,
    business_id INTEGER REFERENCES businesses(id) ON DELETE CASCADE,

    closure_date DATE NOT NULL,
    reason VARCHAR(255),

    UNIQUE(business_id, closure_date)
);

-- =====================================================
-- INTEGRATIONS (WhatsApp, Calendar credentials)
-- =====================================================
CREATE TABLE integrations (
    id SERIAL PRIMARY KEY,
    business_id INTEGER REFERENCES businesses(id) ON DELETE CASCADE,

    -- Integration Type
    type VARCHAR(50) NOT NULL,  -- 'whatsapp', 'google_calendar'

    -- WhatsApp Credentials
    whatsapp_phone_number_id VARCHAR(100),
    whatsapp_access_token TEXT,
    whatsapp_business_id VARCHAR(100),

    -- Google Calendar Credentials
    google_refresh_token TEXT,
    google_access_token TEXT,
    google_token_expiry TIMESTAMP,
    google_calendar_id VARCHAR(255),

    -- Status
    is_connected BOOLEAN DEFAULT FALSE,
    connected_at TIMESTAMP,
    last_error TEXT,

    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(business_id, type)
);

CREATE INDEX idx_integrations_business ON integrations(business_id);
CREATE INDEX idx_integrations_whatsapp ON integrations(whatsapp_phone_number_id);

-- =====================================================
-- APPOINTMENTS (Multi-tenant)
-- =====================================================
CREATE TABLE appointments (
    id SERIAL PRIMARY KEY,
    business_id INTEGER REFERENCES businesses(id) ON DELETE CASCADE,

    -- Customer Info
    customer_phone VARCHAR(50) NOT NULL,
    customer_name VARCHAR(255) NOT NULL,

    -- Appointment Details
    service_id INTEGER REFERENCES services(id),
    service_name VARCHAR(255),  -- Denormalized for history
    service_price DECIMAL(10,2),
    service_duration INTEGER,

    appointment_date DATE NOT NULL,
    appointment_time TIME NOT NULL,

    -- Status
    status VARCHAR(20) DEFAULT 'confirmed',  -- confirmed, cancelled, completed, no_show

    -- Calendar Integration
    calendar_event_id VARCHAR(255),

    -- Reminders
    reminder_sent BOOLEAN DEFAULT FALSE,
    reminder_confirmed BOOLEAN DEFAULT FALSE,

    -- Escalation
    escalated BOOLEAN DEFAULT FALSE,
    escalation_reason TEXT,

    -- Notes
    notes TEXT,

    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Prevent double booking
    UNIQUE(business_id, appointment_date, appointment_time, status)
);

CREATE INDEX idx_appointments_business ON appointments(business_id);
CREATE INDEX idx_appointments_date ON appointments(appointment_date);
CREATE INDEX idx_appointments_phone ON appointments(customer_phone);

-- =====================================================
-- CONVERSATIONS (Chat history per business)
-- =====================================================
CREATE TABLE conversations (
    id SERIAL PRIMARY KEY,
    business_id INTEGER REFERENCES businesses(id) ON DELETE CASCADE,

    customer_phone VARCHAR(50) NOT NULL,
    customer_name VARCHAR(255),
    message TEXT NOT NULL,
    response TEXT NOT NULL,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_conversations_business ON conversations(business_id);
CREATE INDEX idx_conversations_phone ON conversations(customer_phone);

-- =====================================================
-- BLOCKED CHATS (Per business)
-- =====================================================
CREATE TABLE blocked_chats (
    id SERIAL PRIMARY KEY,
    business_id INTEGER REFERENCES businesses(id) ON DELETE CASCADE,

    customer_phone VARCHAR(50) NOT NULL,
    reason TEXT,
    blocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(business_id, customer_phone)
);
```

---

## 7. Bot Changes

### Current vs New Architecture

| Aspect | Current (Single-Tenant) | New (Multi-Tenant) |
|--------|------------------------|-------------------|
| Config | Hardcoded in code | Loaded from DB per request |
| Prompt | Static `get_system_prompt()` | Dynamic `get_system_prompt(business_id)` |
| Services | `SALON_SERVICES` dict | `services` table query |
| Hours | Hardcoded | `business_hours` table query |
| Appointments | One table | Filtered by `business_id` |
| WhatsApp | One token | Token lookup by `phone_number_id` |
| Calendar | One token | Token lookup by `business_id` |

### Key Code Changes

#### 1. Route by WhatsApp Phone Number ID

```python
@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()

    # Extract the phone_number_id from webhook
    phone_number_id = data["entry"][0]["changes"][0]["value"]["metadata"]["phone_number_id"]

    # Look up which business this belongs to
    business = get_business_by_whatsapp_id(phone_number_id)

    if not business:
        logger.error(f"Unknown phone_number_id: {phone_number_id}")
        return {"status": "ignored"}

    # Process with business context
    await process_message(data, business.id)
```

#### 2. Dynamic System Prompt

```python
def get_system_prompt(business_id: int) -> str:
    """Build system prompt dynamically from database"""

    # Load business config
    business = db.get_business(business_id)
    services = db.get_services(business_id)
    hours = db.get_business_hours(business_id)

    # Format services list
    services_text = "\n".join([
        f"- {s.name}: €{s.price} ({s.duration} min)"
        for s in services
    ])

    # Format hours
    hours_text = format_business_hours(hours)

    return f"""You are {business.bot_name}, an employee at {business.name}.

📍 BUSINESS INFO:
- Name: {business.name}
- Address: {business.address}
- Phone: {business.phone}

💇 SERVICES:
{services_text}

🕐 BUSINESS HOURS:
{hours_text}

🌐 LANGUAGE: Reply in {business.primary_language}

... (rest of prompt rules)
"""
```

#### 3. Business-Scoped Database Operations

```python
def create_appointment(business_id: int, customer_phone: str, ...):
    """All operations include business_id"""

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO appointments
        (business_id, customer_phone, customer_name, service_id, ...)
        VALUES (%s, %s, %s, %s, ...)
    """, (business_id, customer_phone, customer_name, service_id, ...))
```

---

## 8. WhatsApp Integration

### Embedded Signup Flow (Recommended)

Meta provides "Embedded Signup" - an OAuth-like flow for WhatsApp Business API.

```
┌─────────────────────────────────────────────────────────────────────┐
│                    WHATSAPP EMBEDDED SIGNUP                         │
└─────────────────────────────────────────────────────────────────────┘

1. Client clicks "Connect WhatsApp" in dashboard
        │
        ▼
2. Dashboard opens Meta's Embedded Signup popup
   URL: https://www.facebook.com/v18.0/dialog/oauth
   Parameters:
   - client_id: YOUR_APP_ID
   - redirect_uri: https://dashboard.lyobot.com/callback/whatsapp
   - scope: whatsapp_business_management,whatsapp_business_messaging
        │
        ▼
3. Client logs into Facebook Business
        │
        ▼
4. Client selects/creates WhatsApp Business Account
        │
        ▼
5. Client selects phone number
        │
        ▼
6. Meta redirects back with authorization code
        │
        ▼
7. Your server exchanges code for:
   - Access Token (long-lived)
   - Phone Number ID
   - WhatsApp Business Account ID
        │
        ▼
8. Store credentials in integrations table
        │
        ▼
9. Configure webhook URL for this phone to point to your server
        │
        ▼
10. Done! Messages to this number now route to your bot
```

### Webhook Configuration

All clients' WhatsApp webhooks point to the same URL:

```
https://api.lyobot.com/webhook
```

The bot identifies the client by the `phone_number_id` in the webhook payload.

---

## 9. Google Calendar Integration

### OAuth Flow

```
1. Client clicks "Connect Google Calendar"
        │
        ▼
2. Redirect to Google OAuth consent screen
   Scopes: https://www.googleapis.com/auth/calendar
        │
        ▼
3. Client authorizes
        │
        ▼
4. Google returns authorization code
        │
        ▼
5. Exchange code for refresh_token + access_token
        │
        ▼
6. Store tokens in integrations table for this business
        │
        ▼
7. Done! Bot creates calendar events using stored tokens
```

### Token Refresh

Access tokens expire. The bot automatically refreshes using the stored refresh_token:

```python
def get_calendar_service(business_id: int):
    integration = db.get_integration(business_id, 'google_calendar')

    creds = Credentials(
        token=integration.google_access_token,
        refresh_token=integration.google_refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET
    )

    if creds.expired:
        creds.refresh(Request())
        # Update stored tokens
        db.update_integration_tokens(business_id, creds)

    return build('calendar', 'v3', credentials=creds)
```

---

## 10. Infrastructure

### AWS Architecture (Scaled)

```
┌─────────────────────────────────────────────────────────────────────┐
│                         AWS INFRASTRUCTURE                           │
└─────────────────────────────────────────────────────────────────────┘

                         Internet
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    Application Load Balancer                         │
│                                                                      │
│  • SSL termination (*.lyobot.com)                                   │
│  • Routes /webhook/* → Bot Target Group                             │
│  • Routes /dashboard/* → Dashboard Target Group                     │
│  • Routes /api/* → API Target Group                                 │
└─────────────────────────────────────────────────────────────────────┘
                            │
            ┌───────────────┼───────────────┐
            │               │               │
            ▼               ▼               ▼
┌───────────────┐  ┌───────────────┐  ┌───────────────┐
│  Bot Service  │  │   Dashboard   │  │  API Service  │
│   (FastAPI)   │  │   (Next.js)   │  │  (FastAPI)    │
│               │  │               │  │               │
│  • Webhook    │  │  • Frontend   │  │  • Auth       │
│  • AI/OpenAI  │  │  • SSR pages  │  │  • CRUD ops   │
│  • WhatsApp   │  │               │  │  • OAuth      │
└───────────────┘  └───────────────┘  └───────────────┘
            │               │               │
            └───────────────┼───────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    RDS PostgreSQL (Multi-AZ)                         │
│                                                                      │
│  • All tables with business_id foreign keys                         │
│  • Encrypted at rest                                                │
│  • Automated backups                                                │
└─────────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    ElastiCache Redis (Optional)                      │
│                                                                      │
│  • Session caching                                                  │
│  • Rate limiting                                                    │
│  • Message buffering                                                │
└─────────────────────────────────────────────────────────────────────┘
```

### Scaling Considerations

| 10 Clients | 100 Clients | 500+ Clients |
|------------|-------------|--------------|
| 1 EC2 t3.small | 2 EC2 t3.medium | ECS Fargate auto-scaling |
| Single RDS | RDS with read replicas | RDS Multi-AZ + read replicas |
| No cache | Redis for sessions | Redis cluster |
| ~$50/month | ~$150/month | ~$400+/month |

---

## 11. Implementation Phases

### Phase 1: Multi-Tenant Bot (2-3 weeks)

**Goal:** Bot can serve multiple clients from database config

- [ ] Database schema migration
- [ ] Route by `phone_number_id`
- [ ] Dynamic prompt generation
- [ ] Business-scoped all operations
- [ ] Manual client setup (SQL inserts)
- [ ] Test with 2-3 pilot clients

### Phase 2: Admin Dashboard MVP (3-4 weeks)

**Goal:** Clients can configure their bot via web UI

- [ ] Dashboard framework setup (Next.js)
- [ ] Authentication (login/register)
- [ ] Business profile management
- [ ] Services CRUD
- [ ] Business hours management
- [ ] Basic analytics

### Phase 3: Integrations (2-3 weeks)

**Goal:** Self-service WhatsApp and Calendar connection

- [ ] WhatsApp Embedded Signup integration
- [ ] Google Calendar OAuth flow
- [ ] Connection status display
- [ ] Disconnect/reconnect functionality

### Phase 4: Polish & Scale (2+ weeks)

**Goal:** Production-ready for 100+ clients

- [ ] Billing integration (Stripe)
- [ ] Onboarding wizard
- [ ] Email notifications
- [ ] Advanced analytics
- [ ] Documentation & help center
- [ ] Performance optimization

---

## 12. Pricing Model

### Suggested SaaS Pricing

| Plan | Price/Month | Features |
|------|-------------|----------|
| **Starter** | €49/month | 1 WhatsApp number, 100 bookings/month, Email support |
| **Professional** | €99/month | 1 WhatsApp number, Unlimited bookings, Calendar sync, Priority support |
| **Business** | €199/month | Multiple numbers, Team access, Custom branding, Phone support |

### Revenue Projections

| Clients | Mix | Monthly Revenue |
|---------|-----|-----------------|
| 10 | 7 Starter, 3 Pro | €643 |
| 50 | 30 Starter, 15 Pro, 5 Business | €3,950 |
| 100 | 50 Starter, 35 Pro, 15 Business | €8,400 |
| 200 | 100 Starter, 70 Pro, 30 Business | €16,800 |

---

## Appendix: Tech Stack Summary

| Component | Technology | Reason |
|-----------|------------|--------|
| Bot Backend | Python FastAPI | Existing codebase, async support |
| Dashboard Frontend | Next.js (React) | SSR, API routes, fast development |
| Dashboard Backend | Next.js API routes or FastAPI | Depends on team skills |
| Database | PostgreSQL | Relational, proven, on AWS RDS |
| Cache | Redis (optional) | Sessions, rate limiting |
| Hosting | AWS (EC2/ECS, RDS, ALB) | Existing infrastructure |
| Auth | NextAuth.js or custom JWT | Standard patterns |
| Payments | Stripe | Industry standard |

---

*Document created: January 30, 2026*
*Status: Ready for review*
