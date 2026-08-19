# Human Takeover (WhatsApp ↔ Chatwoot)

Deployed + verified live: 2026-06-23.

When a human agent replies in the Chatwoot dashboard, the reply is delivered to
the customer over WhatsApp and the bot AI is suspended for that conversation
until it is marked **resolved** (then the AI resumes).

## Flow

```
Agent types in Chatwoot
   └─▶ Chatwoot account webhook (message_created)
         └─▶ POST http://98.89.13.25/webhook/chatwoot?token=<secret>   (bot, Box 2)
               classify_webhook_event():
                 - skip private notes / incoming / empty / bot echoes (by msg id)
                 - else: mark_human_takeover(phone)  [RDS]  +  send_whatsapp_message(phone, content)

Customer messages while takeover active
   └─▶ process_message(): has_human_takeover(phone) -> mirror to Chatwoot, SKIP AI

Agent resolves conversation
   └─▶ webhook (conversation_status_changed, status=resolved)
         └─▶ clear_human_takeover(phone) -> AI resumes
```

## Components

- **Bot code** (`bot/`): `salon_bot_with_booking.py` (route `/webhook/chatwoot`,
  takeover gates in `process_message` + `process_buffered_messages`, startup
  `init_takeover_store`) and `chatwoot_bridge.py` (`classify_webhook_event`,
  `Postgres/InMemoryTakeoverStore`, echo guard, phone extraction).
- **State**: RDS table `chatwoot_takeover(phone PK, active, updated_at)` —
  auto-created at startup (`CREATE TABLE IF NOT EXISTS`, no manual migration).
  Survives bot restarts. Safety TTL (`CHATWOOT_TAKEOVER_TTL_SECONDS`, default 6h)
  self-heals a missed resolve event.
- **Auth**: `CHATWOOT_WEBHOOK_SECRET` in `/home/ec2-user/.env` (Box 2). Chatwoot
  custom webhooks can't send headers, so the secret rides as `?token=` and is
  checked with `hmac.compare_digest` (fail-closed: 503 if unset, 403 if wrong).

## Chatwoot config (Box 1, account 1)

Account webhook (id 1):
- URL: `http://98.89.13.25/webhook/chatwoot?token=<secret>`
- Subscriptions: `message_created`, `conversation_status_changed`, `conversation_updated`

Set via `Account.first.webhooks`. The stale Api-channel **inbox** `webhook_url`
(`.../webhooks/chatwoot`, plural, broken) was cleared — do NOT repoint it to the
working route or agent replies will send twice (account webhook already covers it).

## Live verification done

- Auth: no/wrong token → 403; valid token → 200.
- Real agent message via Chatwoot → bot logged `human takeover ON` + `Forwarded`;
  RDS row `active=True`.
- Resolve → bot logged `takeover OFF`; RDS row `active=False`.
- Startup log: `takeover store: Postgres (durable)`.
- Tests: `bot/tests/` 25 passing.

## Tests

```
cd deployed-live/bot && python3 -m pytest tests/ -q
```

## Known follow-ups

- Secret appears in nginx/Chatwoot logs (URL query). High entropy; consider
  `access_log off` for the path as hardening.
- Echo guard (`_bot_pushed_msg_id_set`) is in-process; fine for single process.
- Multi-tenant: sending uses env WhatsApp creds (single live inbox = Lyo). A
  second inbox needs an inbox→business mapping for `send_whatsapp_message`.
