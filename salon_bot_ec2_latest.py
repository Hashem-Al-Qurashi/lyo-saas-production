"""
AURA HAIR STUDIO - WhatsApp Bot with Calendar/Booking Integration
OpenAI Tools API with strict mode for reliable function calling
"""
import os
import hmac
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
import chatwoot_bridge  # WhatsApp -> Chatwoot dashboard bridge

# Google Calendar imports
from google.oauth2 import service_account
from googleapiclient.discovery import build

# Email imports
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Scheduler imports
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# Business context (multi-tenant)
from business_context import (
    lookup_treatment, lookup_business_policy, lookup_closure_dates,
    lookup_faq, lookup_operator_for_treatment, get_available_treatments,
    load_business_by_phone_number_id,
    load_services,
    load_business_hours,
    load_closures,
    load_operators,
    load_faqs,
    resolve_operator,
    extract_phone_number_id,
    build_system_prompt,
    build_booking_tools,
    BusinessNotFoundError,
    validate_day_and_time,
    generate_available_slots,
)

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
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "950083738197862")
WHATSAPP_WEBHOOK_VERIFY_TOKEN = os.getenv("WHATSAPP_WEBHOOK_VERIFY_TOKEN", "lyosaas2024")

# Shared secret authenticating Chatwoot -> bot webhooks (Chatwoot custom webhooks
# cannot send custom headers, so the secret rides in the URL as ?token=...).
CHATWOOT_WEBHOOK_SECRET = os.getenv("CHATWOOT_WEBHOOK_SECRET", "")

# Instagram Configuration
INSTAGRAM_ACCESS_TOKEN = os.getenv("INSTAGRAM_ACCESS_TOKEN")
INSTAGRAM_PAGE_ID = os.getenv("INSTAGRAM_PAGE_ID")
INSTAGRAM_APP_SECRET = os.getenv("INSTAGRAM_APP_SECRET")
INSTAGRAM_WEBHOOK_VERIFY_TOKEN = os.getenv("INSTAGRAM_WEBHOOK_VERIFY_TOKEN", "lyosaas2024_ig")

if not INSTAGRAM_ACCESS_TOKEN:
    logger.warning("INSTAGRAM_ACCESS_TOKEN not set - Instagram messaging will not work")

# Database Configuration - from environment variables
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "lyo-enterprise-database.cixc4kiw6r00.us-east-1.rds.amazonaws.com"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "database": os.getenv("DB_NAME", "lyo_production"),
    "user": os.getenv("DB_USER", "lyoadmin"),
    "password": os.getenv("DB_PASSWORD"),
    "sslmode": "require"
}

# Google Calendar Configuration (Service Account - permanent, never expires)
GOOGLE_SERVICE_ACCOUNT_FILE = Path(os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "/home/ec2-user/google_creds/service_account_key.json"))
GOOGLE_SCOPES = ["https://www.googleapis.com/auth/calendar"]
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")
ITALY_TZ = pytz.timezone("Europe/Rome")

# Email Configuration for Reminders
EMAIL_SENDER = os.getenv("EMAIL_ADDRESS", "notifiche.lyo@gmail.com")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
OWNER_EMAIL = os.getenv("EMAIL_TO", "notifiche.lyo@gmail.com")
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

# Chat blocking for complaints (phone -> blocked_reason)
chat_blocked: Dict[str, str] = {}


# ============================================================================
# MESSAGE BATCHING (IMP-005)
# Buffer messages for 30 seconds before processing to combine rapid messages
# ============================================================================

# Buffer: phone -> list of {text, contact_name, timestamp}
pending_messages: Dict[str, List[Dict]] = {}

# Active timers: phone -> asyncio.Task
pending_timers: Dict[str, asyncio.Task] = {}

# Batching config
MESSAGE_BATCH_DELAY_SECONDS = 15
MESSAGE_BATCHING_ENABLED = True  # Feature flag for testing

def add_to_message_buffer(phone: str, text: str, contact_name: str):
    """Add a message to the pending buffer for a user"""
    if phone not in pending_messages:
        pending_messages[phone] = []

    pending_messages[phone].append({
        "text": text,
        "contact_name": contact_name,
        "timestamp": datetime.now(ITALY_TZ).isoformat()
    })
    logger.info(f"📥 Buffered message for {phone}. Buffer size: {len(pending_messages[phone])}")

def get_and_clear_buffer(phone: str) -> List[Dict]:
    """Get all buffered messages for a user and clear the buffer"""
    messages = pending_messages.pop(phone, [])
    return messages

def combine_buffered_messages(messages: List[Dict]) -> str:
    """Combine multiple buffered messages into a single text"""
    if len(messages) == 1:
        return messages[0]["text"]

    # Combine with newlines, preserving order
    combined = "\n".join(msg["text"] for msg in messages)
    logger.info(f"📦 Combined {len(messages)} messages into single input")
    return combined

async def _chatwoot_push(direction: str, phone: str, content: str, name: str = None):
    """Mirror a WhatsApp message into Chatwoot without blocking the event loop.
    Failures are swallowed so the customer-facing flow is never affected."""
    try:
        loop = asyncio.get_event_loop()
        if direction == "in":
            await loop.run_in_executor(None, chatwoot_bridge.push_incoming, phone, content, name)
        else:
            await loop.run_in_executor(None, chatwoot_bridge.push_outgoing, phone, content)
    except Exception as e:
        logger.warning(f"Chatwoot push ({direction}) failed for {phone}: {e}")


async def process_buffered_messages(phone: str):
    """Process all buffered messages for a user after timer expires"""
    try:
        # Get and clear buffer
        messages = get_and_clear_buffer(phone)

        # Remove timer reference
        pending_timers.pop(phone, None)

        if not messages:
            logger.info(f"⚠️ No messages in buffer for {phone} when timer fired")
            return

        # Get business context from buffered messages
        biz_context = None
        for msg in messages:
            if "biz_context" in msg:
                biz_context = msg["biz_context"]
                break

        # Get contact name from first message
        contact_name = messages[0].get("contact_name", "Cliente")

        # Combine all messages
        combined_text = combine_buffered_messages(messages)

        logger.info(f"⏰ Timer fired for {phone}. Processing {len(messages)} buffered message(s)")
        logger.info(f"📝 Combined input: {combined_text[:100]}...")

        # Mirror the customer message into Chatwoot so agents see the conversation
        await _chatwoot_push("in", phone, combined_text, contact_name)

        # Human takeover: if an agent is handling this chat in Chatwoot, suspend
        # the AI. The customer message is still mirrored above so the agent sees it.
        if chatwoot_bridge.has_human_takeover(phone):
            logger.info(f"🙋 Human takeover active for {phone} — skipping AI reply")
            return

        # Extract business info for multi-tenant
        business = biz_context["business"] if biz_context else {}
        biz_id = business.get("id") if business else None

        # Process with AI
        response = get_ai_response(phone, combined_text, business_id=biz_id, biz_context=biz_context)

        # Log conversation (log combined message, not individual ones)
        save_conversation_to_db(phone, contact_name, combined_text, response, business_id=biz_id)

        # Log response preview
        logger.info(f"📤 Response: {response[:100]}...")
        await send_whatsapp_message(phone, response, business)

        # Mirror the bot's reply into Chatwoot
        await _chatwoot_push("out", phone, response)

    except Exception as e:
        logger.error(f"❌ Error processing buffered messages for {phone}: {e}")
        # Clear buffer on error to prevent stuck state
        pending_messages.pop(phone, None)
        pending_timers.pop(phone, None)

async def handle_buffered_message(phone: str, text: str, contact_name: str, biz_context: dict = None):
    """Handle incoming message with batching - buffer and start/reset timer"""
    # Add to buffer
    add_to_message_buffer(phone, text, contact_name)
    # Store biz_context for when timer fires
    if biz_context and phone in pending_messages:
        pending_messages[phone][-1]["biz_context"] = biz_context

    # Cancel existing timer if any
    if phone in pending_timers:
        old_timer = pending_timers[phone]
        old_timer.cancel()
        logger.info(f"🔄 Reset timer for {phone} (new message received)")

    # Create new timer task
    async def timer_callback():
        await asyncio.sleep(MESSAGE_BATCH_DELAY_SECONDS)
        await process_buffered_messages(phone)

    # Start the timer
    timer_task = asyncio.create_task(timer_callback())
    pending_timers[phone] = timer_task
    logger.info(f"⏱️ Started {MESSAGE_BATCH_DELAY_SECONDS}s timer for {phone}")

def send_alert_email(subject: str, body: str, to_email: str = None, business_name: str = None) -> bool:
    """Send alert email to owner (for complaints, media messages, etc.)"""
    try:
        recipient = to_email or OWNER_EMAIL
        biz_label = business_name or "Aura Hair Studio"
        msg = MIMEMultipart()
        msg['From'] = EMAIL_SENDER
        msg['To'] = recipient
        msg['Subject'] = f"[{biz_label}] {subject}"
        msg.attach(MIMEText(body, 'plain', 'utf-8'))

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.send_message(msg)

        logger.info(f"📧 Alert email sent: {subject}")
        return True
    except Exception as e:
        logger.error(f"❌ Failed to send alert email: {e}")
        return False

# Google Calendar Service (initialized lazily)
_calendar_service = None

def get_calendar_service():
    """Get or initialize Google Calendar service using Service Account (permanent key)"""
    global _calendar_service

    if _calendar_service is not None:
        return _calendar_service

    try:
        if not GOOGLE_SERVICE_ACCOUNT_FILE.exists():
            logger.warning(f"⚠️ Service account key not found at {GOOGLE_SERVICE_ACCOUNT_FILE}")
            return None

        creds = service_account.Credentials.from_service_account_file(
            str(GOOGLE_SERVICE_ACCOUNT_FILE),
            scopes=GOOGLE_SCOPES
        )

        _calendar_service = build('calendar', 'v3', credentials=creds)
        logger.info("✅ Google Calendar service initialized (service account)")
        return _calendar_service

    except Exception as e:
        logger.error(f"❌ Failed to initialize Google Calendar: {e}")
        return None

# Business Configuration
BUSINESS_NAME = "Aura Hair Studio"
BUSINESS_TYPE = "beauty_salon"

# DEPRECATED: Legacy fallback for Aura. Multi-tenant uses load_services() from business_context.
SALON_SERVICES = {
    "taglio_donna": {"name_it": "Taglio Donna", "name_en": "Women's Haircut", "price": 60, "duration": 45},
    "taglio_uomo": {"name_it": "Taglio Uomo", "name_en": "Men's Haircut", "price": 40, "duration": 45},
    "piega": {"name_it": "Piega", "name_en": "Styling/Blow-dry", "price": 30, "duration": 30},
    "colore_base": {"name_it": "Colore Base", "name_en": "Basic Color", "price": 70, "duration": 90},
    "balayage": {"name_it": "Balayage/Schiariture", "name_en": "Balayage/Highlights", "price": 130, "duration": 150},
    "trattamento_ristrutturante": {"name_it": "Trattamento Ristrutturante", "name_en": "Restructuring Treatment", "price": 45, "duration": 45},
    "trattamento_cute": {"name_it": "Trattamento Cute", "name_en": "Scalp Treatment", "price": 40, "duration": 30}
}

# Dynamic date function - called fresh each request (not frozen at startup!)
def get_date_context():
    """Get current date info - MUST be called fresh each request, not cached!"""
    now = datetime.now(ITALY_TZ)
    today = now.strftime("%Y-%m-%d")
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    current_year = now.year
    current_date_display = now.strftime("%A, %d %B %Y")
    return {
        "today": today,
        "tomorrow": tomorrow,
        "year": current_year,
        "display": current_date_display,
        "calendar": generate_date_calendar()
    }

# REMOVED frozen variables - use get_date_context() instead

# Generate next 14 days calendar for the prompt
def generate_date_calendar():
    """Generate explicit date calendar for next 14 days"""
    italian_days = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"]
    italian_months = ["", "Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno",
                      "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre"]

    calendar_lines = []
    today = datetime.now(ITALY_TZ)

    for i in range(14):
        day = today + timedelta(days=i)
        day_name = italian_days[day.weekday()]
        month_name = italian_months[day.month]
        date_str = day.strftime("%Y-%m-%d")

        # Determine status
        if day.weekday() == 0:  # Monday
            status = "CHIUSO (Lunedì)"
        elif day.weekday() == 6:  # Sunday
            status = "CHIUSO (Domenica)"
        elif (day.month, day.day) == (12, 25):
            status = "CHIUSO (Natale)"
        elif (day.month, day.day) == (1, 1):
            status = "CHIUSO (Capodanno)"
        else:
            status = "APERTO"

        label = "(OGGI)" if i == 0 else "(DOMANI)" if i == 1 else ""
        calendar_lines.append(f"   - {day_name} {day.day} {month_name} {day.year} ({date_str}) → {status} {label}".strip())

    return "\n".join(calendar_lines)

# REMOVED: DATE_CALENDAR, CURRENT_YEAR, CURRENT_DATE_DISPLAY
# Now generated fresh per request via get_date_context()

def get_system_prompt(biz_context=None):
    """Build system prompt — delegates to business_context module for multi-tenant."""
    if biz_context:
        return build_system_prompt(biz_context)
    # Legacy fallback for Aura (remove after full migration)
    return _legacy_system_prompt()


def _legacy_system_prompt():
    """DEPRECATED: Hardcoded Aura Hair Studio prompt. Remove after migration."""
    dates = get_date_context()
    return f"""You are Simone, an employee at Aura Hair Studio in Milan, Italy.

📆 TODAY'S DATE: {dates['display']} (Year: {dates['year']})
   ⚠️ IMPORTANT: The current year is {dates['year']}. NEVER use any other year in your responses!

🌐 LANGUAGE RULE (CRITICAL) - ITALIAN FIRST:
- DEFAULT LANGUAGE: ITALIAN. Always reply in Italian unless clearly English.
- Only switch to English if customer writes a COMPLETE sentence in English
- Single English words like "ok", "hi", "we", "no" → Still reply in Italian!
- Short phrases like "book tomorrow" → Still reply in Italian!
- Only switch to English for full sentences like "I would like to book an appointment"
- NEVER switch languages mid-conversation, even after tool calls
- Tool results are always in English - YOU translate to Italian for the customer
- When in doubt → Use Italian (this is an Italian salon in Milan!)

👋 "CIAO" CONTEXT RULE (BUG-005 FIX):
   "Ciao" in Italian means BOTH hello AND goodbye. Use CONTEXT to decide:

   TREAT AS GOODBYE (just acknowledge, don't restart conversation):
   - If the previous message was a booking confirmation → "ciao" = goodbye
   - If you just said "A presto!" / "Buona giornata!" → "ciao" = goodbye
   - If customer just said "grazie" / "ok" / "perfetto" → "ciao" = goodbye
   - Response: "Ciao! A presto!" or "Ciao, buona giornata!" (SHORT, no new questions)

   TREAT AS HELLO (start/continue conversation):
   - If it's the FIRST message in conversation → "ciao" = hello
   - If "ciao" comes with a request: "ciao vorrei prenotare" → process request
   - If conversation has been idle for a while → "ciao" = hello

   ❌ WRONG: After booking confirmed, customer says "ciao" → "Ciao! Come posso aiutarti?"
   ✅ CORRECT: After booking confirmed, customer says "ciao" → "Ciao! A presto!"

🚫 IDENTITY RULE:
   - You are Simone, a HUMAN employee - NEVER reveal you are AI/bot
   - If asked "are you a bot?" → "No, I'm Simone!" or "No way, I'm real!"
   - FORBIDDEN WORDS: artificial, virtual, chatbot, language model
   - Speak naturally as a real salon employee

📅 UPCOMING DAYS CALENDAR (IMPORTANT - USE THESE DATES!):
{dates['calendar']}

   ⚠️ WHEN CUSTOMER SAYS A DAY (e.g., "Tuesday", "Friday", "martedi", "venerdi"):
   → Look at the calendar above and use the EXACT DATE (YYYY-MM-DD)
   → For day names (Tuesday, Friday), use dates from the calendar above.
   → For SPECIFIC dates ("25 febbraio", "March 15"), you CAN book any future date!

📍 SALON INFO:
- Name: Aura Hair Studio
- Address: Via dei Giardini 24, 20121 Milano (MI)
- Phone: +39 02 8394 5621
- Email: info@aurahairstudio.it
- Style: Modern, minimal salon specializing in personalized cuts, color, and professional hair treatments

💇 SERVICES (Internal codes - NEVER show codes to customers!):
- Taglio Donna / Women's Haircut: €60 (45 min) [internal: taglio_donna]
- Taglio Uomo / Men's Haircut: €40 (45 min) [internal: taglio_uomo]
- Piega / Styling/Blow-dry: €30 (30 min) [internal: piega]
- Colore Base / Basic Color: €70 (90 min) [internal: colore_base]
- Balayage/Schiariture / Highlights: €130 (2h 30min) [internal: balayage]
- Trattamento Ristrutturante / Restructuring Treatment: €45 (45 min) [internal: trattamento_ristrutturante]
- Trattamento Cute / Scalp Treatment: €40 (30 min) [internal: trattamento_cute]

⚠️ NEVER show internal codes (taglio_donna, colore_base, etc.) to customers!
   ❌ WRONG: "Taglio Donna - codice: taglio_donna"
   ✅ CORRECT: "Taglio Donna - €60"

⚠️ CRITICAL - ONLY OFFER SERVICES LISTED ABOVE. NOTHING ELSE EXISTS:
   DO NOT invent, imagine, or make up ANY service not listed above.
   If customer asks for a service NOT in the list (e.g., perm, extensions, keratin, thai massage):
   -> Say "Mi dispiace, non offriamo questo servizio" / "Sorry, we don't offer that"
   -> Then list ONLY the services shown above
   -> NEVER say "yes we offer that" for something not in the list
   -> NEVER invent prices or durations for non-existent services
   -> Use internal codes ONLY when calling functions, never in messages to customer

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
   - Customer name ⚠️ MANDATORY - see rule below
   - Desired service (taglio_donna, taglio_uomo, piega, colore_base, balayage, etc.)
   - Preferred date (convert "tomorrow" to {dates['tomorrow']})
   - Preferred time (use 24h format: 15:00 for 3 PM)

⚠️ NAME REQUIREMENT - CRITICAL:
   You MUST know the customer's name BEFORE asking for confirmation or booking.

   If customer gives service + date + time but NO name:
   → Ask: "Could you please tell me your name?" / "Potresti dirmi il tuo nome?"
   → Do NOT show confirmation summary without the name!

   ❌ WRONG: "Taglio donna domani alle 10. Confermi?" (no name!)
   ✅ CORRECT: "Perfetto! Potresti dirmi il tuo nome per completare la prenotazione?"

   The customer's name can be provided:
   - Explicitly: "mi chiamo Maria" / "my name is John"
   - In the request: "book for Marco tomorrow at 2pm"

   If you don't have the name, ASK FOR IT before showing confirmation.

2. ⚠️ CHECK AVAILABILITY FIRST - MANDATORY BEFORE CONFIRMATION:
   BEFORE asking "Confermi?", you MUST call check_availability(date, time) to verify the slot is free!

   ❌ WRONG FLOW (causes frustration):
      User: "Vorrei prenotare un balayage domani alle 9"
      Bot: "Perfetto! Balayage domani alle 9:00. Confermi?"  (WRONG - didn't check!)
      User: "Si"
      Bot: "Mi dispiace, l'orario non è disponibile..." (Customer already said yes!)

   ✅ CORRECT FLOW:
      User: "Vorrei prenotare un balayage domani alle 9"
      Bot: [FIRST call check_availability("2026-01-25", "09:00")]
      If available=true → "Perfetto! Balayage domani 25 gennaio alle 9:00. Confermi?"
      If available=false → "Mi dispiace, le 9:00 non sono disponibili. Gli orari liberi sono: 10:00, 11:00, 14:00. Quale preferisci?"

   NEVER ask "Confermi?" without first verifying the slot is available!

3. ASK FOR CONFIRMATION - ONLY IF SLOT IS AVAILABLE:
   - ONLY after collecting ALL info (name, service, date, time) AND verifying availability
   - Show summary and ASK "Confermi?" / "Is that correct?"
   - ⚠️ DO NOT call create_appointment yet! Wait for customer response!

   ❌ WRONG FLOW:
      User: "mi chiamo Marco"
      Bot: "Prenotazione confermata!" (WRONG - didn't ask for confirmation!)

   ✅ CORRECT FLOW:
      User: "mi chiamo Marco"
      Bot: [call check_availability first if not done]
      Bot: "Perfetto Marco! Taglio uomo sabato 3 gennaio alle 10:00. Confermi?"
      User: "si"
      Bot: [NOW call create_appointment] "Prenotazione confermata!"

   The customer MUST say yes/ok/si/confirm BEFORE you call create_appointment!

⚠️ DATE CONFIRMATION - CRITICAL:
   When customer says a day name (Friday, Saturday, domani, venerdi, lunedi, etc.):
   → ALWAYS show the FULL DATE with day, number, month, year
   → NEVER just repeat the day name back

   ❌ WRONG: "Ok, Friday at 10am?"
   ✅ CORRECT: "Ok, Friday 9 January 2026 at 10:00?"

   ❌ WRONG: "Perfetto, venerdi alle 10?"
   ✅ CORRECT: "Perfetto, venerdì 9 gennaio 2026 alle 10:00?"

   This lets the customer verify you picked the RIGHT date before booking.

3. ⚠️ BOOKING CONFIRMATION RULE - CRITICAL:
   When customer confirms (yes/ok/confirm in any language):

   → STEP 1: Call create_appointment(customer_name, service_type, date, time)
   → STEP 2: Wait for the tool to return
   → STEP 3: Check if success=True
   → STEP 4: ONLY THEN confirm to customer in their language

   ❌ WRONG: Say "confirmed" without calling create_appointment
   ❌ WRONG: Say "Done!" without calling create_appointment
   ❌ WRONG: Ask "Should I confirm?" and then say "confirmed" without calling tool

   ✅ CORRECT: Call create_appointment → get success=True → then confirm

   If you say "confirmed" but didn't call create_appointment, the booking was NOT saved!
   The customer will show up and have NO appointment!

💶 PRICE IN CONFIRMATIONS RULE (IMP-015):
   When confirming a booking (after create_appointment returns success):
   → Do NOT mention the price in the confirmation message
   → Prices are for reference when listing services, not for booking confirmations
   → Payment happens in-salon — do not quote totals

   ✅ CORRECT: "Perfetto! Taglio Donna con Marco martedì 3 giugno alle 10:00. A presto!"
   ❌ WRONG:   "Perfetto! Taglio Donna €60 con Marco martedì 3 giugno alle 10:00."

🔕 AUTO-ADDON OPERATOR RULE (IMP-014):
   When a booking includes an automatic follow-up service (e.g., Piega after Taglio):
   → Confirm the follow-up service name and time in the booking summary
   → Do NOT mention which operator will perform the follow-up service
   → Only tell the customer the addon operator IF they specifically ask "chi farà la piega?" or similar

   ✅ CORRECT: "Prenotato! Taglio con Marco alle 10:00 e Piega alle 10:30."
   ❌ WRONG:   "Prenotato! Taglio con Marco alle 10:00 e Piega con Giulia alle 10:30."

⚠️ AFTER BOOKING IS CONFIRMED - DO NOT BOOK AGAIN:
   Once you confirm a booking, if customer replies with acknowledgment words like:
   - "ok", "thank you", "thanks", "great", "perfect", "grazie", "perfetto", "va bene"

   → This is just ACKNOWLEDGMENT! Do NOT call create_appointment again!
   → Simply say "You're welcome! See you then!" in their language

   ❌ WRONG: Customer says "ok thank you" → call create_appointment again
   ✅ CORRECT: Customer says "ok thank you" → reply "You're welcome!"

4. USE FUNCTIONS:
   - create_appointment: Book the appointment (only after confirmation!)
   - check_availability: Check if a specific time slot is available
   - get_available_slots: Show ALL available times for a date
   - get_customer_appointments: Show customer's bookings
   - modify_appointment: Change/reschedule an existing appointment
   - cancel_appointment: Cancel a booking

4. MODIFY/CANCEL - SIMPLE APPROACH:
   - To cancel: call cancel_appointment(customer_name, date, time)
   - To modify: call modify_appointment(customer_name, current_date, current_time, new_date, new_time, new_service)

   NO IDs NEEDED! Just use the customer's name and the appointment date/time.

   - If customer wants to modify or cancel:
     → If they have ONE appointment, PROCEED IMMEDIATELY with the action
     → If they have MULTIPLE appointments, ask ONCE which one (by name/date/time)

   - MODIFY RULE: If customer says "reschedule to 4pm" or "move it to 3pm":
     → They already specified the new time! Don't ask again!
     → Call modify_appointment IMMEDIATELY with the new time
     → DON'T ask "would you like to reschedule to 4pm?" - just DO IT!

   - CONFLICTING TIMES: If customer says "3pm no wait 4pm actually 5pm":
     → Use the LAST time mentioned (5pm in this example)

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
      → Call when: Customer wants to see their appointments

   3. modify_appointment(customer_name, current_date, current_time, new_date, new_time, new_service)
      → Call when: Customer wants to reschedule or change service
      → Use the customer's name and CURRENT appointment date/time
      → Use null for new_date/new_time/new_service if not changing
      → NEVER just say "rescheduled" - you MUST call this tool

   4. cancel_appointment(customer_name, date, time)
      → Call when: Customer confirms they want to cancel
      → Use the customer's name and appointment date/time
      → NEVER just say "cancelled" - you MUST call this tool

   5. check_availability(date, time)
      → Call when: Need to verify a specific slot is free

   6. get_available_slots(date)
      → Call when: Customer asks what times are available

   7. confirm_reminder()
      → Call when: Customer confirms they will come to their upcoming appointment
      → Works for ANY future appointment, not just tomorrow
      → Examples: "confermo", "ci sarò", "vengo", "yes I'll be there", "see you Tuesday"
      → IMPORTANT: If customer says "confermo" or "I confirm" → CALL THIS FUNCTION!
      → If success=True, thank them warmly in their language
      → If success=False, just respond normally (they may not have an appointment)

   8. escalate_to_human(reason)
      → Call when: Customer has a COMPLAINT, is ANGRY, FRUSTRATED, or asks to speak with a manager/human
      → This freezes the chat and notifies the owner via email
      → After calling this, respond: "Ho inoltrato la tua richiesta al nostro team. Ti contatteranno il prima possibile."
      → Examples of when to use:
         - "Sono molto deluso dal servizio" (complaint)
         - "Voglio parlare con il responsabile" (ask for manager)
         - "Il taglio era orribile" (complaint about service)
         - "Questo è inaccettabile!" (angry customer)
         - "I want to speak to a human" (ask for human)

   ⚡ ACTION = TOOL CALL
   If customer says "reschedule to 3pm" → CALL modify_appointment with new_time="15:00"
   If customer says "cancel my appointment" → CALL cancel_appointment with name/date/time
   If customer says "yes, book it" → CALL create_appointment
   If customer confirms their appointment ("confermo", "ci sarò", "vengo") → CALL confirm_reminder
   TALKING about doing something is NOT the same as DOING it!

📅 DATE DISPLAY RULE (IMP-001):
   When showing dates to customers, DO NOT include the year if it's the current year ({dates['year']}).

   ❌ WRONG: "mercoledì 6 gennaio 2026 alle 17:00" (year unnecessary)
   ✅ CORRECT: "mercoledì 6 gennaio alle 17:00" (clean, current year implied)

   Only show the year for dates in future years (2027+):
   ✅ "mercoledì 6 gennaio 2027 alle 17:00" (year needed - it's next year)

🚫 SALON TOPICS ONLY (IMP-007):
   You are ONLY here to help with salon-related topics:
   - Appointments (booking, modifying, canceling)
   - Services and prices
   - Business hours and location
   - Hair care advice (basic)

   If customer asks about NON-SALON topics (travel, weather, recipes, etc.):
   → Politely decline: "Mi dispiace, posso aiutarti solo con questioni relative al salone! Hai bisogno di prenotare un appuntamento?"
   → In English: "Sorry, I can only help with salon-related questions! Do you need to book an appointment?"

   Examples of what to DECLINE:
   - "What's the weather tomorrow?" → Decline
   - "Can you recommend a restaurant?" → Decline
   - "Tell me a joke" → Decline
   - "What's the capital of France?" → Decline

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

def create_calendar_event(customer_name: str, service: Dict, date_str: str, time_str: str, customer_phone: str = None, business: dict = None, operator_name: str = None) -> str:
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

        # Build summary and description with operator info
        summary = f"{service.get('name_it', 'Appuntamento')} - {customer_name}"
        if operator_name:
            summary += f" (con {operator_name})"

        description_lines = [
            f"Cliente: {customer_name}",
            f"Telefono: {customer_phone or 'N/A'}",
            f"Servizio: {service.get('name_it')}",
            f"Prezzo: €{service.get('price', 0)}",
        ]
        if operator_name:
            description_lines.append(f"Stilista: {operator_name}")

        event = {
            "summary": summary,
            "description": "\n".join(description_lines),
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

        calendar_id = (business or {}).get("google_calendar_id") or GOOGLE_CALENDAR_ID
        result = service_obj.events().insert(calendarId=calendar_id, body=event).execute()
        event_id = result.get("id")
        logger.info(f"✅ Calendar event created: {event_id}")
        return event_id

    except Exception as e:
        logger.error(f"❌ Failed to create calendar event: {e}")
        return None


def update_calendar_event(event_id: str, customer_name: str, service: Dict, date_str: str, time_str: str, customer_phone: str = None, business: dict = None, operator_name: str = None) -> bool:
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

        summary = f"{service.get('name_it', 'Appuntamento')} - {customer_name}"
        if operator_name:
            summary += f" (con {operator_name})"

        description_lines = [
            f"Cliente: {customer_name}",
            f"Telefono: {customer_phone or 'N/A'}",
            f"Servizio: {service.get('name_it')}",
            f"Prezzo: €{service.get('price', 0)}",
        ]
        if operator_name:
            description_lines.append(f"Stilista: {operator_name}")

        event = {
            "summary": summary,
            "description": "\n".join(description_lines),
            "start": {
                "dateTime": start_dt.isoformat(),
                "timeZone": "Europe/Rome"
            },
            "end": {
                "dateTime": end_dt.isoformat(),
                "timeZone": "Europe/Rome"
            }
        }

        calendar_id = (business or {}).get("google_calendar_id") or GOOGLE_CALENDAR_ID
        service_obj.events().update(calendarId=calendar_id, eventId=event_id, body=event).execute()
        logger.info(f"✅ Calendar event updated: {event_id}")
        return True

    except Exception as e:
        logger.error(f"❌ Failed to update calendar event: {e}")
        return False


def delete_calendar_event(event_id: str, business: dict = None) -> bool:
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

        calendar_id = (business or {}).get("google_calendar_id") or GOOGLE_CALENDAR_ID
        service_obj.events().delete(calendarId=calendar_id, eventId=event_id).execute()
        logger.info(f"✅ Calendar event deleted: {event_id}")
        return True

    except Exception as e:
        logger.error(f"❌ Failed to delete calendar event: {e}")
        return False

# ============================================================================
# REMINDER SYSTEM
# ============================================================================

def send_email(to_email: str, subject: str, body: str) -> bool:
    """Send email using Gmail SMTP"""
    try:
        msg = MIMEMultipart()
        msg['From'] = EMAIL_SENDER
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain', 'utf-8'))

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.send_message(msg)

        logger.info(f"✅ Email sent to {to_email}")
        return True
    except Exception as e:
        logger.error(f"❌ Email send failed: {e}")
        return False


def get_tomorrow_appointments(business_id: int = None) -> List[Dict]:
    """Get all confirmed appointments for tomorrow"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        tomorrow = (datetime.now(ITALY_TZ) + timedelta(days=1)).strftime("%Y-%m-%d")

        appointments = []
        if business_id is not None:
            # Multi-tenant: exclude auto-addon children (they are covered by parent's reminder)
            cur.execute("""
                SELECT id, customer_phone, customer_name, treatment_code,
                       appointment_date, appointment_time, price,
                       treatment_name, operator_name
                FROM appointments
                WHERE business_id = %s AND appointment_date = %s
                  AND status = 'confirmed'
                  AND (reminder_sent_at IS NULL OR reminder_sent_at < CURRENT_DATE)
                  AND COALESCE(is_auto_addon, FALSE) = FALSE
                ORDER BY appointment_time
            """, (business_id, tomorrow))
            for row in cur.fetchall():
                apt = {
                    "id": row[0],
                    "phone": row[1],
                    "name": row[2],
                    "service": row[3],
                    "date": row[4],
                    "time": row[5],
                    "price": row[6],
                    "treatment_name": row[7],
                    "operator_name": row[8],
                    "addons": []
                }
                # Fetch auto-addon children so we can include them in the reminder
                cur.execute("""
                    SELECT id, appointment_time, treatment_name, treatment_code
                    FROM appointments
                    WHERE parent_appointment_id = %s AND status = 'confirmed'
                    ORDER BY appointment_time
                """, (apt["id"],))
                for addon_row in cur.fetchall():
                    apt["addons"].append({
                        "id": addon_row[0],
                        "time": addon_row[1],
                        "treatment_name": addon_row[2],
                        "treatment_code": addon_row[3],
                    })
                appointments.append(apt)
        else:
            # Legacy: old salon_appointments table
            cur.execute("""
                SELECT id, customer_phone, customer_name, service_type,
                       appointment_date, appointment_time, price
                FROM salon_appointments
                WHERE appointment_date = %s
                  AND status = 'confirmed'
                  AND (reminder_sent_at IS NULL OR reminder_sent_at < CURRENT_DATE)
                ORDER BY appointment_time
            """, (tomorrow,))
            for row in cur.fetchall():
                appointments.append({
                    "id": row[0],
                    "phone": row[1],
                    "name": row[2],
                    "service": row[3],
                    "date": row[4],
                    "time": row[5],
                    "price": row[6],
                    "treatment_name": None,
                    "operator_name": None,
                    "addons": []
                })

        cur.close()
        conn.close()
        return appointments
    except Exception as e:
        logger.error(f"❌ Error getting tomorrow appointments: {e}")
        return []


def get_unconfirmed_appointments() -> List[Dict]:
    """Get appointments where reminder was sent but not confirmed"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        tomorrow = (datetime.now(ITALY_TZ) + timedelta(days=1)).strftime("%Y-%m-%d")

        cur.execute("""
            SELECT id, customer_phone, customer_name, service_type,
                   appointment_date, appointment_time, price
            FROM salon_appointments
            WHERE appointment_date = %s
              AND status = 'confirmed'
              AND reminder_sent_at IS NOT NULL
              AND reminder_confirmed = FALSE
            ORDER BY appointment_time
        """, (tomorrow,))

        appointments = []
        for row in cur.fetchall():
            appointments.append({
                "id": row[0],
                "phone": row[1],
                "name": row[2],
                "service": row[3],
                "date": row[4],
                "time": row[5],
                "price": row[6]
            })

        cur.close()
        conn.close()
        return appointments
    except Exception as e:
        logger.error(f"❌ Error getting unconfirmed appointments: {e}")
        return []


def mark_reminder_sent(appointment_id: int, business_id: int = None) -> bool:
    """Mark that reminder was sent for an appointment"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        if business_id is not None:
            cur.execute("""UPDATE appointments SET reminder_sent_at = CURRENT_TIMESTAMP WHERE id = %s""",
                        (appointment_id,))
            # Cascade to auto-addon children so they don't get a duplicate reminder
            cur.execute("""UPDATE appointments SET reminder_sent_at = CURRENT_TIMESTAMP
                           WHERE parent_appointment_id = %s""",
                        (appointment_id,))
        else:
            cur.execute("""UPDATE salon_appointments SET reminder_sent_at = CURRENT_TIMESTAMP WHERE id = %s""",
                        (appointment_id,))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"❌ Error marking reminder sent: {e}")
        return False


def mark_reminder_confirmed(phone: str, business_id: int = None) -> Dict:
    """Mark appointment as confirmed by customer - works for any future appointment"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        today = datetime.now(ITALY_TZ).strftime("%Y-%m-%d")
        normalized_phone = normalize_phone(phone)

        # Find the NEXT upcoming appointment from this phone (not just tomorrow)
        # This fixes BUG-006: allows confirmation for any future appointment
        if business_id is not None:
            cur.execute("""
                UPDATE appointments
                SET reminder_confirmed = TRUE,
                    reminder_confirmed_at = CURRENT_TIMESTAMP
                WHERE customer_phone = %s
                  AND appointment_date >= %s
                  AND business_id = %s
                  AND status = 'confirmed'
                  AND id = (
                      SELECT id FROM appointments
                      WHERE customer_phone = %s
                        AND appointment_date >= %s
                        AND business_id = %s
                        AND status = 'confirmed'
                      ORDER BY appointment_date, appointment_time
                      LIMIT 1
                  )
                RETURNING id, customer_name, treatment_code, appointment_date, appointment_time
            """, (normalized_phone, today, business_id, normalized_phone, today, business_id))
        else:
            cur.execute("""
                UPDATE salon_appointments
                SET reminder_confirmed = TRUE,
                    reminder_confirmed_at = CURRENT_TIMESTAMP
                WHERE customer_phone = %s
                  AND appointment_date >= %s
                  AND status = 'confirmed'
                  AND id = (
                      SELECT id FROM salon_appointments
                      WHERE customer_phone = %s
                        AND appointment_date >= %s
                        AND status = 'confirmed'
                      ORDER BY appointment_date, appointment_time
                      LIMIT 1
                  )
                RETURNING id, customer_name, service_type, appointment_date, appointment_time
            """, (normalized_phone, today, normalized_phone, today))

        result = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()

        if result:
            # Convert date and time to string for JSON serialization
            date_obj = result[3]
            time_obj = result[4]
            date_str = date_obj.strftime("%Y-%m-%d") if hasattr(date_obj, 'strftime') else str(date_obj)
            time_str = time_obj.strftime("%H:%M") if hasattr(time_obj, 'strftime') else str(time_obj)[:5]
            return {
                "success": True,
                "appointment_id": result[0],
                "name": result[1],
                "service": result[2],
                "date": date_str,
                "time": time_str,
                "message": f"Appointment for {result[1]} on {date_str} at {time_str} confirmed!"
            }
        return {"success": False, "reason": "No upcoming appointment found for this phone"}
    except Exception as e:
        logger.error(f"❌ Error confirming reminder: {e}")
        return {"success": False, "reason": str(e)}


def escalate_to_human(phone: str, reason: str, biz_context: dict = None) -> Dict:
    """
    Escalate conversation to a real person (owner).
    - Blocks the chat so bot won't respond
    - Sends email to owner with unblock link
    """
    try:
        # Block the chat
        chat_blocked[phone] = reason
        logger.info(f"🔒 Chat blocked for {phone}. Reason: {reason}")

        # Create unblock link
        # Use the EC2 public IP or domain
        base_url = os.getenv("BOT_BASE_URL", "http://3.239.106.181:8000")
        unblock_link = f"{base_url}/sblocca_chat/{phone}"

        biz_name = biz_context["business"].get("name", "Salon") if biz_context else "Aura Hair Studio"
        owner_email_addr = biz_context["business"].get("owner_email") if biz_context else None

        # Send email to owner
        email_body = f"""⚠️ ATTENZIONE: Richiesta di intervento umano

Telefono cliente: {phone}
Motivo: {reason}

La chat è stata bloccata automaticamente. Il bot non risponderà più a questo cliente.

🔓 Per sbloccare la chat e riprendere il servizio automatico:
{unblock_link}

---
{biz_name} - Sistema di notifica automatico
"""
        email_sent = send_alert_email(
            "⚠️ Intervento umano richiesto", email_body,
            to_email=owner_email_addr, business_name=biz_name
        )

        return {
            "success": True,
            "chat_blocked": True,
            "email_sent": email_sent,
            "message": "La conversazione è stata trasferita al nostro team. Ti contatteremo il prima possibile."
        }
    except Exception as e:
        logger.error(f"❌ Error escalating to human: {e}")
        return {"success": False, "error": str(e)}


async def send_reminder_messages():
    """Send reminder messages for ALL businesses (runs at 10 AM)"""
    logger.info("🔔 Starting daily reminder job...")

    # Get all active businesses with WhatsApp configured
    businesses = []
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """SELECT id, name, whatsapp_phone_number_id, meta_access_token, timezone
               FROM businesses WHERE status = 'active' AND whatsapp_phone_number_id IS NOT NULL"""
        )
        businesses = cur.fetchall()
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"❌ Failed to load businesses for reminders: {e}")

    if not businesses:
        # Legacy single-tenant fallback
        appointments = get_tomorrow_appointments()
        logger.info(f"📋 Found {len(appointments)} appointments for tomorrow (legacy)")
        for apt in appointments:
            time_str = apt["time"].strftime("%H:%M") if hasattr(apt["time"], 'strftime') else str(apt["time"])[:5]
            reminder_message = f"""Buongiorno!😊
Ti ricordiamo che domani alle ore {time_str} hai un appuntamento con noi.
Ti chiediamo gentilmente di confermare rispondendo a questo messaggio entro le 18:00 di oggi.

In caso di mancata conferma, non possiamo garantire la disponibilità dell'appuntamento.

Grazie!"""
            phone = apt["phone"]
            if not phone.startswith("+"):
                phone = "+" + phone
            success = await send_whatsapp_message(phone, reminder_message)
            if success:
                mark_reminder_sent(apt["id"])
                save_conversation_to_db(
                    phone=normalize_phone(phone), name=apt["name"],
                    message="[SISTEMA: Promemoria appuntamento inviato automaticamente]",
                    response=reminder_message
                )
                logger.info(f"✅ Reminder sent to {apt['name']} ({phone})")
            else:
                logger.error(f"❌ Failed to send reminder to {apt['name']} ({phone})")
        logger.info(f"🔔 Reminder job completed (legacy). Sent {len(appointments)} reminders.")
        return

    total_sent = 0
    for biz_id, biz_name, wa_phone_id, wa_token, tz_name in businesses:
        business = {"whatsapp_phone_number_id": wa_phone_id, "meta_access_token": wa_token, "name": biz_name}
        appointments = get_tomorrow_appointments(business_id=biz_id)
        logger.info(f"📋 [{biz_name}] Found {len(appointments)} appointments for tomorrow")

        for apt in appointments:
            time_str = apt["time"].strftime("%H:%M") if hasattr(apt["time"], 'strftime') else str(apt["time"])[:5]
            service_label = apt.get("treatment_name") or apt.get("service") or ""
            service_part = f" per *{service_label}*" if service_label else ""
            addon_part = ""
            for addon in apt.get("addons", []):
                addon_time = addon["time"]
                addon_time_str = addon_time.strftime("%H:%M") if hasattr(addon_time, 'strftime') else str(addon_time)[:5]
                addon_name = addon.get("treatment_name") or addon.get("treatment_code") or ""
                if addon_name:
                    addon_part += f"\nSeguito da *{addon_name}* alle ore {addon_time_str}."
            reminder_message = f"""Buongiorno!😊
Ti ricordiamo che domani alle ore {time_str} hai un appuntamento{service_part} con noi.{addon_part}
Ti chiediamo gentilmente di confermare rispondendo a questo messaggio entro le 18:00 di oggi.

In caso di mancata conferma, non possiamo garantire la disponibilità dell'appuntamento.

Grazie!"""
            phone = apt["phone"]
            if not phone.startswith("+"):
                phone = "+" + phone
            success = await send_whatsapp_message(phone, reminder_message, business)
            if success:
                mark_reminder_sent(apt["id"], business_id=biz_id)
                save_conversation_to_db(
                    phone=normalize_phone(phone), name=apt["name"],
                    message="[SISTEMA: Promemoria appuntamento inviato automaticamente]",
                    response=reminder_message, business_id=biz_id
                )
                total_sent += 1
                logger.info(f"✅ [{biz_name}] Reminder sent to {apt['name']} ({phone})")
            else:
                logger.error(f"❌ [{biz_name}] Failed to send reminder to {apt['name']} ({phone})")

    logger.info(f"🔔 Reminder job completed. Sent {total_sent} reminders across {len(businesses)} businesses.")


async def check_unconfirmed_and_notify():
    """Disabled per Bug #16 — unconfirmed appointment emails turned off."""
    logger.info("📧 check_unconfirmed_and_notify: disabled (Bug #16), skipping.")
    return

    unconfirmed = get_unconfirmed_appointments()
    logger.info(f"📋 Found {len(unconfirmed)} unconfirmed appointments")

    if not unconfirmed:
        logger.info("✅ All appointments confirmed! No email needed.")
        return

    # Build email body
    tomorrow = (datetime.now(ITALY_TZ) + timedelta(days=1)).strftime("%d/%m/%Y")

    email_body = f"""Ciao,

I seguenti appuntamenti per domani ({tomorrow}) NON sono stati confermati:

"""
    for apt in unconfirmed:
        time_str = apt["time"].strftime("%H:%M") if hasattr(apt["time"], 'strftime') else str(apt["time"])[:5]
        email_body += f"• {apt['name']} - {apt['service']} alle {time_str} (Tel: {apt['phone']})\n"

    email_body += """
Puoi decidere se mantenerli o cancellarli.

Saluti,
Sistema Aura Hair Studio"""

    # Send email
    subject = f"⚠️ Appuntamenti non confermati per domani ({tomorrow})"
    success = send_email(OWNER_EMAIL, subject, email_body)

    if success:
        logger.info(f"✅ Unconfirmed appointments email sent to {OWNER_EMAIL}")
    else:
        logger.error(f"❌ Failed to send unconfirmed appointments email")


# Scheduler instance
scheduler = AsyncIOScheduler(timezone=ITALY_TZ)


def setup_reminder_scheduler():
    """Set up the scheduled reminder jobs"""
    # 10:00 AM - Send reminders
    scheduler.add_job(
        send_reminder_messages,
        CronTrigger(hour=10, minute=0, timezone=ITALY_TZ),
        id="send_reminders",
        replace_existing=True
    )

    # 6:00 PM - Check unconfirmed and email owner
    scheduler.add_job(
        check_unconfirmed_and_notify,
        CronTrigger(hour=18, minute=0, timezone=ITALY_TZ),
        id="check_unconfirmed",
        replace_existing=True
    )

    # 10:05 AM - Send Instagram reminders (5 min after WhatsApp to avoid overlap)
    scheduler.add_job(
        send_ig_reminder_messages,
        CronTrigger(hour=10, minute=5, timezone=ITALY_TZ),
        id="ig_send_reminders",
        replace_existing=True
    )

    scheduler.start()
    logger.info("✅ Reminder scheduler started (10:00 WA reminders, 10:05 IG reminders, 18:00 check)")


# ============================================================================
# DATABASE FUNCTIONS
# ============================================================================

def get_db_connection():
    """Get database connection"""
    return psycopg2.connect(**DB_CONFIG)


def load_conversation_history_from_db(phone: str, limit: int = 5, business_id: int = None) -> list:
    """Load recent conversation history from database for context continuity."""
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        if business_id is not None:
            # Multi-tenant: new conversations table with JSONB messages
            # Bug #1 fix: only reuse history if last activity within 4h (else stale = new session)
            cur.execute(
                """SELECT messages, updated_at FROM conversations
                   WHERE business_id = %s AND customer_phone = %s""",
                (business_id, phone),
            )
            row = cur.fetchone()
            conn.close()
            if row and row[0] and isinstance(row[0], list):
                from datetime import datetime, timezone, timedelta
                updated_at = row[1]
                if updated_at is not None:
                    now = datetime.now(timezone.utc)
                    if updated_at.tzinfo is None:
                        updated_at = updated_at.replace(tzinfo=timezone.utc)
                    if now - updated_at > timedelta(hours=4):
                        logger.info(f"⏰ Session idle >4h for {phone} — starting fresh")
                        return []
                return row[0][-(limit * 2):]  # Last N exchanges (user+assistant)
            return []
        else:
            # Legacy: old salon_conversations table
            cur.execute(
                "SELECT message, response FROM salon_conversations WHERE phone = %s ORDER BY timestamp DESC LIMIT %s",
                (phone, limit),
            )
            rows = cur.fetchall()
            conn.close()
            history = []
            for row in reversed(rows):
                user_msg, bot_response = row
                if user_msg:
                    history.append({"role": "user", "content": user_msg})
                if bot_response:
                    history.append({"role": "assistant", "content": bot_response})
            if history:
                logger.info(f"📚 Loaded {len(rows)} conversation(s) from DB for {phone}")
            return history
    except Exception as e:
        logger.warning(f"⚠️ Failed to load conversation history from DB: {e}")
        return []
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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                reminder_sent_at TIMESTAMP,
                reminder_confirmed BOOLEAN DEFAULT FALSE,
                reminder_confirmed_at TIMESTAMP
            )
        """)

        # Add reminder columns if they don't exist (for existing tables)
        try:
            cur.execute("ALTER TABLE salon_appointments ADD COLUMN IF NOT EXISTS reminder_sent_at TIMESTAMP")
            cur.execute("ALTER TABLE salon_appointments ADD COLUMN IF NOT EXISTS reminder_confirmed BOOLEAN DEFAULT FALSE")
            cur.execute("ALTER TABLE salon_appointments ADD COLUMN IF NOT EXISTS reminder_confirmed_at TIMESTAMP")
        except:
            pass  # Columns already exist

        # Add platform column for multi-platform support (Instagram + WhatsApp)
        try:
            cur.execute("ALTER TABLE salon_appointments ADD COLUMN IF NOT EXISTS platform VARCHAR(20) DEFAULT 'whatsapp'")
            cur.execute("ALTER TABLE salon_conversations ADD COLUMN IF NOT EXISTS platform VARCHAR(20) DEFAULT 'whatsapp'")
        except:
            pass

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

def save_conversation_to_db(phone: str, name: str, message: str, response: str, platform: str = "whatsapp", business_id: int = None):
    """
    Save conversation to database for analytics and debugging.
    Wrapped in try/except so logging failure never breaks the bot.
    """
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        if business_id is not None:
            # Multi-tenant: upsert into conversations table with JSONB messages
            new_messages = json.dumps([
                {"role": "user", "content": message},
                {"role": "assistant", "content": response},
            ])
            cur.execute(
                """INSERT INTO conversations (business_id, customer_phone, messages, updated_at)
                   VALUES (%s, %s, %s::jsonb, NOW())
                   ON CONFLICT (business_id, customer_phone) DO UPDATE
                   SET messages = conversations.messages || %s::jsonb,
                       updated_at = NOW()""",
                (business_id, phone, new_messages, new_messages),
            )
        else:
            # Legacy: insert into salon_conversations
            cur.execute("""
                INSERT INTO salon_conversations (phone, name, message, response, platform, timestamp)
                VALUES (%s, %s, %s, %s, %s, NOW())
            """, (phone, name, message, response, platform))

        conn.commit()
        conn.close()
        logger.info(f"💾 Conversation logged for {phone} (platform={platform})")
    except Exception as e:
        # Never fail the bot because of logging issues
        logger.warning(f"⚠️ Failed to log conversation: {e}")

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
            "error_code": "CLOSED_HOLIDAY_CHRISTMAS"
        }
    if month_day == (1, 1):
        return {
            "valid": False,
            "error": "We are closed on New Year's Day (January 1)",
            "error_code": "CLOSED_HOLIDAY_NEWYEAR"
        }

    # Check for closed days (Monday and Sunday)
    if weekday == 0:  # Monday
        return {
            "valid": False,
            "error": "We are closed on Mondays. We're open Tuesday-Saturday.",
            "error_code": "CLOSED_MONDAY"
        }
    if weekday == 6:  # Sunday
        return {
            "valid": False,
            "error": "We are closed on Sundays. We're open Tuesday-Saturday.",
            "error_code": "CLOSED_SUNDAY"
        }

    return {"valid": True}


_LEGACY_WEEKDAY_SLOTS = [
    "09:00", "09:30", "10:00", "10:30", "11:00", "11:30",
    "12:00", "12:30", "13:00", "13:30", "14:00", "14:30",
    "15:00", "15:30", "16:00", "16:30", "17:00", "17:30",
]


def _get_slots_for_date(date_str: str, biz_context: dict = None, parsed_date=None) -> list:
    """Generate all time slots for a date using business hours or legacy defaults."""
    if biz_context:
        dt = parsed_date or datetime.strptime(date_str, "%Y-%m-%d")
        dow = dt.weekday()
        day_hours = biz_context["hours"].get(dow, {})
        if day_hours.get("is_open") and day_hours.get("open_time") and day_hours.get("close_time"):
            return generate_available_slots(day_hours["open_time"], day_hours["close_time"])
        return []
    # Legacy Aura fallback
    if parsed_date:
        weekday = parsed_date.weekday()
    else:
        weekday = datetime.strptime(date_str, "%Y-%m-%d").weekday()
    closing_hour = 17 if weekday == 5 else 18
    return [f"{h:02d}:{m:02d}" for h in range(9, closing_hour) for m in (0, 30)]


# ============================================================================
# BOOKING FUNCTIONS (Called by AI)
# ============================================================================

def create_appointment(customer_phone: str, customer_name: str, service_type: str, date: str, time: str,
                       platform: str = "whatsapp", business_id: int = None, biz_context: dict = None,
                       operator_name: str = None) -> Dict[str, Any]:
    """Create a salon appointment"""
    try:
        # Normalize phone
        normalized_phone = normalize_phone(customer_phone)

        # Validate customer name
        if not customer_name or not customer_name.strip():
            return {"success": False, "error": "CUSTOMER_NAME_REQUIRED"}
        customer_name = customer_name.strip()

        # Reject generic placeholder names
        generic_names = {"client", "cliente", "utente", "customer", "user", "ospite", "guest"}
        if customer_name.lower() in generic_names:
            return {
                "success": False,
                "error": "GENERIC_NAME_NOT_ALLOWED",
                "message_it": "Per favore, chiedi il nome del cliente prima di prenotare.",
                "message_en": "Please ask the customer's name before booking.",
            }

        # Validate service
        if biz_context:
            services = biz_context["services"]
            service = services.get(service_type.lower())
            if not service:
                return {
                    "success": False,
                    "error": "INVALID_SERVICE",
                    "provided": service_type,
                    "valid_services": list(services.keys())
                }
        else:
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
            if appointment_datetime < datetime.now(ITALY_TZ).replace(tzinfo=None):
                return {"success": False, "error": "PAST_DATE_NOT_ALLOWED"}
        except ValueError:
            return {"success": False, "error": "INVALID_DATE_TIME_FORMAT", "provided_date": date, "provided_time": time}

        # Validate business hours, closed days, and holidays
        if biz_context:
            business_validation = validate_day_and_time(date, time, biz_context["hours"], biz_context["closures"])
        else:
            business_validation = validate_business_day_and_time(date, time)
        if not business_validation["valid"]:
            return {
                "success": False,
                "error": business_validation["error_code"],
                "message": business_validation["error"],
                "date": date,
                "time": time
            }

        # Resolve operator (multi-tenant only)
        operators = biz_context.get("operators", []) if biz_context else []
        op_result = resolve_operator(operator_name, service_type, operators)
        if not op_result["success"]:
            return {"success": False, **op_result}

        resolved_operator_id = op_result.get("operator_id")
        resolved_operator_name = op_result.get("operator_name")

        conn = get_db_connection()
        try:
            cur = conn.cursor()

            # Check availability
            if business_id is not None:
                if op_result.get("auto_assign"):
                    # Auto-assign: pick least-busy eligible operator who is FREE at this time
                    eligible_ids = op_result["eligible_operator_ids"]
                    cur.execute(
                        """SELECT o.id, o.display_name FROM operators o
                           LEFT JOIN (
                               SELECT operator_id, COUNT(*) cnt FROM appointments
                               WHERE business_id = %s AND appointment_date = %s AND status = 'confirmed'
                               GROUP BY operator_id
                           ) a ON a.operator_id = o.id
                           WHERE o.id = ANY(%s)
                             AND NOT EXISTS (
                                 SELECT 1 FROM appointments ap
                                 WHERE ap.business_id = %s AND ap.operator_id = o.id
                                   AND ap.appointment_date = %s AND ap.appointment_time = %s
                                   AND ap.status = 'confirmed'
                             )
                           ORDER BY COALESCE(a.cnt, 0), o.sort_order LIMIT 1""",
                        (business_id, date, eligible_ids, business_id, date, time)
                    )
                    row = cur.fetchone()
                    if row:
                        resolved_operator_id = row[0]
                        resolved_operator_name = row[1]
                        count = 0  # This operator is free at this time
                    else:
                        # All eligible operators are booked at this time
                        count = 1

                elif resolved_operator_id is not None:
                    # Specific operator: check per-operator availability
                    cur.execute(
                        """SELECT COUNT(*) FROM appointments
                           WHERE business_id = %s AND operator_id = %s
                                 AND appointment_date = %s AND appointment_time = %s AND status = 'confirmed'""",
                        (business_id, resolved_operator_id, date, time)
                    )
                    count = cur.fetchone()[0]
                    if count > 0 and operators:
                        # Preferred operator busy — try auto-assign fallback
                        eligible_ids = [op["id"] for op in operators]
                        cur.execute(
                            """SELECT o.id, o.display_name FROM operators o
                               LEFT JOIN (
                                   SELECT operator_id, COUNT(*) cnt FROM appointments
                                   WHERE business_id = %s AND appointment_date = %s AND status = 'confirmed'
                                   GROUP BY operator_id
                               ) a ON a.operator_id = o.id
                               WHERE o.id = ANY(%s)
                                 AND NOT EXISTS (
                                     SELECT 1 FROM appointments ap
                                     WHERE ap.business_id = %s AND ap.operator_id = o.id
                                       AND ap.appointment_date = %s AND ap.appointment_time = %s
                                       AND ap.status = 'confirmed'
                                 )
                               ORDER BY COALESCE(a.cnt, 0), o.sort_order LIMIT 1""",
                            (business_id, date, eligible_ids, business_id, date, time)
                        )
                        fallback_row = cur.fetchone()
                        if fallback_row:
                            resolved_operator_id = fallback_row[0]
                            resolved_operator_name = fallback_row[1]
                            count = 0  # fallback operator is free
                else:
                    # No operators configured: legacy global check
                    cur.execute(
                        """SELECT COUNT(*) FROM appointments
                           WHERE business_id = %s AND appointment_date = %s AND appointment_time = %s AND status = 'confirmed'""",
                        (business_id, date, time)
                    )
                    count = cur.fetchone()[0]
            else:
                cur.execute(
                    """SELECT COUNT(*) FROM salon_appointments
                       WHERE appointment_date = %s AND appointment_time = %s AND status = 'confirmed'""",
                    (date, time)
                )
                count = cur.fetchone()[0]

            if count > 0:
                # Get available alternatives for the same date
                if business_id is not None:
                    cur.execute(
                        """SELECT appointment_time FROM appointments
                           WHERE business_id = %s AND appointment_date = %s AND status = 'confirmed'
                           ORDER BY appointment_time""",
                        (business_id, date)
                    )
                else:
                    cur.execute(
                        """SELECT appointment_time FROM salon_appointments
                           WHERE appointment_date = %s AND status = 'confirmed'
                           ORDER BY appointment_time""",
                        (date,)
                    )
                booked_times = [str(row[0])[:5] for row in cur.fetchall()]

                # Generate all available slots
                all_slots = _get_slots_for_date(date, biz_context)
                available_slots = [t for t in all_slots if t not in booked_times]

                # Sort by proximity to requested time (BUG-002 FIX)
                def time_to_minutes(t):
                    h, m = map(int, t.split(':'))
                    return h * 60 + m

                requested_minutes = time_to_minutes(time)
                available_slots.sort(key=lambda t: abs(time_to_minutes(t) - requested_minutes))

                # Take the 4 closest alternatives
                available_alternatives = available_slots[:4]

                return {
                    "success": False,
                    "error": "SLOT_ALREADY_BOOKED",
                    "date": date,
                    "time": time,
                    "available_alternatives": available_alternatives,
                    "message": f"Sorry, {time} on {date} is already booked. Nearest available times: {', '.join(available_alternatives)}"
                }

            # Create Google Calendar event first
            business = biz_context["business"] if biz_context else None
            google_event_id = create_calendar_event(
                customer_name=customer_name,
                service=service,
                date_str=date,
                time_str=time,
                customer_phone=normalized_phone,
                business=business,
                operator_name=resolved_operator_name
            )

            # Create appointment with google_event_id
            if business_id is not None:
                cur.execute(
                    """INSERT INTO appointments
                       (business_id, operator_id, operator_name, customer_phone, customer_name,
                        treatment_code, treatment_name,
                        appointment_date, appointment_time, duration_minutes, price, status,
                        google_event_id, platform)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'confirmed', %s, %s)
                       RETURNING id""",
                    (business_id, resolved_operator_id, resolved_operator_name,
                     normalized_phone, customer_name, service_type,
                     service.get("name_it", service_type), date, time,
                     service["duration"], service["price"], google_event_id, platform)
                )
            else:
                cur.execute(
                    """INSERT INTO salon_appointments
                       (customer_phone, customer_name, service_type, appointment_date, appointment_time, duration_minutes, price, status, google_event_id, platform)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, 'confirmed', %s, %s)
                       RETURNING id""",
                    (normalized_phone, customer_name, service_type, date, time, service["duration"], service["price"], google_event_id, platform)
                )

            appointment_id = cur.fetchone()[0]
            conn.commit()

            calendar_note = " (synced to calendar)" if google_event_id else ""
            logger.info(f"✅ Appointment created: #{appointment_id} for {customer_name}{calendar_note}")

            result = {
                "success": True,
                "appointment_id": appointment_id,
                "customer_name": customer_name,
                "service": service['name_it'],
                "service_en": service.get('name_en', service_type),
                "date": date,
                "time": time,
                "calendar_synced": bool(google_event_id)
            }
            if resolved_operator_name:
                result["operator_name"] = resolved_operator_name

            # --- Auto-addon: book follow-up treatment if configured ---
            addon = service.get("auto_addon") if service else None
            if addon and biz_context:
                try:
                    main_end = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M") + timedelta(minutes=service["duration"])
                    addon_time = main_end.strftime("%H:%M")
                    addon_service = biz_context["services"].get(addon["code"])
                    if addon_service:
                        # Find operator enabled for the addon treatment (may differ from main)
                        addon_op_id = resolved_operator_id
                        addon_op_name = resolved_operator_name
                        addon_operators = biz_context.get("operators", [])
                        logger.info(f"🔍 Addon check: addon_code={addon['code']}, main_op_id={addon_op_id}, operators_count={len(addon_operators)}")
                        if addon_operators and addon_op_id:
                            # Check if main operator can do addon
                            main_op = next((op for op in addon_operators if op["id"] == addon_op_id), None)
                            if main_op:
                                logger.info(f"🔍 Main op {main_op['display_name']} treatments: {main_op.get('treatments', [])}, addon_code='{addon['code']}', match={addon['code'] in main_op.get('treatments', [])}")
                            if main_op and addon["code"] not in main_op.get("treatments", []):
                                # Main operator can't do addon — find one who can and is free
                                for op in addon_operators:
                                    if addon["code"] in op.get("treatments", []):
                                        addon_op_id = op["id"]
                                        addon_op_name = op["display_name"]
                                        break
                        addon_event_id = create_calendar_event(
                            customer_name=customer_name, service=addon_service,
                            date_str=date, time_str=addon_time,
                            customer_phone=normalized_phone,
                            business=biz_context.get("business"),
                            operator_name=addon_op_name,
                        )
                        cur2 = conn.cursor()
                        cur2.execute(
                            """INSERT INTO appointments
                               (business_id, operator_id, operator_name, customer_phone, customer_name,
                                treatment_code, treatment_name,
                                appointment_date, appointment_time, duration_minutes, price, status,
                                google_event_id, platform, is_auto_addon, parent_appointment_id)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'confirmed', %s, %s, TRUE, %s)
                               RETURNING id""",
                            (business_id, addon_op_id, addon_op_name,
                             normalized_phone, customer_name, addon["code"],
                             addon["name_it"], date, addon_time,
                             addon["duration"], addon["price"], addon_event_id, platform,
                             appointment_id)
                        )
                        addon_appt_id = cur2.fetchone()[0]
                        conn.commit()
                        result["auto_addon"] = {
                            "appointment_id": addon_appt_id,
                            "service": addon["name_it"],
                            "time": addon_time,
                            "duration": addon["duration"],
                            "price": addon["price"],
                        }
                        result["total_duration"] = service["duration"] + addon["duration"]
                        logger.info(f"✅ Auto-addon #{addon_appt_id} ({addon['name_it']}) at {addon_time}")
                except Exception as e:
                    logger.error(f"⚠️ Auto-addon failed (main OK): {e}")

            return result
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

def check_availability(date: str, time: str, business_id: int = None, biz_context: dict = None,
                       operator_name: str = None, treatment_code: str = None) -> Dict[str, Any]:
    """Check if a time slot is available. If not, suggest nearest alternatives.

    Bug #2 fix: when treatment_code provided, also validates appt end time
    does not exceed salon close_time (factoring auto-addon duration).
    """
    try:
        operators = biz_context.get("operators", []) if biz_context else []

        # --- Bug #2: closing-time check ---
        if treatment_code and biz_context:
            services = biz_context.get("services") or {}
            hours = biz_context.get("hours") or {}
            svc = services.get(treatment_code)
            if svc:
                total_min = int(svc.get("duration", 0))
                addon = svc.get("auto_addon")
                if addon:
                    total_min += int(addon.get("duration", 0))
                try:
                    from datetime import datetime as _dt2, timedelta as _td2
                    start_dt = _dt2.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
                    end_dt = start_dt + _td2(minutes=total_min)
                    dow = start_dt.weekday()
                    day_hours = hours.get(dow) or hours.get(str(dow))
                    if day_hours and day_hours.get("is_open") is False:
                        return {
                            "success": True,
                            "available": False,
                            "date": date,
                            "time": time,
                            "reason": "salon_closed_day",
                            "message": f"Il salone e' chiuso quel giorno."
                        }
                    if day_hours and day_hours.get("close_time"):
                        close_str = day_hours["close_time"]
                        close_dt = _dt2.strptime(f"{date} {close_str}", "%Y-%m-%d %H:%M")
                        if end_dt > close_dt:
                            return {
                                "success": True,
                                "available": False,
                                "date": date,
                                "time": time,
                                "reason": "exceeds_closing",
                                "total_duration_minutes": total_min,
                                "salon_close_time": close_str,
                                "appointment_end_time": end_dt.strftime("%H:%M"),
                                "message": (
                                    f"Il trattamento dura {total_min} min e finirebbe alle "
                                    f"{end_dt.strftime('%H:%M')}, ma chiudiamo alle {close_str}. "
                                    f"Serve un orario piu' presto."
                                )
                            }
                    if day_hours and day_hours.get("open_time"):
                        open_str = day_hours["open_time"]
                        open_dt = _dt2.strptime(f"{date} {open_str}", "%Y-%m-%d %H:%M")
                        if start_dt < open_dt:
                            return {
                                "success": True,
                                "available": False,
                                "date": date,
                                "time": time,
                                "reason": "before_opening",
                                "salon_open_time": open_str,
                                "message": f"Apriamo alle {open_str}."
                            }
                except Exception as _e_close:
                    logger.warning(f"close-time check failed: {_e_close}")
        # --- end Bug #2 ---

        # --- Bug #4: compute requested duration for overlap checks ---
        _req_duration = 60  # safe default
        if treatment_code and biz_context:
            _svc = (biz_context.get("services") or {}).get(treatment_code)
            if _svc:
                _req_duration = int(_svc.get("duration") or 60)
                _addon = _svc.get("auto_addon")
                if _addon:
                    _req_duration += int(_addon.get("duration") or 0)
        # --- end Bug #4 setup ---

        conn = get_db_connection()
        try:
            cur = conn.cursor()
            if business_id is not None:
                if operator_name and operators:
                    # Specific operator: check per-operator
                    op_result = resolve_operator(operator_name, "", operators)
                    if not op_result["success"]:
                        return {"success": False, **op_result}
                    op_id = op_result["operator_id"]
                    # Bug #4: interval overlap, not exact-time match
                    cur.execute(
                        """SELECT COUNT(*) FROM appointments
                           WHERE business_id = %s AND operator_id = %s
                                 AND appointment_date = %s AND status = 'confirmed'
                                 AND appointment_time < (%s::time + (%s || ' minutes')::interval)
                                 AND (appointment_time + (COALESCE(duration_minutes, 60) || ' minutes')::interval) > %s::time""",
                        (business_id, op_id, date, time, _req_duration, time)
                    )
                    count = cur.fetchone()[0]
                    available = count == 0
                    # Validate time is within operator working hours
                    if available and op_id is not None:
                        try:
                            _dow = datetime.strptime(date, "%Y-%m-%d").weekday()
                            _op_obj = next((op for op in operators if op["id"] == op_id), None)
                            if _op_obj:
                                _op_h = _op_obj.get("hours", {}).get(_dow)
                                if _op_h:
                                    if not _op_h.get("is_working"):
                                        conn.close()
                                        return {
                                            "success": True, "available": False,
                                            "date": date, "time": time,
                                            "reason": "operator_day_off",
                                            "message": str(op_result.get("operator_name", "L'operatore")) + " non lavora in questo giorno.",
                                        }
                                    elif _op_h.get("start") and _op_h.get("end"):
                                        if time < _op_h["start"] or time >= _op_h["end"]:
                                            conn.close()
                                            return {
                                                "success": True, "available": False,
                                                "date": date, "time": time,
                                                "reason": "outside_operator_hours",
                                                "operator_start": _op_h["start"],
                                                "operator_end": _op_h["end"],
                                                "message": str(op_result.get("operator_name", "L'operatore")) + " lavora dalle " + _op_h["start"] + " alle " + _op_h["end"] + " in questo giorno.",
                                            }
                        except Exception as _e_hours:
                            logger.warning(f"operator hours check failed: {_e_hours}")
                elif operators and not operator_name:
                    # No preference + operators exist: available if not ALL operators booked
                    # Bug #4: count operators with overlapping appts
                    cur.execute(
                        """SELECT COUNT(DISTINCT operator_id) FROM appointments
                           WHERE business_id = %s AND appointment_date = %s AND status = 'confirmed'
                                 AND appointment_time < (%s::time + (%s || ' minutes')::interval)
                                 AND (appointment_time + (COALESCE(duration_minutes, 60) || ' minutes')::interval) > %s::time""",
                        (business_id, date, time, _req_duration, time)
                    )
                    booked_count = cur.fetchone()[0]
                    available = booked_count < len(operators)
                    _no_pref_operator = None
                    if available:
                        # Fix: filter to treatment-eligible operators so assigned_operator
                        # can actually perform the requested service (was returning Greta
                        # for taglio_uomo because sort_order picks any free operator).
                        _eligible_ids = [
                            op["id"] for op in operators
                            if not treatment_code
                            or not op.get("treatments")
                            or treatment_code in op.get("treatments", [])
                        ] if treatment_code else [op["id"] for op in operators]
                        _op_ids = _eligible_ids if _eligible_ids else [op["id"] for op in operators]
                        # Bug #4: NOT EXISTS interval overlap
                        cur.execute(
                            """SELECT o.id, o.display_name FROM operators o
                               WHERE o.id = ANY(%s)
                                 AND NOT EXISTS (
                                     SELECT 1 FROM appointments ap
                                     WHERE ap.business_id = %s AND ap.operator_id = o.id
                                       AND ap.appointment_date = %s AND ap.status = 'confirmed'
                                       AND ap.appointment_time < (%s::time + (%s || ' minutes')::interval)
                                       AND (ap.appointment_time + (COALESCE(ap.duration_minutes, 60) || ' minutes')::interval) > %s::time
                                 )
                               ORDER BY o.sort_order LIMIT 1""",
                            (_op_ids, business_id, date, time, _req_duration, time)
                        )
                        _free_op = cur.fetchone()
                        if _free_op:
                            _no_pref_operator = _free_op[1]
                else:
                    # No operators configured: legacy global check (Bug #4: interval overlap)
                    cur.execute(
                        """SELECT COUNT(*) FROM appointments
                           WHERE business_id = %s AND appointment_date = %s AND status = 'confirmed'
                                 AND appointment_time < (%s::time + (%s || ' minutes')::interval)
                                 AND (appointment_time + (COALESCE(duration_minutes, 60) || ' minutes')::interval) > %s::time""",
                        (business_id, date, time, _req_duration, time)
                    )
                    count = cur.fetchone()[0]
                    available = count == 0
            else:
                cur.execute(
                    """SELECT COUNT(*) FROM salon_appointments
                       WHERE appointment_date = %s AND appointment_time = %s AND status = 'confirmed'""",
                    (date, time)
                )
                count = cur.fetchone()[0]
                available = count == 0

            result = {
                "success": True,
                "available": available,
                "date": date,
                "time": time
            }
            # Include free operator name so AI doesn't hallucinate one
            if locals().get("_no_pref_operator"):
                result["assigned_operator"] = _no_pref_operator

            # If not available, suggest nearest alternatives (BUG-001/BUG-002 enhancement)
            if not available:
                if business_id is not None:
                    cur.execute(
                        """SELECT appointment_time FROM appointments
                           WHERE business_id = %s AND appointment_date = %s AND status = 'confirmed'""",
                        (business_id, date)
                    )
                else:
                    cur.execute(
                        """SELECT appointment_time FROM salon_appointments
                           WHERE appointment_date = %s AND status = 'confirmed'""",
                        (date,)
                    )
                booked_times = set(str(row[0])[:5] for row in cur.fetchall())

                # Generate all available slots
                all_slots = _get_slots_for_date(date, biz_context)
                available_slots = [t for t in all_slots if t not in booked_times]

                # Sort by proximity to requested time
                def time_to_minutes(t):
                    h, m = map(int, t.split(':'))
                    return h * 60 + m

                requested_minutes = time_to_minutes(time)
                available_slots.sort(key=lambda t: abs(time_to_minutes(t) - requested_minutes))

                result["nearest_alternatives"] = available_slots[:4]
                result["message"] = f"The slot {time} is not available. Nearest available: {', '.join(available_slots[:4])}"

            return result
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

def get_customer_appointments(customer_phone: str, business_id: int = None, biz_context: dict = None) -> Dict[str, Any]:
    """Get all FUTURE appointments for a customer (filters out past appointments)"""
    try:
        # Normalize phone
        normalized_phone = normalize_phone(customer_phone)
        # Format phone for display (last 4 digits)
        phone_display = f"***{normalized_phone[-4:]}" if len(normalized_phone) >= 4 else normalized_phone
        now = datetime.now()
        today = now.date()
        current_time = now.strftime("%H:%M")

        conn = get_db_connection()
        try:
            cur = conn.cursor()
            # Only get future appointments (today with future time, or future dates)
            if business_id is not None:
                # Bug #7: hide auto-addon entries — they roll up under their parent
                cur.execute(
                    """SELECT id, customer_name, treatment_code, appointment_date, appointment_time,
                              price, status, google_event_id
                       FROM appointments
                       WHERE business_id = %s AND customer_phone = %s AND status = 'confirmed'
                       AND COALESCE(is_auto_addon, FALSE) = FALSE
                       AND (appointment_date > %s OR (appointment_date = %s AND appointment_time > %s))
                       ORDER BY appointment_date, appointment_time""",
                    (business_id, normalized_phone, today, today, current_time)
                )
            else:
                cur.execute(
                    """SELECT id, customer_name, service_type, appointment_date, appointment_time, price, status, google_event_id
                       FROM salon_appointments
                       WHERE customer_phone = %s AND status = 'confirmed'
                       AND (appointment_date > %s OR (appointment_date = %s AND appointment_time > %s))
                       ORDER BY appointment_date, appointment_time""",
                    (normalized_phone, today, today, current_time)
                )

            services = biz_context["services"] if biz_context else SALON_SERVICES

            appointments = []
            for idx, row in enumerate(cur.fetchall(), 1):
                service = services.get(row[2], {})
                time_24h = str(row[4])[:5]  # HH:MM format for function calls
                # Bug #7: surface addon info if parent has one (so AI says "Taglio Donna include Piega")
                addon_info = None
                _addon_obj = service.get("auto_addon") if isinstance(service, dict) else None
                if _addon_obj:
                    addon_info = {
                        "name_it": _addon_obj.get("name_it"),
                        "duration": _addon_obj.get("duration"),
                    }
                _appt = {
                    "customer_name": row[1],
                    "service_code": row[2],
                    "service_en": service.get("name_en", row[2]),
                    "service_it": service.get("name_it", row[2]),
                    "date": str(row[3]),
                    "time": format_time_12h(row[4]),
                    "time_24h": time_24h,
                    "price": float(row[5]) if row[5] else 0,
                    "status": row[6],
                    "google_event_id": row[7]
                }
                if addon_info:
                    _appt["includes_addon"] = addon_info
                appointments.append(_appt)

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
                "appointments": appointments,
                "count": len(appointments)
            }
        finally:
            conn.close()

    except Exception as e:
        return {"success": False, "error": str(e)}

def cancel_appointment(customer_phone: str, customer_name: str, date: str, time: str,
                       business_id: int = None, biz_context: dict = None) -> Dict[str, Any]:
    """Cancel an appointment by customer name, date, and time (no ID needed)"""
    try:
        # Normalize phone
        normalized_phone = normalize_phone(customer_phone)

        # Normalize time to HH:MM format
        normalized_time = time
        if time and len(time) == 4 and ':' not in time:
            normalized_time = f"{time[:2]}:{time[2:]}"

        conn = get_db_connection()
        try:
            cur = conn.cursor()

            # Find appointment by phone + date + time (phone is authoritative; name can change)
            if business_id is not None:
                cur.execute(
                    """SELECT id, google_event_id, customer_name, appointment_date, appointment_time
                       FROM appointments
                       WHERE business_id = %s AND customer_phone = %s
                       AND appointment_date = %s AND appointment_time = %s
                       AND status = 'confirmed'
                       AND COALESCE(is_auto_addon, FALSE) = FALSE""",
                    (business_id, normalized_phone, date, normalized_time)
                )
            else:
                cur.execute(
                    """SELECT id, google_event_id, customer_name, appointment_date, appointment_time
                       FROM salon_appointments
                       WHERE customer_phone = %s
                       AND LOWER(customer_name) LIKE %s
                       AND appointment_date = %s
                       AND appointment_time = %s
                       AND status = 'confirmed'""",
                    (normalized_phone, f"%{customer_name.lower()}%", date, normalized_time)
                )

            row = cur.fetchone()
            if not row:
                return {
                    "success": False,
                    "error": "APPOINTMENT_NOT_FOUND",
                    "searched_for": {
                        "customer_name": customer_name,
                        "date": date,
                        "time": time
                    },
                    "hint": "No matching appointment found. Check the name, date, and time."
                }

            appointment_id = row[0]
            google_event_id = row[1]
            found_name = row[2]
            found_date = str(row[3])
            found_time = str(row[4])

            # Delete from Google Calendar
            if google_event_id:
                business = biz_context["business"] if biz_context else None
                delete_calendar_event(google_event_id, business=business)

            # Cancel appointment + poison-pill reminder_sent_at to stop future reminders
            if business_id is not None:
                cur.execute(
                    """UPDATE appointments SET status = 'cancelled', reminder_sent_at = NOW()
                       WHERE id = %s""",
                    (appointment_id,)
                )
                # Cancel any auto-addon child appointments
                cur.execute(
                    """UPDATE appointments SET status = 'cancelled', reminder_sent_at = NOW()
                       WHERE parent_appointment_id = %s AND status = 'confirmed'
                       RETURNING id, google_event_id""",
                    (appointment_id,)
                )
                addon_rows = cur.fetchall()
                for addon_row in addon_rows:
                    if addon_row[1]:
                        try:
                            delete_calendar_event(addon_row[1], business=biz_context.get("business") if biz_context else None)
                        except Exception as _e_cal:
                            logger.warning(f"Failed deleting addon calendar event: {_e_cal}")
                if addon_rows:
                    logger.info(f"✅ Cascaded cancel to {len(addon_rows)} auto-addon(s)")
            else:
                cur.execute("UPDATE salon_appointments SET status = 'cancelled' WHERE id = %s", (appointment_id,))
            conn.commit()

            calendar_note = " (removed from calendar)" if google_event_id else ""
            logger.info(f"✅ Appointment #{appointment_id} for {found_name} cancelled{calendar_note}")

            return {
                "success": True,
                "cancelled_appointment": {
                    "customer_name": found_name,
                    "date": found_date,
                    "time": found_time
                },
                "calendar_updated": bool(google_event_id)
            }
        finally:
            conn.close()

    except Exception as e:
        return {"success": False, "error": str(e)}

def modify_appointment(
    customer_phone: str,
    customer_name: str,
    current_date: str,
    current_time: str,
    new_date: str = None,
    new_time: str = None,
    new_service: str = None,
    business_id: int = None,
    biz_context: dict = None,
    new_operator: str = None
) -> Dict[str, Any]:
    """
    Modify an existing appointment by customer name, date, and time (no ID needed).
    Can change date, time, service, or any combination.
    """
    try:
        # Normalize phone
        normalized_phone = normalize_phone(customer_phone)

        # Normalize current_time to HH:MM format
        normalized_current_time = current_time
        if current_time and len(current_time) == 4 and ':' not in current_time:
            normalized_current_time = f"{current_time[:2]}:{current_time[2:]}"

        name_pattern = f"%{customer_name.lower()}%"

        conn = get_db_connection()
        try:
            cur = conn.cursor()

            # Find the appointment by name + date + time (fuzzy match on name)
            if business_id is not None:
                cur.execute(
                    """SELECT id, customer_name, treatment_code, appointment_date, appointment_time, google_event_id
                       FROM appointments
                       WHERE business_id = %s AND customer_phone = %s
                       AND LOWER(customer_name) LIKE %s
                       AND appointment_date = %s AND appointment_time = %s
                       AND status = 'confirmed'""",
                    (business_id, normalized_phone, name_pattern, current_date, normalized_current_time)
                )
            else:
                cur.execute(
                    """SELECT id, customer_name, service_type, appointment_date, appointment_time, google_event_id
                       FROM salon_appointments
                       WHERE customer_phone = %s
                       AND LOWER(customer_name) LIKE %s
                       AND appointment_date = %s
                       AND appointment_time = %s
                       AND status = 'confirmed'""",
                    (normalized_phone, name_pattern, current_date, normalized_current_time)
                )

            appointment = cur.fetchone()
            if not appointment:
                return {
                    "success": False,
                    "error": "APPOINTMENT_NOT_FOUND",
                    "searched_for": {
                        "customer_name": customer_name,
                        "date": current_date,
                        "time": current_time
                    },
                    "hint": "No matching appointment found. Check the name, date, and time."
                }

            # Get values from database
            appointment_id = appointment[0]  # ID for internal use only
            db_name = appointment[1]
            db_service = appointment[2]
            db_date = str(appointment[3])
            db_time = str(appointment[4])[:5]  # HH:MM format
            google_event_id = appointment[5]

            # Determine new values (use database values if not provided)
            final_date = new_date if new_date else db_date
            final_time = new_time if new_time else db_time
            final_service = new_service.lower() if new_service else db_service

            # Validate new service ONLY if being changed
            services = biz_context["services"] if biz_context else SALON_SERVICES
            if new_service:
                service = services.get(final_service)
                if not service:
                    return {
                        "success": False,
                        "error": "INVALID_SERVICE",
                        "provided": final_service,
                        "valid_services": list(services.keys())
                    }
            else:
                # Keep existing service - get it for duration/price or use defaults
                service = services.get(final_service, {"duration": 45, "price": 35, "name_it": final_service})

            # Validate new date and time together (check if in the past)
            try:
                final_datetime = datetime.strptime(f"{final_date} {final_time}", "%Y-%m-%d %H:%M")
                if final_datetime < datetime.now():
                    return {"success": False, "error": "PAST_DATE_NOT_ALLOWED"}
            except ValueError:
                return {"success": False, "error": "INVALID_DATE_TIME_FORMAT", "provided_date": final_date, "provided_time": final_time}

            # Validate business hours, closed days, and holidays
            if biz_context:
                business_validation = validate_day_and_time(final_date, final_time, biz_context["hours"], biz_context["closures"])
            else:
                business_validation = validate_business_day_and_time(final_date, final_time)
            if not business_validation["valid"]:
                return {
                    "success": False,
                    "error": business_validation["error_code"],
                    "message": business_validation["error"],
                    "date": final_date,
                    "time": final_time
                }

            # Resolve new operator if provided
            resolved_operator_id = None
            resolved_operator_name = None
            if new_operator and new_operator.strip() and biz_context:
                operators = biz_context.get("operators", [])
                op_result = resolve_operator(new_operator, final_service, operators)
                if not op_result["success"]:
                    return {"success": False, **op_result}
                resolved_operator_id = op_result.get("operator_id")
                resolved_operator_name = op_result.get("operator_name")

            # Check if new slot is available (only if date or time changed)
            if new_date or new_time:
                if business_id is not None:
                    if resolved_operator_id is not None:
                        cur.execute(
                            """SELECT COUNT(*) FROM appointments
                               WHERE business_id = %s AND operator_id = %s AND appointment_date = %s AND appointment_time = %s
                               AND status = 'confirmed' AND id != %s""",
                            (business_id, resolved_operator_id, final_date, final_time, appointment_id)
                        )
                    else:
                        cur.execute(
                            """SELECT COUNT(*) FROM appointments
                               WHERE business_id = %s AND appointment_date = %s AND appointment_time = %s
                               AND status = 'confirmed' AND id != %s""",
                            (business_id, final_date, final_time, appointment_id)
                        )
                else:
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
            if business_id is not None:
                if resolved_operator_id is not None:
                    cur.execute(
                        """UPDATE appointments
                           SET appointment_date = %s, appointment_time = %s, treatment_code = %s,
                               treatment_name = %s, duration_minutes = %s, price = %s,
                               operator_id = %s, operator_name = %s
                           WHERE id = %s""",
                        (final_date, final_time, final_service, service.get("name_it", final_service),
                         service["duration"], service["price"], resolved_operator_id, resolved_operator_name, appointment_id)
                    )
                else:
                    cur.execute(
                        """UPDATE appointments
                           SET appointment_date = %s, appointment_time = %s, treatment_code = %s,
                               treatment_name = %s, duration_minutes = %s, price = %s
                           WHERE id = %s""",
                        (final_date, final_time, final_service, service.get("name_it", final_service),
                         service["duration"], service["price"], appointment_id)
                    )
            else:
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
                business = biz_context["business"] if biz_context else None
                update_calendar_event(
                    event_id=google_event_id,
                    customer_name=db_name,
                    service=service,
                    date_str=final_date,
                    time_str=final_time,
                    customer_phone=normalized_phone,
                    business=business,
                    operator_name=resolved_operator_name
                )

            calendar_note = " (calendar updated)" if google_event_id else ""
            logger.info(f"✅ Appointment #{appointment_id} modified: {final_date} {final_time} {final_service}{calendar_note}")

            # Build change details
            changes = {}
            if new_date and new_date != db_date:
                changes["date"] = {"from": db_date, "to": final_date}
            if new_time and new_time != db_time:
                changes["time"] = {"from": db_time, "to": final_time}
            if new_service and new_service.lower() != db_service.lower():
                changes["service"] = {"from": db_service, "to": final_service}
            if resolved_operator_name:
                changes["operator"] = {"to": resolved_operator_name}

            result = {
                "success": True,
                "customer_name": db_name,
                "service": service['name_it'],
                "service_en": service.get('name_en', final_service),
                "new_date": final_date,
                "new_time": final_time,
                "changes": changes,
                "calendar_updated": bool(google_event_id)
            }
            if resolved_operator_name:
                result["operator_name"] = resolved_operator_name
            return result
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

def get_available_slots(date: str, business_id: int = None, biz_context: dict = None,
                        operator_name: str = None) -> Dict[str, Any]:
    """
    Get available time slots for a specific date.
    Returns 30-minute slots during business hours that are not booked.
    Business hours: Tue-Fri 9:00-18:00, Sat 9:00-17:00 (legacy)
    Multi-tenant: uses biz_context hours
    Closed: Monday, Sunday, Dec 25, Jan 1 (legacy)
    """
    try:
        operators = biz_context.get("operators", []) if biz_context else []

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
        if biz_context:
            business_validation = validate_day_and_time(date, None, biz_context["hours"], biz_context["closures"])
        else:
            business_validation = validate_business_day_and_time(date, None)
        if not business_validation["valid"]:
            return {
                "success": True,  # Success but no slots
                "date": date,
                "available_slots": [],
                "count": 0,
                "closed": True,
                "reason": business_validation["error"]
            }

        conn = get_db_connection()
        try:
            cur = conn.cursor()

            # Get all booked times for this date
            if business_id is not None:
                if operator_name and operators:
                    # Specific operator: only count slots booked for that operator
                    op_result = resolve_operator(operator_name, "", operators)
                    if not op_result["success"]:
                        return {"success": False, **op_result}
                    op_id = op_result["operator_id"]
                    cur.execute(
                        """SELECT appointment_time FROM appointments
                           WHERE business_id = %s AND operator_id = %s AND appointment_date = %s AND status = 'confirmed'""",
                        (business_id, op_id, date)
                    )
                    booked_times = set()
                    for row in cur.fetchall():
                        booked_times.add(str(row[0])[:5])

                elif operators and not operator_name:
                    # No preference + operators exist: slot is "fully booked" only when ALL operators are booked
                    cur.execute(
                        """SELECT appointment_time FROM appointments
                           WHERE business_id = %s AND appointment_date = %s AND status = 'confirmed'
                                 AND operator_id IS NOT NULL
                           GROUP BY appointment_time
                           HAVING COUNT(DISTINCT operator_id) >= %s""",
                        (business_id, date, len(operators))
                    )
                    booked_times = set()
                    for row in cur.fetchall():
                        booked_times.add(str(row[0])[:5])

                else:
                    # No operators configured: legacy global query
                    cur.execute(
                        """SELECT appointment_time FROM appointments
                           WHERE business_id = %s AND appointment_date = %s AND status = 'confirmed'""",
                        (business_id, date)
                    )
                    booked_times = set()
                    for row in cur.fetchall():
                        booked_times.add(str(row[0])[:5])
            else:
                cur.execute(
                    """SELECT appointment_time FROM salon_appointments
                       WHERE appointment_date = %s AND status = 'confirmed'""",
                    (date,)
                )
                booked_times = set()
                for row in cur.fetchall():
                    booked_times.add(str(row[0])[:5])
        finally:
            conn.close()

        # Generate all possible slots based on business hours
        all_slots = _get_slots_for_date(date, biz_context, parsed_date=parsed_date)

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

# DEPRECATED: Legacy fallback for Aura. Multi-tenant uses build_booking_tools() from business_context.
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
                        "description": "Appointment date in YYYY-MM-DD format"
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
            "description": "Check if a specific date and time slot is available for booking. ALWAYS pass treatment_code so the system can validate the appointment fits within salon hours (factoring duration + auto-addon).",
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
                    },
                    "treatment_code": {
                        "type": ["string", "null"],
                        "description": "Treatment code (e.g. 'taglio_donna'). REQUIRED to validate the appointment does not exceed closing time."
                    },
                    "operator_name": {
                        "type": ["string", "null"],
                        "description": "Optional preferred operator name."
                    }
                },
                "required": ["date", "time", "treatment_code", "operator_name"],
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
            "description": "Cancel an appointment by customer name, date, and time. No ID needed - just use the appointment details.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_name": {
                        "type": "string",
                        "description": "The customer's name (e.g., 'Sarah', 'Maria Verdi')"
                    },
                    "date": {
                        "type": "string",
                        "description": "The appointment date in YYYY-MM-DD format"
                    },
                    "time": {
                        "type": "string",
                        "description": "The appointment time in HH:MM 24h format (e.g., '15:00' for 3 PM)"
                    }
                },
                "required": ["customer_name", "date", "time"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "modify_appointment",
            "description": "Modify/reschedule an appointment by customer name, date, and time. No ID needed - just use the appointment details.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_name": {
                        "type": "string",
                        "description": "The customer's name (e.g., 'Sarah', 'Maria Verdi')"
                    },
                    "current_date": {
                        "type": "string",
                        "description": "The CURRENT appointment date in YYYY-MM-DD format"
                    },
                    "current_time": {
                        "type": "string",
                        "description": "The CURRENT appointment time in HH:MM 24h format (e.g., '15:00' for 3 PM)"
                    },
                    "new_date": {
                        "type": ["string", "null"],
                        "description": "New date in YYYY-MM-DD format. Use null if not changing date."
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
                "required": ["customer_name", "current_date", "current_time", "new_date", "new_time", "new_service"],
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
                        "description": "Date in YYYY-MM-DD format"
                    }
                },
                "required": ["date"],
                "additionalProperties": False
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "confirm_reminder",
            "description": "Confirm customer's upcoming appointment. Call this when customer confirms they will attend ANY future appointment (not just tomorrow). Examples: 'confermo', 'ci sarò', 'vengo', 'ok ci vediamo martedì', 'yes I'll be there', 'confermo che vengo'. IMPORTANT: Always call this when customer expresses confirmation of their appointment!",
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
            "name": "escalate_to_human",
            "description": "Escalate conversation to a real person. Call this when customer has a COMPLAINT, is ANGRY/FRUSTRATED, has a PROBLEM you cannot solve, or explicitly asks to speak with a human/manager. This will freeze the chat and notify the owner.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Brief description of why escalation is needed (e.g., 'customer complaint about service', 'customer very frustrated', 'request to speak with manager')"
                    }
                },
                "required": ["reason"],
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

def execute_function(function_name: str, arguments: str, phone: str,
                     platform: str = "whatsapp",
                     business_id: int = None, biz_context: dict = None) -> Dict[str, Any]:
    """Execute a booking function"""
    try:
        if business_id is not None and not biz_context:
            raise ValueError("business_id requires biz_context")
        args = json.loads(arguments) if isinstance(arguments, str) else arguments

        if function_name == "create_appointment":
            return create_appointment(
                customer_phone=phone,
                customer_name=args["customer_name"],
                service_type=args["service_type"],
                date=args["date"],
                time=args["time"],
                platform=platform,
                business_id=business_id,
                biz_context=biz_context,
                operator_name=args.get("operator_name")
            )

        elif function_name == "check_availability":
            return check_availability(
                date=args["date"],
                time=args["time"],
                business_id=business_id,
                biz_context=biz_context,
                operator_name=args.get("operator_name"),
                treatment_code=args.get("treatment_code") or args.get("service_type")
            )

        elif function_name == "get_customer_appointments":
            return get_customer_appointments(customer_phone=phone, business_id=business_id, biz_context=biz_context)

        elif function_name == "cancel_appointment":
            return cancel_appointment(
                customer_phone=phone,
                customer_name=args["customer_name"],
                date=args["date"],
                time=args["time"],
                business_id=business_id,
                biz_context=biz_context
            )

        elif function_name == "modify_appointment":
            return modify_appointment(
                customer_phone=phone,
                customer_name=args["customer_name"],
                current_date=args["current_date"],
                current_time=args["current_time"],
                new_date=args.get("new_date"),
                new_time=args.get("new_time"),
                new_service=args.get("new_service"),
                business_id=business_id,
                biz_context=biz_context,
                new_operator=args.get("new_operator")
            )

        elif function_name == "get_available_slots":
            return get_available_slots(date=args["date"], business_id=business_id, biz_context=biz_context,
                                       operator_name=args.get("operator_name"))

        elif function_name == "confirm_reminder":
            return mark_reminder_confirmed(phone, business_id=business_id)

        elif function_name == "escalate_to_human":
            return escalate_to_human(phone=phone, reason=args["reason"], biz_context=biz_context)

        elif function_name == "lookup_treatment":
            bid = business_id or 1
            return lookup_treatment(bid, args["treatment_name"])

        elif function_name == "lookup_business_policy":
            bid = business_id or 1
            return lookup_business_policy(bid, args["policy_type"])

        elif function_name == "lookup_closure_dates":
            bid = business_id or 1
            return lookup_closure_dates(bid, month=args.get("month"))

        elif function_name == "lookup_faq":
            bid = business_id or 1
            return lookup_faq(bid, args["question"])

        elif function_name == "lookup_operator_for_treatment":
            bid = business_id or 1
            return lookup_operator_for_treatment(bid, args["treatment_code"])

        elif function_name == "get_available_treatments":
            bid = business_id or 1
            return get_available_treatments(bid)

        else:
            return {"success": False, "error": "UNKNOWN_FUNCTION", "function_name": function_name}

    except Exception as e:
        logger.error(f"Function execution error: {e}")
        return {"success": False, "error": str(e)}

# ============================================================================
# CONVERSATION MANAGEMENT
# ============================================================================

conversation_history: Dict[str, List[Dict]] = {}
# Bug #1 fix: track last activity per phone to expire stale in-memory sessions
last_activity: Dict[str, "datetime"] = {}
SESSION_IDLE_HOURS = 1  # Reset conversation context after 1h idle

def get_ai_response(phone: str, message: str, platform: str = "whatsapp", business_id: int = None, biz_context: dict = None) -> str:
    """
    Get AI response with SDK version compatibility.

    Supports both old SDK (v0.x) and new SDK (v1.0+) syntax.
    Key features:
    - Uses gpt-4o for reliable function calling
    - Uses tools/functions API based on SDK version
    - Temperature=0 for deterministic behavior
    """
    try:
        # Clear stale in-memory session if idle > SESSION_IDLE_HOURS
        from datetime import datetime as _dt, timedelta as _td
        _now = _dt.now()
        _was_idle_reset = False
        if phone in last_activity and (_now - last_activity[phone]) > _td(hours=SESSION_IDLE_HOURS):
            logger.info(f"⏰ In-memory session idle >{SESSION_IDLE_HOURS}h for {phone} — clearing")
            conversation_history.pop(phone, None)
            _was_idle_reset = True
        last_activity[phone] = _now

        # Get or create conversation history
        if phone not in conversation_history:
            conversation_history[phone] = load_conversation_history_from_db(phone, business_id=business_id)

        # AI-native language detection: Let GPT-4o detect and maintain language from conversation context
        logger.info(f"🌐 AI-native language detection for message: '{message[:50]}...'")

        # Normalize to lowercase so all-caps input doesn't bypass date/closure parsing (P3 fix)
        normalized_message = message.lower()

        # Bug #5: lookup known customer name to avoid asking again for repeat clients
        _known_name = None
        try:
            _conn_lookup = get_db_connection()
            _cur_lookup = _conn_lookup.cursor()
            if business_id is not None:
                _cur_lookup.execute(
                    """SELECT customer_name FROM appointments
                       WHERE business_id = %s AND customer_phone = %s
                         AND customer_name IS NOT NULL AND customer_name <> ''
                         AND customer_name NOT IN ('Cliente','Utente')
                       ORDER BY created_at DESC LIMIT 1""",
                    (business_id, phone)
                )
                _r = _cur_lookup.fetchone()
                if _r:
                    _known_name = _r[0]
            _conn_lookup.close()
        except Exception as _e_lookup:
            logger.warning(f"known-name lookup failed: {_e_lookup}")

        _system_prompt = get_system_prompt(biz_context)
        if _known_name:
            _system_prompt = (
                f"[KNOWN CUSTOMER NAME: {_known_name}]\n"
                f"This customer has booked with us before. Use this exact name for any new booking. "
                f"DO NOT ask for the name again — the customer is already known.\n\n"
                + _system_prompt
            )
        if _was_idle_reset:
            _system_prompt = (
                f"[NEW SESSION: previous conversation cleared after idle timeout]\n"
                f"Start with a warm greeting like 'Ciao! Come posso aiutarti?' before responding to their message.\n\n"
                + _system_prompt
            )
        # Build messages - GPT-4o will detect language from conversation history
        messages = [{"role": "system", "content": _system_prompt}]
        messages.extend(conversation_history[phone][-10:])  # Last 10 messages for context
        messages.append({"role": "user", "content": normalized_message})

        # Use dynamic tools when biz_context is available, else legacy fallback
        tools = build_booking_tools(biz_context["services"], operators=biz_context.get("operators", [])) if biz_context else BOOKING_TOOLS

        # Call OpenAI with version-appropriate syntax
        if OPENAI_SDK_VERSION >= 1:
            # New SDK v1.0+ syntax
            response = openai_client.chat.completions.create(
                model="gpt-4o",
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=0
            )
            assistant_message = response.choices[0].message
            has_function_call = bool(assistant_message.tool_calls)
        else:
            # Old SDK v0.x syntax
            # Convert dynamic tools to old SDK format
            dynamic_functions = convert_tools_to_functions(tools)
            response = openai.ChatCompletion.create(
                model="gpt-4o",
                messages=messages,
                functions=dynamic_functions,
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

                    function_result = execute_function(function_name, function_args, phone, platform, business_id=business_id, biz_context=biz_context)
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

                function_result = execute_function(function_name, function_args, phone, platform, business_id=business_id, biz_context=biz_context)
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
                    tools=tools,
                    tool_choice="auto",
                    temperature=0
                )
                second_message = second_response.choices[0].message
                has_second_call = bool(second_message.tool_calls)
            else:
                second_response = openai.ChatCompletion.create(
                    model="gpt-4o",
                    messages=messages,
                    functions=dynamic_functions,
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
                        func2_result = execute_function(func2_name, func2_args, phone, platform, business_id=business_id, biz_context=biz_context)
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

                    func2_result = execute_function(func2_name, func2_args, phone, platform, business_id=business_id, biz_context=biz_context)
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
                        tools=tools,
                        tool_choice="auto",
                        temperature=0
                    )
                    third_message = third_response.choices[0].message
                    has_third_call = bool(third_message.tool_calls)
                else:
                    third_response = openai.ChatCompletion.create(
                        model="gpt-4o",
                        messages=messages,
                        functions=dynamic_functions,
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
                            func3_result = execute_function(func3_name, func3_args, phone, platform, business_id=business_id, biz_context=biz_context)
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

                        func3_result = execute_function(func3_name, func3_args, phone, platform, business_id=business_id, biz_context=biz_context)
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
            conversation_history[phone].append({"role": "user", "content": normalized_message})
            conversation_history[phone].append({"role": "assistant", "content": final_message})

            return final_message

        else:
            # No function call - normal conversation
            if OPENAI_SDK_VERSION >= 1:
                response_text = assistant_message.content or ''
            else:
                response_text = assistant_message.get("content", '') or ''

            # Save to history
            conversation_history[phone].append({"role": "user", "content": normalized_message})
            conversation_history[phone].append({"role": "assistant", "content": response_text})

            return response_text

    except openai.RateLimitError as e:
        logger.error(f"❌ Rate limit error: {e}")
        return ("We're experiencing high demand. Please try again in a moment. "
                "/ Alto traffico, riprova tra qualche secondo. "
                "Or call us at +39 02 8394 5621 / Oppure chiamaci.")
    except openai.APITimeoutError as e:
        logger.error(f"❌ API timeout: {e}")
        return ("Connection slow, please try again. "
                "/ Connessione lenta, riprova. "
                "Or call us at +39 02 8394 5621 / Oppure chiamaci.")
    except openai.APIConnectionError as e:
        logger.error(f"❌ API connection error: {e}")
        return ("Connection issue, please try again. "
                "/ Problema di connessione, riprova. "
                "Or call us at +39 02 8394 5621 / Oppure chiamaci.")
    except Exception as e:
        logger.error(f"❌ AI Error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return ("Something went wrong. Please try again or call us at +39 02 8394 5621. "
                "/ Qualcosa è andato storto. Riprova o chiamaci al +39 02 8394 5621.")

# ============================================================================
# WHATSAPP SERVICE
# ============================================================================

async def send_whatsapp_message(phone: str, message: str, business: dict = None) -> bool:
    """Send WhatsApp message using per-business credentials."""
    phone_number_id = (business or {}).get("whatsapp_phone_number_id") or WHATSAPP_PHONE_NUMBER_ID
    access_token = (business or {}).get("meta_access_token") or WHATSAPP_ACCESS_TOKEN

    url = f"https://graph.facebook.com/v18.0/{phone_number_id}/messages"

    headers = {
        "Authorization": f"Bearer {access_token}",
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

async def mark_as_read(message_id: str, business: dict = None) -> bool:
    """Mark message as read using per-business credentials."""
    phone_number_id = (business or {}).get("whatsapp_phone_number_id") or WHATSAPP_PHONE_NUMBER_ID
    access_token = (business or {}).get("meta_access_token") or WHATSAPP_ACCESS_TOKEN

    url = f"https://graph.facebook.com/v18.0/{phone_number_id}/messages"

    headers = {
        "Authorization": f"Bearer {access_token}",
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
# INSTAGRAM MESSAGING
# ============================================================================

def split_message(message: str, max_length: int = 1000) -> List[str]:
    """Split a long message into chunks that fit Instagram's character limit"""
    if len(message) <= max_length:
        return [message]

    chunks = []
    current_chunk = ""

    for line in message.split('\n'):
        if len(current_chunk) + len(line) + 1 <= max_length:
            current_chunk += ('\n' if current_chunk else '') + line
        else:
            if current_chunk:
                chunks.append(current_chunk)
            if len(line) > max_length:
                words = line.split(' ')
                current_chunk = ""
                for word in words:
                    if len(current_chunk) + len(word) + 1 <= max_length:
                        current_chunk += (' ' if current_chunk else '') + word
                    else:
                        if current_chunk:
                            chunks.append(current_chunk)
                        current_chunk = word
            else:
                current_chunk = line

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


async def send_instagram_message(recipient_id: str, message: str) -> bool:
    """Send Instagram DM via Graph API"""
    if not INSTAGRAM_ACCESS_TOKEN:
        logger.error("[IG] No access token configured")
        return False

    url = "https://graph.instagram.com/v21.0/me/messages"

    headers = {
        "Authorization": f"Bearer {INSTAGRAM_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    chunks = split_message(message, max_length=1000)

    success = True
    for chunk in chunks:
        payload = {
            "recipient": {"id": recipient_id},
            "message": {"text": chunk}
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, headers=headers, json=payload, timeout=30)
                if response.status_code == 200:
                    logger.info(f"[IG] Message sent to {recipient_id}")
                else:
                    logger.error(f"[IG] Send failed: {response.status_code} - {response.text}")
                    success = False
        except Exception as e:
            logger.error(f"[IG] API error: {e}")
            success = False

        if len(chunks) > 1:
            await asyncio.sleep(0.5)

    return success


# Instagram message buffering (separate from WhatsApp, keyed by Instagram user IDs)
ig_pending_messages: Dict[str, List[Dict]] = {}
ig_pending_timers: Dict[str, asyncio.Task] = {}


async def ig_process_buffered_messages(user_id: str):
    """Process all buffered Instagram messages for a user after timer expires"""
    try:
        messages = ig_pending_messages.pop(user_id, [])
        ig_pending_timers.pop(user_id, None)

        if not messages:
            return

        username = messages[0].get("username", "Cliente")
        combined_text = "\n".join(msg["text"] for msg in messages) if len(messages) > 1 else messages[0]["text"]

        logger.info(f"[IG] Timer fired for {user_id}. Processing {len(messages)} buffered message(s)")

        response = get_ai_response(user_id, combined_text, platform="instagram")
        save_conversation_to_db(user_id, username, combined_text, response, platform="instagram")
        await send_instagram_message(user_id, response)

    except Exception as e:
        logger.error(f"[IG] Error processing buffered messages for {user_id}: {e}")
        ig_pending_messages.pop(user_id, None)
        ig_pending_timers.pop(user_id, None)


async def ig_handle_buffered_message(user_id: str, text: str, username: str):
    """Handle incoming Instagram message with batching"""
    if user_id not in ig_pending_messages:
        ig_pending_messages[user_id] = []

    ig_pending_messages[user_id].append({
        "text": text,
        "username": username,
        "timestamp": datetime.now(ITALY_TZ).isoformat()
    })
    logger.info(f"[IG] Buffered message for {user_id}. Buffer size: {len(ig_pending_messages[user_id])}")

    if user_id in ig_pending_timers:
        old_timer = ig_pending_timers[user_id]
        old_timer.cancel()
        logger.info(f"[IG] Reset timer for {user_id}")

    async def timer_callback():
        await asyncio.sleep(MESSAGE_BATCH_DELAY_SECONDS)
        await ig_process_buffered_messages(user_id)

    timer_task = asyncio.create_task(timer_callback())
    ig_pending_timers[user_id] = timer_task
    logger.info(f"[IG] Started {MESSAGE_BATCH_DELAY_SECONDS}s timer for {user_id}")


async def process_instagram_event(event: Dict[str, Any]):
    """Process an Instagram messaging event"""
    try:
        sender_id = event.get("sender", {}).get("id")
        if not sender_id:
            return

        # Ignore messages from ourselves (echo)
        if sender_id == INSTAGRAM_PAGE_ID:
            return

        message = event.get("message", {})
        if not message:
            return

        if message.get("is_echo"):
            return

        message_text = message.get("text")
        username = "Cliente"

        logger.info(f"[IG] Message from {sender_id}: {message_text[:100] if message_text else '(no text)'}...")

        if sender_id in chat_blocked:
            logger.info(f"[IG] Chat blocked for {sender_id}, ignoring message")
            return

        if message_text:
            if MESSAGE_BATCHING_ENABLED:
                await ig_handle_buffered_message(sender_id, message_text, username)
            else:
                response = get_ai_response(sender_id, message_text)
                save_conversation_to_db(sender_id, username, message_text, response, platform="instagram")
                await send_instagram_message(sender_id, response)

        elif message.get("attachments"):
            attachments = message.get("attachments", [])
            attachment_type = attachments[0].get("type", "unknown") if attachments else "unknown"

            logger.info(f"[IG] Media message type: {attachment_type} from {sender_id}")

            if attachment_type == "image":
                send_alert_email(
                    "[IG] Immagine ricevuta",
                    f"L'utente Instagram {sender_id} ha inviato un'immagine.\nRichiede attenzione manuale."
                )
                await send_instagram_message(sender_id,
                    "Abbiamo ricevuto la tua immagine. Ti rispondera presto un membro del nostro team.")

            elif attachment_type == "video":
                send_alert_email(
                    "[IG] Video ricevuto",
                    f"L'utente Instagram {sender_id} ha inviato un video.\nRichiede attenzione manuale."
                )
                await send_instagram_message(sender_id,
                    "Abbiamo ricevuto il tuo video. Ti rispondera presto un membro del nostro team.")

            elif attachment_type == "audio":
                send_alert_email(
                    "[IG] Messaggio vocale ricevuto",
                    f"L'utente Instagram {sender_id} ha inviato un messaggio vocale.\nRichiede attenzione manuale."
                )
                await send_instagram_message(sender_id,
                    "Al momento non possiamo ascoltare i messaggi vocali. "
                    "Se puoi, scrivici il tuo messaggio. Altrimenti ti rispondera presto un membro del nostro team.")

            elif attachment_type in ("sticker", "like_heart"):
                logger.info(f"[IG] Sticker/reaction from {sender_id}. Ignored.")

            elif attachment_type == "story_mention":
                send_alert_email(
                    "[IG] Menzione nella storia",
                    f"L'utente Instagram {sender_id} ci ha menzionato nella sua storia."
                )
                await send_instagram_message(sender_id,
                    "Grazie per averci menzionato nella tua storia! Come possiamo aiutarti?")

            elif attachment_type == "story_reply":
                story_reply_text = message.get("reply_to", {}).get("story", {}).get("text", "")
                if story_reply_text:
                    response = get_ai_response(sender_id, story_reply_text)
                    save_conversation_to_db(sender_id, username, f"[Story reply] {story_reply_text}", response, platform="instagram")
                    await send_instagram_message(sender_id, response)
                else:
                    await send_instagram_message(sender_id,
                        "Grazie per la risposta alla nostra storia! Come possiamo aiutarti?")
            else:
                await send_instagram_message(sender_id,
                    "Posso rispondere solo a messaggi di testo. Come posso aiutarti?")

        elif event.get("reaction"):
            logger.info(f"[IG] Reaction from {sender_id}. Ignored.")

    except Exception as e:
        logger.error(f"[IG] Process event error: {e}")
        import traceback
        logger.error(traceback.format_exc())


# ============================================================================
# INSTAGRAM REMINDERS
# ============================================================================

def get_tomorrow_instagram_appointments() -> List[Dict]:
    """Get confirmed appointments for tomorrow that came from Instagram"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        tomorrow = (datetime.now(ITALY_TZ) + timedelta(days=1)).strftime("%Y-%m-%d")

        cur.execute("""
            SELECT id, customer_phone, customer_name, service_type,
                   appointment_date, appointment_time, price
            FROM salon_appointments
            WHERE appointment_date = %s
              AND status = 'confirmed'
              AND platform = 'instagram'
              AND (reminder_sent_at IS NULL OR reminder_sent_at < CURRENT_DATE)
            ORDER BY appointment_time
        """, (tomorrow,))

        appointments = []
        for row in cur.fetchall():
            appointments.append({
                "id": row[0], "user_id": row[1], "name": row[2],
                "service": row[3], "date": row[4], "time": row[5], "price": row[6]
            })

        cur.close()
        conn.close()
        return appointments
    except Exception as e:
        logger.error(f"[IG] Error getting tomorrow appointments: {e}")
        return []


async def send_ig_reminder_messages():
    """Send reminder messages via Instagram DMs"""
    logger.info("[IG] Starting daily reminder job...")

    appointments = get_tomorrow_instagram_appointments()
    logger.info(f"[IG] Found {len(appointments)} Instagram appointments for tomorrow")

    for apt in appointments:
        time_str = apt["time"].strftime("%H:%M") if hasattr(apt["time"], 'strftime') else str(apt["time"])[:5]

        reminder_message = (
            f"Buongiorno!\n"
            f"Ti ricordiamo che domani alle ore {time_str} hai un appuntamento con noi.\n"
            f"Ti chiediamo gentilmente di confermare rispondendo a questo messaggio entro le 18:00 di oggi.\n\n"
            f"In caso di mancata conferma, non possiamo garantire la disponibilita dell'appuntamento.\n\n"
            f"Grazie!"
        )

        user_id = apt["user_id"]
        success = await send_instagram_message(user_id, reminder_message)

        if success:
            try:
                conn = get_db_connection()
                cur = conn.cursor()
                cur.execute("UPDATE salon_appointments SET reminder_sent_at = CURRENT_TIMESTAMP WHERE id = %s", (apt["id"],))
                conn.commit()
                cur.close()
                conn.close()
            except:
                pass

            save_conversation_to_db(
                phone=user_id, name=apt["name"],
                message="[SISTEMA: Promemoria appuntamento inviato automaticamente]",
                response=reminder_message, platform="instagram"
            )
            logger.info(f"[IG] Reminder sent to {apt['name']} ({user_id})")

    logger.info(f"[IG] Reminder job completed. Sent {len(appointments)} reminders.")


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
    setup_reminder_scheduler()
    # Durable human-takeover store (survives restarts). Falls back to in-memory.
    chatwoot_bridge.init_takeover_store(get_db_connection)
    logger.info(f"🚀 {BUSINESS_NAME} WhatsApp + Instagram Bot started!")

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

@app.post("/webhook/chatwoot")
async def chatwoot_webhook(request: Request):
    """Inbound Chatwoot events: deliver human-agent replies to the customer over
    WhatsApp and toggle the AI-suspend (human takeover) flag.

    Auth: Chatwoot custom webhooks cannot add headers, so the shared secret is
    passed as ?token=... in the configured webhook URL.
    """
    # --- authenticate (fail closed) ---
    if not CHATWOOT_WEBHOOK_SECRET:
        logger.error("CHATWOOT_WEBHOOK_SECRET not configured — rejecting webhook")
        return JSONResponse({"status": "not_configured"}, status_code=503)
    token = request.query_params.get("token", "")
    if not hmac.compare_digest(token, CHATWOOT_WEBHOOK_SECRET):
        return JSONResponse({"status": "forbidden"}, status_code=403)

    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"status": "bad_request"}, status_code=400)

    decision = chatwoot_bridge.classify_webhook_event(payload)
    action = decision.get("action")

    if action == "forward":
        phone = decision["phone"]
        content = decision["content"]
        chatwoot_bridge.mark_human_takeover(phone)
        await send_whatsapp_message(phone, content)
        logger.info(f"➡️ Forwarded agent reply to {phone} (takeover ON)")
        return JSONResponse({"status": "forwarded"})

    if action == "set_takeover":
        phone = decision.get("phone")
        if phone:
            chatwoot_bridge.mark_human_takeover(phone)
            logger.info(f"🏷️ bot_paused label detected — takeover ON for {phone}")
        return JSONResponse({"status": "takeover_set"})

    if action == "clear_takeover":
        phone = decision.get("phone")
        if phone:
            chatwoot_bridge.clear_human_takeover(phone)
            logger.info(f"✅ Conversation resolved — takeover OFF for {phone}")
        return JSONResponse({"status": "takeover_cleared"})

    return JSONResponse({"status": "ignored", "reason": decision.get("reason")})


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

                if not messages:
                    continue

                # --- MULTI-TENANT ROUTING ---
                phone_number_id = extract_phone_number_id(value)
                if not phone_number_id:
                    logger.warning("No phone_number_id in webhook payload")
                    continue

                try:
                    business = load_business_by_phone_number_id(phone_number_id)
                except BusinessNotFoundError:
                    logger.warning(f"No business for phone_number_id={phone_number_id}")
                    continue

                business_services = load_services(business["id"])
                business_hours = load_business_hours(business["id"])
                business_closures = load_closures(business["id"])
                business_operators = load_operators(business["id"])

                business_faqs = load_faqs(business["id"])

                biz_context = {
                    "business": business,
                    "services": business_services,
                    "hours": business_hours,
                    "closures": business_closures,
                    "operators": business_operators,
                    "faqs": business_faqs,
                }
                # --- END ROUTING ---

                for message in messages:
                    await process_message(message, value, biz_context)

        return JSONResponse({"status": "processed"})

    except Exception as e:
        logger.error(f"❌ Webhook error: {e}")
        return JSONResponse({"status": "error"})

# ============================================================================
# INSTAGRAM WEBHOOK ENDPOINTS
# ============================================================================

@app.get("/webhook/instagram")
async def verify_instagram_webhook(request: Request):
    """Instagram webhook verification"""
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == INSTAGRAM_WEBHOOK_VERIFY_TOKEN:
        logger.info("[IG] Webhook verified!")
        return PlainTextResponse(challenge)

    logger.warning(f"[IG] Webhook verification failed. Token: {token}")
    return PlainTextResponse("Failed", status_code=403)


@app.post("/webhook/instagram")
async def instagram_webhook(request: Request):
    """Handle incoming Instagram DMs"""
    try:
        body = await request.json()

        if body.get("object") != "instagram":
            return JSONResponse({"status": "ignored"})

        for entry in body.get("entry", []):
            messaging_events = entry.get("messaging", [])

            for event in messaging_events:
                await process_instagram_event(event)

        return JSONResponse({"status": "processed"})

    except Exception as e:
        logger.error(f"[IG] Webhook error: {e}")
        return JSONResponse({"status": "error"})


async def process_message(message: Dict[str, Any], value: Dict[str, Any], biz_context: dict = None):
    """Process incoming message"""
    try:
        business = biz_context["business"] if biz_context else {}
        biz_id = business.get("id") if business else None

        phone = message.get("from")
        message_id = message.get("id")
        message_type = message.get("type", "text")

        # Get contact name
        contacts = value.get("contacts", [])
        contact_name = contacts[0].get("profile", {}).get("name", "Cliente") if contacts else "Cliente"

        biz_name = business.get("name", "Unknown") if business else "Unknown"
        logger.info(f"💬 [{biz_name}] Message from {phone} ({contact_name})")

        await mark_as_read(message_id, business)

        # Check if chat is blocked (complaint was filed)
        if phone in chat_blocked:
            logger.info(f"🔒 Chat blocked for {phone}, ignoring message")
            return  # Don't respond to blocked chats

        # Human takeover: an agent is handling this chat in Chatwoot -> suspend the
        # AI for every reply path below. Still mirror the customer's message so the
        # agent sees it in the dashboard.
        if chatwoot_bridge.has_human_takeover(phone):
            if message_type == "text":
                mirror_text = message.get("text", {}).get("body", "")
            elif message_type == "interactive":
                interactive = message.get("interactive", {})
                mirror_text = (interactive.get("button_reply", {}).get("title", "")
                               or interactive.get("list_reply", {}).get("title", ""))
            else:
                mirror_text = f"[{message_type} message]"
            if mirror_text:
                await _chatwoot_push("in", phone, mirror_text, contact_name)
            logger.info(f"🙋 Human takeover active for {phone} — AI suspended")
            return

        if message_type == "text":
            text = message.get("text", {}).get("body", "")
            if text:
                logger.info(f"📝 Message: {text[:100]}...")

                if MESSAGE_BATCHING_ENABLED:
                    # Buffer the message and start/reset timer
                    await handle_buffered_message(phone, text, contact_name, biz_context)
                else:
                    # Original immediate processing (fallback)
                    response = get_ai_response(phone, text, business_id=biz_id, biz_context=biz_context)
                    save_conversation_to_db(phone, contact_name, text, response, business_id=biz_id)
                    logger.info(f"📤 Response: {response[:100]}...")
                    await send_whatsapp_message(phone, response, business)

        elif message_type == "interactive":
            interactive = message.get("interactive", {})
            text = interactive.get("button_reply", {}).get("title", "") or \
                   interactive.get("list_reply", {}).get("title", "")
            if text:
                response = get_ai_response(phone, text, business_id=biz_id, biz_context=biz_context)

                # Log conversation to database for analytics
                save_conversation_to_db(phone, contact_name, text, response, business_id=biz_id)

                await send_whatsapp_message(phone, response, business)

        else:
            # Non-text message (voice, sticker, image, etc.)
            logger.info(f"🎤 Non-text message type: {message_type} from {phone}")

            # Handle different media types with specific responses and email alerts
            if message_type == "audio":
                send_alert_email(
                    "📥 Messaggio vocale ricevuto",
                    f"L'utente {contact_name} ({phone}) ha inviato un messaggio vocale.\n\nRichiede attenzione manuale."
                )
                response_msg = (
                    "Al momento non siamo ancora in grado di ascoltare i messaggi vocali. "
                    "Ci stiamo lavorando! 😊\n\n"
                    "Se puoi, scrivici qui il tuo messaggio. "
                    "Altrimenti ti risponderà presto un membro del nostro team."
                )
                await send_whatsapp_message(phone, response_msg, business)

            elif message_type == "image":
                send_alert_email(
                    "🖼️ Immagine ricevuta",
                    f"L'utente {contact_name} ({phone}) ha inviato un'immagine.\n\nRichiede attenzione manuale."
                )
                response_msg = "Abbiamo ricevuto la tua immagine. Ti risponderà presto un membro del nostro team. 😊"
                await send_whatsapp_message(phone, response_msg, business)

            elif message_type == "video":
                send_alert_email(
                    "🎞️ Video ricevuto",
                    f"L'utente {contact_name} ({phone}) ha inviato un video.\n\nRichiede attenzione manuale."
                )
                response_msg = "Abbiamo ricevuto il tuo video. Ti risponderà presto un membro del nostro team. 😊"
                await send_whatsapp_message(phone, response_msg, business)

            elif message_type == "document":
                send_alert_email(
                    "📎 Documento ricevuto",
                    f"L'utente {contact_name} ({phone}) ha inviato un file/documento.\n\nRichiede attenzione manuale."
                )
                response_msg = "Ho ricevuto il tuo file. Ti risponderà presto un membro del nostro team. 😊"
                await send_whatsapp_message(phone, response_msg, business)

            elif message_type == "contacts":
                send_alert_email(
                    "👤 Contatto condiviso",
                    f"L'utente {contact_name} ({phone}) ha condiviso un contatto.\n\nRichiede attenzione manuale."
                )
                response_msg = "Ho ricevuto il tuo contatto. Ti risponderà presto un membro del nostro team. 😊"
                await send_whatsapp_message(phone, response_msg, business)

            elif message_type == "sticker":
                # Ignore stickers completely - no response, no email
                logger.info(f"🔕 Sticker ricevuto da {phone}. Ignorato.")

            elif message_type == "location":
                send_alert_email(
                    "📍 Posizione ricevuta",
                    f"L'utente {contact_name} ({phone}) ha condiviso una posizione.\n\nRichiede attenzione manuale."
                )
                response_msg = "Ho ricevuto la tua posizione. Ti risponderà presto un membro del nostro team. 😊"
                await send_whatsapp_message(phone, response_msg, business)

            else:
                # Unknown message type
                logger.info(f"❓ Unknown message type: {message_type} from {phone}")
                response_msg = "Posso rispondere solo a messaggi di testo. Come posso aiutarti? 💇‍♀️"
                await send_whatsapp_message(phone, response_msg, business)

    except Exception as e:
        logger.error(f"Process message error: {e}")

@app.post("/reload-config")
async def reload_config():
    """Force reload business config — called after dashboard changes."""
    return JSONResponse({"status": "reloaded", "note": "Multi-tenant bot reloads config per-request from DB"})


@app.get("/health")
async def health_check():
    """Health check"""
    return JSONResponse({
        "status": "healthy",
        "service": BUSINESS_NAME,
        "type": BUSINESS_TYPE,
        "version": "4.1.0",
        "features": {
            "booking": True,
            "calendar_integration": True,
            "tools_api": True,
            "strict_mode": True,
            "reminders": True,
            "model": "gpt-4o"
        },
        "scheduler_running": scheduler.running if scheduler else False,
        "services": list(SALON_SERVICES.keys()),
        "timestamp": datetime.now().isoformat()
    })

@app.get("/")
async def root():
    """Root endpoint"""
    return JSONResponse({
        "name": f"{BUSINESS_NAME} - WhatsApp Bot",
        "version": "4.0.0",
        "features": ["booking", "calendar", "AI", "tools_api", "strict_mode", "reminders"],
        "model": "gpt-4o",
        "services": [f"{s['name_it']} - €{s['price']}" for s in SALON_SERVICES.values()]
    })

@app.get("/sblocca_chat/{phone}")
async def sblocca_chat(phone: str):
    """Unblock a chat that was frozen due to complaint"""
    if phone in chat_blocked:
        reason = chat_blocked.pop(phone)
        logger.info(f"✅ Chat unblocked for {phone} (was blocked for: {reason})")
        return PlainTextResponse(f"✅ Chat sbloccata per {phone}\n\nMotivo blocco: {reason}")
    else:
        return PlainTextResponse(f"❌ Nessuna chat bloccata per {phone}", status_code=404)

@app.get("/blocked_chats")
async def list_blocked_chats():
    """List all currently blocked chats"""
    return JSONResponse({
        "blocked_count": len(chat_blocked),
        "blocked_chats": {phone: reason for phone, reason in chat_blocked.items()}
    })

# ============================================================================
# REMINDER TEST ENDPOINTS
# ============================================================================

@app.post("/reminders/send-now")
async def trigger_reminders():
    """Manually trigger sending reminders (for testing)"""
    await send_reminder_messages()
    return JSONResponse({
        "status": "completed",
        "message": "Reminder job executed"
    })

@app.post("/reminders/check-unconfirmed")
async def trigger_unconfirmed_check():
    """Manually trigger unconfirmed check (for testing)"""
    await check_unconfirmed_and_notify()
    return JSONResponse({
        "status": "completed",
        "message": "Unconfirmed check executed"
    })

@app.get("/reminders/status")
async def reminder_status():
    """Get reminder system status"""
    tomorrow = (datetime.now(ITALY_TZ) + timedelta(days=1)).strftime("%Y-%m-%d")
    tomorrow_apts = get_tomorrow_appointments()
    unconfirmed = get_unconfirmed_appointments()

    return JSONResponse({
        "scheduler_running": scheduler.running,
        "tomorrow_date": tomorrow,
        "appointments_needing_reminder": len(tomorrow_apts),
        "unconfirmed_count": len(unconfirmed),
        "next_reminder_job": str(scheduler.get_job("send_reminders").next_run_time) if scheduler.get_job("send_reminders") else None,
        "next_check_job": str(scheduler.get_job("check_unconfirmed").next_run_time) if scheduler.get_job("check_unconfirmed") else None
    })

@app.post("/reminders/test-email")
async def test_email():
    """Test email sending"""
    success = send_email(
        OWNER_EMAIL,
        "Test Email - Aura Hair Studio",
        "Questo è un test del sistema di notifiche email.\n\nSe ricevi questa email, il sistema funziona correttamente!"
    )
    return JSONResponse({
        "success": success,
        "sent_to": OWNER_EMAIL
    })

@app.get("/test/conversations/{phone}")
async def get_test_conversations(phone: str, limit: int = 5):
    """Get recent conversations for testing purposes"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Normalize phone - handle partial matches
        cur.execute("""
            SELECT message, response, timestamp
            FROM salon_conversations
            WHERE phone LIKE %s
            ORDER BY timestamp DESC
            LIMIT %s
        """, (f"%{phone}%", limit))

        rows = cur.fetchall()
        conversations = []
        for r in rows:
            conversations.append({
                "customer": r[0][:200] if r[0] else "",
                "bot": r[1][:300] if r[1] else "",
                "time": str(r[2])
            })

        conn.close()
        return JSONResponse({"conversations": conversations})
    except Exception as e:
        logger.error(f"Error getting conversations: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/test/conversations-by-date/{date}")
async def get_conversations_by_date(date: str, limit: int = 50):
    """Get conversations for a specific date (format: YYYY-MM-DD)"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT phone, name, message, response, timestamp
            FROM salon_conversations
            WHERE DATE(timestamp) = %s
            ORDER BY timestamp ASC
            LIMIT %s
        """, (date, limit))

        rows = cur.fetchall()
        conversations = []
        for r in rows:
            conversations.append({
                "phone": r[0],
                "name": r[1],
                "customer": r[2][:200] if r[2] else "",
                "bot": r[3][:300] if r[3] else "",
                "time": str(r[4])
            })

        conn.close()
        return JSONResponse({"date": date, "count": len(conversations), "conversations": conversations})
    except Exception as e:
        logger.error(f"Error getting conversations by date: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/test/appointments/{phone}")
async def get_test_appointments(phone: str):
    """Get appointments for testing purposes"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT customer_name, service_type, appointment_date, appointment_time,
                   status, reminder_confirmed, reminder_sent_at, reminder_confirmed_at
            FROM salon_appointments
            WHERE customer_phone LIKE %s
            ORDER BY appointment_date DESC, appointment_time DESC
            LIMIT 10
        """, (f"%{phone}%",))

        rows = cur.fetchall()
        appointments = []
        for r in rows:
            appointments.append({
                "name": r[0],
                "service": r[1],
                "date": str(r[2]),
                "time": str(r[3]),
                "status": r[4],
                "reminder_confirmed": r[5],
                "reminder_sent_at": str(r[6]) if r[6] else None,
                "reminder_confirmed_at": str(r[7]) if r[7] else None
            })

        conn.close()
        return JSONResponse({"appointments": appointments})
    except Exception as e:
        logger.error(f"Error getting appointments: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/test/buffer")
async def get_buffer_state():
    """Get current state of message buffer (for testing IMP-005)"""
    buffer_state = {}
    for phone, messages in pending_messages.items():
        buffer_state[phone] = {
            "count": len(messages),
            "messages": messages,
            "has_active_timer": phone in pending_timers
        }

    return JSONResponse({
        "total_users_with_pending": len(pending_messages),
        "buffer": buffer_state,
        "batch_delay_seconds": MESSAGE_BATCH_DELAY_SECONDS
    })

@app.get("/test/buffer/{phone}")
async def get_buffer_for_phone(phone: str):
    """Get buffer state for specific phone (for testing IMP-005)"""
    messages = pending_messages.get(phone, [])
    return JSONResponse({
        "phone": phone,
        "count": len(messages),
        "messages": messages,
        "has_active_timer": phone in pending_timers
    })

@app.post("/test/buffer/clear")
async def clear_all_buffers():
    """Clear all message buffers (for testing)"""
    # Cancel all pending timers
    for phone, timer in list(pending_timers.items()):
        timer.cancel()
    pending_timers.clear()
    pending_messages.clear()
    return JSONResponse({"status": "cleared"})

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
        port=8001,
        reload=False,
        log_level="info"
    )



@app.post("/test/simulate-reminder/{phone}")
async def simulate_reminder(phone: str):
    """Simulate a reminder being sent (for testing BUG-007)"""
    reminder_msg = '''Buongiorno!😊
Ti ricordiamo che domani alle ore 10:00 hai un appuntamento con noi.
Ti chiediamo gentilmente di confermare rispondendo a questo messaggio.
Grazie!'''

    save_conversation_to_db(
        phone=phone,
        name='TestUser',
        message='[SISTEMA: Promemoria appuntamento inviato automaticamente]',
        response=reminder_msg
    )
    return JSONResponse({"status": "ok", "message": f"Simulated reminder for {phone}"})
