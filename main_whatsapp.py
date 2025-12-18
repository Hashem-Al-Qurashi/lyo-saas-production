"""
LYO PRODUCTION - WhatsApp Multi-Tenant Webhook Server
Complete webhook handler for Meta WhatsApp Business API with multi-tenant support
"""
import os
import sys
import asyncio
import logging
import json
import httpx
from typing import Dict, Any, Optional
from datetime import datetime

# Add current directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURATION
# ============================================================================

class Config:
    """Application configuration"""
    # OpenAI
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4-turbo")
    
    # WhatsApp
    WHATSAPP_WEBHOOK_VERIFY_TOKEN: str = os.getenv("WHATSAPP_WEBHOOK_VERIFY_TOKEN", "lyo_webhook_verify_token_2024")
    WHATSAPP_ACCESS_TOKEN: str = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
    WHATSAPP_PHONE_NUMBER_ID: str = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
    
    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")
    
    # Chatwoot (fallback)
    CHATWOOT_URL: str = os.getenv("CHATWOOT_URL", "")
    CHATWOOT_ACCOUNT_ID: str = os.getenv("CHATWOOT_ACCOUNT_ID", "")
    CHATWOOT_API_TOKEN: str = os.getenv("CHATWOOT_API_TOKEN", "")
    
    # Business
    BUSINESS_ID: str = os.getenv("BUSINESS_ID", "default_salon")
    BUSINESS_NAME: str = os.getenv("BUSINESS_NAME", "Salon Bella Vita")
    
    @classmethod
    def is_openai_configured(cls) -> bool:
        return bool(cls.OPENAI_API_KEY and cls.OPENAI_API_KEY.startswith("sk-"))
    
    @classmethod
    def is_whatsapp_configured(cls) -> bool:
        return bool(cls.WHATSAPP_ACCESS_TOKEN and cls.WHATSAPP_PHONE_NUMBER_ID)


config = Config()

# ============================================================================
# TENANT MANAGEMENT (Multi-tenant support)
# ============================================================================

class TenantManager:
    """
    Manages multi-tenant configuration.
    In production, this would query the database.
    """
    
    def __init__(self):
        # In-memory tenant cache (in production: load from database)
        self.tenants: Dict[str, Dict[str, Any]] = {}
        self._load_default_tenant()
    
    def _load_default_tenant(self):
        """Load default salon tenant"""
        # Default tenant configuration
        self.tenants["default"] = {
            "tenant_id": "default",
            "business_name": "Salon Bella Vita",
            "business_type": "beauty_salon",
            "whatsapp_phone_number_id": config.WHATSAPP_PHONE_NUMBER_ID,
            "whatsapp_access_token": config.WHATSAPP_ACCESS_TOKEN,
            "language": "it",
            "services": {
                "taglio": {"name_it": "Taglio", "name_en": "Haircut", "price": 35, "duration": 60},
                "piega": {"name_it": "Piega", "name_en": "Styling", "price": 20, "duration": 30},
                "colore": {"name_it": "Colore", "name_en": "Coloring", "price": 80, "duration": 120}
            },
            "location": "Via Roma 123, Milano",
            "hours": {
                "monday": "09:00-19:00",
                "tuesday": "09:00-19:00",
                "wednesday": "closed",
                "thursday": "09:00-19:00",
                "friday": "09:00-20:00",
                "saturday": "09:00-18:00",
                "sunday": "closed"
            },
            "system_prompt_it": """Sei Lyo, la receptionist virtuale del Salon Bella Vita a Milano.
Sei cordiale, professionale e disponibile. Aiuti i clienti a prenotare appuntamenti per servizi di parrucchiere.

Servizi disponibili:
- Taglio donna: €35 (60 min)
- Piega: €20 (30 min)  
- Colore: €80 (120 min)

Indirizzo: Via Roma 123, Milano
Orari: Lun-Mar 9-19, Gio 9-19, Ven 9-20, Sab 9-18 (chiuso Mer e Dom)

Rispondi in modo naturale e conversazionale. Se il cliente vuole prenotare, chiedi nome, servizio desiderato e data/ora preferita.""",
            "system_prompt_en": """You are Lyo, the virtual receptionist at Salon Bella Vita in Milano.
You are friendly, professional and helpful. You help customers book appointments for hairdressing services.

Available services:
- Women's haircut: €35 (60 min)
- Styling: €20 (30 min)
- Coloring: €80 (120 min)

Address: Via Roma 123, Milano
Hours: Mon-Tue 9-19, Thu 9-19, Fri 9-20, Sat 9-18 (closed Wed and Sun)

Respond naturally and conversationally. If the customer wants to book, ask for name, desired service and preferred date/time."""
        }
    
    def get_tenant_by_phone_number_id(self, phone_number_id: str) -> Optional[Dict[str, Any]]:
        """
        Get tenant by WhatsApp phone number ID.
        This is how we identify which business received the message.
        """
        # In production: query database
        # SELECT * FROM lyo_tenants WHERE whatsapp_phone_number_id = $1
        
        for tenant in self.tenants.values():
            if tenant.get("whatsapp_phone_number_id") == phone_number_id:
                return tenant
        
        # Return default tenant if no match (for testing)
        logger.warning(f"No tenant found for phone_number_id: {phone_number_id}, using default")
        return self.tenants.get("default")
    
    def get_tenant(self, tenant_id: str) -> Optional[Dict[str, Any]]:
        """Get tenant by ID"""
        return self.tenants.get(tenant_id)


tenant_manager = TenantManager()

# ============================================================================
# CONVERSATION ENGINE (Simple but effective)
# ============================================================================

class ConversationEngine:
    """
    Handles conversations with OpenAI
    """
    
    def __init__(self):
        from openai import OpenAI
        self.client = OpenAI(api_key=config.OPENAI_API_KEY) if config.is_openai_configured() else None
        self.conversations: Dict[str, list] = {}  # phone -> messages
    
    def detect_language(self, message: str) -> str:
        """Detect language from message"""
        message_lower = message.lower()
        english_words = ["hi", "hello", "what", "how", "i'm", "book", "thank", "you", "when", "where"]
        italian_words = ["ciao", "buon", "che", "come", "sono", "prenota", "grazie", "puoi", "vorrei"]
        
        english_score = sum(1 for word in english_words if word in message_lower)
        italian_score = sum(1 for word in italian_words if word in message_lower)
        
        return "english" if english_score > italian_score else "italian"
    
    async def process_message(self, phone: str, message: str, tenant: Dict[str, Any]) -> str:
        """
        Process incoming message and generate response
        """
        if not self.client:
            return "Mi dispiace, il servizio non è disponibile al momento. Riprova più tardi."
        
        # Detect language
        language = self.detect_language(message)
        
        # Get or create conversation history
        conv_key = f"{tenant['tenant_id']}:{phone}"
        if conv_key not in self.conversations:
            self.conversations[conv_key] = []
        
        # Build system prompt
        system_prompt = tenant.get(f"system_prompt_{language[:2]}", tenant.get("system_prompt_it", ""))
        
        # Build messages
        messages = [{"role": "system", "content": system_prompt}]
        
        # Add conversation history (last 10 messages)
        for msg in self.conversations[conv_key][-10:]:
            messages.append(msg)
        
        # Add current message
        messages.append({"role": "user", "content": message})
        
        try:
            # Call OpenAI
            response = await asyncio.to_thread(
                self.client.chat.completions.create,
                model=config.OPENAI_MODEL,
                messages=messages,
                temperature=0.8,
                max_tokens=300
            )
            
            ai_response = response.choices[0].message.content.strip()
            
            # Save to conversation history
            self.conversations[conv_key].append({"role": "user", "content": message})
            self.conversations[conv_key].append({"role": "assistant", "content": ai_response})
            
            # Keep history manageable
            if len(self.conversations[conv_key]) > 20:
                self.conversations[conv_key] = self.conversations[conv_key][-20:]
            
            return ai_response
            
        except Exception as e:
            logger.error(f"OpenAI error: {e}")
            if language == "italian":
                return "Mi dispiace, ho avuto un problema tecnico. Puoi riprovare?"
            else:
                return "Sorry, I had a technical issue. Can you try again?"


conversation_engine = ConversationEngine()

# ============================================================================
# WHATSAPP API SERVICE
# ============================================================================

class WhatsAppService:
    """
    Handles WhatsApp Cloud API interactions
    """
    
    @staticmethod
    async def send_message(phone: str, message: str, tenant: Dict[str, Any]) -> bool:
        """
        Send a message via WhatsApp Cloud API
        """
        phone_number_id = tenant.get("whatsapp_phone_number_id")
        access_token = tenant.get("whatsapp_access_token")
        
        if not phone_number_id or not access_token:
            logger.error("WhatsApp not configured for tenant")
            return False
        
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
                    logger.error(f"❌ WhatsApp send failed: {response.status_code} - {response.text}")
                    return False
                    
        except Exception as e:
            logger.error(f"❌ WhatsApp API error: {e}")
            return False
    
    @staticmethod
    async def mark_as_read(message_id: str, tenant: Dict[str, Any]) -> bool:
        """
        Mark a message as read
        """
        phone_number_id = tenant.get("whatsapp_phone_number_id")
        access_token = tenant.get("whatsapp_access_token")
        
        if not phone_number_id or not access_token:
            return False
        
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


whatsapp_service = WhatsAppService()

# ============================================================================
# FASTAPI APPLICATION
# ============================================================================

app = FastAPI(
    title="Lyo WhatsApp Multi-Tenant Server",
    description="Production WhatsApp webhook handler with multi-tenant support for Salon Bella Vita",
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
# WEBHOOK ENDPOINTS
# ============================================================================

@app.get("/webhooks/whatsapp")
async def whatsapp_webhook_verify(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge")
):
    """
    WhatsApp webhook verification endpoint.
    Meta calls this when you configure the webhook URL.
    """
    logger.info(f"🔐 Webhook verification: mode={hub_mode}, token={hub_verify_token}")
    
    if hub_mode == "subscribe":
        if hub_verify_token == config.WHATSAPP_WEBHOOK_VERIFY_TOKEN:
            logger.info("✅ Webhook verified successfully!")
            return PlainTextResponse(content=hub_challenge)
        else:
            logger.warning(f"❌ Invalid verify token: {hub_verify_token}")
            raise HTTPException(status_code=403, detail="Invalid verify token")
    
    raise HTTPException(status_code=400, detail="Invalid request")


@app.post("/webhooks/whatsapp")
async def whatsapp_webhook(request: Request):
    """
    WhatsApp webhook for incoming messages.
    This is called by Meta when a user sends a message.
    """
    try:
        body = await request.json()
        
        logger.info(f"📨 WhatsApp webhook received")
        logger.debug(f"Payload: {json.dumps(body, indent=2)}")
        
        # Validate webhook structure
        if body.get("object") != "whatsapp_business_account":
            logger.info("Ignoring non-WhatsApp webhook")
            return JSONResponse({"status": "ignored"})
        
        # Process each entry
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                
                # Get metadata (contains phone_number_id for tenant identification)
                metadata = value.get("metadata", {})
                phone_number_id = metadata.get("phone_number_id", "")
                
                # Identify tenant
                tenant = tenant_manager.get_tenant_by_phone_number_id(phone_number_id)
                if not tenant:
                    logger.error(f"No tenant for phone_number_id: {phone_number_id}")
                    continue
                
                logger.info(f"📍 Tenant identified: {tenant['business_name']}")
                
                # Process messages
                messages = value.get("messages", [])
                for message in messages:
                    await process_whatsapp_message(message, tenant)
                
                # Process status updates (optional)
                statuses = value.get("statuses", [])
                for status in statuses:
                    await process_whatsapp_status(status, tenant)
        
        return JSONResponse({"status": "processed"})
        
    except Exception as e:
        logger.error(f"❌ Webhook error: {e}", exc_info=True)
        # Return 200 to prevent Meta from retrying
        return JSONResponse({"status": "error", "message": str(e)})


async def process_whatsapp_message(message: Dict[str, Any], tenant: Dict[str, Any]):
    """
    Process an incoming WhatsApp message
    """
    try:
        # Extract message details
        from_number = message.get("from")
        message_id = message.get("id")
        message_type = message.get("type", "text")
        
        logger.info(f"💬 Message from {from_number} (type: {message_type})")
        
        # Mark as read
        await whatsapp_service.mark_as_read(message_id, tenant)
        
        # Handle text messages
        if message_type == "text":
            text = message.get("text", {}).get("body", "")
            
            if not text:
                return
            
            logger.info(f"📝 Message content: {text[:100]}...")
            
            # Process with conversation engine
            response = await conversation_engine.process_message(
                phone=from_number,
                message=text,
                tenant=tenant
            )
            
            # Send response
            await whatsapp_service.send_message(from_number, response, tenant)
        
        # Handle interactive messages (buttons, lists)
        elif message_type == "interactive":
            interactive = message.get("interactive", {})
            interactive_type = interactive.get("type")
            
            if interactive_type == "button_reply":
                button_reply = interactive.get("button_reply", {})
                text = button_reply.get("title", "")
                
                response = await conversation_engine.process_message(
                    phone=from_number,
                    message=text,
                    tenant=tenant
                )
                await whatsapp_service.send_message(from_number, response, tenant)
            
            elif interactive_type == "list_reply":
                list_reply = interactive.get("list_reply", {})
                text = list_reply.get("title", "")
                
                response = await conversation_engine.process_message(
                    phone=from_number,
                    message=text,
                    tenant=tenant
                )
                await whatsapp_service.send_message(from_number, response, tenant)
        
        # Handle other message types
        elif message_type == "image":
            await whatsapp_service.send_message(
                from_number,
                "Grazie per l'immagine! Al momento posso solo rispondere a messaggi di testo. Come posso aiutarti?",
                tenant
            )
        
        elif message_type == "audio":
            await whatsapp_service.send_message(
                from_number,
                "Grazie per il messaggio vocale! Al momento posso solo rispondere a messaggi di testo. Come posso aiutarti?",
                tenant
            )
        
        else:
            logger.info(f"Unsupported message type: {message_type}")
            
    except Exception as e:
        logger.error(f"Error processing message: {e}", exc_info=True)


async def process_whatsapp_status(status: Dict[str, Any], tenant: Dict[str, Any]):
    """
    Process WhatsApp message status updates
    """
    message_id = status.get("id")
    status_type = status.get("status")
    recipient = status.get("recipient_id")
    
    logger.debug(f"📊 Status update: {message_id} -> {status_type} for {recipient}")
    
    # Could track delivery/read status in database


# ============================================================================
# CHATWOOT WEBHOOK (Fallback/Alternative)
# ============================================================================

@app.post("/webhooks/chatwoot")
async def chatwoot_webhook(request: Request):
    """
    Chatwoot webhook for WhatsApp/Instagram messages (alternative integration)
    """
    try:
        data = await request.json()
        
        logger.info(f"📨 Chatwoot webhook: {data.get('event', 'unknown')}")
        
        # Filter incoming messages only
        if data.get("message_type") != "incoming":
            return JSONResponse({"status": "ignored"})
        
        # Extract details
        phone = data.get("contact", {}).get("phone_number", "")
        message = data.get("content", "").strip()
        conversation_id = data.get("conversation", {}).get("id")
        
        if not message or not phone:
            return JSONResponse({"status": "empty"})
        
        # Get default tenant (Chatwoot doesn't provide phone_number_id)
        tenant = tenant_manager.get_tenant("default")
        
        # Process message
        response = await conversation_engine.process_message(
            phone=phone,
            message=message,
            tenant=tenant
        )
        
        # Send response via Chatwoot API
        if config.CHATWOOT_URL and config.CHATWOOT_API_TOKEN:
            await send_chatwoot_response(conversation_id, response)
        
        return JSONResponse({"status": "processed"})
        
    except Exception as e:
        logger.error(f"Chatwoot webhook error: {e}")
        return JSONResponse({"status": "error"})


async def send_chatwoot_response(conversation_id: str, message: str):
    """Send response via Chatwoot API"""
    if not config.CHATWOOT_URL:
        return
    
    url = f"{config.CHATWOOT_URL}/api/v1/accounts/{config.CHATWOOT_ACCOUNT_ID}/conversations/{conversation_id}/messages"
    
    headers = {
        "Api-Access-Token": config.CHATWOOT_API_TOKEN,
        "Content-Type": "application/json"
    }
    
    payload = {
        "content": message,
        "message_type": "outgoing",
        "private": False
    }
    
    try:
        async with httpx.AsyncClient() as client:
            await client.post(url, json=payload, headers=headers, timeout=10)
    except Exception as e:
        logger.error(f"Chatwoot send error: {e}")


# ============================================================================
# HEALTH & API ENDPOINTS
# ============================================================================

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return JSONResponse({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": "2.0.0",
        "service": "lyo-whatsapp-multitenant",
        "business": config.BUSINESS_NAME,
        "configuration": {
            "openai_configured": config.is_openai_configured(),
            "whatsapp_configured": config.is_whatsapp_configured(),
            "webhook_verify_token": "configured" if config.WHATSAPP_WEBHOOK_VERIFY_TOKEN else "missing",
            "tenants_loaded": len(tenant_manager.tenants)
        }
    })


@app.get("/")
async def root():
    """Root endpoint"""
    return JSONResponse({
        "name": "Lyo WhatsApp Multi-Tenant Server",
        "version": "2.0.0",
        "status": "operational",
        "business": config.BUSINESS_NAME,
        "endpoints": {
            "whatsapp_webhook": "/webhooks/whatsapp",
            "chatwoot_webhook": "/webhooks/chatwoot",
            "health": "/health",
            "test_message": "/api/test-message"
        },
        "features": {
            "multi_tenant": True,
            "whatsapp_cloud_api": True,
            "openai_powered": True,
            "conversation_memory": True
        }
    })


@app.post("/api/test-message")
async def test_message(request: Request):
    """
    Test endpoint for sending messages directly
    """
    try:
        data = await request.json()
        phone = data.get("phone", "+39123456789")
        message = data.get("message", "")
        
        if not message.strip():
            raise HTTPException(status_code=400, detail="Empty message")
        
        tenant = tenant_manager.get_tenant("default")
        
        response = await conversation_engine.process_message(
            phone=phone,
            message=message,
            tenant=tenant
        )
        
        return JSONResponse({
            "user_message": message,
            "bot_response": response,
            "phone": phone,
            "tenant": tenant["business_name"]
        })
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# STARTUP
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    
    logger.info("=" * 60)
    logger.info("🚀 STARTING LYO WHATSAPP MULTI-TENANT SERVER")
    logger.info("=" * 60)
    logger.info(f"📍 Business: {config.BUSINESS_NAME}")
    logger.info(f"🤖 OpenAI configured: {config.is_openai_configured()}")
    logger.info(f"📱 WhatsApp configured: {config.is_whatsapp_configured()}")
    logger.info(f"🔐 Webhook verify token: {'✅' if config.WHATSAPP_WEBHOOK_VERIFY_TOKEN else '❌'}")
    logger.info(f"👥 Tenants loaded: {len(tenant_manager.tenants)}")
    logger.info("=" * 60)
    
    uvicorn.run(
        "main_whatsapp:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info"
    )

