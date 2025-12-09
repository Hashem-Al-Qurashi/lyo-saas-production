#!/bin/bash

# SSL Certificate Setup Script using Let's Encrypt

set -e

# Check if domain is provided
if [ -z "$1" ]; then
    echo "Usage: ./setup-ssl.sh your-domain.com [email@example.com]"
    exit 1
fi

DOMAIN=$1
EMAIL=${2:-admin@$DOMAIN}

echo "Setting up SSL certificate for domain: $DOMAIN"
echo "Email for notifications: $EMAIL"

# Check if running as root
if [ "$EUID" -ne 0 ]; then 
    echo "Please run as root"
    exit 1
fi

# Stop nginx temporarily
echo "Stopping nginx..."
docker-compose -f docker-compose.production.yml stop nginx

# Get certificate using certbot
echo "Obtaining SSL certificate..."
docker run -it --rm \
    -v /opt/lyo/nginx/ssl:/etc/letsencrypt \
    -v /opt/lyo/nginx/www:/var/www/certbot \
    -p 80:80 \
    certbot/certbot certonly \
    --standalone \
    --email $EMAIL \
    --agree-tos \
    --no-eff-email \
    --force-renewal \
    -d $DOMAIN \
    -d www.$DOMAIN

# Create symbolic links for nginx
cd /opt/lyo/nginx/ssl
ln -sf live/$DOMAIN/fullchain.pem fullchain.pem
ln -sf live/$DOMAIN/privkey.pem privkey.pem
ln -sf live/$DOMAIN/chain.pem chain.pem

# Update nginx configuration with domain
sed -i "s/server_name _;/server_name $DOMAIN www.$DOMAIN;/g" /opt/lyo/nginx/nginx.conf

# Start nginx
echo "Starting nginx with SSL..."
docker-compose -f docker-compose.production.yml up -d nginx

# Setup auto-renewal cron job
echo "Setting up auto-renewal..."
cat > /etc/cron.d/certbot-renew << EOF
# Renew SSL certificate twice daily
0 */12 * * * root docker run --rm -v /opt/lyo/nginx/ssl:/etc/letsencrypt -v /opt/lyo/nginx/www:/var/www/certbot certbot/certbot renew --quiet && docker-compose -f /opt/lyo/docker-compose.production.yml restart nginx
EOF

echo "SSL setup complete!"
echo ""
echo "Your site is now accessible at:"
echo "  https://$DOMAIN"
echo "  https://www.$DOMAIN"
echo ""
echo "Certificate will auto-renew every 12 hours."
echo ""
echo "To test SSL configuration:"
echo "  curl -I https://$DOMAIN/health"