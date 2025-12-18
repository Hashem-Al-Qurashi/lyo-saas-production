#!/usr/bin/env python3
"""
Test webhook on EC2 instance
"""
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
import uvicorn
import httpx
import os

app = FastAPI(title="Lyo AWS Test")

# Configuration
DATABASE_URL = "postgresql://lyoadmin:LyoSaaS2024Enterprise!@lyo-enterprise-database.cixc4kiw6r00.us-east-1.rds.amazonaws.com:5432/lyo_production"
OPENAI_API_KEY = "REPLACE_WITH_OPENAI_API_KEY"

@app.get("/")
@app.get("/health")
async def health():
    # Test database connection
    try:
        import psycopg2
        conn = psycopg2.connect(DATABASE_URL)
        conn.close()
        db_status = "connected"
    except Exception as e:
        db_status = f"error: {str(e)}"
    
    return {
        "status": "healthy",
        "service": "Lyo AWS Enterprise",
        "database": db_status,
        "load_balancer": "configured",
        "webhook": "ready"
    }

@app.get("/webhook")
async def verify_webhook(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    
    if mode == "subscribe" and token == "lyo_verify_2024":
        return PlainTextResponse(challenge or "OK")
    return PlainTextResponse("Forbidden")

@app.post("/webhook")
async def handle_webhook(request: Request):
    try:
        data = await request.json()
        print(f"🔍 AWS Enterprise Webhook received: {data}")
        
        # Simple AI response test
        response = "Ciao! Benvenuto al Salon Bella Vita Enterprise! Come posso aiutarti?"
        
        return {"status": "processed", "service": "aws_enterprise"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    print("🚀 Starting Lyo AWS Enterprise webhook...")
    uvicorn.run(app, host="0.0.0.0", port=8000)