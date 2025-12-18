"""
SALON BELLA VITA - WhatsApp Multi-Tenant Bot
Compatible with OpenAI 0.28.x (EC2 environment)
"""
import os
import asyncio
import logging
import json
from typing import Dict, Any, Optional
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request, Query
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

# OpenAI Configuration (use existing key on EC2)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "REPLACE_WITH_OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY

# WhatsApp Configuration
WHATSAPP_WEBHOOK_VERIFY_TOKEN = os.getenv("WHATSAPP_WEBHOOK_VERIFY_TOKEN", "lyosaas2024")
WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")

# Business Configuration
BUSINESS_NAME = "Salon Bella Vita"
BUSINESS_TYPE = "beauty_salon"

# ============================================================================
# SALON CONFIGURATION
# ============================================================================

SALON_CONFIG = {
    "name": "Salon Bella Vita",
    "type": "beauty_salon",
    "location": "Via Roma 123, Milano",
    "phone": "+39 02 1234567",
    "services": {
        "taglio": {"name_it": "Taglio Donna", "name_en": "Women's Haircut", "price": 35, "duration": 60},
        "piega": {"name_it": "Piega", "name_en": "Styling", "price": 20, "duration": 30},
        "colore": {"name_it": "Colore", "name_en": "Coloring", "price": 80, "duration": 120},
        "colpi_sole": {"name_it": "Colpi di Sole", "name_en": "Highlights", "price": 60, "duration": 90},
        "trattamento": {"name_it": "Trattamento", "name_en": "Treatment", "price": 25, "duration": 30}
    },
    "hours": {
        "monday": "09:00-19:00",
        "tuesday": "09:00-19:00",
        "wednesday": "closed",
        "thursday": "09:00-19:00",
        "friday": "09:00-20:00",
        "saturday": "09:00-18:00",
        "sunday": "closed"
    }
}

# System prompts
SYSTEM_PROMPT_IT = f"""Sei Lyo, la receptionist virtuale del {SALON_CONFIG['name']} a Milano.
Sei cordiale, professionale e disponibile. Il tuo compito è aiutare i clienti a prenotare appuntamenti per servizi di parrucchiere.

📍 INFORMAZIONI SALONE:
- Indirizzo: {SALON_CONFIG['location']}
- Telefono: {SALON_CONFIG['phone']}

💇 SERVIZI DISPONIBILI:
- Taglio Donna: €35 (60 min)
- Piega: €20 (30 min)
- Colore: €80 (120 min)
- Colpi di Sole: €60 (90 min)
- Trattamento: €25 (30 min)

🕐 ORARI DI APERTURA:
- Lunedì: 09:00-19:00
- Martedì: 09:00-19:00
- Mercoledì: CHIUSO
- Giovedì: 09:00-19:00
- Venerdì: 09:00-20:00
- Sabato: 09:00-18:00
- Domenica: CHIUSO

📅 OGGI È: {datetime.now().strftime('%A %d %B %Y')}

COME PRENOTARE:
1. Chiedi al cliente il nome
2. Chiedi quale servizio desidera
3. Chiedi la data e l'ora preferita
4. Conferma tutti i dettagli prima di procedere

Rispondi sempre in modo naturale e conversazionale, come una vera receptionist esperta.
Se il cliente parla in inglese, rispondi in inglese."""

SYSTEM_PROMPT_EN = f"""You are Lyo, the virtual receptionist at {SALON_CONFIG['name']} in Milano.
You are friendly, professional and helpful. Your job is to help customers book appointments for hair services.

📍 SALON INFO:
- Address: {SALON_CONFIG['location']}
- Phone: {SALON_CONFIG['phone']}

💇 AVAILABLE SERVICES:
- Women's Haircut: €35 (60 min)
- Styling: €20 (30 min)
- Coloring: €80 (120 min)
- Highlights: €60 (90 min)
- Treatment: €25 (30 min)

🕐 OPENING HOURS:
- Monday: 09:00-19:00
- Tuesday: 09:00-19:00
- Wednesday: CLOSED
- Thursday: 09:00-19:00
- Friday: 09:00-20:00
- Saturday: 09:00-18:00
- Sunday: CLOSED

📅 TODAY IS: {datetime.now().strftime('%A %B %d, %Y')}

HOW TO BOOK:
1. Ask for the customer's name
2. Ask which service they want
3. Ask for preferred date and time
4. Confirm all details before proceeding

Always respond naturally and conversationally, like a real expert receptionist.
If the customer speaks Italian, respond in Italian."""

# ============================================================================
# CONVERSATION ENGINE
# ============================================================================

class ConversationEngine:
    """Handles conversations with OpenAI (0.28.x compatible)"""
    
    def __init__(self):
        self.conversations: Dict[str, list] = {}  # phone -> messages
    
    def detect_language(self, message: str) -> str:
        """Detect language from message"""
        message_lower = message.lower()
        english_words = ["hi", "hello", "what", "how", "i'm", "book", "thank", "you", "when", "where", "want", "need"]
        italian_words = ["ciao", "buon", "che", "come", "sono", "prenota", "grazie", "puoi", "vorrei", "voglio", "bisogno"]
        
        english_score = sum(1 for word in english_words if word in message_lower)
        italian_score = sum(1 for word in italian_words if word in message_lower)
        
        return "english" if english_score > italian_score else "italian"
    
    async def process_message(self, phone: str, message: str) -> str:
        """Process message and generate response"""
        
        # Detect language
        language = self.detect_language(message)
        system_prompt = SYSTEM_PROMPT_EN if language == "english" else SYSTEM_PROMPT_IT
        
        # Get or create conversation history
        if phone not in self.conversations:
            self.conversations[phone] = []
        
        # Build messages for OpenAI
        messages = [{"role": "system", "content": system_prompt}]
        
        # Add conversation history (last 10 messages)
        for msg in self.conversations[phone][-10:]:
            messages.append(msg)
        
        # Add current message
        messages.append({"role": "user", "content": message})
        
        try:
            # Call OpenAI (0.28.x API)
            response = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=messages,
                temperature=0.8,
                max_tokens=300
            )
            
            ai_response = response.choices[0].message.content.strip()
            
            # Save to conversation history
            self.conversations[phone].append({"role": "user", "content": message})
            self.conversations[phone].append({"role": "assistant", "content": ai_response})
            
            # Keep history manageable
            if len(self.conversations[phone]) > 20:
                self.conversations[phone] = self.conversations[phone][-20:]
            
            return ai_response
            
        except Exception as e:
            logger.error(f"OpenAI error: {e}")
            if language == "italian":
                return "Mi dispiace, ho avuto un problema tecnico. Puoi riprovare tra un momento?"
            else:
                return "Sorry, I had a technical issue. Can you try again in a moment?"


conversation_engine = ConversationEngine()

# ============================================================================
# WHATSAPP SERVICE
# ============================================================================

async def send_whatsapp_message(phone: str, message: str) -> bool:
    """Send WhatsApp message via Meta Cloud API"""
    
    if not WHATSAPP_ACCESS_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        logger.warning("WhatsApp not configured - cannot send message")
        return False
    
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
                logger.error(f"❌ WhatsApp send failed: {response.status_code} - {response.text}")
                return False
                
    except Exception as e:
        logger.error(f"❌ WhatsApp API error: {e}")
        return False


async def mark_as_read(message_id: str) -> bool:
    """Mark message as read"""
    
    if not WHATSAPP_ACCESS_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        return False
    
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
    title="Salon Bella Vita - WhatsApp Bot",
    description="Virtual receptionist for hair salon booking",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================================
# WEBHOOK ENDPOINTS (Same path as current bot for compatibility)
# ============================================================================

@app.get("/webhook")
async def verify_webhook(request: Request):
    """WhatsApp webhook verification"""
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    
    logger.info(f"🔐 Webhook verification: mode={mode}, token={token}")
    
    if mode == "subscribe" and token == WHATSAPP_WEBHOOK_VERIFY_TOKEN:
        logger.info("✅ Webhook verified!")
        return PlainTextResponse(challenge)
    
    logger.warning(f"❌ Verification failed: expected {WHATSAPP_WEBHOOK_VERIFY_TOKEN}")
    return PlainTextResponse("Failed", status_code=403)


@app.post("/webhook")
async def webhook(request: Request):
    """Handle incoming WhatsApp messages"""
    try:
        body = await request.json()
        
        logger.info(f"📨 Webhook received")
        
        if body.get("object") != "whatsapp_business_account":
            return JSONResponse({"status": "ignored"})
        
        # Process entries
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                
                # Process messages
                messages = value.get("messages", [])
                for message in messages:
                    await process_message(message, value)
        
        return JSONResponse({"status": "processed"})
        
    except Exception as e:
        logger.error(f"❌ Webhook error: {e}", exc_info=True)
        return JSONResponse({"status": "error"})


async def process_message(message: Dict[str, Any], value: Dict[str, Any]):
    """Process an incoming WhatsApp message"""
    try:
        phone = message.get("from")
        message_id = message.get("id")
        message_type = message.get("type", "text")
        
        # Get contact name if available
        contacts = value.get("contacts", [])
        contact_name = contacts[0].get("profile", {}).get("name", "Cliente") if contacts else "Cliente"
        
        logger.info(f"💬 Message from {phone} ({contact_name})")
        
        # Mark as read
        await mark_as_read(message_id)
        
        # Handle text messages
        if message_type == "text":
            text = message.get("text", {}).get("body", "")
            
            if not text:
                return
            
            logger.info(f"📝 Message: {text[:100]}...")
            
            # Process with conversation engine
            response = await conversation_engine.process_message(phone, text)
            
            # Send response
            await send_whatsapp_message(phone, response)
        
        # Handle other message types
        elif message_type in ["image", "audio", "video", "document"]:
            response = "Grazie! Al momento posso rispondere solo a messaggi di testo. Come posso aiutarti con la prenotazione? 💇‍♀️"
            await send_whatsapp_message(phone, response)
        
        # Handle interactive (buttons, lists)
        elif message_type == "interactive":
            interactive = message.get("interactive", {})
            interactive_type = interactive.get("type")
            
            if interactive_type == "button_reply":
                text = interactive.get("button_reply", {}).get("title", "")
            elif interactive_type == "list_reply":
                text = interactive.get("list_reply", {}).get("title", "")
            else:
                text = ""
            
            if text:
                response = await conversation_engine.process_message(phone, text)
                await send_whatsapp_message(phone, response)
                
    except Exception as e:
        logger.error(f"Error processing message: {e}", exc_info=True)


# ============================================================================
# ADDITIONAL ENDPOINTS (keeping compatibility)
# ============================================================================

@app.get("/webhooks/whatsapp")
async def whatsapp_webhook_verify_alt(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge")
):
    """Alternative webhook verification endpoint"""
    logger.info(f"🔐 Alt webhook verification: mode={hub_mode}")
    
    if hub_mode == "subscribe":
        if hub_verify_token == WHATSAPP_WEBHOOK_VERIFY_TOKEN:
            logger.info("✅ Webhook verified!")
            return PlainTextResponse(content=hub_challenge)
        else:
            raise HTTPException(status_code=403, detail="Invalid verify token")
    
    raise HTTPException(status_code=400, detail="Invalid request")


@app.post("/webhooks/whatsapp")
async def whatsapp_webhook_alt(request: Request):
    """Alternative webhook endpoint"""
    return await webhook(request)


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return JSONResponse({
        "status": "healthy",
        "service": BUSINESS_NAME,
        "type": BUSINESS_TYPE,
        "timestamp": datetime.now().isoformat(),
        "version": "2.0.0",
        "ai": "GPT-3.5-turbo",
        "configuration": {
            "openai_configured": bool(OPENAI_API_KEY),
            "whatsapp_configured": bool(WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID),
            "webhook_verify_token": WHATSAPP_WEBHOOK_VERIFY_TOKEN
        },
        "services": list(SALON_CONFIG["services"].keys())
    })


@app.get("/")
async def root():
    """Root endpoint"""
    return JSONResponse({
        "name": f"{BUSINESS_NAME} - WhatsApp Bot",
        "version": "2.0.0",
        "status": "operational",
        "business_type": BUSINESS_TYPE,
        "endpoints": {
            "webhook": "/webhook",
            "webhook_alt": "/webhooks/whatsapp",
            "health": "/health"
        },
        "services": [
            f"{s['name_it']} - €{s['price']}" 
            for s in SALON_CONFIG["services"].values()
        ]
    })


@app.post("/api/test-message")
async def test_message(request: Request):
    """Test endpoint for sending messages directly"""
    try:
        data = await request.json()
        phone = data.get("phone", "+39123456789")
        message = data.get("message", "")
        
        if not message.strip():
            raise HTTPException(status_code=400, detail="Empty message")
        
        response = await conversation_engine.process_message(phone, message)
        
        return JSONResponse({
            "user_message": message,
            "bot_response": response,
            "phone": phone,
            "business": BUSINESS_NAME
        })
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# STARTUP
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    
    logger.info("=" * 60)
    logger.info(f"🚀 STARTING {BUSINESS_NAME.upper()} WHATSAPP BOT")
    logger.info("=" * 60)
    logger.info(f"📍 Business: {BUSINESS_NAME}")
    logger.info(f"💇 Type: {BUSINESS_TYPE}")
    logger.info(f"🤖 OpenAI: {'✅' if OPENAI_API_KEY else '❌'}")
    logger.info(f"📱 WhatsApp: {'✅' if WHATSAPP_ACCESS_TOKEN else '❌'}")
    logger.info(f"🔐 Verify Token: {WHATSAPP_WEBHOOK_VERIFY_TOKEN}")
    logger.info("=" * 60)
    
    uvicorn.run(
        "salon_bot_ec2:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info"
    )

