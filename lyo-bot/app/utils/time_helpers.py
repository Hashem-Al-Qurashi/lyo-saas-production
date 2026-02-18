"""Date/time helpers for dynamic prompt generation.

Ported from salon_bot_ec2_latest.py and made multi-tenant:
each function now takes a Business to respect the tenant's timezone and hours.
"""

from datetime import datetime, timedelta

import pytz

from app.models.schemas import Business


# Italian day/month names for calendar display
_ITALIAN_DAYS = [
    "Lunedi", "Martedi", "Mercoledi", "Giovedi",
    "Venerdi", "Sabato", "Domenica",
]
_ITALIAN_MONTHS = [
    "", "Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno",
    "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre",
]


def get_date_context(business: Business) -> dict:
    """Return fresh date context for the business timezone.

    Must be called per-request (never cached) so the prompt always
    contains the correct today/tomorrow.

    Returns
    -------
    dict with keys: today, tomorrow, year, display, calendar
    """
    tz = pytz.timezone(business.timezone)
    now = datetime.now(tz)
    today = now.strftime("%Y-%m-%d")
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    current_year = now.year
    current_date_display = now.strftime("%A, %d %B %Y")

    return {
        "today": today,
        "tomorrow": tomorrow,
        "year": current_year,
        "display": current_date_display,
        "calendar": generate_date_calendar(business),
    }


def generate_date_calendar(business: Business) -> str:
    """Generate a 14-day calendar string using business hours to mark closed days."""
    tz = pytz.timezone(business.timezone)
    now = datetime.now(tz)

    # Build a quick lookup: day_of_week -> is_open
    closed_days: set[int] = set()
    for h in business.hours:
        if not h.is_open:
            closed_days.add(h.day_of_week)

    lines: list[str] = []
    for i in range(14):
        day = now + timedelta(days=i)
        day_name = _ITALIAN_DAYS[day.weekday()]
        month_name = _ITALIAN_MONTHS[day.month]
        date_str = day.strftime("%Y-%m-%d")

        # Determine open/closed from business hours
        if day.weekday() in closed_days:
            status = f"CHIUSO ({day_name})"
        else:
            status = "APERTO"

        label = "(OGGI)" if i == 0 else "(DOMANI)" if i == 1 else ""
        line = f"   - {day_name} {day.day} {month_name} {day.year} ({date_str}) -> {status}"
        if label:
            line = f"{line} {label}"
        lines.append(line)

    return "\n".join(lines)
