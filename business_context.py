"""Business context loader -- loads multi-tenant config from the database.

Used by the bot to dynamically load services, hours, persona per business
based on the WhatsApp phone_number_id from the webhook payload.
"""

import os
import psycopg2
from datetime import datetime, timedelta
import pytz

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
            raise BusinessNotFoundError(
                f"No active business for phone_number_id={phone_number_id}"
            )
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))


def load_services(business_id: int) -> dict:
    """Load treatments from DB in SALON_SERVICES-compatible format.

    Returns:
        {"taglio_donna": {"name_it": "Taglio Donna", "name_en": "...",
                          "price": 60, "duration": 45, "description": "..."}, ...}
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

    Returns:
        {0: {"is_open": False, ...},
         1: {"is_open": True, "open_time": "09:00", "close_time": "19:00"}, ...}
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

    Returns:
        [{"date": "2026-12-25", "reason": "Natale"}, ...]
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


def load_operators(business_id: int) -> list:
    """Load active operators with their treatment codes.

    Returns:
        [{"id": 1, "display_name": "Giulia", "is_active": True,
          "treatments": ["taglio_donna", "balayage", ...]}, ...]
    """
    with get_db_connection() as conn:
        cur = conn.cursor()
        # 1. Get active operators
        cur.execute(
            """SELECT id, display_name, is_active
               FROM operators
               WHERE business_id = %s AND is_active = true
               ORDER BY sort_order""",
            (business_id,),
        )
        op_rows = cur.fetchall()
        if not op_rows:
            return []

        operators = []
        op_ids = []
        for op_id, display_name, is_active in op_rows:
            operators.append({
                "id": op_id,
                "display_name": display_name,
                "is_active": is_active,
                "treatments": [],
            })
            op_ids.append(op_id)

        # 2. Get treatment codes for these operators
        cur.execute(
            """SELECT ot.operator_id, t.code
               FROM operator_treatments ot
               JOIN treatments t ON ot.treatment_id = t.id
               WHERE ot.operator_id = ANY(%s)
               ORDER BY t.sort_order""",
            (op_ids,),
        )

        # Build lookup
        op_map = {op["id"]: op for op in operators}
        for op_id, code in cur.fetchall():
            if op_id in op_map:
                op_map[op_id]["treatments"].append(code)

    return operators


def extract_phone_number_id(value: dict) -> str | None:
    """Extract phone_number_id from Meta webhook payload value object."""
    return value.get("metadata", {}).get("phone_number_id")


def build_services_dict(services: dict) -> str:
    """Build service list string for AI system prompt."""
    lines = []
    for code, s in services.items():
        lines.append(
            f"   - {code}: {s['name_it']} ({s['name_en']}) "
            f"- \u20ac{s['price']:.0f}, {s['duration']} min"
        )
    return "\n".join(lines)


def build_system_prompt(biz_context: dict) -> str:
    """Build system prompt dynamically from business context.

    This replaces the hardcoded get_system_prompt() in salon_bot_ec2_latest.py.
    The booking flow rules (STEP 1, STEP 2, etc.) stay the same -- only
    identity, services, and hours are dynamic.
    """
    biz = biz_context["business"]
    services = biz_context["services"]
    hours = biz_context["hours"]
    closures = biz_context["closures"]
    tz = pytz.timezone(biz.get("timezone", "Europe/Rome"))

    # Fresh dates
    now = datetime.now(tz)
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

    # Persona -- use bot_persona if available, otherwise generate default
    persona = biz.get("bot_persona") or f"You are {biz['bot_name']}, an employee at {biz['name']}."

    return f"""{persona}

TODAY'S DATE: {current_date_display} (Year: {current_year})
   IMPORTANT: The current year is {current_year}. NEVER use any other year!

LANGUAGE RULE (CRITICAL) - ITALIAN FIRST:
- DEFAULT LANGUAGE: ITALIAN. Always reply in Italian unless clearly English.
- Single English words like "ok", "hi" -> Still reply in Italian!
- Only switch to English for full sentences like "I would like to book an appointment"

SALON INFO:
   Name: {biz['name']}
   Address: {biz.get('address', 'N/A')}
   Phone: {biz.get('phone', 'N/A')}
   Email: {biz.get('email', 'N/A')}

AVAILABLE SERVICES (use the CODE for function calls, show name_it to customer):
{services_text}

BUSINESS HOURS:
{hours_text}

SPECIAL CLOSURES:
{closures_text}

DATE CALENDAR (next 14 days):
{calendar_text}

BOOKING RULES:
   STEP 1: Customer says what they want -> suggest times (call get_available_slots)
   STEP 2: Customer picks a time -> confirm details -> ask "Confermi?"
   STEP 3: Customer says "si/ok/confermo" -> CALL create_appointment -> THEN confirm
   NEVER say "Prenotazione confermata" without calling create_appointment first!
   If customer says "ok grazie" AFTER booking, do NOT call create_appointment again!

TOOLS:
   - create_appointment: Book (only after customer confirms!)
   - check_availability: Check if a slot is free
   - get_customer_appointments: Show customer's bookings
   - cancel_appointment: Cancel a booking
   - modify_appointment: Change date/time/service
   - get_available_slots: Show all available times for a date
   - confirm_reminder: Customer confirms tomorrow's appointment
   - escalate_to_human: Customer is angry/frustrated

RULES:
   - Only help with salon-related topics
   - Never reveal you are AI/bot
   - Never show internal service codes to customer
   - If year is not specified, use {current_year}
"""


def _build_date_calendar(tz, hours: dict, closures: list) -> str:
    """Build 14-day calendar showing open/closed status."""
    italian_days = ["Lunedi", "Martedi", "Mercoledi", "Giovedi", "Venerdi", "Sabato", "Domenica"]
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
        lines.append(f"   - {day_name} {day.day} {month_name} {day.year} ({date_str}) -> {status} {label}".strip())

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
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "customer_name": {"type": "string", "description": "Customer's full name"},
                        "service_type": {"type": "string", "enum": service_codes, "description": "Service code"},
                        "date": {"type": "string", "description": "Date YYYY-MM-DD"},
                        "time": {"type": "string", "description": "Time HH:MM 24h"},
                    },
                    "required": ["customer_name", "service_type", "date", "time"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "check_availability",
                "description": "Check if a specific time slot is available",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "date": {"type": "string", "description": "Date YYYY-MM-DD"},
                        "time": {"type": "string", "description": "Time HH:MM 24h"},
                    },
                    "required": ["date", "time"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_customer_appointments",
                "description": "Get all future appointments for current customer",
                "strict": True,
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "cancel_appointment",
                "description": "Cancel an appointment",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "customer_name": {"type": "string"},
                        "date": {"type": "string", "description": "Date YYYY-MM-DD"},
                        "time": {"type": "string", "description": "Time HH:MM 24h"},
                    },
                    "required": ["customer_name", "date", "time"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "modify_appointment",
                "description": "Modify an existing appointment",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "customer_name": {"type": "string"},
                        "current_date": {"type": "string"},
                        "current_time": {"type": "string"},
                        "new_date": {"type": ["string", "null"]},
                        "new_time": {"type": ["string", "null"]},
                        "new_service": {"type": ["string", "null"], "enum": service_codes + [None]},
                    },
                    "required": ["customer_name", "current_date", "current_time", "new_date", "new_time", "new_service"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_available_slots",
                "description": "Get all available time slots for a date",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "date": {"type": "string", "description": "Date YYYY-MM-DD"},
                    },
                    "required": ["date"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "confirm_reminder",
                "description": "Customer confirms tomorrow's appointment reminder",
                "strict": True,
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "escalate_to_human",
                "description": "Escalate to human when customer is frustrated/angry",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reason": {"type": "string", "description": "Why escalating"},
                    },
                    "required": ["reason"],
                    "additionalProperties": False,
                },
            },
        },
    ]


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
    if time_str and open_t and close_t:
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
