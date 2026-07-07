# Calendar View Design — Lyo Management Dashboard

**Date:** 2026-03-02
**Status:** Approved
**Approach:** B — Build into existing `/manage/` dashboard

## Problem

The current appointments page (`/manage/appointments/`) shows a flat table filtered by date. Salon owners need a visual calendar to scan the day at a glance. Stylists need to see their own schedule as a timeline. Both need operator-column layout (X = operators, Y = time slots).

## Decision: Why not Retool?

- $10/user/month per operator — scales badly for multi-tenant SaaS
- Retool's calendar widget can't do resource columns (operators as columns)
- Separate login for stylists (not integrated with existing JWT auth)
- Multi-tenant isolation relies on query discipline, not enforced at API level
- Clients see Retool's UI, not Lyo branding

## Decision: Chatwoot stays

- Staff uses Chatwoot inbox for human takeover when bot can't handle a request
- Chatwoot = messaging relay + human inbox (separate concern from calendar)
- Calendar reads from `appointments` table regardless of how appointments were created

## Architecture

### Views

| View | URL | Layout |
|------|-----|--------|
| Day (default) | `/manage/calendar/?view=day&date=YYYY-MM-DD` | X = operator columns, Y = 15-min time slots, appointments as colored blocks |
| Week | `/manage/calendar/?view=week&date=YYYY-MM-DD` | 7-day grid, appointment counts + revenue per day, click → day view |

### Color coding
- Green = confirmed
- Blue = completed
- Red = cancelled

### JSON API Endpoints (inside existing `/manage/` app)

```
GET  /manage/api/appointments?start=DATE&end=DATE
     → FullCalendar event objects: {id, title, start, end, resourceId, color, extendedProps}

GET  /manage/api/operators
     → FullCalendar resource objects: {id, title}

POST /manage/api/appointments/{id}/move
     → Drag-and-drop reschedule: {new_date, new_time, new_operator_id}
```

- Same JWT cookie auth (`_get_user()` pattern)
- CSRF token passed via `X-CSRF-Token` header for AJAX
- All queries filtered by `business_id` (multi-tenant enforcement)

### Tech Stack

- **FullCalendar v6** (CDN, MIT license) — `resourceTimeGridDay` view for operator columns
- **No npm/bundler** — `<script>` tag like existing Tailwind CDN
- **Mobile responsive** — FullCalendar switches to list view on narrow screens
- **No new dependencies** in `requirements.txt`

### Files to create/modify

| File | Action |
|------|--------|
| `management/routes/calendar.py` | NEW — JSON API endpoints + calendar page route |
| `management/templates/calendar.html` | NEW — FullCalendar JS + page layout |
| `management/templates/base.html` | MODIFY — add "Calendario" nav link |
| `management/app.py` | MODIFY — include calendar router |
| `management/csrf.py` | MODIFY — accept CSRF via header for AJAX |
| `tests/test_mgmt.py` | MODIFY — add calendar API tests |

### CSRF for AJAX

Current CSRF middleware only reads `csrf_token` from form body. For FullCalendar AJAX calls, also check `X-CSRF-Token` header:

```python
# In CSRFMiddleware.dispatch, after form parsing:
if not submitted_token:
    submitted_token = request.headers.get("X-CSRF-Token", "")
```

## YAGNI — Not building

- No drag-to-create new appointments (bot handles booking)
- No recurring appointments
- No month view (salons work day-by-day)
- No real-time websocket updates (page refresh is fine)
- No Retool integration
