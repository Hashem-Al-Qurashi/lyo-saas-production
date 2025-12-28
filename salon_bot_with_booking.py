"""
AURA HAIR STUDIO - WhatsApp Bot with Calendar/Booking Integration
OpenAI Tools API with strict mode for reliable function calling
"""
import os
import asyncio
import logging
import json
import psycopg2
import psycopg2.errors
import pytz
from datetime import datetime, timedelta
from typing import Dict, Any, List
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx
import openai

# Google Calendar imports
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request as GoogleRequest
from googleapiclient.discovery import build

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURATION
# ============================================================================

# API Keys - MUST be set via environment variables
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY environment variable is required")

# Detect OpenAI SDK version and initialize appropriately
def get_openai_version():
    """Get OpenAI SDK major version"""
    try:
        version_str = openai.__version__
        major = int(version_str.split('.')[0])
        return major
    except:
        return 0

OPENAI_SDK_VERSION = get_openai_version()
logger.info(f"OpenAI SDK version: {openai.__version__} (major: {OPENAI_SDK_VERSION})")

# Initialize client based on SDK version
if OPENAI_SDK_VERSION >= 1:
    # New SDK v1.0+ syntax
    openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)
else:
    # Old SDK v0.x syntax
    openai.api_key = OPENAI_API_KEY
    openai_client = None  # Use module-level calls for old SDK

# WhatsApp Configuration - MUST be set via environment variables
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "961636900357709")
WHATSAPP_WEBHOOK_VERIFY_TOKEN = os.getenv("WHATSAPP_WEBHOOK_VERIFY_TOKEN", "lyosaas2024")

# Database Configuration - from environment variables
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "lyo-enterprise-database.cixc4kiw6r00.us-east-1.rds.amazonaws.com"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "database": os.getenv("DB_NAME", "lyo_production"),
    "user": os.getenv("DB_USER", "lyoadmin"),
    "password": os.getenv("DB_PASSWORD"),
    "sslmode": "require"
}

# Google Calendar Configuration
GOOGLE_CREDS_DIR = Path(os.getenv("GOOGLE_CREDS_DIR", "/home/sakr_quraish/Projects/italian/lyo-saas-clean/google_creds"))
GOOGLE_CREDENTIALS_FILE = GOOGLE_CREDS_DIR / "credentials.json"
GOOGLE_TOKEN_FILE = GOOGLE_CREDS_DIR / "token.json"
GOOGLE_SCOPES = ["https://www.googleapis.com/auth/calendar"]
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")
ITALY_TZ = pytz.timezone("Europe/Rome")

# Google Calendar Service (initialized lazily)
_calendar_service = None

def get_calendar_service():
    """Get or initialize Google Calendar service"""
    global _calendar_service

    if _calendar_service is not None:
        return _calendar_service

    try:
        creds = None

        # Load existing token
        if GOOGLE_TOKEN_FILE.exists():
            creds = Credentials.from_authorized_user_file(str(GOOGLE_TOKEN_FILE), GOOGLE_SCOPES)

        # Refresh or reauthorize if needed
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(GoogleRequest())
            # Save refreshed token
            with open(GOOGLE_TOKEN_FILE, 'w') as f:
                f.write(creds.to_json())

        if not creds or not creds.valid:
            logger.warning("⚠️ Google Calendar credentials invalid or missing")
            return None

        _calendar_service = build('calendar', 'v3', credentials=creds)
        logger.info("✅ Google Calendar service initialized")
        return _calendar_service

    except Exception as e:
        logger.error(f"❌ Failed to initialize Google Calendar: {e}")
        return None

# Business Configuration
BUSINESS_NAME = "Aura Hair Studio"
BUSINESS_TYPE = "beauty_salon"

# Salon Services
SALON_SERVICES = {
    "taglio_donna": {"name_it": "Taglio Donna", "name_en": "Women's Haircut", "price": 60, "duration": 45},
    "taglio_uomo": {"name_it": "Taglio Uomo", "name_en": "Men's Haircut", "price": 40, "duration": 45},
    "piega": {"name_it": "Piega", "name_en": "Styling/Blow-dry", "price": 30, "duration": 30},
    "colore_base": {"name_it": "Colore Base", "name_en": "Basic Color", "price": 70, "duration": 90},
    "balayage": {"name_it": "Balayage/Schiariture", "name_en": "Balayage/Highlights", "price": 130, "duration": 150},
    "trattamento_ristrutturante": {"name_it": "Trattamento Ristrutturante", "name_en": "Restructuring Treatment", "price": 45, "duration": 45},
    "trattamento_cute": {"name_it": "Trattamento Cute", "name_en": "Scalp Treatment", "price": 40, "duration": 30}
}

# Get today's date for the prompt
TODAY = datetime.now().strftime("%Y-%m-%d")
TOMORROW = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

# System prompt with booking capabilities
SYSTEM_PROMPT = f"""You are Simone, an employee at Aura Hair Studio in Milan, Italy.

══════════════════════════════════════════════════════════════
🌐 LANGUAGE RULE #1 - THIS IS THE MOST IMPORTANT RULE:
══════════════════════════════════════════════════════════════
   - DETECT the customer's language from their FIRST message
   - English message (hi, hello, book, haircut, etc.) → REPLY IN ENGLISH ONLY
   - Italian message (ciao, buongiorno, prenotare, taglio, etc.) → REPLY IN ITALIAN ONLY
   - NEVER MIX LANGUAGES! NEVER SWITCH LANGUAGES!
   - If customer says "Book haircut Monday" → Reply in ENGLISH: "Sorry, we're closed on Monday"
   - If customer says "Prenota taglio lunedì" → Reply in ITALIAN: "Mi dispiace, siamo chiusi il lunedì"
══════════════════════════════════════════════════════════════

🚫 IDENTITY RULE:
   - You are Simone, a HUMAN employee - NEVER reveal you are AI/bot
   - If asked "are you a bot?" → "No, I'm Simone!" or "No way, I'm real!"
   - FORBIDDEN WORDS: artificial, virtual, chatbot, language model
   - Speak naturally as a real salon employee

📅 CURRENT DATE INFO:
   - TODAY: {datetime.now().strftime('%A %d %B %Y')} ({TODAY})
   - TOMORROW: {(datetime.now() + timedelta(days=1)).strftime('%A %d %B %Y')} ({TOMORROW})

📍 SALON INFO:
- Name: Aura Hair Studio
- Address: Via dei Giardini 24, 20121 Milano (MI)
- Phone: +39 02 8394 5621
- Email: info@aurahairstudio.it
- Style: Modern, minimal salon specializing in personalized cuts, color, and professional hair treatments

💇 SERVICES (English / Italian):
- Women's Haircut / Taglio Donna: €60 (45 min) - code: "taglio_donna"
- Men's Haircut / Taglio Uomo: €40 (45 min) - code: "taglio_uomo"
- Styling/Blow-dry / Piega: €30 (30 min) - code: "piega"
- Basic Color / Colore Base: €70 (90 min) - code: "colore_base"
- Balayage/Highlights / Balayage/Schiariture: €130 (2h 30min) - code: "balayage"
- Restructuring Treatment / Trattamento Ristrutturante: €45 (45 min) - code: "trattamento_ristrutturante"
- Scalp Treatment / Trattamento Cute: €40 (30 min) - code: "trattamento_cute"

⚠️ If customer asks for a service we DON'T offer (perm, extensions, keratin, etc.):
   → Say "We don't offer [service], but we have: Taglio Donna, Taglio Uomo, Piega, Colore Base, Balayage, Trattamento Ristrutturante, Trattamento Cute"
   → Always list the actual service names!

🕐 BUSINESS HOURS:
══════════════════════════════════════════════════════════════
   CLOSED DAYS: Monday and Sunday ONLY
   OPEN DAYS: Tuesday, Wednesday, Thursday, Friday, Saturday
══════════════════════════════════════════════════════════════
   - Tuesday to Friday: 9:00 AM - 6:00 PM (09:00-18:00)
   - Saturday: 9:00 AM - 5:00 PM (09:00-17:00)

   ⚠️ FRIDAY IS DEFINITELY OPEN (9am-6pm)!
   ⚠️ If customer asks for after 6pm → Say "We close at 6pm, latest slot is around 5pm"

   🎄 HOLIDAY CLOSURES (ONLY these days):
   - December 25 (Christmas Day) - CLOSED
   - January 1 (New Year's Day) - CLOSED
   - ALL OTHER DAYS follow normal schedule!
   - December 26 is a NORMAL Friday - OPEN!
   - December 27 is a NORMAL Saturday - OPEN!

📱 PHONE NUMBER RULE - CRITICAL:
   - You ALREADY have the customer's phone number from WhatsApp
   - When customer wants to modify/cancel → IMMEDIATELY call get_customer_appointments
   - NEVER ask "what's your name?" or "what's your phone?" for modify/cancel
   - Just look up their appointments directly using the tool!

BOOKING FLOW:
1. COLLECT INFO (only for NEW bookings):
   - Customer name
   - Desired service (taglio_donna, taglio_uomo, piega, colore_base, balayage, etc.)
   - Preferred date (convert "tomorrow" to {TOMORROW})
   - Preferred time (use 24h format: 15:00 for 3 PM)

2. CONFIRM ONCE:
   - After collecting everything, confirm: "Perfect! Summary: [Name], [Service] on [Date] at [Time]. Should I confirm the booking?"
   - Wait for confirmation (yes/ok/confirm)
   - THEN call create_appointment

3. USE FUNCTIONS:
   - create_appointment: Book the appointment (only after confirmation!)
   - check_availability: Check if a specific time slot is available
   - get_available_slots: Show ALL available times for a date
   - get_customer_appointments: Show customer's bookings
   - modify_appointment: Change/reschedule an existing appointment
   - cancel_appointment: Cancel a booking

4. MODIFY/CANCEL - IMPORTANT:
   - If customer wants to modify or cancel:
     → Call get_customer_appointments to see their appointments
     → If they have ONE appointment, PROCEED IMMEDIATELY with the action
     → If they have MULTIPLE appointments, ask ONCE which one

   - MODIFY RULE: If customer says "reschedule to 4pm" or "move it to 3pm":
     → They already specified the new time! Don't ask again!
     → Call modify_appointment IMMEDIATELY with the new time
     → DON'T ask "would you like to reschedule to 4pm?" - just DO IT!

   - CONFLICTING TIMES: If customer says "3pm no wait 4pm actually 5pm":
     → Use the LAST time mentioned (5pm in this example)
     → Call modify_appointment with that final time
     → Don't check all times - just use the last one they said

   - To modify: MUST call modify_appointment(appointment_id, new_date, new_time, new_service)
   - To cancel: MUST call cancel_appointment(appointment_id)

5. TIME FORMAT:
   - Always show times in 12h format (e.g., "6:00 PM" instead of "18:00")
   - Accept input in both 12h and 24h from customer
   - Always convert to 24h format when calling functions (e.g., "10 AM" → "10:00", "6 PM" → "18:00")

⚠️ CRITICAL RULE - NEVER LIE:
   - NEVER say "done", "confirmed", "modified", "cancelled" WITHOUT calling the tool!
   - You MUST call the tool (create_appointment, modify_appointment, cancel_appointment)
   - ONLY AFTER the tool returns success=True can you confirm to the customer
   - If you don't call the tool, NOTHING was done!
   - WRONG example: saying "I modified it" without calling modify_appointment
   - CORRECT example: call modify_appointment, see success=True, then say "modified"

🔧 AVAILABLE TOOLS - YOU MUST USE THESE:
   You have access to these tools. To perform ANY booking action, you MUST call the appropriate tool:

   1. create_appointment(customer_name, service_type, date, time)
      → Call when: Customer confirms a booking
      → NEVER just say "booked" - you MUST call this tool

   2. get_customer_appointments()
      → Call when: Customer wants to see, modify, or cancel appointments
      → Returns appointment 'id' needed for modify/cancel

   3. modify_appointment(appointment_id, new_date, new_time, new_service)
      → Call when: Customer wants to reschedule or change service
      → Use null for fields that shouldn't change
      → NEVER just say "rescheduled" - you MUST call this tool

   4. cancel_appointment(appointment_id)
      → Call when: Customer confirms they want to cancel
      → NEVER just say "cancelled" - you MUST call this tool

   5. check_availability(date, time)
      → Call when: Need to verify a specific slot is free

   6. get_available_slots(date)
      → Call when: Customer asks what times are available

   ⚡ ACTION = TOOL CALL
   If customer says "reschedule to 3pm" → CALL modify_appointment
   If customer says "cancel my appointment" → CALL cancel_appointment
   If customer says "yes, book it" → CALL create_appointment
   TALKING about doing something is NOT the same as DOING it!

Respond naturally and warmly like a real salon employee named Simone."""

# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def normalize_phone(phone: str) -> str:
    """
    Normalize phone number to consistent format.
    Handles: +393312671591, 393312671591, 03312671591, etc.
    Returns: digits only, no leading zeros
    """
    if not phone:
        return phone
    # Remove all non-digits
    digits = ''.join(c for c in phone if c.isdigit())
    # Remove leading zeros (but keep at least the number)
    digits = digits.lstrip('0') or digits
    return digits

# ============================================================================
# GOOGLE CALENDAR FUNCTIONS
# ============================================================================

def create_calendar_event(customer_name: str, service: Dict, date_str: str, time_str: str, customer_phone: str = None) -> str:
    """
    Create a Google Calendar event for the appointment.
    Returns: event_id if successful, None if failed
    """
    try:
        service_obj = get_calendar_service()
        if not service_obj:
            logger.warning("⚠️ Google Calendar not available, skipping event creation")
            return None

        # Parse date and time
        dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        start_dt = ITALY_TZ.localize(dt)
        end_dt = start_dt + timedelta(minutes=service.get("duration", 60))

        event = {
            "summary": f"{service.get('name_it', 'Appuntamento')} - {customer_name}",
            "description": f"Cliente: {customer_name}\nTelefono: {customer_phone or 'N/A'}\nServizio: {service.get('name_it')}\nPrezzo: €{service.get('price', 0)}",
            "start": {
                "dateTime": start_dt.isoformat(),
                "timeZone": "Europe/Rome"
            },
            "end": {
                "dateTime": end_dt.isoformat(),
                "timeZone": "Europe/Rome"
            },
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": 60},
                    {"method": "popup", "minutes": 15}
                ]
            }
        }

        result = service_obj.events().insert(calendarId=GOOGLE_CALENDAR_ID, body=event).execute()
        event_id = result.get("id")
        logger.info(f"✅ Calendar event created: {event_id}")
        return event_id

    except Exception as e:
        logger.error(f"❌ Failed to create calendar event: {e}")
        return None


def update_calendar_event(event_id: str, customer_name: str, service: Dict, date_str: str, time_str: str, customer_phone: str = None) -> bool:
    """
    Update an existing Google Calendar event.
    Returns: True if successful, False if failed
    """
    if not event_id:
        return False

    try:
        service_obj = get_calendar_service()
        if not service_obj:
            logger.warning("⚠️ Google Calendar not available, skipping event update")
            return False

        # Parse date and time
        dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        start_dt = ITALY_TZ.localize(dt)
        end_dt = start_dt + timedelta(minutes=service.get("duration", 60))

        event = {
            "summary": f"{service.get('name_it', 'Appuntamento')} - {customer_name}",
            "description": f"Cliente: {customer_name}\nTelefono: {customer_phone or 'N/A'}\nServizio: {service.get('name_it')}\nPrezzo: €{service.get('price', 0)}",
            "start": {
                "dateTime": start_dt.isoformat(),
                "timeZone": "Europe/Rome"
            },
            "end": {
                "dateTime": end_dt.isoformat(),
                "timeZone": "Europe/Rome"
            }
        }

        service_obj.events().update(calendarId=GOOGLE_CALENDAR_ID, eventId=event_id, body=event).execute()
        logger.info(f"✅ Calendar event updated: {event_id}")
        return True

    except Exception as e:
        logger.error(f"❌ Failed to update calendar event: {e}")
        return False


def delete_calendar_event(event_id: str) -> bool:
    """
    Delete a Google Calendar event.
    Returns: True if successful, False if failed
    """
    if not event_id:
        return False

    try:
        service_obj = get_calendar_service()
        if not service_obj:
            logger.warning("⚠️ Google Calendar not available, skipping event deletion")
            return False

        service_obj.events().delete(calendarId=GOOGLE_CALENDAR_ID, eventId=event_id).execute()
        logger.info(f"✅ Calendar event deleted: {event_id}")
        return True

    except Exception as e:
        logger.error(f"❌ Failed to delete calendar event: {e}")
        return False

# ============================================================================
# DATABASE FUNCTIONS
# ============================================================================

def get_db_connection():
    """Get database connection"""
    return psycopg2.connect(**DB_CONFIG)

def initialize_database():
    """Initialize salon appointments table"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Create appointments table for salon
        cur.execute("""
            CREATE TABLE IF NOT EXISTS salon_appointments (
                id SERIAL PRIMARY KEY,
                customer_phone VARCHAR(20) NOT NULL,
                customer_name VARCHAR(100) NOT NULL,
                service_type VARCHAR(50) NOT NULL,
                appointment_date DATE NOT NULL,
                appointment_time TIME NOT NULL,
                duration_minutes INTEGER DEFAULT 60,
                price DECIMAL(10,2),
                status VARCHAR(20) DEFAULT 'confirmed',
                google_event_id VARCHAR(255),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Create conversation history table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS salon_conversations (
                id SERIAL PRIMARY KEY,
                phone VARCHAR(20) NOT NULL,
                name VARCHAR(100),
                message TEXT,
                response TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        conn.commit()
        conn.close()
        logger.info("✅ Database initialized")
        return True
    except Exception as e:
        logger.error(f"❌ Database init error: {e}")
        return False

# ============================================================================
# BUSINESS HOURS VALIDATION
# ============================================================================

def validate_business_day_and_time(date_str: str, time_str: str = None) -> Dict[str, Any]:
    """
    Validate that the date/time falls within business hours.

    Business Rules:
    - CLOSED: Monday (weekday 0) and Sunday (weekday 6)
    - OPEN: Tuesday-Friday 9:00-18:00
    - OPEN: Saturday 9:00-17:00
    - HOLIDAYS CLOSED: December 25, January 1

    Returns: {"valid": True} or {"valid": False, "error": str, "error_code": str}
    """
    try:
        parsed_date = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return {"valid": False, "error": "Invalid date format", "error_code": "INVALID_DATE_FORMAT"}

    weekday = parsed_date.weekday()  # Monday=0, Sunday=6
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    day_names_it = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"]

    # Check for holidays (December 25 and January 1)
    month_day = (parsed_date.month, parsed_date.day)
    if month_day == (12, 25):
        return {
            "valid": False,
            "error": "We are closed on Christmas Day (December 25)",
            "error_it": "Siamo chiusi il giorno di Natale (25 dicembre)",
            "error_code": "CLOSED_HOLIDAY_CHRISTMAS"
        }
    if month_day == (1, 1):
        return {
            "valid": False,
            "error": "We are closed on New Year's Day (January 1)",
            "error_it": "Siamo chiusi il giorno di Capodanno (1 gennaio)",
            "error_code": "CLOSED_HOLIDAY_NEWYEAR"
        }

    # Check for closed days (Monday and Sunday)
    if weekday == 0:  # Monday
        return {
            "valid": False,
            "error": f"We are closed on Mondays. We're open Tuesday-Saturday.",
            "error_it": f"Siamo chiusi il lunedì. Siamo aperti da martedì a sabato.",
            "error_code": "CLOSED_MONDAY"
        }
    if weekday == 6:  # Sunday
        return {
            "valid": False,
            "error": f"We are closed on Sundays. We're open Tuesday-Saturday.",
            "error_it": f"Siamo chiusi la domenica. Siamo aperti da martedì a sabato.",
            "error_code": "CLOSED_SUNDAY"
        }

    # Business hours validation - COMMENTED OUT until client confirms hours
    # if time_str:
    #     try:
    #         parsed_time = datetime.strptime(time_str, "%H:%M")
    #         hour = parsed_time.hour
    #         minute = parsed_time.minute
    #     except ValueError:
    #         return {"valid": False, "error": "Invalid time format", "error_code": "INVALID_TIME_FORMAT"}
    #
    #     # Saturday hours: 9:00-17:00
    #     if weekday == 5:  # Saturday
    #         if hour < 9 or (hour >= 17):
    #             return {
    #                 "valid": False,
    #                 "error": f"On Saturdays we're open 9:00-17:00. {time_str} is outside our hours.",
    #                 "error_it": f"Il sabato siamo aperti dalle 9:00 alle 17:00. {time_str} è fuori orario.",
    #                 "error_code": "OUTSIDE_SATURDAY_HOURS"
    #             }
    #     else:
    #         # Tuesday-Friday hours: 9:00-18:00
    #         if hour < 9 or (hour >= 18):
    #             return {
    #                 "valid": False,
    #                 "error": f"We're open 9:00-18:00 on {day_names[weekday]}. {time_str} is outside our hours.",
    #                 "error_it": f"Siamo aperti dalle 9:00 alle 18:00 il {day_names_it[weekday]}. {time_str} è fuori orario.",
    #                 "error_code": "OUTSIDE_BUSINESS_HOURS"
    #             }

    return {"valid": True}


# ============================================================================
# BOOKING FUNCTIONS (Called by AI)
# ============================================================================

def create_appointment(customer_phone: str, customer_name: str, service_type: str, date: str, time: str) -> Dict[str, Any]:
    """Create a salon appointment"""
    try:
        # Normalize phone
        normalized_phone = normalize_phone(customer_phone)

        # Validate customer name
        if not customer_name or not customer_name.strip():
            return {"success": False, "error": "CUSTOMER_NAME_REQUIRED"}
        customer_name = customer_name.strip()

        # Validate service
        service = SALON_SERVICES.get(service_type.lower())
        if not service:
            return {
                "success": False,
                "error": "INVALID_SERVICE",
                "provided": service_type,
                "valid_services": list(SALON_SERVICES.keys())
            }

        # Validate date and time together (check if in the past)
        try:
            appointment_datetime = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
            if appointment_datetime < datetime.now():
                return {"success": False, "error": "PAST_DATE_NOT_ALLOWED"}
        except ValueError:
            return {"success": False, "error": "INVALID_DATE_TIME_FORMAT", "provided_date": date, "provided_time": time}

        # Validate business hours, closed days, and holidays
        business_validation = validate_business_day_and_time(date, time)
        if not business_validation["valid"]:
            return {
                "success": False,
                "error": business_validation["error_code"],
                "message": business_validation["error"],
                "message_it": business_validation.get("error_it", ""),
                "date": date,
                "time": time
            }

        conn = get_db_connection()
        try:
            cur = conn.cursor()

            # Check availability
            cur.execute(
                """SELECT COUNT(*) FROM salon_appointments
                   WHERE appointment_date = %s AND appointment_time = %s AND status = 'confirmed'""",
                (date, time)
            )
            count = cur.fetchone()[0]

            if count > 0:
                return {
                    "success": False,
                    "error": "SLOT_ALREADY_BOOKED",
                    "date": date,
                    "time": time
                }

            # Create Google Calendar event first
            google_event_id = create_calendar_event(
                customer_name=customer_name,
                service=service,
                date_str=date,
                time_str=time,
                customer_phone=normalized_phone
            )

            # Create appointment with google_event_id
            cur.execute(
                """INSERT INTO salon_appointments
                   (customer_phone, customer_name, service_type, appointment_date, appointment_time, duration_minutes, price, status, google_event_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, 'confirmed', %s)
                   RETURNING id""",
                (normalized_phone, customer_name, service_type, date, time, service["duration"], service["price"], google_event_id)
            )

            appointment_id = cur.fetchone()[0]
            conn.commit()

            calendar_note = " (synced to calendar)" if google_event_id else ""
            logger.info(f"✅ Appointment created: #{appointment_id} for {customer_name}{calendar_note}")

            return {
                "success": True,
                "appointment_id": appointment_id,
                "customer_name": customer_name,
                "service": service['name_it'],
                "service_en": service.get('name_en', service_type),
                "date": date,
                "time": time,
                "price": service['price'],
                "calendar_synced": bool(google_event_id)
            }
        finally:
            conn.close()

    except psycopg2.errors.UniqueViolation:
        # Race condition: slot was taken between check and insert
        logger.warning(f"⚠️ Race condition: slot {date} {time} taken by another booking")
        return {
            "success": False,
            "error": "SLOT_JUST_BOOKED",
            "date": date,
            "time": time
        }
    except Exception as e:
        logger.error(f"❌ Create appointment error: {e}")
        return {"success": False, "error": "BOOKING_ERROR", "details": str(e)}

def check_availability(date: str, time: str) -> Dict[str, Any]:
    """Check if a time slot is available"""
    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """SELECT COUNT(*) FROM salon_appointments
                   WHERE appointment_date = %s AND appointment_time = %s AND status = 'confirmed'""",
                (date, time)
            )
            count = cur.fetchone()[0]
            available = count == 0

            return {
                "success": True,
                "available": available,
                "date": date,
                "time": time
            }
        finally:
            conn.close()

    except Exception as e:
        return {"success": False, "error": str(e)}

def format_time_12h(time_str: str) -> str:
    """Convert 24h time (HH:MM) to 12h format (h:MM AM/PM)"""
    try:
        time_obj = datetime.strptime(str(time_str)[:5], "%H:%M")
        return time_obj.strftime("%I:%M %p").lstrip("0")  # "6:00 PM" not "06:00 PM"
    except:
        return str(time_str)[:5]

def get_customer_appointments(customer_phone: str) -> Dict[str, Any]:
    """Get all FUTURE appointments for a customer (filters out past appointments)"""
    try:
        # Normalize phone
        normalized_phone = normalize_phone(customer_phone)
        # Format phone for display (last 4 digits)
        phone_display = f"***{normalized_phone[-4:]}" if len(normalized_phone) >= 4 else normalized_phone
        now = datetime.now()
        today = now.date()

        conn = get_db_connection()
        try:
            cur = conn.cursor()
            # Only get future appointments (today with future time, or future dates)
            cur.execute(
                """SELECT id, customer_name, service_type, appointment_date, appointment_time, price, status
                   FROM salon_appointments
                   WHERE customer_phone = %s AND status = 'confirmed'
                   AND (appointment_date > %s OR (appointment_date = %s AND appointment_time > %s))
                   ORDER BY appointment_date, appointment_time""",
                (normalized_phone, today, today, now.strftime("%H:%M"))
            )

            appointments = []
            for idx, row in enumerate(cur.fetchall(), 1):
                service = SALON_SERVICES.get(row[2], {})
                apt_id = row[0]
                appointments.append({
                    "appointment_id": apt_id,  # ⚠️ USE THIS ID for modify_appointment and cancel_appointment
                    "booked_name": row[1],  # Name given when booking
                    "service_code": row[2],
                    "service_en": service.get("name_en", row[2]),
                    "service_it": service.get("name_it", row[2]),
                    "date": str(row[3]),
                    "time": format_time_12h(row[4]),
                    "price": float(row[5]) if row[5] else 0,
                    "status": row[6]
                })

            if not appointments:
                return {
                    "success": True,
                    "your_phone": phone_display,
                    "appointments": [],
                    "count": 0
                }

            return {
                "success": True,
                "your_phone": phone_display,
                "note": "IMPORTANT: Use 'appointment_id' (not any other number) when calling modify_appointment or cancel_appointment",
                "appointments": appointments,
                "count": len(appointments)
            }
        finally:
            conn.close()

    except Exception as e:
        return {"success": False, "error": str(e)}

def cancel_appointment(customer_phone: str, appointment_id: int) -> Dict[str, Any]:
    """Cancel an appointment"""
    try:
        # Normalize phone
        normalized_phone = normalize_phone(customer_phone)

        conn = get_db_connection()
        try:
            cur = conn.cursor()

            # Verify appointment belongs to customer and get google_event_id
            cur.execute(
                """SELECT id, google_event_id FROM salon_appointments
                   WHERE id = %s AND customer_phone = %s AND status = 'confirmed'""",
                (appointment_id, normalized_phone)
            )

            row = cur.fetchone()
            if not row:
                return {
                    "success": False,
                    "error": "APPOINTMENT_NOT_FOUND",
                    "appointment_id": appointment_id
                }

            google_event_id = row[1]

            # Delete from Google Calendar
            if google_event_id:
                delete_calendar_event(google_event_id)

            # Cancel appointment
            cur.execute(
                "UPDATE salon_appointments SET status = 'cancelled' WHERE id = %s",
                (appointment_id,)
            )
            conn.commit()

            calendar_note = " (removed from calendar)" if google_event_id else ""
            logger.info(f"✅ Appointment #{appointment_id} cancelled{calendar_note}")

            return {
                "success": True,
                "cancelled_appointment_id": appointment_id,
                "calendar_updated": bool(google_event_id)
            }
        finally:
            conn.close()

    except Exception as e:
        return {"success": False, "error": str(e)}

def modify_appointment(
    customer_phone: str,
    appointment_id: int,
    new_date: str = None,
    new_time: str = None,
    new_service: str = None
) -> Dict[str, Any]:
    """
    Modify an existing appointment.
    Can change date, time, service, or any combination.
    """
    try:
        # Normalize phone
        normalized_phone = normalize_phone(customer_phone)

        conn = get_db_connection()
        try:
            cur = conn.cursor()

            # Find the appointment
            cur.execute(
                """SELECT id, customer_name, service_type, appointment_date, appointment_time, google_event_id
                   FROM salon_appointments
                   WHERE id = %s AND customer_phone = %s AND status = 'confirmed'""",
                (appointment_id, normalized_phone)
            )

            appointment = cur.fetchone()
            if not appointment:
                return {
                    "success": False,
                    "error": "APPOINTMENT_NOT_FOUND",
                    "appointment_id": appointment_id
                }

            # Current values
            current_name = appointment[1]
            current_service = appointment[2]
            current_date = str(appointment[3])
            current_time = str(appointment[4])[:5]  # HH:MM format
            google_event_id = appointment[5]

            # Determine new values (use current if not provided)
            final_date = new_date if new_date else current_date
            final_time = new_time if new_time else current_time
            final_service = new_service.lower() if new_service else current_service

            # Validate new service ONLY if being changed
            if new_service:
                service = SALON_SERVICES.get(final_service)
                if not service:
                    return {
                        "success": False,
                        "error": "INVALID_SERVICE",
                        "provided": final_service,
                        "valid_services": list(SALON_SERVICES.keys())
                    }
            else:
                # Keep existing service - get it for duration/price or use defaults
                service = SALON_SERVICES.get(final_service, {"duration": 45, "price": 35, "name_it": final_service})

            # Validate new date and time together (check if in the past)
            try:
                final_datetime = datetime.strptime(f"{final_date} {final_time}", "%Y-%m-%d %H:%M")
                if final_datetime < datetime.now():
                    return {"success": False, "error": "PAST_DATE_NOT_ALLOWED"}
            except ValueError:
                return {"success": False, "error": "INVALID_DATE_TIME_FORMAT", "provided_date": final_date, "provided_time": final_time}

            # Validate business hours, closed days, and holidays
            business_validation = validate_business_day_and_time(final_date, final_time)
            if not business_validation["valid"]:
                return {
                    "success": False,
                    "error": business_validation["error_code"],
                    "message": business_validation["error"],
                    "message_it": business_validation.get("error_it", ""),
                    "date": final_date,
                    "time": final_time
                }

            # Check if new slot is available (only if date or time changed)
            if new_date or new_time:
                cur.execute(
                    """SELECT COUNT(*) FROM salon_appointments
                       WHERE appointment_date = %s AND appointment_time = %s
                       AND status = 'confirmed' AND id != %s""",
                    (final_date, final_time, appointment_id)
                )
                if cur.fetchone()[0] > 0:
                    return {
                        "success": False,
                        "error": "SLOT_ALREADY_BOOKED",
                        "date": final_date,
                        "time": final_time
                    }

            # Update the appointment
            cur.execute(
                """UPDATE salon_appointments
                   SET appointment_date = %s, appointment_time = %s, service_type = %s,
                       duration_minutes = %s, price = %s
                   WHERE id = %s""",
                (final_date, final_time, final_service, service["duration"], service["price"], appointment_id)
            )

            conn.commit()

            # Update Google Calendar event
            if google_event_id:
                update_calendar_event(
                    event_id=google_event_id,
                    customer_name=current_name,
                    service=service,
                    date_str=final_date,
                    time_str=final_time,
                    customer_phone=normalized_phone
                )

            calendar_note = " (calendar updated)" if google_event_id else ""
            logger.info(f"✅ Appointment #{appointment_id} modified: {final_date} {final_time} {final_service}{calendar_note}")

            # Build change details
            changes = {}
            if new_date and new_date != current_date:
                changes["date"] = {"from": current_date, "to": final_date}
            if new_time and new_time != current_time:
                changes["time"] = {"from": current_time, "to": final_time}
            if new_service and new_service.lower() != current_service.lower():
                changes["service"] = {"from": current_service, "to": final_service}

            return {
                "success": True,
                "appointment_id": appointment_id,
                "customer_name": current_name,
                "service": service['name_it'],
                "service_en": service.get('name_en', final_service),
                "new_date": final_date,
                "new_time": final_time,
                "changes": changes,
                "calendar_updated": bool(google_event_id)
            }
        finally:
            conn.close()

    except psycopg2.errors.UniqueViolation:
        # Race condition: new slot was taken between check and update
        logger.warning(f"⚠️ Race condition: slot taken during modify for appointment #{appointment_id}")
        return {
            "success": False,
            "error": "SLOT_JUST_BOOKED"
        }
    except Exception as e:
        logger.error(f"❌ Modify appointment error: {e}")
        return {"success": False, "error": "MODIFICATION_ERROR", "details": str(e)}

def get_available_slots(date: str) -> Dict[str, Any]:
    """
    Get available time slots for a specific date.
    Returns 30-minute slots during business hours that are not booked.
    Business hours: Tue-Fri 9:00-18:00, Sat 9:00-17:00
    Closed: Monday, Sunday, Dec 25, Jan 1
    """
    try:
        # Validate date
        try:
            parsed_date = datetime.strptime(date, "%Y-%m-%d")
            now = datetime.now()
            if parsed_date.date() < now.date():
                return {"success": False, "error": "PAST_DATE_NOT_ALLOWED"}
            is_today = parsed_date.date() == now.date()
        except ValueError:
            return {"success": False, "error": "INVALID_DATE_FORMAT", "provided_date": date}

        # Check if it's a closed day (without time validation)
        business_validation = validate_business_day_and_time(date, None)
        if not business_validation["valid"]:
            return {
                "success": True,  # Success but no slots
                "date": date,
                "available_slots": [],
                "count": 0,
                "closed": True,
                "reason": business_validation["error"],
                "reason_it": business_validation.get("error_it", "")
            }

        # Determine closing hour based on day
        weekday = parsed_date.weekday()
        closing_hour = 17 if weekday == 5 else 18  # Saturday: 17:00, others: 18:00

        conn = get_db_connection()
        try:
            cur = conn.cursor()

            # Get all booked times for this date
            cur.execute(
                """SELECT appointment_time FROM salon_appointments
                   WHERE appointment_date = %s AND status = 'confirmed'""",
                (date,)
            )

            booked_times = set()
            for row in cur.fetchall():
                # Store as HH:MM string
                booked_times.add(str(row[0])[:5])
        finally:
            conn.close()

        # Generate all possible slots based on business hours
        all_slots = []
        for hour in range(9, closing_hour):
            all_slots.append(f"{hour:02d}:00")
            all_slots.append(f"{hour:02d}:30")

        # Filter out booked slots
        available_slots = [slot for slot in all_slots if slot not in booked_times]

        # If today, filter out past times (with 30 min buffer)
        if is_today:
            current_time = now + timedelta(minutes=30)
            current_str = current_time.strftime("%H:%M")
            available_slots = [slot for slot in available_slots if slot >= current_str]

        if not available_slots:
            return {
                "success": True,
                "date": date,
                "available_slots": [],
                "count": 0
            }

        return {
            "success": True,
            "date": date,
            "available_slots": available_slots,
            "count": len(available_slots)
        }

    except Exception as e:
        logger.error(f"❌ Get available slots error: {e}")
        return {"success": False, "error": str(e)}

# ============================================================================
# OPENAI TOOLS DEFINITIONS (New API - replaces deprecated 'functions')
# Using strict mode for guaranteed schema compliance
# ============================================================================

BOOKING_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "create_appointment",
            "description": "Create a salon appointment. Call this IMMEDIATELY when customer confirms booking (says yes/ok/confirm).",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_name": {
                        "type": "string",
                        "description": "Customer's full name"
                    },
                    "service_type": {
                        "type": "string",
                        "description": "Service type code",
                        "enum": ["taglio_donna", "taglio_uomo", "piega", "colore_base", "balayage", "trattamento_ristrutturante", "trattamento_cute"]
                    },
                    "date": {
                        "type": "string",
                        "description": f"Appointment date in YYYY-MM-DD format. Today is {TODAY}, tomorrow is {TOMORROW}"
                    },
                    "time": {
                        "type": "string",
                        "description": "Appointment time in HH:MM 24h format (e.g., 15:00 for 3 PM)"
                    }
                },
                "required": ["customer_name", "service_type", "date", "time"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_availability",
            "description": "Check if a specific date and time slot is available for booking.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Date in YYYY-MM-DD format"
                    },
                    "time": {
                        "type": "string",
                        "description": "Time in HH:MM 24h format"
                    }
                },
                "required": ["date", "time"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_customer_appointments",
            "description": "Get customer's active appointments. MUST call this FIRST before modify or cancel. Returns 'id' field needed for modify/cancel operations.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_appointment",
            "description": "Cancel an appointment. Use the 'id' field from get_customer_appointments result.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "appointment_id": {
                        "type": "integer",
                        "description": "The 'id' field from get_customer_appointments result"
                    }
                },
                "required": ["appointment_id"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "modify_appointment",
            "description": "Modify/reschedule an appointment. Use the 'id' field from get_customer_appointments result.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "appointment_id": {
                        "type": "integer",
                        "description": "The 'id' field from get_customer_appointments result"
                    },
                    "new_date": {
                        "type": ["string", "null"],
                        "description": f"New date in YYYY-MM-DD format. Today is {TODAY}, tomorrow is {TOMORROW}. Use null if not changing date."
                    },
                    "new_time": {
                        "type": ["string", "null"],
                        "description": "New time in HH:MM 24h format. Use null if not changing time."
                    },
                    "new_service": {
                        "type": ["string", "null"],
                        "description": "New service code. Use null if not changing service.",
                        "enum": ["taglio_donna", "taglio_uomo", "piega", "colore_base", "balayage", "trattamento_ristrutturante", "trattamento_cute", None]
                    }
                },
                "required": ["appointment_id", "new_date", "new_time", "new_service"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_available_slots",
            "description": "Show all available time slots for a specific date.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": f"Date in YYYY-MM-DD format. Today is {TODAY}, tomorrow is {TOMORROW}"
                    }
                },
                "required": ["date"],
                "additionalProperties": False
            }
        }
    }
]

# Convert tools format to functions format for old SDK (v0.x)
def convert_tools_to_functions(tools):
    """Convert new 'tools' format to old 'functions' format for SDK v0.x"""
    functions = []
    for tool in tools:
        if tool.get("type") == "function":
            func = tool["function"].copy()
            # Remove 'strict' field not supported in old SDK
            func.pop("strict", None)
            # Handle None in enum for old SDK
            params = func.get("parameters", {})
            if "properties" in params:
                for prop_name, prop_val in params["properties"].items():
                    if "enum" in prop_val and None in prop_val["enum"]:
                        prop_val["enum"] = [e for e in prop_val["enum"] if e is not None]
            functions.append(func)
    return functions

BOOKING_FUNCTIONS = convert_tools_to_functions(BOOKING_TOOLS) if OPENAI_SDK_VERSION < 1 else None

# ============================================================================
# FUNCTION EXECUTION
# ============================================================================

def execute_function(function_name: str, arguments: str, phone: str) -> Dict[str, Any]:
    """Execute a booking function"""
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else arguments
        
        if function_name == "create_appointment":
            return create_appointment(
                customer_phone=phone,
                customer_name=args["customer_name"],
                service_type=args["service_type"],
                date=args["date"],
                time=args["time"]
            )
        
        elif function_name == "check_availability":
            return check_availability(
                date=args["date"],
                time=args["time"]
            )
        
        elif function_name == "get_customer_appointments":
            return get_customer_appointments(customer_phone=phone)
        
        elif function_name == "cancel_appointment":
            return cancel_appointment(
                customer_phone=phone,
                appointment_id=args["appointment_id"]
            )

        elif function_name == "modify_appointment":
            return modify_appointment(
                customer_phone=phone,
                appointment_id=args["appointment_id"],
                new_date=args.get("new_date"),
                new_time=args.get("new_time"),
                new_service=args.get("new_service")
            )

        elif function_name == "get_available_slots":
            return get_available_slots(date=args["date"])

        else:
            return {"success": False, "error": "UNKNOWN_FUNCTION", "function_name": function_name}
    
    except Exception as e:
        logger.error(f"Function execution error: {e}")
        return {"success": False, "error": str(e)}

# ============================================================================
# CONVERSATION MANAGEMENT
# ============================================================================

conversation_history: Dict[str, List[Dict]] = {}

def get_ai_response(phone: str, message: str) -> str:
    """
    Get AI response with SDK version compatibility.

    Supports both old SDK (v0.x) and new SDK (v1.0+) syntax.
    Key features:
    - Uses gpt-4o for reliable function calling
    - Uses tools/functions API based on SDK version
    - Temperature=0 for deterministic behavior
    """
    try:
        # Get or create conversation history
        if phone not in conversation_history:
            conversation_history[phone] = []

        # Build messages
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(conversation_history[phone][-10:])  # Last 10 messages
        messages.append({"role": "user", "content": message})

        # Call OpenAI with version-appropriate syntax
        if OPENAI_SDK_VERSION >= 1:
            # New SDK v1.0+ syntax
            response = openai_client.chat.completions.create(
                model="gpt-4o",
                messages=messages,
                tools=BOOKING_TOOLS,
                tool_choice="auto",
                temperature=0
            )
            assistant_message = response.choices[0].message
            has_function_call = bool(assistant_message.tool_calls)
        else:
            # Old SDK v0.x syntax
            response = openai.ChatCompletion.create(
                model="gpt-4o",
                messages=messages,
                functions=BOOKING_FUNCTIONS,
                function_call="auto",
                temperature=0
            )
            assistant_message = response["choices"][0]["message"]
            has_function_call = "function_call" in assistant_message

        # Handle function/tool calls
        if has_function_call:
            if OPENAI_SDK_VERSION >= 1:
                # New SDK: handle tool_calls array
                tool_calls = assistant_message.tool_calls

                messages.append({
                    "role": "assistant",
                    "content": assistant_message.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments
                            }
                        } for tc in tool_calls
                    ]
                })

                for tool_call in tool_calls:
                    function_name = tool_call.function.name
                    function_args = tool_call.function.arguments
                    tool_call_id = tool_call.id

                    logger.info(f"🔧 AI calling tool: {function_name}")
                    logger.info(f"   Args: {function_args}")

                    function_result = execute_function(function_name, function_args, phone)
                    logger.info(f"   Result: {function_result}")

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": json.dumps(function_result)
                    })
            else:
                # Old SDK: handle function_call object
                function_call = assistant_message["function_call"]
                function_name = function_call["name"]
                function_args = function_call["arguments"]

                logger.info(f"🔧 AI calling function: {function_name}")
                logger.info(f"   Args: {function_args}")

                messages.append(assistant_message)

                function_result = execute_function(function_name, function_args, phone)
                logger.info(f"   Result: {function_result}")

                messages.append({
                    "role": "function",
                    "name": function_name,
                    "content": json.dumps(function_result)
                })

            # Get second response after function execution
            if OPENAI_SDK_VERSION >= 1:
                second_response = openai_client.chat.completions.create(
                    model="gpt-4o",
                    messages=messages,
                    tools=BOOKING_TOOLS,
                    tool_choice="auto",
                    temperature=0
                )
                second_message = second_response.choices[0].message
                has_second_call = bool(second_message.tool_calls)
            else:
                second_response = openai.ChatCompletion.create(
                    model="gpt-4o",
                    messages=messages,
                    functions=BOOKING_FUNCTIONS,
                    function_call="auto",
                    temperature=0
                )
                second_message = second_response["choices"][0]["message"]
                has_second_call = "function_call" in second_message

            # Handle second round of function calls
            if has_second_call:
                if OPENAI_SDK_VERSION >= 1:
                    tool_calls_2 = second_message.tool_calls
                    messages.append({
                        "role": "assistant",
                        "content": second_message.content,
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments
                                }
                            } for tc in tool_calls_2
                        ]
                    })

                    for tool_call in tool_calls_2:
                        func2_name = tool_call.function.name
                        func2_args = tool_call.function.arguments
                        tool_call_id_2 = tool_call.id

                        logger.info(f"🔧 AI calling second tool: {func2_name}")
                        func2_result = execute_function(func2_name, func2_args, phone)
                        logger.info(f"   Result: {func2_result}")

                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call_id_2,
                            "content": json.dumps(func2_result)
                        })
                else:
                    function_call_2 = second_message["function_call"]
                    func2_name = function_call_2["name"]
                    func2_args = function_call_2["arguments"]

                    logger.info(f"🔧 AI calling second function: {func2_name}")

                    messages.append(second_message)

                    func2_result = execute_function(func2_name, func2_args, phone)
                    logger.info(f"   Result: {func2_result}")

                    messages.append({
                        "role": "function",
                        "name": func2_name,
                        "content": json.dumps(func2_result)
                    })

                # Get third response
                if OPENAI_SDK_VERSION >= 1:
                    third_response = openai_client.chat.completions.create(
                        model="gpt-4o",
                        messages=messages,
                        tools=BOOKING_TOOLS,
                        tool_choice="auto",
                        temperature=0
                    )
                    third_message = third_response.choices[0].message
                    has_third_call = bool(third_message.tool_calls)
                else:
                    third_response = openai.ChatCompletion.create(
                        model="gpt-4o",
                        messages=messages,
                        functions=BOOKING_FUNCTIONS,
                        function_call="auto",
                        temperature=0
                    )
                    third_message = third_response["choices"][0]["message"]
                    has_third_call = "function_call" in third_message

                # Handle third round (rare)
                if has_third_call:
                    if OPENAI_SDK_VERSION >= 1:
                        tool_calls_3 = third_message.tool_calls
                        messages.append({
                            "role": "assistant",
                            "content": third_message.content,
                            "tool_calls": [
                                {
                                    "id": tc.id,
                                    "type": "function",
                                    "function": {
                                        "name": tc.function.name,
                                        "arguments": tc.function.arguments
                                    }
                                } for tc in tool_calls_3
                            ]
                        })

                        for tool_call in tool_calls_3:
                            func3_name = tool_call.function.name
                            func3_args = tool_call.function.arguments
                            tool_call_id_3 = tool_call.id

                            logger.info(f"🔧 AI calling third tool: {func3_name}")
                            func3_result = execute_function(func3_name, func3_args, phone)
                            logger.info(f"   Result: {func3_result}")

                            messages.append({
                                "role": "tool",
                                "tool_call_id": tool_call_id_3,
                                "content": json.dumps(func3_result)
                            })

                        fourth_response = openai_client.chat.completions.create(
                            model="gpt-4o",
                            messages=messages,
                            temperature=0
                        )
                        final_message = fourth_response.choices[0].message.content or ''
                    else:
                        function_call_3 = third_message["function_call"]
                        func3_name = function_call_3["name"]
                        func3_args = function_call_3["arguments"]

                        logger.info(f"🔧 AI calling third function: {func3_name}")

                        messages.append(third_message)

                        func3_result = execute_function(func3_name, func3_args, phone)
                        logger.info(f"   Result: {func3_result}")

                        messages.append({
                            "role": "function",
                            "name": func3_name,
                            "content": json.dumps(func3_result)
                        })

                        fourth_response = openai.ChatCompletion.create(
                            model="gpt-4o",
                            messages=messages,
                            temperature=0
                        )
                        final_message = fourth_response["choices"][0]["message"]["content"] or ''
                else:
                    if OPENAI_SDK_VERSION >= 1:
                        final_message = third_message.content or ''
                    else:
                        final_message = third_message.get("content", '') or ''
            else:
                if OPENAI_SDK_VERSION >= 1:
                    final_message = second_message.content or ''
                else:
                    final_message = second_message.get("content", '') or ''

            # Save to history
            conversation_history[phone].append({"role": "user", "content": message})
            conversation_history[phone].append({"role": "assistant", "content": final_message})

            return final_message

        else:
            # No function call - normal conversation
            if OPENAI_SDK_VERSION >= 1:
                response_text = assistant_message.content or ''
            else:
                response_text = assistant_message.get("content", '') or ''

            # Save to history
            conversation_history[phone].append({"role": "user", "content": message})
            conversation_history[phone].append({"role": "assistant", "content": response_text})

            return response_text

    except Exception as e:
        logger.error(f"❌ AI Error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return "⚠️ Technical issue / Problema tecnico. Please try again / Riprova."

# ============================================================================
# WHATSAPP SERVICE
# ============================================================================

async def send_whatsapp_message(phone: str, message: str) -> bool:
    """Send WhatsApp message"""
    url = f"https://graph.facebook.com/v18.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    
    headers = {
        "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": phone,
        "type": "text",
        "text": {"body": message}
    }
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=payload, timeout=30)
            if response.status_code == 200:
                logger.info(f"✅ WhatsApp message sent to {phone}")
                return True
            else:
                logger.error(f"❌ WhatsApp send failed: {response.text}")
                return False
    except Exception as e:
        logger.error(f"❌ WhatsApp API error: {e}")
        return False

async def mark_as_read(message_id: str) -> bool:
    """Mark message as read"""
    url = f"https://graph.facebook.com/v18.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    
    headers = {
        "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id
    }
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=payload, timeout=10)
            return response.status_code == 200
    except:
        return False

# ============================================================================
# FASTAPI APPLICATION
# ============================================================================

app = FastAPI(
    title="Aura Hair Studio - WhatsApp Bot with Booking",
    description="Virtual receptionist (Simone) with calendar integration and reliable tool calling",
    version="4.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup():
    """Initialize on startup"""
    initialize_database()
    logger.info(f"🚀 {BUSINESS_NAME} WhatsApp Bot with Booking started!")

@app.get("/webhook")
async def verify_webhook(request: Request):
    """WhatsApp webhook verification"""
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    
    if mode == "subscribe" and token == WHATSAPP_WEBHOOK_VERIFY_TOKEN:
        logger.info("✅ Webhook verified!")
        return PlainTextResponse(challenge)
    
    return PlainTextResponse("Failed", status_code=403)

@app.post("/webhook")
async def webhook(request: Request):
    """Handle incoming WhatsApp messages"""
    try:
        body = await request.json()
        
        if body.get("object") != "whatsapp_business_account":
            return JSONResponse({"status": "ignored"})
        
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                messages = value.get("messages", [])
                
                for message in messages:
                    await process_message(message, value)
        
        return JSONResponse({"status": "processed"})
    
    except Exception as e:
        logger.error(f"❌ Webhook error: {e}")
        return JSONResponse({"status": "error"})

async def process_message(message: Dict[str, Any], value: Dict[str, Any]):
    """Process incoming message"""
    try:
        phone = message.get("from")
        message_id = message.get("id")
        message_type = message.get("type", "text")
        
        # Get contact name
        contacts = value.get("contacts", [])
        contact_name = contacts[0].get("profile", {}).get("name", "Cliente") if contacts else "Cliente"
        
        logger.info(f"💬 Message from {phone} ({contact_name})")
        
        await mark_as_read(message_id)
        
        if message_type == "text":
            text = message.get("text", {}).get("body", "")
            if text:
                logger.info(f"📝 Message: {text[:100]}...")
                
                # Get AI response with function calling
                response = get_ai_response(phone, text)
                
                await send_whatsapp_message(phone, response)
        
        elif message_type == "interactive":
            interactive = message.get("interactive", {})
            text = interactive.get("button_reply", {}).get("title", "") or \
                   interactive.get("list_reply", {}).get("title", "")
            if text:
                response = get_ai_response(phone, text)
                await send_whatsapp_message(phone, response)
        
        else:
            await send_whatsapp_message(phone,
                "I can only respond to text messages. How can I help you with your booking? 💇‍♀️\n\n"
                "Posso rispondere solo a messaggi di testo. Come posso aiutarti con la prenotazione? 💇‍♀️")
    
    except Exception as e:
        logger.error(f"Process message error: {e}")

@app.get("/health")
async def health_check():
    """Health check"""
    return JSONResponse({
        "status": "healthy",
        "service": BUSINESS_NAME,
        "type": BUSINESS_TYPE,
        "version": "4.0.0",
        "features": {
            "booking": True,
            "calendar_integration": True,
            "tools_api": True,
            "strict_mode": True,
            "model": "gpt-4o"
        },
        "services": list(SALON_SERVICES.keys()),
        "timestamp": datetime.now().isoformat()
    })

@app.get("/")
async def root():
    """Root endpoint"""
    return JSONResponse({
        "name": f"{BUSINESS_NAME} - WhatsApp Bot",
        "version": "4.0.0",
        "features": ["booking", "calendar", "AI", "tools_api", "strict_mode"],
        "model": "gpt-4o",
        "services": [f"{s['name_it']} - €{s['price']}" for s in SALON_SERVICES.values()]
    })

# ============================================================================
# STARTUP
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    
    logger.info("=" * 60)
    logger.info(f"🚀 STARTING {BUSINESS_NAME.upper()} WITH BOOKING")
    logger.info("=" * 60)
    logger.info(f"📍 Business: {BUSINESS_NAME}")
    logger.info(f"💇 Services: {list(SALON_SERVICES.keys())}")
    logger.info(f"📅 Booking: ENABLED")
    logger.info(f"🤖 AI: Function Calling ENABLED")
    logger.info("=" * 60)
    
    uvicorn.run(
        "salon_bot_with_booking:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info"
    )


