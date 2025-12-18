# Deployed State Backup (2025-12-18)

- Source: `ec2-user@3.239.106.181:/home/ec2-user/salon_bot_with_booking.py`
- Local copy: `backups/salon_bot_with_booking_deployed_2025-12-18.py`

## Runtime Config on EC2
- `WHATSAPP_ACCESS_TOKEN` (system user, valid)
- `WHATSAPP_PHONE_NUMBER_ID=961636900357709`
- `WHATSAPP_WEBHOOK_VERIFY_TOKEN=lyosaas2024`
- Webhook URL (Meta): `https://d34dcl62ecf71w.cloudfront.net/webhook`

## Bot Capabilities (deployed file)
- Booking: create, cancel, list (DB-backed, table `salon_appointments`)
- Availability: DB-only (no Google Calendar check)
- Modify appointment: implemented in code but **not** wired to OpenAI (AI can’t call it)
- Conversation memory: in-process (not persisted to DB)
- Proactive messaging endpoints: not present in deployed file

## Observed Issue
- Reschedule (modify) fails because `modify_appointment` is not exposed to the AI; bot falls back to “appointment not found.”

## Next Actions (post-backup)
- Wire `modify_appointment` and `get_available_slots` into OpenAI function list/handler.
- Add Google Calendar sync for create/modify/cancel and availability (DB + Calendar).
- Persist conversation history to DB.
