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

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx
import openai

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURATION
# ============================================================================

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "REPLACE_WITH_OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY

WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN", "EAAP6Cj4xZBVgBQLCIcNRMNCVPEEcNu4i6VBkp2UZA4MaCOdABtXBMCToMrNmT759EnsZBOzCykwulWLkIy1yTFpBanPPlRpRdGeCqTZAaOWH15WYRgr0leN7LzaJKmwjyckQVxYeY5lbsk21zn5HQ3PyIgqEQzJNk6VZCGlfTuqbMZC2sX9AjtnxatBtoesgZDZD")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "961636900357709")
WHATSAPP_WEBHOOK_VERIFY_TOKEN = os.getenv("WHATSAPP_WEBHOOK_VERIFY_TOKEN", "lyosaas2024")

# Database Configuration
DB_CONFIG = {
    "host": "lyo-enterprise-database.cixc4kiw6r00.us-east-1.rds.amazonaws.com",
    "port": 5432,
    "database": "lyo_production",
    "user": "lyoadmin",
    "password": "LyoSaaS2024Enterprise!"
}

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
   - Data preferita (converti "domani" in {TOMORROW}, "today" in {TODAY})
   - Ora preferita (converti "2 pm" in 14:00, "3pm" in 15:00, usa formato 24h)

2. ⚠️ CRITICAL - SEMPRE VERIFICA DISPONIBILITÀ PRIMA:
   - MAI dire che un orario è "fully booked" o "occupato" SENZA aver chiamato check_availability(date, time)!
   - Quando il cliente chiede un orario, DEVI chiamare check_availability PRIMA di rispondere
   - Se check_availability ritorna available=True → l'orario è LIBERO, puoi procedere
   - Se check_availability ritorna available=False → allora è occupato, suggerisci alternative
   - NON fare supposizioni! SEMPRE verificare con la funzione!

3. CONFERMA UNA VOLTA:
   - Dopo aver VERIFICATO disponibilità e raccolto tutto, conferma: "Perfetto! Riepilogo: [Nome], [Servizio] il [Data] alle [Ora]. Confermo la prenotazione?"
   - Aspetta la conferma (sì/ok/perfetto/confermo)
   - POI chiama create_appointment

4. USA LE FUNZIONI:
   - check_availability: OBBLIGATORIO prima di dire se un orario è disponibile! Controlla sia database che Google Calendar.
   - get_available_slots: Mostra tutti gli orari disponibili per una data
   - create_appointment: Prenota l'appuntamento (solo dopo conferma!)
   - get_customer_appointments: Mostra le prenotazioni del cliente
   - modify_appointment: Modifica un appuntamento esistente
   - cancel_appointment: Cancella una prenotazione

5. SII ONESTA:
   - MAI dire "fully booked" senza aver chiamato check_availability!
   - Conferma solo se create_appointment ritorna success=True
   - Se errore o non disponibile, comunica chiaramente

Rispondi naturalmente come una vera receptionist. Se il cliente parla inglese, rispondi in inglese."""

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
        # Validate service
        service = SALON_SERVICES.get(service_type.lower())
        if not service:
            return {
                "success": False,
                "error": f"Servizio non riconosciuto: {service_type}. Servizi disponibili: taglio, piega, colore, colpi_sole, trattamento"
            }
        
        # Validate date
        try:
            appointment_date = datetime.strptime(date, "%Y-%m-%d")
            if appointment_date.date() < datetime.now().date():
                return {"success": False, "error": "Non è possibile prenotare nel passato"}
        except ValueError:
            return {"success": False, "error": f"Formato data non valido: {date}. Usa YYYY-MM-DD"}
        
        # Validate time
        try:
            datetime.strptime(time, "%H:%M")
        except ValueError:
            return {"success": False, "error": f"Formato ora non valido: {time}. Usa HH:MM"}
        
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Check availability
        cur.execute(
            """SELECT COUNT(*) FROM salon_appointments 
               WHERE appointment_date = %s AND appointment_time = %s AND status = 'confirmed'""",
            (date, time)
        )
        count = cur.fetchone()[0]
        
        if count > 0:
            conn.close()
            return {
                "success": False,
                "error": f"Mi dispiace, il {date} alle {time} è già occupato. Prova un altro orario."
            }
        
        # Create appointment
        cur.execute(
            """INSERT INTO salon_appointments 
               (customer_phone, customer_name, service_type, appointment_date, appointment_time, duration_minutes, price, status)
               VALUES (%s, %s, %s, %s, %s, %s, %s, 'confirmed')
               RETURNING id""",
            (customer_phone, customer_name, service_type, date, time, service["duration"], service["price"])
        )
        
        appointment_id = cur.fetchone()[0]
        conn.commit()
        
        # Create Google Calendar event with timezone
        rome_tz = pytz.timezone('Europe/Rome')
        appointment_datetime = datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M")
        appointment_datetime = rome_tz.localize(appointment_datetime)
        end_datetime = appointment_datetime + timedelta(minutes=service["duration"])
        
        # Get Google credentials and create calendar event
        cur.execute("SELECT access_token, refresh_token, token_uri, client_id, client_secret FROM google_credentials ORDER BY id DESC LIMIT 1")
        creds_row = cur.fetchone()
        
        if creds_row:
            try:
                from google.oauth2.credentials import Credentials
                from googleapiclient.discovery import build
                
                creds = Credentials(
                    token=creds_row[0],
                    refresh_token=creds_row[1],
                    token_uri=creds_row[2],
                    client_id=creds_row[3],
                    client_secret=creds_row[4]
                )
                
                service_cal = build('calendar', 'v3', credentials=creds)
                
                event = {
                    'summary': f'Salon Bella Vita - {service["name_it"]} - {customer_name}',
                    'description': f'{service["name_it"]} - €{service["price"]}\nTelefono: {customer_phone}',
                    'start': {
                        'dateTime': appointment_datetime.isoformat(),
                        'timeZone': 'Europe/Rome',
                    },
                    'end': {
                        'dateTime': end_datetime.isoformat(),
                        'timeZone': 'Europe/Rome',
                    }
                }
                
                created_event = service_cal.events().insert(calendarId='primary', body=event).execute()
                
                # Save event ID
                cur.execute("UPDATE salon_appointments SET google_event_id = %s WHERE id = %s", (created_event['id'], appointment_id))
                conn.commit()
                
                logger.info(f"✅ Google Calendar event created: {created_event['id']}")
            except Exception as e:
                logger.warning(f"⚠️ Google Calendar error (booking still saved): {e}")
        
        conn.close()
        
        logger.info(f"✅ Appointment created: #{appointment_id} for {customer_name}")
        
        return {
            "success": True,
            "appointment_id": appointment_id,
            "message": f"Prenotazione confermata! Appuntamento #{appointment_id}: {customer_name}, {service['name_it']} il {date} alle {time}. Costo: €{service['price']}"
        }
        
    except Exception as e:
        logger.error(f"❌ Create appointment error: {e}")
        return {"success": False, "error": f"Errore nella prenotazione: {str(e)}"}

def check_availability(date: str, time: str) -> Dict[str, Any]:
    """Check if a time slot is available - checks both database and Google Calendar"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Check database
        cur.execute(
            """SELECT COUNT(*) FROM salon_appointments 
               WHERE appointment_date = %s AND appointment_time = %s AND status = 'confirmed'""",
            (date, time)
        )
        
        db_count = cur.fetchone()[0]
        
        # Also check Google Calendar
        calendar_busy = False
        try:
            cur.execute("SELECT access_token, refresh_token, token_uri, client_id, client_secret FROM google_credentials ORDER BY id DESC LIMIT 1")
            creds_row = cur.fetchone()
            
            if creds_row:
                from google.oauth2.credentials import Credentials
                from googleapiclient.discovery import build
                
                creds = Credentials(
                    token=creds_row[0],
                    refresh_token=creds_row[1],
                    token_uri=creds_row[2],
                    client_id=creds_row[3],
                    client_secret=creds_row[4]
                )
                
                service = build('calendar', 'v3', credentials=creds)
                
                # Convert to datetime for calendar query
                rome_tz = pytz.timezone('Europe/Rome')
                start_datetime = rome_tz.localize(datetime.strptime(f"{date} {time}", "%Y-%m-%d %H:%M"))
                end_datetime = start_datetime + timedelta(hours=2)  # Check 2 hour window
                
                # Query Google Calendar
                events_result = service.events().list(
                    calendarId='primary',
                    timeMin=start_datetime.isoformat(),
                    timeMax=end_datetime.isoformat(),
                    maxResults=10,
                    singleEvents=True,
                    orderBy='startTime'
                ).execute()
                
                events = events_result.get('items', [])
                calendar_busy = len(events) > 0
                
                logger.info(f"📅 Google Calendar check: {len(events)} events found for {date} {time}")
        except Exception as e:
            logger.warning(f"⚠️ Google Calendar check failed (using DB only): {e}")
        
        conn.close()
        
        # Available if both database and calendar are free
        available = (db_count == 0) and (not calendar_busy)
        
        return {
            "success": True,
            "available": available,
            "message": f"{date} alle {time}: {'Disponibile ✅' if available else 'Già prenotato ❌'}",
            "database_check": db_count == 0,
            "calendar_check": not calendar_busy
        }
        
    except Exception as e:
        logger.error(f"❌ Check availability error: {e}")
        return {"success": False, "error": str(e)}

def get_customer_appointments(customer_phone: str) -> Dict[str, Any]:
    """Get all appointments for a customer"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute(
            """SELECT id, customer_name, service_type, appointment_date, appointment_time, price, status
               FROM salon_appointments
               WHERE customer_phone = %s AND status = 'confirmed'
               ORDER BY appointment_date, appointment_time""",
            (customer_phone,)
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
        
        conn.close()
        
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
        
    except Exception as e:
        return {"success": False, "error": str(e)}

def cancel_appointment(customer_phone: str, appointment_id: int) -> Dict[str, Any]:
    """Cancel an appointment"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Verify appointment belongs to customer and get Google event ID
        cur.execute(
            """SELECT id, google_event_id FROM salon_appointments 
               WHERE id = %s AND customer_phone = %s AND status = 'confirmed'""",
            (appointment_id, customer_phone)
        )
        
        result = cur.fetchone()
        if not result:
            conn.close()
            return {
                "success": False,
                "error": f"Appuntamento #{appointment_id} non trovato o già cancellato."
            }
        
        google_event_id = result[1]
        
        # Cancel appointment in database
        cur.execute(
            "UPDATE salon_appointments SET status = 'cancelled' WHERE id = %s",
            (appointment_id,)
        )
        conn.commit()
        
        # Delete from Google Calendar if event exists
        if google_event_id:
            try:
                cur.execute("SELECT access_token, refresh_token, token_uri, client_id, client_secret FROM google_credentials ORDER BY id DESC LIMIT 1")
                creds_row = cur.fetchone()
                
                if creds_row:
                    from google.oauth2.credentials import Credentials
                    from googleapiclient.discovery import build
                    
                    creds = Credentials(
                        token=creds_row[0],
                        refresh_token=creds_row[1],
                        token_uri=creds_row[2],
                        client_id=creds_row[3],
                        client_secret=creds_row[4]
                    )
                    
                    service = build('calendar', 'v3', credentials=creds)
                    service.events().delete(calendarId='primary', eventId=google_event_id).execute()
                    
                    logger.info(f"✅ Google Calendar event deleted: {google_event_id}")
            except Exception as e:
                logger.warning(f"⚠️ Google Calendar deletion failed (appointment still cancelled): {e}")
        
        conn.close()
        
        logger.info(f"✅ Appointment #{appointment_id} cancelled")
        
        return {
            "success": True,
            "message": f"Appuntamento #{appointment_id} cancellato con successo."
        }
        
    except Exception as e:
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
        "description": "Cancella un appuntamento specifico tramite ID. Chiedi l'ID se non fornito.",
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

