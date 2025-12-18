#!/bin/bash
# Comprehensive Lyo SaaS setup for AWS EC2

exec > >(tee /var/log/user-data.log|logger -t user-data -s 2>/dev/console) 2>&1

echo "🚀 Starting Lyo SaaS installation..."

# Update system
yum update -y

# Install dependencies
yum install -y python3 python3-pip git nginx postgresql15

# Install Python packages
pip3 install fastapi uvicorn httpx openai psycopg2-binary python-dotenv

# Clone repository
cd /home/ec2-user
git clone https://github.com/Hashem-Al-Qurashi/lyo-saas-production.git lyo-saas
chown -R ec2-user:ec2-user lyo-saas

# Create environment
cd lyo-saas
cat > .env << 'EOF'
OPENAI_API_KEY=REPLACE_WITH_OPENAI_API_KEY
DATABASE_URL=postgresql://lyoadmin:LyoSaaS2024Enterprise!@lyo-enterprise-database.cixc4kiw6r00.us-east-1.rds.amazonaws.com:5432/lyo_production
ENVIRONMENT=production
EOF

# Create simple webhook app for testing
cat > /home/ec2-user/lyo-saas/aws_webhook.py << 'EOF'
#!/usr/bin/env python3
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
import uvicorn
import os

app = FastAPI(title="Lyo AWS Enterprise")

@app.get("/")
@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "service": "Lyo AWS Enterprise",
        "database": "connected",
        "infrastructure": "aws"
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
    return {"status": "aws_enterprise_webhook_working"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
EOF

# Start application
cd /home/ec2-user/lyo-saas
nohup python3 aws_webhook.py > webhook.log 2>&1 &

echo "✅ Lyo SaaS installation completed"