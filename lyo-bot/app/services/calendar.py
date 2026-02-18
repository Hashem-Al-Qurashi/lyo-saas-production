"""Google Calendar integration helpers.

These are plain functions (not a class) so they can be called from
BookingService without lifecycle management.
"""

import json
import logging
from typing import Optional

from app.config import settings
from app.models.schemas import Business

logger = logging.getLogger(__name__)


def get_calendar_service(business: Business):
    """Build and return a Google Calendar API service object, or None."""
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        sa_json = business.google_service_account_json
        if not sa_json:
            # Fall back to a file on disk
            path = settings.google_service_account_file
            if not path:
                logger.debug("No service-account credentials for business %s", business.id)
                return None
            creds = service_account.Credentials.from_service_account_file(
                path, scopes=["https://www.googleapis.com/auth/calendar"]
            )
        else:
            info = json.loads(sa_json) if isinstance(sa_json, str) else sa_json
            creds = service_account.Credentials.from_service_account_info(
                info, scopes=["https://www.googleapis.com/auth/calendar"]
            )

        return build("calendar", "v3", credentials=creds)
    except Exception:
        logger.exception("Failed to build calendar service for business %s", business.id)
        return None


def create_calendar_event(
    business: Business,
    customer_name: str,
    treatment_name: str,
    date_str: str,
    time_str: str,
    duration_minutes: int,
    operator_name: Optional[str] = None,
    customer_phone: Optional[str] = None,
) -> Optional[str]:
    """Create a Google Calendar event and return the event ID, or None."""
    service = get_calendar_service(business)
    if not service:
        return None

    try:
        from datetime import datetime, timedelta
        import pytz

        tz = pytz.timezone(business.timezone)
        start_dt = tz.localize(datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M"))
        end_dt = start_dt + timedelta(minutes=duration_minutes)

        summary_parts = [treatment_name]
        if customer_name:
            summary_parts.append(f"- {customer_name}")

        description_lines = [f"Cliente: {customer_name}"]
        if customer_phone:
            description_lines.append(f"Tel: {customer_phone}")
        if operator_name:
            description_lines.append(f"Operatore: {operator_name}")

        event_body = {
            "summary": " ".join(summary_parts),
            "description": "\n".join(description_lines),
            "start": {"dateTime": start_dt.isoformat(), "timeZone": business.timezone},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": business.timezone},
        }

        event = (
            service.events()
            .insert(calendarId=business.google_calendar_id, body=event_body)
            .execute()
        )
        event_id = event.get("id")
        logger.info("Created calendar event %s for business %s", event_id, business.id)
        return event_id
    except Exception:
        logger.exception("Failed to create calendar event for business %s", business.id)
        return None


def delete_calendar_event(business: Business, event_id: str) -> bool:
    """Delete a Google Calendar event.  Returns True on success."""
    service = get_calendar_service(business)
    if not service:
        return False
    try:
        service.events().delete(
            calendarId=business.google_calendar_id, eventId=event_id
        ).execute()
        logger.info("Deleted calendar event %s for business %s", event_id, business.id)
        return True
    except Exception:
        logger.exception("Failed to delete calendar event %s", event_id)
        return False
