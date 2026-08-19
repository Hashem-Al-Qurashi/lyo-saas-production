# Dashboard EC2 Deployment Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Deploy the management dashboard on the existing EC2 alongside the bot, fronted by nginx, managed by systemd.

**Architecture:** Nginx on port 80 routes `/manage/*` to the dashboard (port 8002, Python 3.11) and everything else to the bot (port 8000, Python 3.7). Both run as systemd services. The existing nginx.conf template is adapted for direct EC2 (non-Docker) use.

**Tech Stack:** Amazon Linux 2, nginx, systemd, Python 3.7 (bot) + Python 3.11 (dashboard), PostgreSQL (RDS)

**EC2:** 3.239.106.181, SSH key: `~/.ssh/lyo-saas-key.pem`, user: `ec2-user`

---

## Task 1: Install Dashboard Dependencies on EC2

**What:** Install missing Python 3.11 pip packages required by the management app.

**Step 1: Install packages**

```bash
ssh -o IdentitiesOnly=yes -i ~/.ssh/lyo-saas-key.pem ec2-user@3.239.106.181 \
  'python3.11 -m pip install pydantic-settings python-jose[cryptography] passlib[bcrypt] python-multipart itsdangerous jinja2'
```

**Step 2: Verify all imports work**

```bash
ssh -o IdentitiesOnly=yes -i ~/.ssh/lyo-saas-key.pem ec2-user@3.239.106.181 \
  'python3.11 -c "from jose import jwt; from passlib.context import CryptContext; from pydantic_settings import BaseSettings; import jinja2; import multipart; print(\"All imports OK\")"'
```

Expected: `All imports OK`

---

## Task 2: Upload Dashboard Files to EC2

**What:** Upload the `lyo-bot/` directory structure (minimum required files) to EC2.

**Files to upload:**
```
lyo-bot/
├── app/
│   ├── __init__.py
│   ├── config.py
│   ├── models/
│   │   ├── __init__.py
│   │   ├── database.py
│   │   └── schemas.py
│   └── services/
│       ├── __init__.py
│       └── tenant.py
└── management/
    ├── __init__.py
    ├── app.py
    ├── auth.py
    ├── csrf.py
    ├── routes/
    │   ├── __init__.py
    │   ├── appointments.py
    │   ├── calendar.py
    │   ├── hours.py
    │   ├── onboarding.py
    │   ├── operators.py
    │   ├── settings.py
    │   ├── treatments.py
    │   └── users.py
    └── templates/
        ├── appointments.html
        ├── base.html
        ├── calendar.html
        ├── dashboard.html
        ├── hours.html
        ├── login.html
        ├── operators.html
        ├── settings.html
        ├── treatments.html
        └── users.html
```

**Step 1: Create directory structure on EC2**

```bash
ssh -o IdentitiesOnly=yes -i ~/.ssh/lyo-saas-key.pem ec2-user@3.239.106.181 \
  'mkdir -p ~/lyo-bot/app/models ~/lyo-bot/app/services ~/lyo-bot/management/routes ~/lyo-bot/management/templates'
```

**Step 2: Upload files via scp**

```bash
# From local machine:
scp -o IdentitiesOnly=yes -i ~/.ssh/lyo-saas-key.pem -r \
  /home/sakr_quraish/Projects/italian/lyo-bot/app/__init__.py \
  /home/sakr_quraish/Projects/italian/lyo-bot/app/config.py \
  ec2-user@3.239.106.181:~/lyo-bot/app/

scp -o IdentitiesOnly=yes -i ~/.ssh/lyo-saas-key.pem -r \
  /home/sakr_quraish/Projects/italian/lyo-bot/app/models/__init__.py \
  /home/sakr_quraish/Projects/italian/lyo-bot/app/models/database.py \
  /home/sakr_quraish/Projects/italian/lyo-bot/app/models/schemas.py \
  ec2-user@3.239.106.181:~/lyo-bot/app/models/

scp -o IdentitiesOnly=yes -i ~/.ssh/lyo-saas-key.pem -r \
  /home/sakr_quraish/Projects/italian/lyo-bot/app/services/__init__.py \
  /home/sakr_quraish/Projects/italian/lyo-bot/app/services/tenant.py \
  ec2-user@3.239.106.181:~/lyo-bot/app/services/

scp -o IdentitiesOnly=yes -i ~/.ssh/lyo-saas-key.pem -r \
  /home/sakr_quraish/Projects/italian/lyo-bot/management/ \
  ec2-user@3.239.106.181:~/lyo-bot/
```

**Step 3: Create .env symlink in lyo-bot/**

The management app's `app.config` uses `pydantic_settings` which reads from `.env`. Symlink the existing `.env`:

```bash
ssh -o IdentitiesOnly=yes -i ~/.ssh/lyo-saas-key.pem ec2-user@3.239.106.181 \
  'ln -sf /home/ec2-user/.env /home/ec2-user/lyo-bot/.env'
```

**Step 4: Add JWT_SECRET to .env**

```bash
ssh -o IdentitiesOnly=yes -i ~/.ssh/lyo-saas-key.pem ec2-user@3.239.106.181 \
  'grep -q JWT_SECRET ~/.env || echo "JWT_SECRET=$(python3.11 -c \"import secrets; print(secrets.token_hex(32))\")" >> ~/.env'
```

**Step 5: Verify dashboard starts**

```bash
ssh -o IdentitiesOnly=yes -i ~/.ssh/lyo-saas-key.pem ec2-user@3.239.106.181 \
  'cd ~/lyo-bot && set -a && source ~/.env && set +a && timeout 5 python3.11 -m uvicorn management.app:mgmt_app --host 127.0.0.1 --port 8002 2>&1 || true'
```

Expected: Should see `Uvicorn running on http://127.0.0.1:8002` before timeout kills it.

---

## Task 3: Install and Configure Nginx

**What:** Install nginx, write a config that routes `/manage/*` to dashboard and everything else to bot.

**Step 1: Install nginx**

```bash
ssh -o IdentitiesOnly=yes -i ~/.ssh/lyo-saas-key.pem ec2-user@3.239.106.181 \
  'sudo amazon-linux-extras install nginx1 -y'
```

**Step 2: Write nginx config**

```bash
ssh -o IdentitiesOnly=yes -i ~/.ssh/lyo-saas-key.pem ec2-user@3.239.106.181 'sudo tee /etc/nginx/conf.d/lyo.conf > /dev/null << '\''NGINX'\''
# Lyo Bot + Dashboard reverse proxy

# Rate limiting
limit_req_zone $binary_remote_addr zone=api_limit:10m rate=10r/s;
limit_req_zone $binary_remote_addr zone=webhook_limit:10m rate=30r/s;
limit_req_zone $binary_remote_addr zone=mgmt_limit:10m rate=5r/s;

upstream bot_backend {
    server 127.0.0.1:8000;
    keepalive 16;
}

upstream mgmt_backend {
    server 127.0.0.1:8002;
    keepalive 8;
}

server {
    listen 80;
    server_name _;

    # Security headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

    # Gzip
    gzip on;
    gzip_vary on;
    gzip_types text/plain text/css text/javascript application/json application/javascript text/xml;
    gzip_comp_level 6;

    client_max_body_size 10M;

    # --- Management Dashboard ---
    location /manage/ {
        limit_req zone=mgmt_limit burst=10 nodelay;

        proxy_pass http://mgmt_backend;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_connect_timeout 5s;
        proxy_send_timeout 30s;
        proxy_read_timeout 30s;
    }

    # --- Bot webhook (Meta sends here) ---
    location /webhook {
        limit_req zone=webhook_limit burst=50 nodelay;

        proxy_pass http://bot_backend;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_connect_timeout 10s;
        proxy_send_timeout 30s;
        proxy_read_timeout 30s;
    }

    # --- Bot health check ---
    location /health {
        proxy_pass http://bot_backend;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        access_log off;
    }

    # --- Default: bot handles everything else ---
    location / {
        limit_req zone=api_limit burst=20 nodelay;

        proxy_pass http://bot_backend;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_connect_timeout 5s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }
}
NGINX'
```

**Step 3: Remove default nginx config (conflicts on port 80)**

```bash
ssh -o IdentitiesOnly=yes -i ~/.ssh/lyo-saas-key.pem ec2-user@3.239.106.181 \
  'sudo rm -f /etc/nginx/conf.d/default.conf; sudo mv /etc/nginx/nginx.conf /etc/nginx/nginx.conf.bak'
```

Then write a minimal main nginx.conf:

```bash
ssh -o IdentitiesOnly=yes -i ~/.ssh/lyo-saas-key.pem ec2-user@3.239.106.181 'sudo tee /etc/nginx/nginx.conf > /dev/null << '\''CONF'\''
user nginx;
worker_processes auto;
error_log /var/log/nginx/error.log warn;
pid /var/run/nginx.pid;

events {
    worker_connections 1024;
    use epoll;
    multi_accept on;
}

http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;

    log_format main '"$remote_addr" [$time_local] "$request" $status $body_bytes_sent rt=$request_time';
    access_log /var/log/nginx/access.log main;

    sendfile on;
    tcp_nopush on;
    tcp_nodelay on;
    keepalive_timeout 65;

    include /etc/nginx/conf.d/*.conf;
}
CONF'
```

**Step 4: Test nginx config and start**

```bash
ssh -o IdentitiesOnly=yes -i ~/.ssh/lyo-saas-key.pem ec2-user@3.239.106.181 \
  'sudo nginx -t && sudo systemctl start nginx && sudo systemctl enable nginx'
```

Expected: `nginx: configuration file /etc/nginx/nginx.conf test is successful`

---

## Task 4: Create systemd Services

**What:** Replace `nohup` with systemd for both bot and dashboard. Auto-restart on crash, start on boot.

**Step 1: Create bot systemd service**

```bash
ssh -o IdentitiesOnly=yes -i ~/.ssh/lyo-saas-key.pem ec2-user@3.239.106.181 'sudo tee /etc/systemd/system/lyo-bot.service > /dev/null << '\''SVC'\''
[Unit]
Description=Lyo WhatsApp Bot
After=network.target

[Service]
Type=simple
User=ec2-user
WorkingDirectory=/home/ec2-user
EnvironmentFile=/home/ec2-user/.env
ExecStart=/usr/bin/python3 salon_bot_with_booking.py
Restart=always
RestartSec=5
StandardOutput=append:/home/ec2-user/bot.log
StandardError=append:/home/ec2-user/bot.log

[Install]
WantedBy=multi-user.target
SVC'
```

**Step 2: Create dashboard systemd service**

```bash
ssh -o IdentitiesOnly=yes -i ~/.ssh/lyo-saas-key.pem ec2-user@3.239.106.181 'sudo tee /etc/systemd/system/lyo-mgmt.service > /dev/null << '\''SVC'\''
[Unit]
Description=Lyo Management Dashboard
After=network.target

[Service]
Type=simple
User=ec2-user
WorkingDirectory=/home/ec2-user/lyo-bot
EnvironmentFile=/home/ec2-user/.env
ExecStart=/usr/local/bin/python3.11 -m uvicorn management.app:mgmt_app --host 127.0.0.1 --port 8002
Restart=always
RestartSec=5
StandardOutput=append:/home/ec2-user/mgmt.log
StandardError=append:/home/ec2-user/mgmt.log

[Install]
WantedBy=multi-user.target
SVC'
```

**Step 3: Stop the old nohup bot process**

```bash
ssh -o IdentitiesOnly=yes -i ~/.ssh/lyo-saas-key.pem ec2-user@3.239.106.181 \
  'kill $(pgrep -f "salon_bot_with_booking") 2>/dev/null; sleep 2; pgrep -f salon_bot && echo "STILL RUNNING" || echo "Stopped"'
```

**Step 4: Start both services via systemd**

```bash
ssh -o IdentitiesOnly=yes -i ~/.ssh/lyo-saas-key.pem ec2-user@3.239.106.181 \
  'sudo systemctl daemon-reload && sudo systemctl enable lyo-bot lyo-mgmt && sudo systemctl start lyo-bot lyo-mgmt'
```

**Step 5: Verify both services are running**

```bash
ssh -o IdentitiesOnly=yes -i ~/.ssh/lyo-saas-key.pem ec2-user@3.239.106.181 \
  'sudo systemctl status lyo-bot --no-pager -l; echo "---"; sudo systemctl status lyo-mgmt --no-pager -l'
```

Expected: Both show `active (running)`.

---

## Task 5: Open Port 80 and Verify End-to-End

**What:** Update the EC2 security group to allow port 80 traffic. Close direct access to port 8000.

**Step 1: Find the security group**

```bash
ssh -o IdentitiesOnly=yes -i ~/.ssh/lyo-saas-key.pem ec2-user@3.239.106.181 \
  'curl -s http://169.254.169.254/latest/meta-data/security-groups; echo; curl -s http://169.254.169.254/latest/meta-data/network/interfaces/macs/ | head -1'
```

Note: The security group update may need to be done via AWS Console if the CLI isn't configured with the right credentials. Check with:

```bash
ssh -o IdentitiesOnly=yes -i ~/.ssh/lyo-saas-key.pem ec2-user@3.239.106.181 \
  'aws ec2 describe-security-groups --query "SecurityGroups[*].{ID:GroupId,Name:GroupName}" --output table 2>&1 | head -10'
```

If AWS CLI works, open port 80:

```bash
# Get security group ID from instance metadata
SG_ID=$(ssh -o IdentitiesOnly=yes -i ~/.ssh/lyo-saas-key.pem ec2-user@3.239.106.181 \
  'MAC=$(curl -s http://169.254.169.254/latest/meta-data/network/interfaces/macs/ | head -1) && curl -s http://169.254.169.254/latest/meta-data/network/interfaces/macs/${MAC}security-group-ids')

# Add port 80
ssh -o IdentitiesOnly=yes -i ~/.ssh/lyo-saas-key.pem ec2-user@3.239.106.181 \
  "aws ec2 authorize-security-group-ingress --group-id $SG_ID --protocol tcp --port 80 --cidr 0.0.0.0/0"
```

If AWS CLI is NOT available, tell the user:
> Open the AWS Console → EC2 → Security Groups → find the SG for this instance → Add inbound rule: HTTP (port 80) from 0.0.0.0/0.

**Step 2: Test webhook via nginx (port 80)**

```bash
curl -s "http://3.239.106.181/webhook?hub.mode=subscribe&hub.verify_token=lyosaas2024&hub.challenge=nginx_works"
```

Expected: `nginx_works`

**Step 3: Test dashboard via nginx (port 80)**

```bash
curl -s -o /dev/null -w "%{http_code}" "http://3.239.106.181/manage/login"
```

Expected: `200`

**Step 4: Test dashboard login**

```bash
curl -s -c /tmp/cookies -d "email=admin@aura.it&password=admin123" -L "http://3.239.106.181/manage/login" | head -20
```

Expected: Redirects to `/manage/dashboard` and returns HTML.

**Step 5: Update Meta webhook URL (if needed)**

If Meta currently sends webhooks to `http://3.239.106.181:8000/webhook`, update to `http://3.239.106.181/webhook` (port 80). Check current webhook config in the Meta App Dashboard (App ID: 1119346830211416).

Note: Meta requires HTTPS for production webhooks. The current setup works because the ALB or Meta config may already handle this. Verify before changing.

---

## Task 6: Verify Everything Survives a Reboot

**What:** Simulate a reboot to confirm systemd starts all services automatically.

**Step 1: Reboot and wait**

```bash
ssh -o IdentitiesOnly=yes -i ~/.ssh/lyo-saas-key.pem ec2-user@3.239.106.181 'sudo reboot'
# Wait 60 seconds
sleep 60
```

**Step 2: Verify all services came back**

```bash
ssh -o IdentitiesOnly=yes -i ~/.ssh/lyo-saas-key.pem ec2-user@3.239.106.181 \
  'sudo systemctl status lyo-bot lyo-mgmt nginx --no-pager | grep Active'
```

Expected: All three show `active (running)`.

**Step 3: Final smoke test**

```bash
# Bot webhook
curl -s "http://3.239.106.181/webhook?hub.mode=subscribe&hub.verify_token=lyosaas2024&hub.challenge=reboot_test"

# Dashboard login page
curl -s -o /dev/null -w "%{http_code}" "http://3.239.106.181/manage/login"
```

Expected: `reboot_test` and `200`.

---

## Verification Checklist

- [ ] `python3.11 -m pip list` shows all required packages
- [ ] `~/lyo-bot/` directory structure exists on EC2
- [ ] `systemctl status lyo-bot` → active (running)
- [ ] `systemctl status lyo-mgmt` → active (running)
- [ ] `systemctl status nginx` → active (running)
- [ ] `curl http://3.239.106.181/webhook?hub.mode=subscribe&hub.verify_token=lyosaas2024&hub.challenge=test` → `test`
- [ ] `curl http://3.239.106.181/manage/login` → 200 with login HTML
- [ ] Dashboard login with admin@aura.it / admin123 works
- [ ] Services survive `sudo reboot`
- [ ] WhatsApp bot responds to messages (send test via WhatsApp)
