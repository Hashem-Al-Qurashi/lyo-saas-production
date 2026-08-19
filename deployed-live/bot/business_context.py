"""Business context loader -- loads multi-tenant config from the database.

Used by the bot to dynamically load services, hours, persona per business
based on the WhatsApp phone_number_id from the webhook payload.
"""

import os
import psycopg2
from datetime import datetime, timedelta
from typing import Optional
import pytz

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "lyo-enterprise-database-v2.cixc4kiw6r00.us-east-1.rds.amazonaws.com"),
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


def load_business_by_instagram_page_id(instagram_page_id: str) -> dict:
    """Look up business by Instagram Business Account ID (entry[].id in IG webhook)."""
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT id, name, slug, timezone, language, bot_name, bot_persona,
                      address, phone, email, owner_email, google_calendar_id,
                      whatsapp_phone_number_id, waba_id, meta_access_token,
                      instagram_page_id, instagram_access_token,
                      google_service_account_json, status, settings
               FROM businesses
               WHERE instagram_page_id = %s AND status = 'active'""",
            (instagram_page_id,),
        )
        row = cur.fetchone()
        if not row:
            raise BusinessNotFoundError(
                f"No active business for instagram_page_id={instagram_page_id}"
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
            """SELECT t.code, t.name_it, t.name_en, t.duration_minutes, t.price,
                      t.description_it, addon.code AS addon_code, addon.name_it AS addon_name,
                      addon.duration_minutes AS addon_duration, addon.price AS addon_price
               FROM treatments t
               LEFT JOIN treatments addon ON t.auto_addon_id = addon.id AND addon.is_active = true
               WHERE t.business_id = %s AND t.is_active = true
               ORDER BY t.sort_order""",
            (business_id,),
        )
        rows = cur.fetchall()

    services = {}
    for row in rows:
        code, name_it, name_en, duration, price, desc = row[0], row[1], row[2], row[3], row[4], row[5]
        addon_code, addon_name, addon_dur, addon_price = row[6], row[7], row[8], row[9]
        svc = {
            "name_it": name_it,
            "name_en": name_en or name_it,
            "duration": duration,
            "price": float(price) if price else 0,
            "description": desc,
        }
        if addon_code:
            svc["auto_addon"] = {
                "code": addon_code,
                "name_it": addon_name,
                "duration": addon_dur,
                "price": float(addon_price) if addon_price else 0,
            }
        services[code] = svc
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
            "open_time": open_time[:5] if open_time else None,
            "close_time": close_time[:5] if close_time else None,
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
            """SELECT closure_date::text, reason, closure_end_date::text
               FROM business_closures
               WHERE business_id = %s
                 AND COALESCE(closure_end_date, closure_date) >= CURRENT_DATE
               ORDER BY closure_date""",
            (business_id,),
        )
        rows = cur.fetchall()

    return [{"date": d, "reason": r, "end_date": e} for d, r, e in rows]


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
            """SELECT id, display_name, is_active, notes
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
        for row in op_rows:
            op_id, display_name, is_active = row[0], row[1], row[2]
            notes = row[3] if len(row) > 3 else None
            operators.append({
                "id": op_id,
                "display_name": display_name,
                "is_active": is_active,
                "notes": notes,
                "treatments": [],
                "hours": {},
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

        # 3. Get per-operator working hours
        cur.execute(
            """SELECT operator_id, day_of_week, is_working,
                      TO_CHAR(start_time, 'HH24:MI'), TO_CHAR(end_time, 'HH24:MI'),
                      TO_CHAR(break_start, 'HH24:MI'), TO_CHAR(break_end, 'HH24:MI')
               FROM operator_hours
               WHERE operator_id = ANY(%s)
               ORDER BY operator_id, day_of_week""",
            (op_ids,),
        )
        for row in cur.fetchall():
            op_id = row[0]
            if op_id in op_map:
                op_map[op_id]["hours"][row[1]] = {
                    "is_working": row[2],
                    "start": row[3],
                    "end": row[4],
                    "break_start": row[5],
                    "break_end": row[6],
                }

    return operators


def resolve_operator(operator_name: Optional[str], service_code: str, operators: list) -> dict:
    """Resolve operator display name to operator_id with validation.

    Returns:
        {"success": True, "operator_id": 1, "operator_name": "Giulia"}  -- specific match
        {"success": True, "auto_assign": True, "eligible_operator_ids": [...]}  -- no preference
        {"success": False, "error": "OPERATOR_SERVICE_MISMATCH", "alternatives": [...]}  -- wrong service
        {"success": False, "error": "OPERATOR_NOT_FOUND"}  -- doesn't exist
        {"success": True, "operator_id": None, "operator_name": None}  -- no operators configured
    """
    # No operators configured -- legacy behavior
    if not operators:
        return {"success": True, "operator_id": None, "operator_name": None}

    # No preference -- auto-assign
    if operator_name is None:
        eligible = [
            op["id"] for op in operators
            if not op.get("treatments") or service_code in op.get("treatments", [])
        ]
        return {"success": True, "auto_assign": True, "eligible_operator_ids": eligible}

    # Find operator by name: exact match first, then unique partial match
    matched_op = None
    partial_matches = []
    for op in operators:
        if op["display_name"].lower() == operator_name.lower():
            matched_op = op
            break
        elif operator_name.lower() in op["display_name"].lower():
            partial_matches.append(op)

    if not matched_op:
        if len(partial_matches) == 1:
            matched_op = partial_matches[0]
        else:
            return {"success": False, "error": "OPERATOR_NOT_FOUND"}

    # Check if operator offers this service (skip if service_code is empty/None)
    if service_code and matched_op.get("treatments") and service_code not in matched_op["treatments"]:
        alternatives = [
            op["display_name"] for op in operators
            if not op.get("treatments") or service_code in op.get("treatments", [])
        ]
        return {
            "success": False,
            "error": "OPERATOR_SERVICE_MISMATCH",
            "operator_name": matched_op["display_name"],
            "service_code": service_code,
            "alternatives": alternatives,
        }

    return {
        "success": True,
        "operator_id": matched_op["id"],
        "operator_name": matched_op["display_name"],
    }


def extract_phone_number_id(value: dict) -> Optional[str]:
    """Extract phone_number_id from Meta webhook payload value object."""
    return value.get("metadata", {}).get("phone_number_id")


# ---------------------------------------------------------------------------
# Legacy lookup helpers (used by older tool dispatcher in salon_bot_with_booking.py)
# ---------------------------------------------------------------------------

def lookup_treatment(business_id: int, treatment_name: str) -> dict:
    """Return treatment info by name (case-insensitive search)."""
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT code, name_it, name_en, duration_minutes, price, description_it
               FROM treatments
               WHERE business_id = %s
                 AND (LOWER(name_it) LIKE LOWER(%s) OR LOWER(code) LIKE LOWER(%s))
                 AND is_active = true
               ORDER BY sort_order LIMIT 1""",
            (business_id, f"%{treatment_name}%", f"%{treatment_name}%"),
        )
        row = cur.fetchone()
    if not row:
        return {"found": False, "treatment_name": treatment_name}
    return {
        "found": True, "code": row[0], "name_it": row[1], "name_en": row[2],
        "duration": row[3], "price": float(row[4]) if row[4] else 0,
        "description": row[5],
    }


def lookup_business_policy(business_id: int, policy_type: str) -> dict:
    """Return a business policy (deposit, cancellation, etc.) from settings."""
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT settings FROM businesses WHERE id = %s", (business_id,))
        row = cur.fetchone()
    if not row or not row[0]:
        return {"found": False, "policy_type": policy_type}
    rules = row[0].get("rules", {}) if isinstance(row[0], dict) else {}
    value = rules.get(policy_type)
    return {"found": bool(value), "policy_type": policy_type, "value": value or "not configured"}


def lookup_closure_dates(business_id: int, month: Optional[str] = None) -> dict:
    """Return special closure dates, optionally filtered by month (YYYY-MM)."""
    with get_db_connection() as conn:
        cur = conn.cursor()
        if month:
            cur.execute(
                """SELECT closure_date::text, closure_end_date::text, reason
                   FROM business_closures
                   WHERE business_id = %s AND TO_CHAR(closure_date, 'YYYY-MM') = %s
                   ORDER BY closure_date""",
                (business_id, month),
            )
        else:
            cur.execute(
                """SELECT closure_date::text, closure_end_date::text, reason
                   FROM business_closures
                   WHERE business_id = %s AND COALESCE(closure_end_date, closure_date) >= CURRENT_DATE
                   ORDER BY closure_date""",
                (business_id,),
            )
        rows = cur.fetchall()
    return {"closures": [{"date": r[0], "end_date": r[1], "reason": r[2]} for r in rows]}


def lookup_faq(business_id: int, question: str) -> dict:
    """Return the best-matching FAQ answer for a question."""
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT question, answer FROM business_faqs
               WHERE business_id = %s AND is_active = true
               ORDER BY sort_order""",
            (business_id,),
        )
        rows = cur.fetchall()
    if not rows:
        return {"found": False, "question": question}
    q_lower = question.lower()
    for q, a in rows:
        if any(word in q.lower() for word in q_lower.split() if len(word) > 3):
            return {"found": True, "question": q, "answer": a}
    # Fallback: first FAQ
    return {"found": True, "question": rows[0][0], "answer": rows[0][1]}


def lookup_operator_for_treatment(business_id: int, treatment_code: str) -> dict:
    """Return operators that can perform the given treatment code."""
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT o.display_name FROM operators o
               JOIN operator_treatments ot ON ot.operator_id = o.id
               JOIN treatments t ON t.id = ot.treatment_id
               WHERE o.business_id = %s AND t.code = %s AND o.is_active = true
               ORDER BY o.sort_order""",
            (business_id, treatment_code),
        )
        names = [r[0] for r in cur.fetchall()]
    return {"treatment_code": treatment_code, "operators": names, "found": bool(names)}


def get_available_treatments(business_id: int) -> dict:
    """Return all active treatments for a business."""
    services = load_services(business_id)
    return {"treatments": list(services.keys()), "details": services}


def build_services_dict(services: dict, operators: list = None) -> str:
    """Build service list string for AI system prompt, including operator assignments."""
    lines = []
    for code, s in services.items():
        total_dur = s['duration']
        total_price = s['price']
        addon = s.get("auto_addon")
        if addon:
            total_dur += addon["duration"]
            total_price += addon["price"]

        line = (
            f"   - {code}: {s['name_it']} ({s['name_en']}) "
            f"- \u20ac{s['price']:.0f}, {s['duration']} min"
        )
        if addon:
            line += (
                f"\n     AUTO-ADDON: After {s['name_it']}, a {addon['name_it']} "
                f"(\u20ac{addon['price']:.0f}, {addon['duration']} min) is AUTOMATICALLY included."
                f"\n     Total: \u20ac{total_price:.0f}, {total_dur} min."
                f"\n     Tell the customer the total duration and price when they book this service."
                f"\n     The system books both automatically — you do NOT need to book the {addon['name_it']} separately."
            )
        if operators:
            eligible = [op["display_name"] for op in operators if code in op.get("treatments", [])]
            if not eligible:
                line += "\n     [INTERNAL: no operators configured — service unavailable]"
        if s.get("description"):
            line += f"\n     Descrizione: {s['description']}"
        lines.append(line)
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

    # Build services list (with per-service operator list)
    operators = biz_context.get("operators", [])
    services_text = build_services_dict(services, operators)

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

    # Build closures text — show full date range so bot knows duration
    def _format_closure_range(c: dict) -> str:
        end = c.get("end_date")
        reason = c.get("reason") or "chiusura speciale"
        if end and end != c["date"]:
            return f"   - {c['date']} fino a {end}: {reason}"
        return f"   - {c['date']}: {reason}"

    closures_text = "\n".join(_format_closure_range(c) for c in closures) if closures else "   (nessuna chiusura speciale)"

    # Build date calendar (next 30 days — must cover any closure + first APERTO date)
    calendar_text = _build_date_calendar(tz, hours, closures)

    # Compute next open day deterministically (P1/P2 fix — never let LLM calculate this)
    italian_days_full = [
        "lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"
    ]
    italian_months_full = [
        "", "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
        "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"
    ]
    next_open_date_str = _compute_next_open_date(closures, hours)
    if next_open_date_str:
        _nod = datetime.strptime(next_open_date_str, "%Y-%m-%d")
        next_open_display = (
            f"{italian_days_full[_nod.weekday()]} {_nod.day} "
            f"{italian_months_full[_nod.month]} {_nod.year}"
        )
    else:
        next_open_display = None

    # Permanently closed weekdays (for redirect reminder — P4)
    perm_closed_days = [
        italian_days_full[dow] for dow in range(7)
        if not hours.get(dow, {"is_open": False}).get("is_open")
    ]
    perm_open_days = [
        italian_days_full[dow] for dow in range(7)
        if hours.get(dow, {"is_open": False}).get("is_open")
    ]
    perm_closed_text = " e ".join(perm_closed_days) if perm_closed_days else "nessuno"
    perm_open_text = ", ".join(perm_open_days) if perm_open_days else "tutti i giorni"

    # Build operators section
    operators_text = _build_operators_section(operators)

    # Build FAQ section
    faqs = biz_context.get("faqs", [])
    faq_text = _build_faq_section(faqs)

    # Pre-compute next-open-day section (can't nest f-strings in Python < 3.12)
    if next_open_display:
        next_open_section = (
            f"NEXT_OPEN_DAY: {next_open_display}\n"
            f"   <- When asked 'quando riaprite?' use EXACTLY this date. Do NOT calculate.\n"
            f"   <- When redirecting to a future date, suggest {next_open_display} or later."
        )
    else:
        next_open_section = "   (nessuna chiusura attiva — il salone è aperto normalmente)"

    perm_closed_reminder = (
        f"PERMANENT WEEKLY CLOSURES: {perm_closed_text} (sempre chiusi, ogni settimana)\n"
        f"   <- When redirecting from any closure/unavailable date, ALWAYS remind the customer:\n"
        f"      \"Ricorda che il {perm_closed_text} siamo sempre chiusi — puoi scegliere tra {perm_open_text}.\""
    )

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

SPECIAL CLOSURES (periodi di chiusura oltre al normale orario settimanale):
{closures_text}
   IMPORTANT: On closure dates the salon is CLOSED even if that weekday is normally open.
   The DATE CALENDAR below already marks these dates as CHIUSO.
   Services marked [MOMENTANEAMENTE NON DISPONIBILE] have no operator — do NOT offer to book them.

{next_open_section}

{perm_closed_reminder}

DATE CALENDAR (next 30 days):
{calendar_text}
{operators_text}{faq_text}
WHEN CUSTOMER SAYS "settimana del X" / "questa settimana" / "settimana prossima":
   - This means the FULL WEEK (Lunedi-Domenica) containing that date, NOT just that single day.
   - Example: "settimana del 20 aprile" = week Mon 20 Apr - Sun 26 Apr.
   - If Monday of that week is CLOSED (giorno di chiusura settimanale o closure period), skip to Tuesday or later.
   - Ask: "In quale giorno e a che ora preferisci in quella settimana?" — do NOT assume it's Monday.

MULTI-DAY BATCH BOOKINGS (Bug #9 + Bug #10 — IMPORTANT):
   - When customer says "tutti i giorni da X a Y", "da mercoledi a sabato", "ogni mattina dal X al Y",
     "every day from X to Y" — recognize this as a MULTI-DAY batch booking, NOT a single-day request.
   - Do NOT reply "In quale giorno preferisci?" — the customer already specified the range.
   - Compute the list of dates in the range (skip CHIUSO days from DATE CALENDAR).
   - Ask ONLY for the time if missing (e.g., "A che ora preferisci ogni mattina?").
   - Then call create_appointment for EACH date in the batch.
   - LIMIT (Bug #10): max 3 appointments per single batch / session. If range exceeds 3 days,
     book only the first 3 and tell customer: "Posso prenotare fino a 3 appuntamenti per volta.
     Ti ho prenotato i primi 3 (mer 22, gio 23, ven 24). Per il resto fammi sapere."
   - The 3-appt cap is per conversation session, counting only newly-created appointments
     (existing ones not counted).

CUSTOMER NAME MEMORY (CRITICAL):
   - If the customer gave their name EARLIER in this conversation, use it — DO NOT ask again.
   - This applies even for a second booking within the same chat session.
   - Example: Customer said "Sono Sara" → use "Sara" for ALL subsequent bookings in this chat.

BOOKING FLOW (Bug #5 — STRICT ORDER, one question at a time):
   STEP 1: Identify TREATMENT. If ambiguous (e.g. "un taglio" → donna o uomo?), ask ONLY about
           treatment. DO NOT bundle with name or day questions.
   STEP 2: Identify DAY. If missing, ask ONLY day.
   STEP 3: Identify TIME. If missing, ask ONLY time.
   STEP 4: Call check_availability(date, time, treatment_code) — BEFORE writing reply.
   STEP 5: ONLY AFTER check returns available=true:
      a) If KNOWN CUSTOMER NAME present in system context → use it, call create_appointment.
      b) If customer gave name earlier in this chat → reuse it, call create_appointment.
      c) ONLY if no name known anywhere → ask casually "A che nome prenoto?" (alone, no other questions).
   STEP 6: AFTER create_appointment succeeds → "Fatto! Ti aspettiamo X alle Y 😊"

   ORDER IS LAW: treatment → day → time → check → (name only if unknown) → create.
   NEVER ask name in step 1, 2, or 3. NEVER bundle questions.

CRITICAL — Bug #6 rule (do NOT misroute mid-booking input):
   While you're collecting day/time for a NEW booking, treat short numeric input as the time/day
   the customer is providing — NOT a query about existing appointments.
   - "30"  / "alle 30" / "15:30" / "le 4" → user is giving you a TIME for the booking in progress.
   - "domani" / "lunedi" / "il 20" → user is giving you a DAY.
   - DO NOT call get_customer_appointments unless customer explicitly says one of:
     "i miei appuntamenti", "cosa ho prenotato", "che appuntamenti ho", "quali appuntamenti".

CRITICAL — Bug #3 rule (NEVER VIOLATE):
   - NEVER write "Perfetto", "Prenoto", "Ti prenoto", "Ti ho prenotato", "Fatto", "Confermato"
     BEFORE check_availability has returned available=true.
   - While checking availability use neutral wording: "Fammi controllare", "Vediamo",
     "Un attimo che controllo".
   - If check returns available=false → propose nearest_alternatives. Do NOT confirm.
   - If you said "Prenoto..." and check then fails, you've already broken the rule —
     check FIRST, speak SECOND.

   If customer says "ok grazie" AFTER booking, do NOT book again — just say bye warmly.

   NAME RULES:
   - Need real name before booking. If already given earlier, reuse it — don't ask twice.
   - Never use "Cliente", "Utente" etc as name. Must be a real name.
   - If name given after booking, use modify_appointment to update.

CRITICAL — DO NOT SUBSTITUTE OPERATOR (Bug H):
   - When customer asks for a SPECIFIC operator (e.g. "con Greta") and they're unavailable:
     - DO offer ALTERNATIVE DAYS for the same operator.
     - DO say "non è disponibile quel giorno".
     - DO NOT suggest a different operator unless customer EXPLICITLY asks "con un'altra?".
   - WRONG: "Posso prenotarti il taglio con un altro stilista" — never say this unprompted.
   - RIGHT: "Greta non è disponibile lunedì. Possiamo provare un altro giorno per Greta?"

CRITICAL — TOOL RESULT IS THE ONLY TRUTH (Bug D):
   - NEVER propose a specific time without first calling check_availability or get_available_slots.
   - The tool result is the ONLY source of truth. Do not "remember" or "guess" availability between turns.
   - Each new time proposal MUST come from a fresh tool call result for THAT specific date+time+operator.
   - WRONG: offer 17:30, customer accepts, you say "17:30 non disponibile" (you guessed wrong twice).

CRITICAL — TIME RANGE → SCAN MULTIPLE DAYS (Bug A/E):
   - When customer says "dalle X in poi" / "ogni giorno dopo le X" / "after X o'clock"
     WITHOUT specifying a single day:
     1. Call get_available_slots in PARALLEL for the next 5 OPEN days.
     2. Find the FIRST day where the requested treatment + operator fits the time range.
     3. Propose ONLY that ONE day.
   - DO NOT reply "troppo tardi, in un giorno in cui chiudiamo più tardi" without naming the day.
   - WRONG: "Potremmo farlo alle 17:00 in un giorno in cui chiudiamo più tardi"
   - RIGHT: "Venerdì possiamo fare alle 18:00. Va bene?"

CRITICAL — MULTI-TREATMENT SEQUENTIAL BOOKING (Bug I):
   - When customer wants 2+ treatments in same chat: "taglio E piega", "taglio poi piega",
     "X dopo Y", "X e poi Y" — interpret as ONE SESSION, sequential, SAME DAY.
   - Calculate end_time of first = start_time + duration. Use that as start_time of second.
   - Call create_appointment for both same date.
   - Confirm together: "Ti aspettiamo X alle Y per [first], poi alle Z per [second]."
   - WRONG: book on different days, or say "possiamo prenotare separatamente" — NEVER.

CRITICAL — BOOKING FOR SOMEONE ELSE (Bug C):
   - When customer says "puo venire la mia amica" / "mia sorella" / "per il mio amico" /
     "per un'altra persona": ASK "Per chi prenoto? Mi serve nome e numero di telefono."
   - DO NOT auto-book with the same phone. Each customer = one phone in DB.
   - If they don't give phone: "Devo registrarla — qual è il suo numero?"

CRITICAL — IDENTITY (Bug J/K):
   - Tu sei "{biz['bot_name']}". MAI usare un nome diverso.
   - Se ti chiedono "chi sei?" → "Sono {biz['bot_name']}, l'assistente di {biz['name']}."
   - VIETATO: "Sono Gianmarco" / "Sono Marco" / qualsiasi altro nome inventato.

TOOLS:
   - create_appointment: Book (only after customer confirms!)
   - check_availability: Check if a slot is free
   - get_customer_appointments: Show customer's bookings
     NOTE (Bug #11): when the customer asks to see/modify/cancel THEIR appointments,
     IMMEDIATELY call get_customer_appointments — do NOT ask for their name or phone.
     You already have their phone number from WhatsApp. Just call the tool directly.
   - cancel_appointment: Cancel a booking
   - modify_appointment: Change date/time/service
   - get_available_slots: Show all available times for a date
   - confirm_reminder: Customer confirms tomorrow's appointment
   - escalate_to_human: Customer is angry/frustrated

CANCELLATION RULE (CRITICAL):
   After a successful cancel_appointment call, treat that slot as COMPLETELY FREE.
   NEVER say "hai già un appuntamento" for a slot you just cancelled.
   If the customer then asks to book the same slot, check availability fresh — do NOT
   assume it is still occupied.

RULES:
   - Only help with salon-related topics
   - Never reveal you are AI/bot
   - Never show internal service codes to customer
   - If year is not specified, use {current_year}
   - NEVER mention prices in booking confirmations. Payment is handled in-salon.
     WRONG: "Ti aspettiamo per il Balayage a €130!" — never include price.
     RIGHT: "Perfetto! Ti aspettiamo sabato per il Balayage. A presto! 😊"
"""


def _build_operators_section(operators: list) -> str:
    """Build STYLISTS/OPERATORS section for the system prompt."""
    if not operators:
        return ""

    # Exclude operators with no treatments when others have treatments configured
    # (no treatments = operator not yet configured for any service)
    has_any_treatments = any(op.get("treatments") for op in operators)
    active_operators = [op for op in operators if op.get("treatments")] if has_any_treatments else operators

    if not active_operators:
        return ""

    if len(active_operators) == 1:
        op = active_operators[0]
        return (
            f"\nSTYLISTS/OPERATORS:\n"
            f"   All appointments are with {op['display_name']}.\n"
            f"   Always pass operator_name=\"{op['display_name']}\" to booking tools.\n"
        )

    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    lines = ["\nSTYLISTS/OPERATORS:"]
    for op in active_operators:
        treatments = ", ".join(op.get("treatments", []))
        notes = f" — {op.get('notes')}" if op.get("notes") else ""
        lines.append(f"   - {op['display_name']}: {treatments}{notes}")
        # Per-operator working hours
        op_hours = op.get("hours", {})
        if op_hours:
            hours_parts = []
            for dow in range(7):
                if dow in op_hours:
                    h = op_hours[dow]
                    dn = day_names[dow]
                    if not h.get("is_working"):
                        hours_parts.append(f"{dn}: OFF")
                    elif h.get("start") and h.get("end"):
                        part = f"{dn}: {h['start']}-{h['end']}"
                        if h.get("break_start") and h.get("break_end"):
                            part += f" (break {h['break_start']}-{h['break_end']})"
                        hours_parts.append(part)
            if hours_parts:
                lines.append(f"     Schedule: {', '.join(hours_parts)}")
                lines.append(f"     IMPORTANT: When asked about {op['display_name']}'s hours, use THESE hours above, NOT the salon hours.")
    lines.append("")
    lines.append("   OPERATOR ASSIGNMENT RULES (CRITICAL):")
    lines.append("   - Each operator listed above can ONLY perform the services listed after their name.")
    lines.append("   - NEVER mention operator names to the customer. Operators are INTERNAL information.")
    lines.append("     Do NOT say 'gli operatori disponibili sono Federica e Sara'.")
    lines.append("     Do NOT list operators. Do NOT ask which operator they prefer.")
    lines.append("     The customer does NOT need to know who will do their service.")
    lines.append("   - ONLY mention an operator name in TWO cases:")
    lines.append("     1. The customer EXPLICITLY asks for someone: 'vorrei con Giulia'")
    lines.append("     2. In the final confirmation AFTER booking, for the MAIN treatment only:")
    lines.append("        'Il tuo appuntamento sarà con [name]' — do NOT name the operator for auto-addon services.")
    lines.append("   - AUTO-ADDON rule: When a service includes an automatic addon (e.g. Taglio + Piega),")
    lines.append("     do NOT reveal the addon operator in your reply. Confirm only the main service and operator.")
    lines.append("     WRONG: 'Il taglio sarà con Giulia, poi la piega con Federica'")
    lines.append("     RIGHT: 'Perfetto! Ti aspettiamo per Taglio Donna + Piega con Giulia'")
    lines.append("   - ADDON OPERATOR PREFERENCE (CRITICAL): Customers CAN request a specific operator for the addon.")
    lines.append("     If the customer says 'taglio con Vittorio e piega con Martina' → ACCEPT IT.")
    lines.append("     Pass operator_name='Vittorio' and addon_operator_name='Martina' in create_appointment.")
    lines.append("     NEVER tell the customer they cannot choose the addon operator. They can.")
    lines.append("     After booking, confirm only: 'Perfetto! Taglio con Vittorio alle 9:00. A presto!'")
    lines.append("   - NEVER confuse the CUSTOMER'S NAME with an OPERATOR NAME. customer_name = person booking.")
    lines.append("   - When calling check_availability: pass operator_name=null (auto-assign).")
    lines.append("   - When calling create_appointment: pass operator_name=null (auto-assign).")
    lines.append("   - EXCEPTION: Customer EXPLICITLY requests a specific person → pass that name.")
    lines.append("   - If a service is temporarily unavailable (no operators for it) → just say")
    lines.append("     'questo servizio non è al momento disponibile', do NOT explain why.")
    lines.append("")
    return "\n".join(lines)


def _build_faq_section(faqs: list) -> str:
    """Build FAQ section for the system prompt."""
    if not faqs:
        return ""
    lines = ["\nFREQUENTLY ASKED QUESTIONS (use these answers when customers ask):"]
    for faq in faqs:
        lines.append(f"   Q: {faq['question']}")
        lines.append(f"   A: {faq['answer']}")
        lines.append("")
    return "\n".join(lines)


def _build_date_calendar(tz, hours: dict, closures: list) -> str:
    """Build 14-day calendar showing open/closed status."""
    italian_days = ["Lunedi", "Martedi", "Mercoledi", "Giovedi", "Venerdi", "Sabato", "Domenica"]
    italian_months = ["", "Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno",
                      "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre"]
    def _is_closed(date_str: str) -> Optional[str]:
        """Return closure reason if date_str falls within any closure range, else None."""
        for c in closures:
            start = c["date"]
            end = c.get("end_date") or start
            if start <= date_str <= end:
                return c.get("reason") or "Chiuso"
        return None

    lines = []
    today = datetime.now(tz)
    for i in range(30):
        day = today + timedelta(days=i)
        day_name = italian_days[day.weekday()]
        month_name = italian_months[day.month]
        date_str = day.strftime("%Y-%m-%d")

        h = hours.get(day.weekday(), {"is_open": False})
        closure_reason = _is_closed(date_str)
        if closure_reason:
            status = f"CHIUSO ({closure_reason})"
        elif not h["is_open"]:
            status = f"CHIUSO ({day_name})"
        else:
            status = "APERTO"

        label = "(OGGI)" if i == 0 else "(DOMANI)" if i == 1 else ""
        lines.append(f"   - {day_name} {day.day} {month_name} {day.year} ({date_str}) -> {status} {label}".strip())

    return "\n".join(lines)


def _compute_next_open_date(closures: list, hours: dict, reference_date: str = None) -> Optional[str]:
    """Return the first YYYY-MM-DD on which the salon is open, scanning from reference_date.

    Accounts for ALL overlapping closure ranges and permanently closed weekdays.
    This is the authoritative source for "quando riaprite?" — never delegate to LLM.

    Returns None if there are no active closures.
    """
    from datetime import date as _date
    ref = _date.fromisoformat(reference_date) if reference_date else _date.today()

    if not closures:
        return None

    for i in range(180):
        candidate = ref + timedelta(days=i)
        date_str = candidate.isoformat()

        # Skip permanently closed weekdays (is_open=False)
        dow = candidate.weekday()  # 0=Monday
        h = hours.get(dow, {"is_open": False})
        if not h.get("is_open"):
            continue

        # Skip if ANY closure range covers this date
        in_closure = any(
            c["date"] <= date_str <= (c.get("end_date") or c["date"])
            for c in closures
        )
        if not in_closure:
            return date_str

    return None


def _build_modify_params(service_codes: list, operator_names: list = None) -> dict:
    """Build modify_appointment tool parameters."""
    props = {
        "customer_name": {"type": "string"},
        "current_date": {"type": "string"},
        "current_time": {"type": "string"},
        "new_date": {"type": ["string", "null"]},
        "new_time": {"type": ["string", "null"]},
        "new_service": {"type": ["string", "null"], "enum": service_codes + [None]},
    }
    required = ["customer_name", "current_date", "current_time", "new_date", "new_time", "new_service"]
    if operator_names:
        props["new_operator"] = {
            "type": ["string", "null"],
            "enum": operator_names + [None],
            "description": "New stylist name, or null to keep current",
        }
        required.append("new_operator")
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


def _build_get_slots_params(service_codes: list = None, operator_names: list = None) -> dict:
    """Build get_available_slots tool parameters."""
    props = {"date": {"type": "string", "description": "Date YYYY-MM-DD"}}
    required = ["date"]
    if service_codes:
        props["service_type"] = {
            "type": ["string", "null"],
            "enum": service_codes + [None],
            "description": "Service code to filter eligible operators. Pass null if unknown.",
        }
        required.append("service_type")
    if operator_names:
        props["operator_name"] = {
            "type": ["string", "null"],
            "enum": operator_names + [None],
            "description": "Stylist name, or null for any available",
        }
        required.append("operator_name")
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


def build_booking_tools(services: dict, operators: list = None) -> list:
    """Build OpenAI function calling tools with dynamic service_type enum.

    Returns the same BOOKING_TOOLS structure but with service codes from DB.
    When operators is non-empty, adds operator_name param to booking tools.
    """
    service_codes = list(services.keys())
    operator_names = [op["display_name"] for op in (operators or [])]
    has_operators = bool(operator_names)

    # Build create_appointment properties
    create_props = {
        "customer_name": {"type": "string", "description": "Customer's full name"},
        "service_type": {"type": "string", "enum": service_codes, "description": "Service code"},
        "date": {"type": "string", "description": "Date YYYY-MM-DD"},
        "time": {"type": "string", "description": "Time HH:MM 24h"},
    }
    create_required = ["customer_name", "service_type", "date", "time"]
    if has_operators:
        create_props["operator_name"] = {
            "type": ["string", "null"],
            "enum": operator_names + [None],
            "description": "Stylist name for the MAIN service, or null for auto-assignment",
        }
        create_required.append("operator_name")
        create_props["addon_operator_name"] = {
            "type": ["string", "null"],
            "enum": operator_names + [None],
            "description": (
                "Stylist name for the AUTO-ADDON service (e.g. piega after taglio/balayage), "
                "if the customer explicitly requested a specific stylist for it. "
                "Pass null to auto-assign the addon operator."
            ),
        }
        create_required.append("addon_operator_name")

    # Build check_availability properties
    check_props = {
        "date": {"type": "string", "description": "Date YYYY-MM-DD"},
        "time": {"type": "string", "description": "Time HH:MM 24h"},
        "service_type": {
            "type": ["string", "null"],
            "enum": service_codes + [None],
            "description": "Service code to verify operator eligibility. Pass null if unknown.",
        },
    }
    check_required = ["date", "time", "service_type"]
    if has_operators:
        check_props["operator_name"] = {
            "type": ["string", "null"],
            "enum": operator_names + [None],
            "description": "Stylist name, or null to check any",
        }
        check_required.append("operator_name")

    return [
        {
            "type": "function",
            "function": {
                "name": "create_appointment",
                "description": "Create a new appointment. ONLY call after customer explicitly confirms.",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": create_props,
                    "required": create_required,
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
                    "properties": check_props,
                    "required": check_required,
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_customer_appointments",
                "description": "Get all future appointments for current customer. ONLY call when customer EXPLICITLY asks to see their bookings (e.g. 'i miei appuntamenti', 'cosa ho prenotato', 'che appuntamenti ho'). NEVER call during a new booking flow when customer is providing a date or time.",
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
                "parameters": _build_modify_params(service_codes, operator_names if has_operators else None),
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_available_slots",
                "description": "Get all available time slots for a date",
                "strict": True,
                "parameters": _build_get_slots_params(service_codes, operator_names if has_operators else None),
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


def load_faqs(business_id: int) -> list:
    """Load FAQ entries for a business.

    Returns:
        [{"question": "...", "answer": "..."}, ...]
    """
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """SELECT question, answer FROM business_faqs
                   WHERE business_id = %s AND is_active = true
                   ORDER BY sort_order""",
                (business_id,),
            )
            return [{"question": q, "answer": a} for q, a in cur.fetchall()]
    except Exception:
        return []


def validate_day_and_time(date_str: str, time_str: str, hours: dict, closures: list) -> dict:
    """Validate if a date/time is within business hours.

    Replaces the hardcoded validate_business_day_and_time().
    """
    # Check closure dates (supports single dates and date ranges)
    for c in closures:
        start = c["date"]
        end = c.get("end_date") or start
        if start <= date_str <= end:
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
    h, m = map(int, open_time.split(":")[:2])
    close_h, close_m = map(int, close_time.split(":")[:2])
    close_total = close_h * 60 + close_m

    current = h * 60 + m
    while current < close_total:
        slots.append(f"{current // 60:02d}:{current % 60:02d}")
        current += interval_minutes

    return slots
