# Calendar View Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a visual calendar view to the Lyo management dashboard showing appointments as colored blocks in operator columns, using FullCalendar v6.

**Architecture:** New route module `calendar.py` with 3 JSON API endpoints (appointments, operators, move) + 1 HTML page route. FullCalendar loaded via CDN renders the calendar client-side, fetching data via AJAX. CSRF middleware extended to accept header-based tokens for AJAX POST.

**Tech Stack:** FastAPI, Jinja2, FullCalendar v6 (CDN), Tailwind (existing CDN)

**Design doc:** `docs/plans/2026-03-02-calendar-view-design.md`

---

### Task 1: CSRF Header Support for AJAX

**Files:**
- Modify: `management/csrf.py:41-43` (after form token extraction, check header)
- Test: `tests/test_mgmt.py` (add CSRF header test class)

**Step 1: Write the failing test**

Add to `tests/test_mgmt.py`:

```python
class TestCSRFHeaderSupport:
    """CSRF token can be passed via X-CSRF-Token header (for AJAX)."""

    def test_post_with_csrf_header_succeeds(self):
        """POST with CSRF token in header should be accepted."""
        cookies = _auth_cookie()
        # GET to establish session + get CSRF token
        get_resp = client.get("/manage/login", cookies=cookies)
        session_cookie = get_resp.cookies.get("session")
        all_cookies = dict(cookies)
        if session_cookie:
            all_cookies["session"] = session_cookie
        get_resp2 = client.get("/manage/dashboard", cookies=all_cookies)
        csrf_match = re.search(r'csrf-token" content="([^"]+)"', get_resp2.text)
        assert csrf_match, "CSRF token not found in meta tag"
        csrf_token = csrf_match.group(1)
        if get_resp2.cookies.get("session"):
            all_cookies["session"] = get_resp2.cookies.get("session")

        # POST with token in header (no csrf_token in body)
        resp = client.post(
            "/manage/appointments/9999/cancel",
            data={},
            cookies=all_cookies,
            headers={"X-CSRF-Token": csrf_token},
        )
        # 302 redirect = CSRF passed (appointment not found is fine)
        assert resp.status_code == 302

    def test_post_without_any_csrf_still_fails(self):
        """POST without CSRF token in body OR header should 403."""
        cookies = _auth_cookie()
        get_resp = client.get("/manage/login", cookies=cookies)
        session_cookie = get_resp.cookies.get("session")
        all_cookies = dict(cookies)
        if session_cookie:
            all_cookies["session"] = session_cookie
        resp = client.post(
            "/manage/appointments/9999/cancel",
            data={},
            cookies=all_cookies,
        )
        assert resp.status_code == 403
```

**Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_mgmt.py::TestCSRFHeaderSupport -v`
Expected: `test_post_with_csrf_header_succeeds` FAILS (403 because header not checked)

**Step 3: Write minimal implementation**

In `management/csrf.py`, after line 43 (`submitted_token = submitted_tokens[0] if submitted_tokens else ""`), add header fallback:

```python
            submitted_token = submitted_tokens[0] if submitted_tokens else ""
            # Also accept CSRF token via header (for AJAX/fetch calls)
            if not submitted_token:
                submitted_token = request.headers.get("X-CSRF-Token", "")
            expected_token = request.session.get("csrf_token", "")
```

**Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_mgmt.py::TestCSRFHeaderSupport -v`
Expected: 2 PASSED

**Step 5: Run full suite to verify no regression**

Run: `python3 -m pytest tests/test_mgmt.py -v`
Expected: All pass

**Step 6: Commit**

```bash
git add management/csrf.py tests/test_mgmt.py
git commit -m "feat: accept CSRF token via X-CSRF-Token header for AJAX"
```

---

### Task 2: JSON API — Operators Endpoint

**Files:**
- Create: `management/routes/calendar.py`
- Test: `tests/test_mgmt.py` (add CalendarAPI test class)

**Step 1: Write the failing test**

Add to `tests/test_mgmt.py`:

```python
class TestCalendarAPIOperators:
    """GET /manage/api/operators returns FullCalendar resource objects."""

    @patch("management.routes.calendar.get_connection")
    def test_returns_operators_as_resources(self, mock_conn):
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [
            (1, "Giulia", True),
            (2, "Martina", True),
        ]
        mock_conn.return_value.__enter__ = lambda s: MagicMock(cursor=lambda: mock_cur)
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)

        cookies = _auth_cookie()
        resp = client.get("/manage/api/operators", cookies=cookies)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["id"] == 1
        assert data[0]["title"] == "Giulia"

    def test_unauthenticated_returns_401(self):
        resp = client.get("/manage/api/operators")
        assert resp.status_code == 401
```

**Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_mgmt.py::TestCalendarAPIOperators -v`
Expected: FAIL (404 — route doesn't exist)

**Step 3: Write minimal implementation**

Create `management/routes/calendar.py`:

```python
import os
from datetime import date, datetime

from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from management.auth import decode_token
from app.models.database import get_connection

router = APIRouter()
templates_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
templates = Jinja2Templates(directory=templates_dir)


def _get_user(request: Request):
    token = request.cookies.get("access_token")
    if not token:
        return None
    return decode_token(token)


@router.get("/manage/api/operators")
async def api_operators(request: Request):
    user = _get_user(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT id, display_name, is_active
               FROM operators
               WHERE business_id = %s AND is_active = true
               ORDER BY sort_order""",
            (user["business_id"],),
        )
        rows = cur.fetchall()

    resources = [{"id": r[0], "title": r[1]} for r in rows]
    return JSONResponse(resources)
```

Also register in `management/app.py` — add after line 74:

```python
from management.routes import treatments, operators, hours, settings as settings_routes, appointments, users, calendar  # noqa: E402
```

And after line 81:

```python
mgmt_app.include_router(calendar.router)
```

**Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_mgmt.py::TestCalendarAPIOperators -v`
Expected: 2 PASSED

**Step 5: Commit**

```bash
git add management/routes/calendar.py management/app.py tests/test_mgmt.py
git commit -m "feat: add GET /manage/api/operators JSON endpoint for calendar"
```

---

### Task 3: JSON API — Appointments Endpoint

**Files:**
- Modify: `management/routes/calendar.py`
- Test: `tests/test_mgmt.py`

**Step 1: Write the failing test**

```python
class TestCalendarAPIAppointments:
    """GET /manage/api/appointments returns FullCalendar event objects."""

    @patch("management.routes.calendar.get_connection")
    def test_returns_appointments_as_events(self, mock_conn):
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [
            (10, "Maria Rossi", "+39123", "Taglio Donna", "taglio_donna",
             "Giulia", "2026-03-02", "10:00:00", 45, "confirmed", 60.00, 1),
        ]
        mock_conn.return_value.__enter__ = lambda s: MagicMock(cursor=lambda: mock_cur)
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)

        cookies = _auth_cookie()
        resp = client.get(
            "/manage/api/appointments?start=2026-03-02&end=2026-03-03",
            cookies=cookies,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        evt = data[0]
        assert evt["id"] == 10
        assert evt["title"] == "Maria Rossi\nTaglio Donna"
        assert evt["start"] == "2026-03-02T10:00:00"
        assert evt["end"] == "2026-03-02T10:45:00"
        assert evt["resourceId"] == 1
        assert evt["color"] == "#16a34a"  # green for confirmed

    @patch("management.routes.calendar.get_connection")
    def test_cancelled_appointment_is_red(self, mock_conn):
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [
            (11, "Luca B", "+39456", "Piega", "piega",
             "Martina", "2026-03-02", "14:00:00", 30, "cancelled", 30.00, 2),
        ]
        mock_conn.return_value.__enter__ = lambda s: MagicMock(cursor=lambda: mock_cur)
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)

        cookies = _auth_cookie()
        resp = client.get(
            "/manage/api/appointments?start=2026-03-02&end=2026-03-03",
            cookies=cookies,
        )
        data = resp.json()
        assert data[0]["color"] == "#dc2626"  # red for cancelled

    def test_missing_dates_returns_400(self):
        cookies = _auth_cookie()
        resp = client.get("/manage/api/appointments", cookies=cookies)
        assert resp.status_code == 400

    def test_unauthenticated_returns_401(self):
        resp = client.get("/manage/api/appointments?start=2026-03-02&end=2026-03-03")
        assert resp.status_code == 401
```

**Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_mgmt.py::TestCalendarAPIAppointments -v`
Expected: FAIL (404 — endpoint doesn't exist)

**Step 3: Write minimal implementation**

Add to `management/routes/calendar.py`:

```python
STATUS_COLORS = {
    "confirmed": "#16a34a",   # green-600
    "completed": "#2563eb",   # blue-600
    "cancelled": "#dc2626",   # red-600
}


@router.get("/manage/api/appointments")
async def api_appointments(
    request: Request,
    start: str = Query(default=None),
    end: str = Query(default=None),
):
    user = _get_user(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    if not start or not end:
        return JSONResponse({"error": "start and end required"}, status_code=400)

    try:
        start_date = date.fromisoformat(start[:10])
        end_date = date.fromisoformat(end[:10])
    except ValueError:
        return JSONResponse({"error": "invalid date format"}, status_code=400)

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT id, customer_name, customer_phone, treatment_name, treatment_code,
                      operator_name, appointment_date::text, appointment_time::text,
                      duration_minutes, status, price, operator_id
               FROM appointments
               WHERE business_id = %s
                 AND appointment_date >= %s AND appointment_date < %s
               ORDER BY appointment_date, appointment_time""",
            (user["business_id"], start_date, end_date),
        )
        rows = cur.fetchall()

    events = []
    for r in rows:
        appt_id, name, phone, treat_name, treat_code, op_name, appt_date, appt_time, duration, status, price, op_id = r
        start_dt = f"{appt_date}T{appt_time}"
        # Calculate end time
        h, m, s = appt_time.split(":")
        total_min = int(h) * 60 + int(m) + duration
        end_h, end_m = divmod(total_min, 60)
        end_dt = f"{appt_date}T{end_h:02d}:{end_m:02d}:00"

        events.append({
            "id": appt_id,
            "title": f"{name}\n{treat_name or treat_code}",
            "start": start_dt,
            "end": end_dt,
            "resourceId": op_id,
            "color": STATUS_COLORS.get(status, "#6b7280"),
            "extendedProps": {
                "phone": phone,
                "status": status,
                "price": float(price) if price else 0,
                "operator": op_name,
            },
        })

    return JSONResponse(events)
```

**Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_mgmt.py::TestCalendarAPIAppointments -v`
Expected: 4 PASSED

**Step 5: Commit**

```bash
git add management/routes/calendar.py tests/test_mgmt.py
git commit -m "feat: add GET /manage/api/appointments JSON endpoint for calendar"
```

---

### Task 4: JSON API — Move (Drag-and-Drop Reschedule)

**Files:**
- Modify: `management/routes/calendar.py`
- Test: `tests/test_mgmt.py`

**Step 1: Write the failing test**

```python
class TestCalendarAPIMoveAppointment:
    """POST /manage/api/appointments/{id}/move reschedules via drag-and-drop."""

    @patch("management.routes.calendar.get_connection")
    def test_move_updates_date_time_operator(self, mock_conn):
        mock_cur = MagicMock()
        mock_cur.rowcount = 1
        mock_conn.return_value.__enter__ = lambda s: MagicMock(cursor=lambda: mock_cur)
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)

        cookies = _auth_cookie()
        # Need CSRF for POST
        get_resp = client.get("/manage/login", cookies=cookies)
        all_cookies = dict(cookies)
        session_cookie = get_resp.cookies.get("session")
        if session_cookie:
            all_cookies["session"] = session_cookie
        get_resp2 = client.get("/manage/dashboard", cookies=all_cookies)
        csrf_match = re.search(r'csrf-token" content="([^"]+)"', get_resp2.text)
        csrf_token = csrf_match.group(1) if csrf_match else ""
        if get_resp2.cookies.get("session"):
            all_cookies["session"] = get_resp2.cookies.get("session")

        resp = client.post(
            "/manage/api/appointments/10/move",
            json={"new_date": "2026-03-03", "new_time": "11:00", "new_operator_id": 2},
            cookies=all_cookies,
            headers={"X-CSRF-Token": csrf_token, "Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "moved"

    def test_unauthenticated_returns_401(self):
        resp = client.post("/manage/api/appointments/10/move", json={})
        assert resp.status_code in (401, 403)
```

**Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_mgmt.py::TestCalendarAPIMoveAppointment -v`
Expected: FAIL (404)

**Step 3: Write minimal implementation**

Add to `management/routes/calendar.py`:

```python
@router.post("/manage/api/appointments/{appointment_id}/move")
async def api_move_appointment(request: Request, appointment_id: int):
    user = _get_user(request)
    if not user:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    new_date = body.get("new_date")
    new_time = body.get("new_time")
    new_operator_id = body.get("new_operator_id")

    if not new_date or not new_time:
        return JSONResponse({"error": "new_date and new_time required"}, status_code=400)

    with get_connection() as conn:
        cur = conn.cursor()
        # Get operator name for denormalized column
        op_name = None
        if new_operator_id:
            cur.execute(
                "SELECT display_name FROM operators WHERE id = %s AND business_id = %s",
                (new_operator_id, user["business_id"]),
            )
            row = cur.fetchone()
            op_name = row[0] if row else None

        cur.execute(
            """UPDATE appointments
               SET appointment_date = %s, appointment_time = %s,
                   operator_id = COALESCE(%s, operator_id),
                   operator_name = COALESCE(%s, operator_name),
                   updated_at = NOW()
               WHERE id = %s AND business_id = %s AND status = 'confirmed'""",
            (new_date, new_time, new_operator_id, op_name, appointment_id, user["business_id"]),
        )

        if cur.rowcount == 0:
            return JSONResponse({"error": "appointment not found or not confirmed"}, status_code=404)

    return JSONResponse({"status": "moved"})
```

**Note on CSRF for JSON POST:** The CSRF middleware parses `application/x-www-form-urlencoded` body. For JSON body, the form token won't be found — the header fallback from Task 1 handles this.

**Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_mgmt.py::TestCalendarAPIMoveAppointment -v`
Expected: 2 PASSED

**Step 5: Commit**

```bash
git add management/routes/calendar.py tests/test_mgmt.py
git commit -m "feat: add POST /manage/api/appointments/{id}/move for drag-and-drop"
```

---

### Task 5: Calendar Page Route + Template

**Files:**
- Modify: `management/routes/calendar.py` (add page route)
- Create: `management/templates/calendar.html`
- Test: `tests/test_mgmt.py`

**Step 1: Write the failing test**

```python
class TestCalendarPage:
    """GET /manage/calendar/ returns calendar HTML page."""

    def test_calendar_page_returns_200(self):
        cookies = _auth_cookie()
        resp = client.get("/manage/calendar/", cookies=cookies)
        assert resp.status_code == 200
        assert "fullcalendar" in resp.text.lower() or "FullCalendar" in resp.text

    def test_calendar_page_has_csrf_meta(self):
        cookies = _auth_cookie()
        resp = client.get("/manage/calendar/", cookies=cookies)
        assert 'csrf-token' in resp.text

    def test_unauthenticated_redirects(self):
        resp = client.get("/manage/calendar/", follow_redirects=False)
        assert resp.status_code == 302
```

**Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_mgmt.py::TestCalendarPage -v`
Expected: FAIL (404)

**Step 3a: Add page route to `management/routes/calendar.py`**

```python
@router.get("/manage/calendar/", response_class=HTMLResponse)
async def calendar_page(request: Request):
    user = _get_user(request)
    if not user:
        return RedirectResponse(url="/manage/login", status_code=302)

    return templates.TemplateResponse("calendar.html", {
        "request": request,
        "user": user,
    })
```

**Step 3b: Create `management/templates/calendar.html`**

```html
{% extends "base.html" %}
{% block title %}Calendario - Lyo Management{% endblock %}
{% block content %}
<div class="flex justify-between items-center mb-4">
    <h1 class="text-2xl font-bold">Calendario</h1>
    <a href="/manage/appointments/" class="text-sm text-indigo-600 hover:text-indigo-800">Vista tabella &rarr;</a>
</div>

<div id="calendar" class="bg-white rounded-lg shadow p-4"></div>

<!-- FullCalendar v6 CDN -->
<link href="https://cdn.jsdelivr.net/npm/fullcalendar@6.1.11/index.global.min.css" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/fullcalendar@6.1.11/index.global.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/@fullcalendar/resource@6.1.11/index.global.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/@fullcalendar/resource-timegrid@6.1.11/index.global.min.js"></script>

<script>
document.addEventListener('DOMContentLoaded', function() {
    var csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
    var calendarEl = document.getElementById('calendar');

    var calendar = new FullCalendar.Calendar(calendarEl, {
        schedulerLicenseKey: 'GPL-My-Project-Is-Open-Source',
        initialView: 'resourceTimeGridDay',
        headerToolbar: {
            left: 'prev,next today',
            center: 'title',
            right: 'resourceTimeGridDay,timeGridWeek'
        },
        locale: 'it',
        timeZone: 'Europe/Rome',
        slotMinTime: '08:00:00',
        slotMaxTime: '21:00:00',
        slotDuration: '00:15:00',
        allDaySlot: false,
        editable: true,
        eventDurationEditable: false,
        height: 'auto',

        // Resources (operators)
        resources: {
            url: '/manage/api/operators',
            method: 'GET'
        },

        // Events (appointments)
        events: {
            url: '/manage/api/appointments',
            method: 'GET'
        },

        // Drag-and-drop reschedule
        eventDrop: function(info) {
            var evt = info.event;
            var newResource = evt.getResources()[0];
            fetch('/manage/api/appointments/' + evt.id + '/move', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRF-Token': csrfToken
                },
                body: JSON.stringify({
                    new_date: evt.start.toISOString().slice(0, 10),
                    new_time: evt.start.toTimeString().slice(0, 5),
                    new_operator_id: newResource ? parseInt(newResource.id) : null
                })
            }).then(function(resp) {
                if (!resp.ok) {
                    info.revert();
                    alert('Errore nello spostamento');
                }
            }).catch(function() {
                info.revert();
                alert('Errore di rete');
            });
        },

        // Click for details
        eventClick: function(info) {
            var props = info.event.extendedProps;
            alert(
                info.event.title + '\n' +
                'Tel: ' + (props.phone || '-') + '\n' +
                'Operatore: ' + (props.operator || '-') + '\n' +
                'Stato: ' + (props.status || '-') + '\n' +
                'Prezzo: €' + (props.price || 0)
            );
        },

        // Responsive: list view on mobile
        windowResize: function(arg) {
            if (window.innerWidth < 768) {
                calendar.changeView('listDay');
            } else {
                if (calendar.view.type === 'listDay') {
                    calendar.changeView('resourceTimeGridDay');
                }
            }
        }
    });

    calendar.render();

    // Trigger responsive check on load
    if (window.innerWidth < 768) {
        calendar.changeView('listDay');
    }
});
</script>

<style>
    /* Override FullCalendar styles to match Tailwind indigo theme */
    .fc .fc-button-primary {
        background-color: #4f46e5 !important;
        border-color: #4f46e5 !important;
    }
    .fc .fc-button-primary:hover {
        background-color: #4338ca !important;
    }
    .fc .fc-button-primary:not(:disabled).fc-button-active {
        background-color: #3730a3 !important;
    }
    .fc .fc-today-button {
        text-transform: capitalize;
    }
    .fc-event {
        cursor: pointer;
        border: none !important;
        font-size: 0.75rem;
        padding: 2px 4px;
    }
    .fc .fc-col-header-cell {
        background-color: #f9fafb;
        font-weight: 600;
    }
</style>
{% endblock %}
```

**Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_mgmt.py::TestCalendarPage -v`
Expected: 3 PASSED

**Step 5: Commit**

```bash
git add management/routes/calendar.py management/templates/calendar.html tests/test_mgmt.py
git commit -m "feat: add calendar page with FullCalendar day/week views"
```

---

### Task 6: Wire Up — Nav Link + Dashboard Card

**Files:**
- Modify: `management/templates/base.html:19` (add Calendario nav link)
- Modify: `management/templates/dashboard.html` (add calendar card)
- Test: `tests/test_mgmt.py`

**Step 1: Write the failing test**

```python
class TestCalendarNavigation:
    """Calendar is accessible from navigation and dashboard."""

    def test_nav_has_calendario_link(self):
        cookies = _auth_cookie()
        resp = client.get("/manage/dashboard", cookies=cookies)
        assert '/manage/calendar/' in resp.text
        assert 'Calendario' in resp.text

    def test_dashboard_has_calendario_card(self):
        cookies = _auth_cookie()
        resp = client.get("/manage/dashboard", cookies=cookies)
        assert 'Calendario' in resp.text
```

**Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_mgmt.py::TestCalendarNavigation -v`
Expected: FAIL (no "Calendario" link in nav or dashboard)

**Step 3a: Add nav link in `management/templates/base.html`**

After the "Appuntamenti" nav link (line 19), add:

```html
                    <a href="/manage/calendar/" class="text-gray-600 hover:text-gray-900">Calendario</a>
```

**Step 3b: Add calendar card in `management/templates/dashboard.html`**

Add after the "Appuntamenti" card:

```html
    <a href="/manage/calendar/" class="bg-white p-6 rounded-lg shadow hover:shadow-md transition">
        <h2 class="text-lg font-semibold text-indigo-600 mb-2">Calendario</h2>
        <p class="text-gray-500 text-sm">Vista calendario operatori</p>
    </a>
```

**Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_mgmt.py::TestCalendarNavigation -v`
Expected: 2 PASSED

**Step 5: Run full test suite**

Run: `python3 -m pytest tests/ --ignore=tests/test_integration.py -v`
Expected: All pass (151 original + new calendar tests)

**Step 6: Commit**

```bash
git add management/templates/base.html management/templates/dashboard.html tests/test_mgmt.py
git commit -m "feat: add Calendario nav link and dashboard card"
```

---

## Post-Implementation Gates

After all 6 tasks pass:

1. **Gate 3 (Validate Claims):** Start the server (`uvicorn management.app:mgmt_app --port 8000`), open browser, verify calendar renders with real data
2. **Gate 6 (Dead Code):** Grep for unused imports, commented code
3. **Gate 7 (SE Practices):** Run code-reviewer agent
4. **Gate 8 (Engineer Brief):** Write PR summary

## Task Summary

| Task | What | Tests |
|------|------|-------|
| 1 | CSRF header support for AJAX | 2 tests |
| 2 | GET /manage/api/operators | 2 tests |
| 3 | GET /manage/api/appointments | 4 tests |
| 4 | POST /manage/api/appointments/{id}/move | 2 tests |
| 5 | Calendar page + FullCalendar template | 3 tests |
| 6 | Nav link + dashboard card | 2 tests |
| **Total** | | **15 new tests** |
