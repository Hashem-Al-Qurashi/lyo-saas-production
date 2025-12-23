"""
SALON BELLA VITA - WhatsApp Bot with Calendar/Booking Integration
OpenAI 0.28.x compatible with function calling
"""
import os
import asyncio
import logging
import json
import psycopg2
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
openai.api_key = OPENAI_API_KEY

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
BUSINESS_NAME = "Salon Bella Vita"
BUSINESS_TYPE = "beauty_salon"

# Salon Services
SALON_SERVICES = {
    "taglio": {"name_it": "Taglio Donna", "name_en": "Women's Haircut", "price": 35, "duration": 60},
    "piega": {"name_it": "Piega", "name_en": "Styling", "price": 20, "duration": 30},
    "colore": {"name_it": "Colore", "name_en": "Coloring", "price": 80, "duration": 120},
    "colpi_sole": {"name_it": "Colpi di Sole", "name_en": "Highlights", "price": 60, "duration": 90},
    "trattamento": {"name_it": "Trattamento", "name_en": "Treatment", "price": 25, "duration": 30}
}

# Get today's date for the prompt
TODAY = datetime.now().strftime("%Y-%m-%d")
TOMORROW = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

# System prompt with booking capabilities
SYSTEM_PROMPT = f"""Sei Lyo, la receptionist virtuale del Salon Bella Vita a Milano.
Sei cordiale, professionale e disponibile. Aiuti i clienti a prenotare appuntamenti per servizi di parrucchiere.

📅 OGGI È: {datetime.now().strftime('%A %d %B %Y')} ({TODAY})
📅 DOMANI È: {(datetime.now() + timedelta(days=1)).strftime('%A %d %B %Y')} ({TOMORROW})

📍 INFORMAZIONI SALONE:
- Indirizzo: Via Roma 123, Milano
- Telefono: +39 02 1234567

💇 SERVIZI DISPONIBILI:
- Taglio Donna: €35 (60 min) - codice: "taglio"
- Piega: €20 (30 min) - codice: "piega"
- Colore: €80 (120 min) - codice: "colore"
- Colpi di Sole: €60 (90 min) - codice: "colpi_sole"
- Trattamento: €25 (30 min) - codice: "trattamento"

🕐 ORARI DI APERTURA:
- Lunedì: 09:00-19:00
- Martedì: 09:00-19:00
- Mercoledì: CHIUSO
- Giovedì: 09:00-19:00
- Venerdì: 09:00-20:00
- Sabato: 09:00-18:00
- Domenica: CHIUSO

FLUSSO DI PRENOTAZIONE:
1. RACCOGLI INFO:
   - Nome del cliente
   - Servizio desiderato (taglio, piega, colore, etc.)
   - Data preferita (converti "domani" in {TOMORROW})
   - Ora preferita (usa formato 24h: 15:00 per le 3 del pomeriggio)

2. CONFERMA UNA VOLTA:
   - Dopo aver raccolto tutto, conferma: "Perfetto! Riepilogo: [Nome], [Servizio] il [Data] alle [Ora]. Confermo la prenotazione?"
   - Aspetta la conferma (sì/ok/perfetto/confermo)
   - POI chiama create_appointment

3. USA LE FUNZIONI:
   - create_appointment: Prenota l'appuntamento (solo dopo conferma!)
   - check_availability: Verifica se un orario specifico è disponibile
   - get_available_slots: Mostra TUTTI gli orari disponibili per una data
   - get_customer_appointments: Mostra le prenotazioni del cliente
   - modify_appointment: Modifica/sposta un appuntamento esistente
   - cancel_appointment: Cancella una prenotazione

4. MODIFICA/CANCELLAZIONE:
   - Se il cliente vuole modificare o cancellare MA non specifica quale appuntamento:
     → Chiama PRIMA get_customer_appointments per vedere i suoi appuntamenti
     → Se ha UN SOLO appuntamento, usa quello
     → Se ha PIÙ appuntamenti, chiedi quale vuole modificare/cancellare
   - Per modificare: chiama modify_appointment con l'ID e i nuovi valori
   - Per cancellare: chiama cancel_appointment con l'ID

5. SII ONESTA:
   - Conferma solo se la funzione ritorna success=True
   - Se errore o non disponibile, comunica chiaramente

Rispondi naturalmente come una vera receptionist. Se il cliente parla inglese, rispondi in inglese."""

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
# BOOKING FUNCTIONS (Called by AI)
# ============================================================================

def create_appointment(customer_phone: str, customer_name: str, service_type: str, date: str, time: str) -> Dict[str, Any]:
    """Create a salon appointment"""
    try:
        # Normalize phone
        normalized_phone = normalize_phone(customer_phone)

        # Validate customer name
        if not customer_name or not customer_name.strip():
            return {"success": False, "error": "Nome cliente richiesto."}
        customer_name = customer_name.strip()

        # Validate service
        service = SALON_SERVICES.get(service_type.lower())
        if not service:
            return {
                "success": False,
                "error": f"Servizio non riconosciuto: {service_type}. Servizi disponibili: taglio, piega, colore, colpi_sole, trattamento"
            }

        # Validate date and time together (check if in the past)
        try:
            appointment_datetime = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
            if appointment_datetime < datetime.now():
                return {"success": False, "error": "Non è possibile prenotare nel passato."}
        except ValueError:
            return {"success": False, "error": f"Formato data/ora non valido: {date} {time}. Usa YYYY-MM-DD e HH:MM"}
        
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
                    "error": f"Mi dispiace, il {date} alle {time} è già occupato. Prova un altro orario."
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

            calendar_note = " (sincronizzato con calendario)" if google_event_id else ""
            logger.info(f"✅ Appointment created: #{appointment_id} for {customer_name}{calendar_note}")

            return {
                "success": True,
                "appointment_id": appointment_id,
                "message": f"Prenotazione confermata! Appuntamento #{appointment_id}: {customer_name}, {service['name_it']} il {date} alle {time}. Costo: €{service['price']}"
            }
        finally:
            conn.close()

    except Exception as e:
        logger.error(f"❌ Create appointment error: {e}")
        return {"success": False, "error": f"Errore nella prenotazione: {str(e)}"}

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
                "message": f"{date} alle {time}: {'Disponibile ✅' if available else 'Già prenotato ❌'}"
            }
        finally:
            conn.close()

    except Exception as e:
        return {"success": False, "error": str(e)}

def get_customer_appointments(customer_phone: str) -> Dict[str, Any]:
    """Get all appointments for a customer"""
    try:
        # Normalize phone
        normalized_phone = normalize_phone(customer_phone)

        conn = get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """SELECT id, customer_name, service_type, appointment_date, appointment_time, price, status
                   FROM salon_appointments
                   WHERE customer_phone = %s AND status = 'confirmed'
                   ORDER BY appointment_date, appointment_time""",
                (normalized_phone,)
            )

            appointments = []
            for row in cur.fetchall():
                service = SALON_SERVICES.get(row[2], {})
                appointments.append({
                    "appointment_id": row[0],
                    "customer_name": row[1],
                    "service": service.get("name_it", row[2]),
                    "date": str(row[3]),
                    "time": str(row[4]),
                    "price": float(row[5]) if row[5] else 0,
                    "status": row[6]
                })

            if not appointments:
                return {
                    "success": True,
                    "appointments": [],
                    "message": "Non hai appuntamenti attivi."
                }

            return {
                "success": True,
                "appointments": appointments,
                "message": f"Hai {len(appointments)} appuntamento/i attivo/i."
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
                    "error": f"Appuntamento #{appointment_id} non trovato o già cancellato."
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

            calendar_note = " (rimosso dal calendario)" if google_event_id else ""
            logger.info(f"✅ Appointment #{appointment_id} cancelled{calendar_note}")

            return {
                "success": True,
                "message": f"Appuntamento #{appointment_id} cancellato con successo."
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
                    "error": f"Appuntamento #{appointment_id} non trovato. Usa get_customer_appointments per vedere gli appuntamenti attivi."
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

            # Validate new service if changed
            service = SALON_SERVICES.get(final_service)
            if not service:
                return {
                    "success": False,
                    "error": f"Servizio non riconosciuto: {final_service}. Servizi: taglio, piega, colore, colpi_sole, trattamento"
                }

            # Validate new date and time together (check if in the past)
            try:
                final_datetime = datetime.strptime(f"{final_date} {final_time}", "%Y-%m-%d %H:%M")
                if final_datetime < datetime.now():
                    return {"success": False, "error": "Non è possibile spostare l'appuntamento nel passato."}
            except ValueError:
                return {"success": False, "error": f"Formato data/ora non valido: {final_date} {final_time}"}

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
                        "error": f"Il {final_date} alle {final_time} è già occupato. Scegli un altro orario."
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

            calendar_note = " (calendario aggiornato)" if google_event_id else ""
            logger.info(f"✅ Appointment #{appointment_id} modified: {final_date} {final_time} {final_service}{calendar_note}")

            # Build change summary
            changes = []
            if new_date and new_date != current_date:
                changes.append(f"data: {current_date} → {final_date}")
            if new_time and new_time != current_time:
                changes.append(f"ora: {current_time} → {final_time}")
            if new_service and new_service.lower() != current_service.lower():
                changes.append(f"servizio: {current_service} → {final_service}")

            change_text = ", ".join(changes) if changes else "nessuna modifica"

            return {
                "success": True,
                "appointment_id": appointment_id,
                "message": f"Appuntamento #{appointment_id} modificato! {current_name}: {service['name_it']} il {final_date} alle {final_time}. Modifiche: {change_text}"
            }
        finally:
            conn.close()

    except Exception as e:
        logger.error(f"❌ Modify appointment error: {e}")
        return {"success": False, "error": f"Errore nella modifica: {str(e)}"}

def get_available_slots(date: str) -> Dict[str, Any]:
    """
    Get available time slots for a specific date.
    Returns 30-minute slots from 09:00 to 19:00 that are not booked.
    """
    try:
        # Validate date
        try:
            parsed_date = datetime.strptime(date, "%Y-%m-%d")
            now = datetime.now()
            if parsed_date.date() < now.date():
                return {"success": False, "error": "Non posso mostrare disponibilità per date passate."}
            is_today = parsed_date.date() == now.date()
        except ValueError:
            return {"success": False, "error": f"Formato data non valido: {date}. Usa YYYY-MM-DD"}

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

        # Generate all possible slots (09:00 to 19:00, every 30 minutes)
        all_slots = []
        for hour in range(9, 19):
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
                "available_slots": [],
                "message": f"Nessun orario disponibile per il {date}."
            }

        return {
            "success": True,
            "date": date,
            "available_slots": available_slots,
            "message": f"Orari disponibili per il {date}: {', '.join(available_slots)}"
        }

    except Exception as e:
        logger.error(f"❌ Get available slots error: {e}")
        return {"success": False, "error": str(e)}

# ============================================================================
# OPENAI FUNCTION DEFINITIONS
# ============================================================================

BOOKING_FUNCTIONS = [
    {
        "name": "create_appointment",
        "description": "Crea un appuntamento al salone. CHIAMARE SOLO dopo aver ricevuto conferma esplicita dal cliente (sì/ok/confermo). Raccogliere sempre: nome, servizio, data, ora.",
        "parameters": {
            "type": "object",
            "properties": {
                "customer_name": {
                    "type": "string",
                    "description": "Nome completo del cliente"
                },
                "service_type": {
                    "type": "string",
                    "description": "Tipo di servizio: taglio, piega, colore, colpi_sole, trattamento",
                    "enum": ["taglio", "piega", "colore", "colpi_sole", "trattamento"]
                },
                "date": {
                    "type": "string",
                    "description": f"Data appuntamento in formato YYYY-MM-DD. Oggi è {TODAY}, domani è {TOMORROW}"
                },
                "time": {
                    "type": "string",
                    "description": "Ora appuntamento in formato HH:MM (24 ore, es: 15:00 per le 3 del pomeriggio)"
                }
            },
            "required": ["customer_name", "service_type", "date", "time"]
        }
    },
    {
        "name": "check_availability",
        "description": "Verifica se una data e ora specifica è disponibile per la prenotazione",
        "parameters": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": "Data in formato YYYY-MM-DD"
                },
                "time": {
                    "type": "string",
                    "description": "Ora in formato HH:MM (24 ore)"
                }
            },
            "required": ["date", "time"]
        }
    },
    {
        "name": "get_customer_appointments",
        "description": "Ottieni tutti gli appuntamenti attivi del cliente. Usa quando il cliente chiede di vedere/controllare le sue prenotazioni.",
        "parameters": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "cancel_appointment",
        "description": "Cancella un appuntamento specifico tramite ID. IMPORTANTE: Se il cliente non specifica l'ID, chiama PRIMA get_customer_appointments per vedere i suoi appuntamenti.",
        "parameters": {
            "type": "object",
            "properties": {
                "appointment_id": {
                    "type": "integer",
                    "description": "L'ID dell'appuntamento da cancellare (es: 123 da 'Appuntamento #123')"
                }
            },
            "required": ["appointment_id"]
        }
    },
    {
        "name": "modify_appointment",
        "description": "Modifica un appuntamento esistente (data, ora, servizio). IMPORTANTE: Se il cliente non specifica l'ID, chiama PRIMA get_customer_appointments per vedere i suoi appuntamenti e ottenere l'ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "appointment_id": {
                    "type": "integer",
                    "description": "L'ID dell'appuntamento da modificare"
                },
                "new_date": {
                    "type": "string",
                    "description": f"Nuova data in formato YYYY-MM-DD (opzionale). Oggi è {TODAY}, domani è {TOMORROW}"
                },
                "new_time": {
                    "type": "string",
                    "description": "Nuovo orario in formato HH:MM (opzionale)"
                },
                "new_service": {
                    "type": "string",
                    "description": "Nuovo servizio (opzionale): taglio, piega, colore, colpi_sole, trattamento",
                    "enum": ["taglio", "piega", "colore", "colpi_sole", "trattamento"]
                }
            },
            "required": ["appointment_id"]
        }
    },
    {
        "name": "get_available_slots",
        "description": "Mostra tutti gli orari disponibili per una data specifica. Utile quando il cliente chiede 'che orari avete?' o 'quando siete liberi?'",
        "parameters": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": f"Data in formato YYYY-MM-DD. Oggi è {TODAY}, domani è {TOMORROW}"
                }
            },
            "required": ["date"]
        }
    }
]

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
            return {"success": False, "error": f"Funzione sconosciuta: {function_name}"}
    
    except Exception as e:
        logger.error(f"Function execution error: {e}")
        return {"success": False, "error": str(e)}

# ============================================================================
# CONVERSATION MANAGEMENT
# ============================================================================

conversation_history: Dict[str, List[Dict]] = {}

def get_ai_response(phone: str, message: str) -> str:
    """Get AI response with function calling"""
    try:
        # Get or create conversation history
        if phone not in conversation_history:
            conversation_history[phone] = []
        
        # Build messages
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(conversation_history[phone][-10:])  # Last 10 messages
        messages.append({"role": "user", "content": message})
        
        # Call OpenAI with function calling
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=messages,
            functions=BOOKING_FUNCTIONS,
            function_call="auto",
            temperature=0.7
        )
        
        assistant_message = response['choices'][0]['message']
        
        # Handle function calls
        if assistant_message.get('function_call'):
            function_name = assistant_message['function_call']['name']
            function_args = assistant_message['function_call']['arguments']
            
            logger.info(f"🔧 AI calling function: {function_name}")
            logger.info(f"   Args: {function_args}")
            
            # Execute the function
            function_result = execute_function(function_name, function_args, phone)
            
            logger.info(f"   Result: {function_result}")
            
            # Add function result to messages
            messages.append(assistant_message)
            messages.append({
                "role": "function",
                "name": function_name,
                "content": json.dumps(function_result)
            })
            
            # Get final response after function execution
            second_response = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=messages,
                temperature=0.7
            )
            
            final_message = second_response['choices'][0]['message']['content']
            
            # Save to history
            conversation_history[phone].append({"role": "user", "content": message})
            conversation_history[phone].append({"role": "assistant", "content": final_message})
            
            return final_message
        
        else:
            # No function call, just return response
            response_text = assistant_message['content']
            
            # Save to history
            conversation_history[phone].append({"role": "user", "content": message})
            conversation_history[phone].append({"role": "assistant", "content": response_text})
            
            return response_text
    
    except Exception as e:
        logger.error(f"❌ AI Error: {e}")
        return "Mi dispiace, ho avuto un problema tecnico. Puoi riprovare?"

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
    title="Salon Bella Vita - WhatsApp Bot with Booking",
    description="Virtual receptionist with calendar integration",
    version="3.0.0"
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
                "Grazie! Al momento posso rispondere solo a messaggi di testo. Come posso aiutarti con la prenotazione? 💇‍♀️")
    
    except Exception as e:
        logger.error(f"Process message error: {e}")

@app.get("/health")
async def health_check():
    """Health check"""
    return JSONResponse({
        "status": "healthy",
        "service": BUSINESS_NAME,
        "type": BUSINESS_TYPE,
        "version": "3.0.0",
        "features": {
            "booking": True,
            "calendar_integration": True,
            "function_calling": True
        },
        "services": list(SALON_SERVICES.keys()),
        "timestamp": datetime.now().isoformat()
    })

@app.get("/")
async def root():
    """Root endpoint"""
    return JSONResponse({
        "name": f"{BUSINESS_NAME} - WhatsApp Bot",
        "version": "3.0.0",
        "features": ["booking", "calendar", "AI"],
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


