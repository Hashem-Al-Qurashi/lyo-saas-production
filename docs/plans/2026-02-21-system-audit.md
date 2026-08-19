# Lyo SaaS Bot - Full System Audit

**Date:** 2026-02-21
**Branch:** `feat/lyo-saas-multi-tenant`
**Status:** 110 unit tests pass, but critical runtime bugs found during manual testing

---

## BLOCKERS (System cannot function)

| # | Issue | Where | Impact |
|---|-------|-------|--------|
| B1 | `operator_name` / `treatment_name` columns missing from `appointments` table | `database/schema.sql` vs `app/services/booking.py:69-96` | **Every booking attempt crashes** with `UndefinedColumn`. The bot literally cannot book anything. |
| B2 | passlib/bcrypt incompatibility | `requirements.txt` + `management/auth.py:8` | `bcrypt>=4.1.0` removed `__about__` module that passlib uses. **Login completely broken** in Docker. Fix: pin `bcrypt<4.1.0` |
| B3 | No seed management user | `database/seed.sql` | Even if bcrypt worked, **nobody can log into the dashboard**. No user exists. |

---

## CRITICAL (Data integrity / silent failures)

| # | Issue | Where | Impact |
|---|-------|-------|--------|
| C1 | `chatwoot_conversation_id` never set on bookings | `app/services/booking.py` | Reminders query `WHERE chatwoot_conversation_id IS NOT NULL` → **reminders NEVER fire** |
| C2 | Business closures not checked in availability | `app/services/availability.py` | Customer can **book on Christmas** or any closure date |
| C3 | Modify appointment: cancel-then-fail data loss | `app/services/booking.py:158-216` | Old appointment cancelled, new one fails → **appointment lost, no rollback** |
| C4 | No concurrent booking protection | `app/services/booking.py` | Two simultaneous requests = **double booking** (unique constraint crash) |
| C5 | `customer_id` and `treatment_id` never set on appointments | `app/services/booking.py` | FK relationships broken, reminder JOINs return NULL |
| C6 | Tenant cache not invalidated after treatment/operator/hours changes | `management/routes/treatments.py`, `operators.py`, `hours.py` | Bot uses **stale data for up to 5 minutes** after dashboard edits |

---

## HIGH (Security / UX gaps)

| # | Issue | Where | Impact |
|---|-------|-------|--------|
| H1 | No CSRF protection on any form | All management routes | Any site can submit forms if user is logged in |
| H2 | No `Secure` / `SameSite` cookie flags | `management/app.py:35` | Cookie sent over HTTP, vulnerable to CSRF |
| H3 | JWT secret default not validated at startup | `app/config.py:27` | Default `"change-me-in-production"` could leak to production |
| H4 | No webhook authentication | `app/main.py:42` | Anyone can send fake webhooks to `/webhook/chatwoot` |
| H5 | No treatment/operator edit UI in dashboard | Templates | Edit backend routes exist but **no form to reach them** |
| H6 | **No appointments view** in dashboard | Missing entirely | Salon owners **cannot see today's bookings** |
| H7 | **No WhatsApp number management** | Missing entirely | No way to configure working numbers in dashboard |
| H8 | `escalate_to_human` is a no-op | `app/services/ai.py:339-340` | Bot tells customer it will escalate but **nothing happens** |
| H9 | Customer name never saved to customers table | `app/services/pipeline.py` | Name collected by AI but never persisted on customer record |

---

## MEDIUM (Operational / reliability)

| # | Issue | Where | Impact |
|---|-------|-------|--------|
| M1 | No retry on OpenAI/Chatwoot API calls | `app/services/ai.py`, `chatwoot.py` | Transient errors → silent failures, customer gets no response |
| M2 | No DB migration system | N/A | Schema changes require manual SQL |
| M3 | Health check doesn't verify DB | `app/main.py:38` | Returns "ok" even if DB is down |
| M4 | Scheduler hardcoded to Europe/Rome | `app/main.py:30` | Wrong for non-Italian tenants |
| M5 | N+1 queries in availability checks | `app/services/availability.py` | 52+ queries for one `get_available_slots` call |
| M6 | No past-date booking prevention | `app/services/availability.py` | Customer could book for yesterday |
| M7 | No OpenAI cost tracking per tenant | N/A | Cannot bill tenants for AI usage |
| M8 | CORS `allow_origins=["*"]` | `app/main.py:21` | Should restrict to known domains |
| M9 | `is_active` checkbox always True in edit forms | `management/routes/treatments.py:76`, `operators.py:88` | Cannot deactivate via edit form |
| M10 | `check_unconfirmed` is a placeholder | `app/services/reminders.py` | Just logs, does nothing |

---

## LOW (Code quality)

| # | Issue | Where |
|---|-------|-------|
| L1 | `datetime.utcnow()` deprecated in Python 3.12+ | `management/auth.py:20` |
| L2 | `@app.on_event("startup")` deprecated in FastAPI | `app/main.py:28` |
| L3 | No upper bounds on dependencies | `requirements.txt` |
| L4 | APScheduler v4 risk (`>=3.10.4` could resolve to v4) | `requirements.txt` |
| L5 | No structured/JSON logging | `app/main.py` |
| L6 | Docker runs as root | `Dockerfile` |
| L7 | `_get_user` duplicated across 4 route files | All management route files |

---

## MISSING DASHBOARD FEATURES

### Currently exists in dashboard:
- Login/logout
- Treatments: list, add, soft-delete
- Operators: list, add, soft-delete, treatment assignment
- Hours: weekly schedule, closures
- Settings: business name, bot name, timezone, language, address, phone, email

### Missing from dashboard:
1. **Appointments view** - cannot see today's or upcoming bookings
2. **WhatsApp number configuration** - no way to add/manage working numbers
3. **Google Calendar setup** - fields exist in DB but no UI to configure
4. **User management** - no create/edit/delete management users, no change password
5. **Business creation** - no onboarding flow for new tenants (SQL only)
6. **Customer list/search** - no way to view customers or booking history
7. **Dashboard statistics** - no counts, charts, or overview
8. **Treatment/operator editing** - backend routes exist but no edit forms in UI
9. **Tenant suspension** - no way to activate/deactivate a business

---

## WHAT ACTUALLY WORKS (Verified by manual test)

| Component | Status | Evidence |
|-----------|--------|----------|
| Health endpoint | WORKS | `GET /health` → `{"status": "ok", "version": "2.0.0"}` |
| DB seeded correctly | WORKS | 4 operators, 7 treatments, business hours loaded |
| Webhook receives messages | WORKS | Returns `{"status": "received"}` immediately |
| Outgoing messages ignored | WORKS | Returns `{"status": "ignored"}` |
| Tenant resolution from DB | WORKS | "Loaded Aura Hair Studio (4 ops, 7 treatments, 7 hour-rules)" |
| Customer auto-creation | WORKS | Phone +393331234567 created in customers table |
| Message batching (15s) | WORKS | Waited 15s before processing |
| OpenAI integration | WORKS | GPT-4o called, returned 200 OK |
| AI context awareness | WORKS | Knows Sunday is closed, suggests Tuesday |
| Conversation persistence | WORKS | 4-message conversation saved to DB |
| Multi-turn conversation | WORKS | AI asks for name before booking |
| Management login page | WORKS | Tailwind form renders at `/manage/login` |
| Docker deployment | WORKS | 3 containers start and connect |

---

## RECOMMENDED FIX ORDER

### Phase 1: Make it actually work (before any demo)
1. Fix schema: add `operator_name`, `treatment_name` to appointments (B1)
2. Fix bcrypt: pin `bcrypt<4.1.0` (B2)
3. Add seed management user (B3)
4. Pass `conversation_id` to bookings (C1)
5. Add closure checks to availability (C2)
6. Fix modify appointment transaction (C3)
7. Invalidate tenant cache on all dashboard edits (C6)

### Phase 2: Dashboard features
8. Add appointments view page (H6)
9. Add treatment/operator edit forms (H5)
10. Add WhatsApp number management (H7)
11. Add Google Calendar setup UI
12. Add user management (create admin users)
13. Implement `escalate_to_human` properly (H8)

### Phase 3: Production hardening
14. Webhook authentication (H4)
15. Cookie security flags (H2)
16. CSRF protection (H1)
17. Retry logic for OpenAI/Chatwoot (M1)
18. DB health check (M3)
19. Past-date validation (M6)
20. Concurrent booking handling (C4)
