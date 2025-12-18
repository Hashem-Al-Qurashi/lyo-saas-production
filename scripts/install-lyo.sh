#!/bin/bash
# Lyo SaaS Installation Script for EC2
# Installs and configures Lyo production system

set -e

# Update system
yum update -y

# Install Python 3.9, git, nginx
amazon-linux-extras install python3.8 -y
yum install -y git nginx postgresql15-devel gcc python3-devel

# Install pip
curl https://bootstrap.pypa.io/get-pip.py -o get-pip.py
python3 get-pip.py

# Clone Lyo repository
cd /opt
git clone https://github.com/Hashem-Al-Qurashi/lyo-saas-production.git lyo-saas
cd lyo-saas

# Install Python dependencies
pip3 install -r requirements.txt

# Create environment file
cat > .env << 'EOF'
OPENAI_API_KEY=REPLACE_WITH_OPENAI_API_KEY
WHATSAPP_TOKEN=EAANnWHIj0VkBQOseol46TjgFZADH5GTj9cY45WSZBEGd4qmEOCPFyO4DmQh6bM34k0moGZCgJO1XanNpgKaagPbtSAw8Lc8yq
WHATSAPP_PHONE_ID=961636900357709
WEBHOOK_VERIFY_TOKEN=lyo_verify_2024
EOF

# Configure nginx
cat > /etc/nginx/conf.d/lyo-saas.conf << 'EOF'
server {
    listen 80;
    server_name _;
    
    location / {
        proxy_pass http://127.0.0.1:8005;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    
    location /webhook {
        proxy_pass http://127.0.0.1:8005/webhook;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
    
    location /health {
        proxy_pass http://127.0.0.1:8005/health;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
EOF

# Start and enable nginx
systemctl start nginx
systemctl enable nginx

# Create systemd service for Lyo
cat > /etc/systemd/system/lyo-saas.service << 'EOF'
[Unit]
Description=Lyo SaaS Italian Booking Assistant
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/lyo-saas
Environment=PATH=/usr/local/bin:/usr/bin:/bin
EnvironmentFile=/opt/lyo-saas/.env
ExecStart=/usr/bin/python3 lyo_production.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Start Lyo service
systemctl enable lyo-saas
systemctl start lyo-saas

# Log completion
echo "Lyo SaaS installation completed at $(date)" >> /var/log/lyo-install.log