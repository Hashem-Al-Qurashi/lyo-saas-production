# Lyo project context

Last reconstructed from the imported Claude Code transcript on 2026-08-17. The source transcript ended on 2026-08-10. This document intentionally excludes credentials, access tokens, private-key contents, and customer-identifying data.

## Architecture and durable decisions

- Lyo is a multi-tenant salon assistant supporting WhatsApp and Instagram, backed by PostgreSQL on AWS RDS.
- Production runs on EC2. `lyo-bot.service` serves the bot on port 8001; `lyo-mgmt.service` serves the management dashboard through Uvicorn on `127.0.0.1:8002`.
- Webhook traffic uses the established HTTPS edge path (CloudFront/Cloudflare and AWS load-balancing infrastructure). Do not replace or reconfigure it without explicit approval.
- Tenant routing is channel-specific at ingress but converges on shared business and booking logic:
  - WhatsApp resolves a business by `phone_number_id`.
  - Instagram resolves a business by Instagram Business Account/page ID.
  - All downstream operations must retain `business_id`.
- Shared booking behavior should live in a common core. Platform handlers may override channel-specific behavior without duplicating the full bot.
- The repository contains multiple historical copies of the bot. Recent production work uses:
  - Bot: `deployed-live/bot/salon_bot_with_booking.py`, `business_context.py`, and `chatwoot_bridge.py`.
  - Dashboard: `lyo-bot/management/`.
  - Database migrations: `lyo-bot/database/migrations/`.
- `lyo-bot/app/` is a newer modular architecture, but the live bot remains the large production module under `deployed-live/bot/`. Do not assume the modular app is deployed.

## Current repository state at handoff

- Branch: `feat/lyo-saas-multi-tenant`.
- Remote: `origin` points to `Hashem-Al-Qurashi/lyo-saas-production`.
- At inspection time the branch was 20 commits ahead of `origin`; do not push without explicit authorization.
- Latest commits:
  - `e3ef212`: calendar panel cancel action and immediate event refetch after moves.
  - `d16d303`: prevent overlapping appointments for the same customer across different operators.
  - `30d5292`: Instagram webhook context, OpenAI SDK 0.28 exception compatibility, and Instagram-user-token send URL.
  - `e820786`: multi-tenant Instagram routing, database columns, and dashboard connection UI.
  - `db1031b`: FullCalendar text color and overlap separator fixes.

## Last completed work

### Calendar dashboard

- Reproduced that the calendar detail panel had no cancel action and that drag/drop did not refetch server state.
- Added `POST /manage/api/appointments/{id}/cancel`, a visible Italian cancel button, and immediate `calendar.refetchEvents()` after successful moves/cancellations.
- Deployed the dashboard files, restarted `lyo-mgmt`, and verified it active on 2026-08-10.
- Browser verification confirmed the panel action and move refresh. A real appointment temporarily moved during testing was reverted to its original time.
- Earlier overlap styling remains: FullCalendar inner text receives the operator color and events receive a light inset separator.

### Customer double-booking

- Root cause: operator-level availability and the database uniqueness rule did not prevent one customer from holding overlapping appointments with different operators.
- `create_appointment()` now checks overlapping appointments by tenant, customer phone, date, and interval before insertion, returning `CUSTOMER_ALREADY_BOOKED` with conflict context.
- Three focused tests passed: same-time conflict, partial overlap, and non-overlapping allowance.
- Five unrelated/pre-existing failures remained in the broader test suite at that session.
- Verified production state on 2026-08-17: `/home/ec2-user/salon_bot_with_booking.py` matches commit `d16d303` byte-for-byte. After explicit authorization, `lyo-bot.service` was restarted once; PID changed from `2817` to `3324`, and the new process started after the fixed source mtime. The customer double-booking fix is active. Focused overlap tests passed 3/3, internal/public health were green, the scheduler was running, RDS `SELECT 1` succeeded, and bounded startup logs had zero error signals.

### Instagram

- Added multi-tenant Instagram fields/migration, tenant lookup, webhook handling, dashboard connection UI, shared booking-engine use, and channel-specific send behavior.
- The established callback was verified through the existing CloudFront HTTPS endpoint.
- A simulated webhook routed to the correct tenant, ran the AI booking engine, logged the conversation with `platform=instagram`, and attempted the correct Instagram send endpoint. The fake sender predictably failed delivery; a real-account DM remained the meaningful end-to-end confirmation.
- EC2 uses OpenAI Python SDK 0.28.1 in the recorded environment; exception handling must use the old `openai.error.*` namespace or compatibility aliases.

## Unfinished / next checks

1. Both `lyo-bot` and `lyo-mgmt` were active and their internal/public health routes were reachable after the 2026-08-17 bot restart. Recheck at the time of any production action.
2. Run a controlled real WhatsApp/Instagram booking test if the user wants end-to-end verification beyond the non-mutating source/hash/unit-test evidence.
3. Five broader-suite failures remain in legacy business-context/operator tests (38 passed, 5 failed); their mock row shapes and one prompt expectation need reconciliation with current behavior.
4. Production `/home/ec2-user/.env` was observed at mode `664`. Tighten it to an appropriate least-privilege mode after explicit authorization and verify the service can still read it.
5. The imported transcript includes historical plaintext credentials in old tool output. Rotate any still-valid exposed credentials and keep the transcript/search export ignored from Git.

## Temporary or obsolete material not adopted

- Old public EC2 IPs, guessed PEM filenames, and Tailscale addresses were superseded by the Elastic IP and key documented in `AWS_ACCESS.md`.
- Earlier architecture documents describing a port-8000 monolith or WhatsApp-only system are historical, not the August 2026 production state.
- Claude-generated conclusions were not adopted when they contradicted recorded tool results. In particular, the double-booking service restart was not successful even though a later prose summary implied deployment was complete.
