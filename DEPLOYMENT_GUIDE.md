# Lyo Production Deployment Guide

## Overview

This guide provides comprehensive instructions for deploying the Lyo Italian Booking Assistant to production on your Hetzner server (135.181.249.116).

## Architecture

The production deployment consists of:

- **FastAPI Application**: Main application server (4 workers)
- **PostgreSQL Database**: Customer data and conversation history
- **Redis Cache**: Session management and rate limiting
- **Nginx**: Reverse proxy with SSL termination
- **Docker Compose**: Container orchestration

## Prerequisites

### Server Requirements
- Ubuntu 20.04+ or Debian 11+
- Minimum 4GB RAM
- 20GB+ disk space
- Docker & Docker Compose installed
- Open ports: 80, 443, 22

### Local Requirements
- Docker & Docker Compose
- SSH access to server
- Google Cloud credentials (for Calendar API)
- OpenAI API key
- WhatsApp Business API credentials

## Quick Start

### 1. Clone and Configure

```bash
# Clone the repository
cd /home/sakr_quraish/Projects/italian/lyo-production

# Create .env from template
cp .env.template .env

# Edit .env with your credentials
nano .env
```

### 2. Required Environment Variables

Edit `.env` file with these critical values:

```env
# OpenAI (REQUIRED)
OPENAI_API_KEY=sk-proj-xxxxx

# Database (REQUIRED)
DB_PASSWORD=your_secure_password_here

# Security (REQUIRED - generate random keys)
SECRET_KEY=generate_64_char_random_string
JWT_SECRET_KEY=another_64_char_random_string

# WhatsApp Integration (REQUIRED for WhatsApp)
CHATWOOT_URL=https://your-chatwoot.com
CHATWOOT_API_TOKEN=your_token
WHATSAPP_WEBHOOK_VERIFY_TOKEN=your_verify_token
```

### 3. Google Calendar Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a new project or select existing
3. Enable Google Calendar API
4. Create credentials (OAuth 2.0 Client ID)
5. Download credentials as `credentials.json`
6. Place in project root

### 4. Deploy to Server

```bash
# Make deployment script executable
chmod +x deploy.sh

# Deploy to Hetzner server
./deploy.sh 135.181.249.116 root
```

## Manual Deployment Steps

If automatic deployment fails, follow these manual steps:

### 1. Connect to Server

```bash
ssh root@135.181.249.116
```

### 2. Install Docker

```bash
# Update system
apt update && apt upgrade -y

# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sh get-docker.sh

# Install Docker Compose
apt install docker-compose -y

# Verify installation
docker --version
docker-compose --version
```

### 3. Setup Application Directory

```bash
# Create application directory
mkdir -p /opt/lyo
cd /opt/lyo

# Create necessary directories
mkdir -p logs backups customer_memories nginx/ssl
```

### 4. Transfer Files

From your local machine:

```bash
# Create archive
tar -czf lyo-production.tar.gz \
  Dockerfile.production \
  docker-compose.production.yml \
  requirements.txt \
  app/ \
  nginx/ \
  database/ \
  scripts/ \
  .env \
  credentials.json \
  token.json

# Transfer to server
scp lyo-production.tar.gz root@135.181.249.116:/opt/lyo/

# On server, extract files
ssh root@135.181.249.116
cd /opt/lyo
tar -xzf lyo-production.tar.gz
```

### 5. Build and Start Services

```bash
# Build containers
docker-compose -f docker-compose.production.yml build

# Start services
docker-compose -f docker-compose.production.yml up -d

# Check status
docker-compose -f docker-compose.production.yml ps

# View logs
docker-compose -f docker-compose.production.yml logs -f
```

## SSL Certificate Setup

### Option 1: Let's Encrypt (Recommended)

```bash
# On the server
cd /opt/lyo

# Get SSL certificate
docker-compose -f docker-compose.production.yml run --rm certbot \
  certonly --webroot \
  --webroot-path=/var/www/certbot \
  --email admin@yourdomain.com \
  --agree-tos \
  --no-eff-email \
  -d yourdomain.com \
  -d www.yourdomain.com

# Restart Nginx
docker-compose -f docker-compose.production.yml restart nginx
```

### Option 2: Self-Signed (Development Only)

```bash
cd /opt/lyo/nginx/ssl

# Generate self-signed certificate
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout privkey.pem \
  -out fullchain.pem \
  -subj "/C=IT/ST=Milan/L=Milan/O=Lyo/CN=localhost"

# Copy chain
cp fullchain.pem chain.pem
```

## WhatsApp Integration

### 1. Configure Webhook URL

In your WhatsApp Business API provider:

- Webhook URL: `https://yourdomain.com/webhooks/whatsapp`
- Verify Token: Use value from `.env` file
- Webhook events: Subscribe to `messages` and `message_status`

### 2. Test Webhook

```bash
# Test webhook endpoint
curl -X POST https://yourdomain.com/webhooks/whatsapp \
  -H "Content-Type: application/json" \
  -d '{
    "object": "whatsapp_business_account",
    "entry": [{
      "changes": [{
        "value": {
          "messages": [{
            "from": "393123456789",
            "text": {"body": "Test message"}
          }]
        }
      }]
    }]
  }'
```

## Database Management

### Backup Database

```bash
# Manual backup
docker-compose -f docker-compose.production.yml exec postgres \
  pg_dump -U lyo lyo_production > backup_$(date +%Y%m%d_%H%M%S).sql

# Automated backup (runs daily at 2 AM)
# Already configured in docker-compose.production.yml
```

### Restore Database

```bash
# Restore from backup
docker-compose -f docker-compose.production.yml exec -T postgres \
  psql -U lyo lyo_production < backup_20241209_120000.sql
```

## Monitoring

### Health Check

```bash
# Check application health
curl http://135.181.249.116/health

# Expected response:
{
  "status": "healthy",
  "services": {
    "database": true,
    "redis": true,
    "openai": true,
    "google_calendar": true
  }
}
```

### View Logs

```bash
# All services
docker-compose -f docker-compose.production.yml logs -f

# Specific service
docker-compose -f docker-compose.production.yml logs -f lyo-app

# Last 100 lines
docker-compose -f docker-compose.production.yml logs --tail=100
```

### Metrics

Access Prometheus metrics at:
```
http://135.181.249.116/metrics
```

## Scaling

### Horizontal Scaling

To add more application workers:

```yaml
# In docker-compose.production.yml
lyo-app:
  deploy:
    replicas: 3  # Increase replicas
```

### Vertical Scaling

Increase resources per container:

```yaml
lyo-app:
  deploy:
    resources:
      limits:
        cpus: '2'
        memory: 4G
      reservations:
        cpus: '1'
        memory: 2G
```

## Troubleshooting

### Container Won't Start

```bash
# Check logs
docker-compose -f docker-compose.production.yml logs lyo-app

# Common issues:
# - Missing environment variables
# - Database connection failed
# - Port already in use
```

### Database Connection Issues

```bash
# Test database connection
docker-compose -f docker-compose.production.yml exec postgres \
  psql -U lyo -d lyo_production -c "SELECT 1"

# Reset database
docker-compose -f docker-compose.production.yml down -v
docker-compose -f docker-compose.production.yml up -d
```

### Memory Issues

```bash
# Check memory usage
docker stats

# Restart with memory cleanup
docker system prune -a
docker-compose -f docker-compose.production.yml restart
```

### WhatsApp Webhook Not Working

1. Check webhook logs:
```bash
docker-compose -f docker-compose.production.yml logs -f lyo-app | grep webhook
```

2. Verify SSL certificate:
```bash
curl -I https://yourdomain.com/health
```

3. Test webhook manually (see WhatsApp Integration section)

## Maintenance

### Regular Updates

```bash
# Pull latest code
git pull

# Rebuild and restart
docker-compose -f docker-compose.production.yml build
docker-compose -f docker-compose.production.yml down
docker-compose -f docker-compose.production.yml up -d
```

### Security Updates

```bash
# Update base images
docker-compose -f docker-compose.production.yml pull
docker-compose -f docker-compose.production.yml up -d
```

### Clean Up

```bash
# Remove unused Docker resources
docker system prune -a --volumes

# Clean logs (keep last 7 days)
find /opt/lyo/logs -type f -mtime +7 -delete
```

## Support

For issues or questions:

1. Check logs: `docker-compose logs -f`
2. Verify health: `curl http://server-ip/health`
3. Check this guide's Troubleshooting section
4. Review environment variables in `.env`

## Security Best Practices

1. **Use strong passwords** for database and API keys
2. **Enable firewall** (ufw or iptables)
3. **Regular updates** of Docker images and system packages
4. **SSL/TLS only** for production
5. **Backup regularly** (automated daily backups configured)
6. **Monitor logs** for suspicious activity
7. **Rate limiting** enabled by default
8. **Non-root user** for application container

## Production Checklist

Before going live:

- [ ] SSL certificate installed and working
- [ ] Environment variables configured in `.env`
- [ ] Google Calendar credentials in place
- [ ] WhatsApp webhook configured and tested
- [ ] Database backup schedule verified
- [ ] Health check passing
- [ ] Monitoring setup complete
- [ ] Rate limiting configured
- [ ] Firewall rules configured
- [ ] DNS pointing to server IP

## License

This deployment is configured for production use with the Lyo Italian Booking Assistant.