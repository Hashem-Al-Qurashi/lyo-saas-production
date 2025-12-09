"""
Webhook handlers for WhatsApp and other platforms
"""

import hashlib
import hmac
import json
from typing import Dict, Any, Optional
from datetime import datetime

from fastapi import APIRouter, Request, HTTPException, Header, Query
from fastapi.responses import JSONResponse, PlainTextResponse
import logging

from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

@router.get("/whatsapp")
async def whatsapp_webhook_verify(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge")
):
    """
    WhatsApp webhook verification endpoint
    This is called by WhatsApp when setting up the webhook
    """
    logger.info(f"WhatsApp webhook verification request: mode={hub_mode}")
    
    if hub_mode == "subscribe":
        if hub_verify_token == settings.WHATSAPP_WEBHOOK_VERIFY_TOKEN:
            logger.info("WhatsApp webhook verified successfully")
            return PlainTextResponse(content=hub_challenge)
        else:
            logger.warning(f"Invalid verify token: {hub_verify_token}")
            raise HTTPException(status_code=403, detail="Invalid verify token")
    
    raise HTTPException(status_code=400, detail="Invalid request")

@router.post("/whatsapp")
async def whatsapp_webhook(request: Request):
    """
    WhatsApp webhook for incoming messages
    Handles messages from WhatsApp Business API
    """
    try:
        # Get request body
        body = await request.json()
        
        logger.info(f"WhatsApp webhook received: {json.dumps(body, indent=2)}")
        
        # Validate webhook structure
        if body.get("object") != "whatsapp_business_account":
            return JSONResponse({"status": "ignored"})
        
        # Process each entry
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                
                # Process messages
                messages = value.get("messages", [])
                for message in messages:
                    await process_whatsapp_message(message, value.get("metadata", {}))
                
                # Process status updates
                statuses = value.get("statuses", [])
                for status in statuses:
                    await process_whatsapp_status(status)
        
        return JSONResponse({"status": "processed"})
        
    except Exception as e:
        logger.error(f"WhatsApp webhook error: {e}", exc_info=True)
        # Return 200 to prevent WhatsApp from retrying
        return JSONResponse({"status": "error", "message": str(e)})

async def process_whatsapp_message(message: Dict[str, Any], metadata: Dict[str, Any]):
    """
    Process incoming WhatsApp message
    """
    try:
        from app.main import lyo_engine
        
        # Extract message details
        from_number = message.get("from")
        message_id = message.get("id")
        timestamp = message.get("timestamp")
        
        # Handle different message types
        message_type = message.get("type", "text")
        
        if message_type == "text":
            text = message.get("text", {}).get("body", "")
            
            if not text:
                return
            
            logger.info(f"Processing WhatsApp message from {from_number}: {text[:100]}")
            
            # Process with Lyo engine
            if lyo_engine:
                result = await lyo_engine.process_message(
                    phone=from_number,
                    message=text,
                    platform="whatsapp",
                    message_id=message_id
                )
                
                # Send response back via WhatsApp API
                await send_whatsapp_reply(from_number, result.get("response", ""))
                
                # Track metrics
                from app.main import message_counter
                message_counter.labels(
                    platform="whatsapp",
                    language=result.get("language", "unknown")
                ).inc()
        
        elif message_type == "interactive":
            # Handle interactive messages (buttons, lists)
            interactive = message.get("interactive", {})
            await handle_interactive_message(from_number, interactive)
        
        elif message_type == "image":
            # Handle image messages
            image = message.get("image", {})
            logger.info(f"Received image from {from_number}: {image.get('id')}")
            # Could process images for business cards, menus, etc.
        
        else:
            logger.info(f"Unsupported message type from {from_number}: {message_type}")
            
    except Exception as e:
        logger.error(f"Error processing WhatsApp message: {e}", exc_info=True)

async def process_whatsapp_status(status: Dict[str, Any]):
    """
    Process WhatsApp message status updates
    """
    message_id = status.get("id")
    status_type = status.get("status")
    timestamp = status.get("timestamp")
    recipient = status.get("recipient_id")
    
    logger.info(f"WhatsApp status update: {message_id} - {status_type} for {recipient}")
    
    # Track delivery metrics
    # Could update database with delivery status

async def send_whatsapp_reply(phone: str, message: str):
    """
    Send reply message via WhatsApp Business API
    """
    try:
        # This would integrate with your WhatsApp Business API provider
        # Example: Twilio, MessageBird, WhatsApp Cloud API, etc.
        
        logger.info(f"Sending WhatsApp reply to {phone}: {message[:100]}")
        
        # Placeholder for actual API call
        # Example with WhatsApp Cloud API:
        # response = await httpx.post(
        #     f"https://graph.facebook.com/v17.0/{phone_number_id}/messages",
        #     headers={"Authorization": f"Bearer {access_token}"},
        #     json={
        #         "messaging_product": "whatsapp",
        #         "to": phone,
        #         "type": "text",
        #         "text": {"body": message}
        #     }
        # )
        
        return True
        
    except Exception as e:
        logger.error(f"Failed to send WhatsApp message: {e}")
        return False

async def handle_interactive_message(phone: str, interactive: Dict[str, Any]):
    """
    Handle interactive WhatsApp messages (buttons, lists)
    """
    interactive_type = interactive.get("type")
    
    if interactive_type == "button_reply":
        button_reply = interactive.get("button_reply", {})
        button_id = button_reply.get("id")
        button_title = button_reply.get("title")
        
        logger.info(f"Button clicked by {phone}: {button_id} - {button_title}")
        
        # Process button action
        # Could trigger specific flows based on button_id
        
    elif interactive_type == "list_reply":
        list_reply = interactive.get("list_reply", {})
        item_id = list_reply.get("id")
        item_title = list_reply.get("title")
        
        logger.info(f"List item selected by {phone}: {item_id} - {item_title}")
        
        # Process list selection
        # Could handle service selection, time slot selection, etc.

@router.post("/chatwoot")
async def chatwoot_webhook(request: Request):
    """
    Chatwoot webhook for WhatsApp/Instagram messages
    """
    try:
        body = await request.json()
        
        logger.info(f"Chatwoot webhook received: {body.get('event')}")
        
        # Process based on event type
        event = body.get("event")
        
        if event == "message_created":
            await process_chatwoot_message(body)
        elif event == "conversation_status_changed":
            await process_chatwoot_status(body)
        
        return JSONResponse({"status": "processed"})
        
    except Exception as e:
        logger.error(f"Chatwoot webhook error: {e}", exc_info=True)
        return JSONResponse({"status": "error", "message": str(e)})

async def process_chatwoot_message(data: Dict[str, Any]):
    """
    Process incoming Chatwoot message
    """
    try:
        from app.main import lyo_engine
        
        # Skip if not incoming message
        if data.get("message_type") != "incoming":
            return
        
        # Extract details
        content = data.get("content", "")
        conversation = data.get("conversation", {})
        contact = conversation.get("meta", {}).get("sender", {})
        
        phone = contact.get("phone_number", "")
        channel = conversation.get("channel", "")
        
        if not content or not phone:
            return
        
        logger.info(f"Processing Chatwoot message from {phone} ({channel}): {content[:100]}")
        
        # Process with Lyo
        if lyo_engine:
            result = await lyo_engine.process_message(
                phone=phone,
                message=content,
                platform=channel.lower(),
                message_id=str(data.get("id"))
            )
            
            # Send response back through Chatwoot
            await send_chatwoot_reply(
                conversation_id=conversation.get("id"),
                message=result.get("response", "")
            )
            
    except Exception as e:
        logger.error(f"Error processing Chatwoot message: {e}", exc_info=True)

async def process_chatwoot_status(data: Dict[str, Any]):
    """
    Process Chatwoot conversation status changes
    """
    status = data.get("status")
    conversation_id = data.get("id")
    
    logger.info(f"Chatwoot conversation {conversation_id} status changed to: {status}")

async def send_chatwoot_reply(conversation_id: int, message: str):
    """
    Send reply through Chatwoot API
    """
    try:
        import httpx
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{settings.CHATWOOT_URL}/api/v1/accounts/{settings.CHATWOOT_ACCOUNT_ID}/conversations/{conversation_id}/messages",
                headers={"api_access_token": settings.CHATWOOT_API_TOKEN},
                json={
                    "content": message,
                    "message_type": "outgoing",
                    "private": False
                }
            )
            
            if response.status_code == 200:
                logger.info(f"Chatwoot reply sent to conversation {conversation_id}")
                return True
            else:
                logger.error(f"Failed to send Chatwoot reply: {response.text}")
                return False
                
    except Exception as e:
        logger.error(f"Error sending Chatwoot reply: {e}")
        return False

@router.post("/instagram")
async def instagram_webhook(request: Request):
    """
    Instagram webhook for DM messages
    """
    try:
        body = await request.json()
        
        logger.info(f"Instagram webhook received")
        
        # Process Instagram-specific webhook format
        # This would be customized based on your Instagram API integration
        
        return JSONResponse({"status": "processed"})
        
    except Exception as e:
        logger.error(f"Instagram webhook error: {e}", exc_info=True)
        return JSONResponse({"status": "error", "message": str(e)})