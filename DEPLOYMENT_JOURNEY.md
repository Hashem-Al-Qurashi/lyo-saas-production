# Deployment Journey & Webhook Fix (Salon Bot)

## Timeline (AWS, WhatsApp Cloud API)
1. **DNS/SSL**  
   - Route53 A/ALIAS → ALB  
   - HTTPS via CloudFront (valid Amazon cert) → `https://d34dcl62ecf71w.cloudfront.net/webhook`
2. **App Deployed on EC2**  
   - FastAPI `salon_bot_with_booking.py`  
   - WhatsApp webhook GET/POST `/webhook`  
   - Health `/health`
3. **WhatsApp Configuration**  
   - phone_number_id: `961636900357709` (Test Number 15551356042)  
   - Verify token: `lyosaas2024`  
   - Webhook URL: CloudFront URL above  
   - Access token: system user token (never expires)
4. **Bot Capabilities (before improvements)**  
   - Booking: create, cancel, list (DB-backed)  
   - Availability: DB-only (no calendar)  
   - Persistent memory: per-process (not DB)  
   - Proactive messaging endpoints existed in another branch, not in deployed file  
   - Modify appointment: referenced, but not wired to OpenAI

## Infra Diagram
- Client (WhatsApp) → Meta webhook → CloudFront → ALB → EC2 (FastAPI)  
- Outbound: EC2 → Meta Graph API (send messages), EC2 → RDS PostgreSQL, EC2 → (optionally) Google Calendar

## Webhook Verification
- GET `/webhook?hub.mode=subscribe&hub.verify_token=lyosaas2024&hub.challenge=XYZ` → echoes `XYZ` (200)
- POST `/webhook` → logs and processes messages; returns 200 `{"status":"processed"}`

## Current Deployed State (backed up)
- File backed up: `backups/salon_bot_with_booking_deployed_2025-12-18.py`
- Token/IDs (already configured on server):
  - `WHATSAPP_ACCESS_TOKEN` (system user)
  - `WHATSAPP_PHONE_NUMBER_ID=961636900357709`
  - `WHATSAPP_WEBHOOK_VERIFY_TOKEN=lyosaas2024`
- RDS: `salon_appointments` table holds bookings; `google_credentials` table exists (for future calendar sync)

## Known Gaps Prior to Fix
- `modify_appointment` not exposed to AI → reschedules failed
- Availability only checks DB → could say “booked” incorrectly if calendar is empty
- Conversation memory not persisted to DB in deployed file
- Proactive endpoints not in deployed file

## Plan (to be executed after backup)
1) Wire `modify_appointment` + `get_available_slots` into OpenAI function list/handler.  
2) Enforce availability check in prompt; normalize times (“2 pm” → 14:00).  
3) Add Google Calendar sync (create/update/delete) and availability (DB + Calendar).  
4) Persist conversation history to DB; ensure replies use incoming phone_number_id.  
5) Validate flows locally: create → modify → list → cancel; availability; calendar event sync.  
6) Deploy to EC2; re-verify webhook, health, and WhatsApp flows.
