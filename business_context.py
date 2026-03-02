"""Business context loader -- loads multi-tenant config from the database.

Used by the bot to dynamically load services, hours, persona per business
based on the WhatsApp phone_number_id from the webhook payload.
"""

import os
import psycopg2

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


def build_services_dict(services: dict) -> str:
    """Build service list string for AI system prompt."""
    lines = []
    for code, s in services.items():
        lines.append(
            f"   - {code}: {s['name_it']} ({s['name_en']}) "
            f"- \u20ac{s['price']:.0f}, {s['duration']} min"
        )
    return "\n".join(lines)
