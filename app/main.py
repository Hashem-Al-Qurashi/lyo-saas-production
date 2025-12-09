"""
Lyo Production Main Application
Enterprise-grade WhatsApp/Instagram booking assistant
"""

import os
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Dict, Any, Optional

from fastapi import FastAPI, Request, HTTPException, Depends, BackgroundTasks
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from fastapi.responses import Response

from app.core.config import settings
from app.core.lyo_engine import LyoEngine
from app.core.memory import MemoryManager
from app.api import webhooks, chat, admin
from app.utils.logging import setup_logging

# Setup structured logging
logger = setup_logging()

# Metrics
request_counter = Counter('lyo_requests_total', 'Total requests', ['method', 'endpoint', 'status'])
request_duration = Histogram('lyo_request_duration_seconds', 'Request duration', ['method', 'endpoint'])
message_counter = Counter('lyo_messages_processed', 'Messages processed', ['platform', 'language'])

# Rate limiter
limiter = Limiter(key_func=get_remote_address)

# Global instances
lyo_engine: Optional[LyoEngine] = None
memory_manager: Optional[MemoryManager] = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifecycle manager
    """
    global lyo_engine, memory_manager
    
    logger.info("Starting Lyo Production System", version=settings.VERSION)
    
    try:
        # Initialize memory manager
        memory_manager = MemoryManager(
            redis_url=settings.REDIS_URL,
            postgres_url=settings.DATABASE_URL
        )
        await memory_manager.initialize()
        logger.info("Memory manager initialized")
        
        # Initialize Lyo engine
        lyo_engine = LyoEngine(
            openai_api_key=settings.OPENAI_API_KEY,
            memory_manager=memory_manager,
            business_id=settings.BUSINESS_ID
        )
        await lyo_engine.initialize()
        logger.info("Lyo engine initialized")
        
        # Load business configurations
        await lyo_engine.load_business_config()
        logger.info(f"Business config loaded: {settings.BUSINESS_ID}")
        
        logger.info("Lyo Production System Ready", 
                   environment=settings.ENVIRONMENT,
                   business_id=settings.BUSINESS_ID)
        
        yield
        
    except Exception as e:
        logger.error(f"Startup failed: {e}", exc_info=True)
        raise
    
    finally:
        # Cleanup
        logger.info("Shutting down Lyo Production System")
        if memory_manager:
            await memory_manager.close()
        logger.info("Shutdown complete")

# Create FastAPI app
app = FastAPI(
    title="Lyo Production API",
    description="Enterprise WhatsApp/Instagram Booking Assistant",
    version=settings.VERSION,
    lifespan=lifespan,
    docs_url="/api/docs" if settings.DEBUG else None,
    redoc_url="/api/redoc" if settings.DEBUG else None,
    openapi_url="/api/openapi.json" if settings.DEBUG else None
)

# Add middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=settings.ALLOWED_HOSTS
)

# Add rate limit error handler
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Include routers
app.include_router(webhooks.router, prefix="/webhooks", tags=["webhooks"])
app.include_router(chat.router, prefix="/api", tags=["chat"])
app.include_router(admin.router, prefix="/admin", tags=["admin"])

@app.get("/health")
async def health_check():
    """
    Health check endpoint
    """
    health_status = {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "version": settings.VERSION,
        "environment": settings.ENVIRONMENT,
        "services": {
            "database": False,
            "redis": False,
            "openai": False,
            "google_calendar": False
        }
    }
    
    # Check services
    if memory_manager:
        health_status["services"]["database"] = await memory_manager.check_postgres_health()
        health_status["services"]["redis"] = await memory_manager.check_redis_health()
    
    if lyo_engine:
        health_status["services"]["openai"] = await lyo_engine.check_openai_health()
        health_status["services"]["google_calendar"] = await lyo_engine.check_calendar_health()
    
    # Determine overall health
    all_healthy = all(health_status["services"].values())
    if not all_healthy:
        health_status["status"] = "degraded"
        return JSONResponse(health_status, status_code=503)
    
    return JSONResponse(health_status)

@app.get("/metrics")
async def metrics():
    """
    Prometheus metrics endpoint
    """
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.get("/")
async def root():
    """
    Root endpoint - returns API information
    """
    return {
        "name": "Lyo Production API",
        "version": settings.VERSION,
        "status": "operational",
        "documentation": "/api/docs" if settings.DEBUG else "Disabled in production",
        "health": "/health"
    }

@app.get("/demo")
async def demo_interface():
    """
    Demo chat interface
    """
    if not settings.DEBUG:
        raise HTTPException(status_code=404, detail="Demo not available in production")
    
    return HTMLResponse("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Lyo Chat Demo</title>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                height: 100vh;
                display: flex;
                justify-content: center;
                align-items: center;
            }
            .chat-container {
                width: 100%;
                max-width: 500px;
                height: 90vh;
                max-height: 700px;
                background: white;
                border-radius: 20px;
                box-shadow: 0 20px 60px rgba(0,0,0,0.3);
                display: flex;
                flex-direction: column;
                overflow: hidden;
            }
            .chat-header {
                background: linear-gradient(90deg, #667eea, #764ba2);
                color: white;
                padding: 20px;
                display: flex;
                align-items: center;
                gap: 15px;
            }
            .chat-avatar {
                width: 50px;
                height: 50px;
                background: white;
                border-radius: 50%;
                display: flex;
                align-items: center;
                justify-content: center;
                font-size: 24px;
            }
            .chat-info h2 {
                font-size: 20px;
                margin-bottom: 5px;
            }
            .chat-info .status {
                font-size: 14px;
                opacity: 0.9;
            }
            .chat-messages {
                flex: 1;
                overflow-y: auto;
                padding: 20px;
                background: #f8f9fa;
            }
            .message {
                margin-bottom: 15px;
                display: flex;
                animation: slideIn 0.3s ease;
            }
            @keyframes slideIn {
                from { opacity: 0; transform: translateY(10px); }
                to { opacity: 1; transform: translateY(0); }
            }
            .message.user {
                justify-content: flex-end;
            }
            .message-bubble {
                max-width: 70%;
                padding: 12px 18px;
                border-radius: 18px;
                word-wrap: break-word;
            }
            .message.user .message-bubble {
                background: linear-gradient(90deg, #667eea, #764ba2);
                color: white;
                border-bottom-right-radius: 4px;
            }
            .message.assistant .message-bubble {
                background: white;
                color: #333;
                border: 1px solid #e0e0e0;
                border-bottom-left-radius: 4px;
            }
            .message-time {
                font-size: 11px;
                color: #999;
                margin-top: 5px;
            }
            .chat-input {
                padding: 20px;
                background: white;
                border-top: 1px solid #e0e0e0;
                display: flex;
                gap: 10px;
            }
            .chat-input input {
                flex: 1;
                padding: 12px 18px;
                border: 1px solid #e0e0e0;
                border-radius: 25px;
                font-size: 16px;
                outline: none;
                transition: border-color 0.3s;
            }
            .chat-input input:focus {
                border-color: #667eea;
            }
            .chat-input button {
                padding: 12px 24px;
                background: linear-gradient(90deg, #667eea, #764ba2);
                color: white;
                border: none;
                border-radius: 25px;
                font-size: 16px;
                cursor: pointer;
                transition: transform 0.2s;
            }
            .chat-input button:hover {
                transform: scale(1.05);
            }
            .chat-input button:disabled {
                opacity: 0.5;
                cursor: not-allowed;
            }
            .typing-indicator {
                display: none;
                padding: 20px;
                color: #999;
                font-style: italic;
            }
            .typing-indicator.active {
                display: block;
            }
            .system-message {
                text-align: center;
                color: #999;
                font-size: 14px;
                margin: 10px 0;
                padding: 8px;
                background: rgba(103, 126, 234, 0.1);
                border-radius: 10px;
            }
        </style>
    </head>
    <body>
        <div class="chat-container">
            <div class="chat-header">
                <div class="chat-avatar">🤖</div>
                <div class="chat-info">
                    <h2>Lyo Assistant</h2>
                    <div class="status">Online • Ready to help</div>
                </div>
            </div>
            
            <div class="chat-messages" id="chatMessages">
                <div class="system-message">
                    Welcome! I'm Lyo, your booking assistant. How can I help you today?
                </div>
            </div>
            
            <div class="typing-indicator" id="typingIndicator">
                Lyo is typing...
            </div>
            
            <div class="chat-input">
                <input 
                    type="text" 
                    id="messageInput" 
                    placeholder="Type your message..." 
                    onkeypress="handleKeyPress(event)"
                    autocomplete="off"
                />
                <button onclick="sendMessage()" id="sendButton">Send</button>
            </div>
        </div>

        <script>
            const chatMessages = document.getElementById('chatMessages');
            const messageInput = document.getElementById('messageInput');
            const typingIndicator = document.getElementById('typingIndicator');
            const sendButton = document.getElementById('sendButton');
            
            // Test phone number for demo
            const testPhone = '+39' + Math.floor(Math.random() * 1000000000);
            
            function handleKeyPress(event) {
                if (event.key === 'Enter' && !event.shiftKey) {
                    event.preventDefault();
                    sendMessage();
                }
            }
            
            function addMessage(content, isUser = false) {
                const messageDiv = document.createElement('div');
                messageDiv.className = `message ${isUser ? 'user' : 'assistant'}`;
                
                const bubble = document.createElement('div');
                bubble.className = 'message-bubble';
                bubble.textContent = content;
                
                messageDiv.appendChild(bubble);
                chatMessages.appendChild(messageDiv);
                
                // Add timestamp
                const time = document.createElement('div');
                time.className = 'message-time';
                time.textContent = new Date().toLocaleTimeString('en-US', { 
                    hour: '2-digit', 
                    minute: '2-digit' 
                });
                bubble.appendChild(time);
                
                chatMessages.scrollTop = chatMessages.scrollHeight;
            }
            
            function addSystemMessage(content) {
                const messageDiv = document.createElement('div');
                messageDiv.className = 'system-message';
                messageDiv.textContent = content;
                chatMessages.appendChild(messageDiv);
                chatMessages.scrollTop = chatMessages.scrollHeight;
            }
            
            async function sendMessage() {
                const message = messageInput.value.trim();
                if (!message) return;
                
                // Disable input
                messageInput.value = '';
                sendButton.disabled = true;
                
                // Add user message
                addMessage(message, true);
                
                // Show typing indicator
                typingIndicator.classList.add('active');
                
                try {
                    const response = await fetch('/api/chat', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                        },
                        body: JSON.stringify({
                            phone: testPhone,
                            message: message
                        })
                    });
                    
                    const data = await response.json();
                    
                    // Hide typing indicator
                    typingIndicator.classList.remove('active');
                    
                    if (data.response) {
                        addMessage(data.response);
                        
                        // Show system info if relevant
                        if (data.customer_name) {
                            addSystemMessage(`Remembered: ${data.customer_name} (${data.language})`);
                        }
                    } else if (data.error) {
                        addSystemMessage(`Error: ${data.error}`);
                    }
                    
                } catch (error) {
                    typingIndicator.classList.remove('active');
                    addSystemMessage(`Connection error: ${error.message}`);
                } finally {
                    sendButton.disabled = false;
                    messageInput.focus();
                }
            }
            
            // Focus input on load
            messageInput.focus();
        </script>
    </body>
    </html>
    """)

# Error handlers
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """
    Handle HTTP exceptions
    """
    logger.warning(f"HTTP {exc.status_code}: {exc.detail}", 
                  path=request.url.path,
                  method=request.method)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail, "status_code": exc.status_code}
    )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """
    Handle general exceptions
    """
    logger.error(f"Unhandled exception: {exc}", 
                exc_info=True,
                path=request.url.path,
                method=request.method)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "status_code": 500}
    )