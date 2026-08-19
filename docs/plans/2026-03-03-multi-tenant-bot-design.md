# Multi-Tenant Bot + Self-Service Onboarding Design

**Date:** 2026-03-03
**Status:** Approved
**Approach:** Refactor existing production bot to multi-tenant + add Meta Embedded Signup

## Problem

The production bot (`salon_bot_ec2_latest.py`) is hardcoded for one salon (Aura Hair Studio). Services, persona, hours, and WhatsApp credentials are all hardcoded. The management dashboard and calendar read from a multi-tenant `appointments` table with `business_id`, but the bot writes to a single-tenant `salon_appointments` table. New salons can't be added without duplicating the bot.

## Goal

One deployment, one webhook, unlimited salons. New salon owners connect their WhatsApp number in a few clicks and immediately get a working booking bot + management dashboard + calendar.

## Architecture

### Data Flow

```
Salon owner → "Collega WhatsApp" in dashboard
  → Meta Embedded Signup OAuth popup
  → Meta returns phone_number_id + waba_id + access_token
  → Saved to businesses table
  → Webhook auto-subscribed

Customer → messages salon's WhatsApp number
  → Meta → POST /webhook (single endpoint)
  → Bot extracts phone_number_id from payload metadata
  → Looks up businesses table → gets business context
  → Loads treatments, hours, persona from DB
  → AI responds with that salon's personality/services
  → Appointment saved to appointments table (with business_id)
  → Shows up in that salon's calendar dashboard
```

### Key Principle

One Meta App, one webhook URL, one bot deployment. All salons share the same infrastructure. Business isolation via `business_id` in every query.

## Phase 1: Bot Multi-Tenant Refactor

### Database Changes

```sql
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS whatsapp_phone_number_id VARCHAR(50);
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS waba_id VARCHAR(50);
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS meta_access_token TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_businesses_phone_number_id ON businesses(whatsapp_phone_number_id);
```

### Bot Changes (salon_bot_ec2_latest.py)

1. **Business lookup at webhook entry:**
   - Extract `phone_number_id` from `value.metadata.phone_number_id` (already in payload, currently ignored)
   - `SELECT * FROM businesses WHERE whatsapp_phone_number_id = %s`
   - If not found → log warning, ignore message
   - Cache business context per request (not across requests — DB is source of truth)

2. **Dynamic services (replace SALON_SERVICES dict):**
   - `SELECT code, name_it, name_en, price, duration_minutes FROM treatments WHERE business_id = %s AND is_active = true`
   - Build same dict structure the AI tools expect
   - Fallback: if no treatments configured, return error to salon owner

3. **Dynamic persona (replace hardcoded "Simone at Aura"):**
   - Use `businesses.bot_name`, `businesses.bot_persona`, `businesses.name`, `businesses.address`
   - Build system prompt dynamically per business
   - Keep the booking flow rules (STEP 1, STEP 2, etc.) — just swap identity/services

4. **Dynamic business hours (replace hardcoded Mon/Sun closed):**
   - `SELECT day_of_week, is_open, open_time, close_time FROM business_hours WHERE business_id = %s`
   - Also check `business_closures` table for holidays
   - Generate date calendar using actual hours instead of hardcoded

5. **Write to `appointments` table (not `salon_appointments`):**
   - Add `business_id`, `treatment_code`, `treatment_name`, `operator_id` to INSERT
   - Operator assignment: for now, NULL (auto-assign later). Or first available active operator.
   - Dashboard calendar immediately shows these appointments

6. **Send messages via correct phone_number_id:**
   - `url = f"https://graph.facebook.com/v18.0/{business.whatsapp_phone_number_id}/messages"`
   - `Authorization: Bearer {business.meta_access_token}`

7. **Per-business Google Calendar:**
   - `businesses.google_calendar_id` (already in schema)
   - `businesses.google_service_account_json` (already in schema)
   - If not configured → skip Google Calendar sync (graceful degradation)

8. **Conversations table:**
   - Change from `salon_conversations` → `conversations` with `business_id`
   - Conversation lookup: `WHERE business_id = %s AND customer_phone = %s`

### What Stays Unchanged

- Message batching logic
- AI tool calling flow (OpenAI function calling)
- Conversation memory pattern
- Reminder/scheduler system
- Instagram webhook (separate concern, Phase 2+)
- Error handling and logging patterns

### Migration: Existing Aura Salon

1. Create `businesses` row for Aura: `{name: "Aura Hair Studio", whatsapp_phone_number_id: "961636900357709", ...}`
2. Create `treatments` rows from current `SALON_SERVICES` dict
3. Create `business_hours` rows from current hardcoded schedule
4. Create `operators` rows (even if just one default operator)
5. Migrate `salon_appointments` → `appointments` with `business_id = 1`
6. Migrate `salon_conversations` → `conversations` with `business_id = 1`

## Phase 2: Embedded Signup (Self-Service Onboarding)

### Meta Setup (one-time)

- Register as Meta Tech Provider / Solution Partner
- Configure Embedded Signup in Meta App Dashboard
- Set webhook URL + verify token
- Enable WhatsApp Business Management API permissions

### Dashboard Onboarding Flow

1. **Sign up page** (`/manage/signup/`):
   - Email, password, salon name, timezone
   - Creates `management_users` + `businesses` records
   - Redirects to dashboard

2. **Connect WhatsApp** (`/manage/settings/` or dedicated page):
   - "Collega WhatsApp" button
   - Loads Meta Embedded Signup JS SDK
   - Opens Facebook OAuth popup
   - Owner logs into Facebook Business, selects/creates WhatsApp number
   - Callback receives: `phone_number_id`, `waba_id`, access token
   - Saves to `businesses` table
   - Meta auto-subscribes webhook

3. **Configure salon:**
   - Add treatments (already exists in dashboard)
   - Add operators (already exists)
   - Set business hours (already exists)
   - Customize bot persona (new: settings page field)

### API Endpoints

```
POST /manage/api/whatsapp/callback  → Receive Embedded Signup result
GET  /manage/api/whatsapp/status    → Check if WhatsApp is connected
```

## Phase 3: Data Migration

- Script to migrate `salon_appointments` → `appointments`
- Script to migrate `salon_conversations` → `conversations`
- Verify data integrity
- Drop old tables after verification

## YAGNI — Not Building

- No per-salon deployment (single deployment handles all)
- No number porting (salons use existing or get new via Meta)
- No billing/subscription system (manual for now)
- No Instagram Embedded Signup (WhatsApp first)
- No real-time websocket updates (page refresh fine)
- No auto-operator assignment algorithm (NULL or first available)
- No rate limiting per business (all share same limits for now)
