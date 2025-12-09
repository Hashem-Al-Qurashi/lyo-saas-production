#!/bin/bash

# Lyo Production Deployment Script
# This script deploys the Lyo system to a production server

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
SERVER_IP="${1:-135.181.249.116}"
SERVER_USER="${2:-root}"
PROJECT_NAME="lyo-production"
REMOTE_PATH="/opt/lyo"

echo -e "${GREEN}==================================${NC}"
echo -e "${GREEN}  Lyo Production Deployment${NC}"
echo -e "${GREEN}==================================${NC}"

# Function to check command availability
check_command() {
    if ! command -v $1 &> /dev/null; then
        echo -e "${RED}Error: $1 is not installed${NC}"
        exit 1
    fi
}

# Check required commands
echo -e "${YELLOW}Checking requirements...${NC}"
check_command docker
check_command docker-compose
check_command ssh
check_command rsync

# Check if .env file exists
if [ ! -f .env ]; then
    echo -e "${RED}Error: .env file not found${NC}"
    echo "Please create .env file from .env.template"
    exit 1
fi

# Check if credentials files exist
if [ ! -f credentials.json ]; then
    echo -e "${YELLOW}Warning: credentials.json not found${NC}"
    echo "Google Calendar integration will not work without it"
fi

echo -e "${GREEN}Requirements check passed${NC}"

# Build Docker image locally
echo -e "${YELLOW}Building Docker image...${NC}"
docker build -f Dockerfile.production -t lyo-production:latest .

# Save Docker image
echo -e "${YELLOW}Saving Docker image...${NC}"
docker save lyo-production:latest | gzip > lyo-production.tar.gz

# Create deployment package
echo -e "${YELLOW}Creating deployment package...${NC}"
mkdir -p deployment_package
cp -r \
    docker-compose.production.yml \
    .env \
    nginx/ \
    database/ \
    scripts/ \
    deployment_package/

# Add credentials if they exist
[ -f credentials.json ] && cp credentials.json deployment_package/
[ -f token.json ] && cp token.json deployment_package/

# Create deployment archive
tar -czf deployment.tar.gz deployment_package/ lyo-production.tar.gz

echo -e "${GREEN}Deployment package created${NC}"

# Deploy to server
echo -e "${YELLOW}Deploying to server ${SERVER_IP}...${NC}"

# Copy deployment package
echo "Copying files to server..."
scp deployment.tar.gz ${SERVER_USER}@${SERVER_IP}:/tmp/

# Execute deployment on server
echo "Executing deployment..."
ssh ${SERVER_USER}@${SERVER_IP} << 'ENDSSH'
set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}Setting up Lyo on server...${NC}"

# Create application directory
mkdir -p /opt/lyo
cd /opt/lyo

# Extract deployment package
echo "Extracting deployment package..."
tar -xzf /tmp/deployment.tar.gz

# Move files to correct locations
mv deployment_package/* .
rm -rf deployment_package

# Load Docker image
echo "Loading Docker image..."
gunzip -c lyo-production.tar.gz | docker load
rm lyo-production.tar.gz

# Create necessary directories
mkdir -p logs backups customer_memories

# Set permissions
chmod 600 .env
[ -f credentials.json ] && chmod 600 credentials.json
[ -f token.json ] && chmod 600 token.json

# Stop existing containers
echo "Stopping existing containers..."
docker-compose -f docker-compose.production.yml down || true

# Start new containers
echo "Starting containers..."
docker-compose -f docker-compose.production.yml up -d

# Wait for services to be healthy
echo "Waiting for services to be healthy..."
sleep 10

# Check health
if docker-compose -f docker-compose.production.yml exec lyo-app curl -f http://localhost:8000/health > /dev/null 2>&1; then
    echo -e "${GREEN}Deployment successful!${NC}"
else
    echo -e "${RED}Health check failed${NC}"
    docker-compose -f docker-compose.production.yml logs --tail=50
    exit 1
fi

# Show status
docker-compose -f docker-compose.production.yml ps

echo -e "${GREEN}==================================${NC}"
echo -e "${GREEN}  Deployment Complete!${NC}"
echo -e "${GREEN}==================================${NC}"
echo ""
echo "Access points:"
echo "  - API: http://${SERVER_IP}"
echo "  - Health: http://${SERVER_IP}/health"
echo "  - Metrics: http://${SERVER_IP}/metrics"
echo ""
echo "To view logs:"
echo "  docker-compose -f docker-compose.production.yml logs -f"
echo ""

ENDSSH

# Cleanup local files
rm -rf deployment_package deployment.tar.gz

echo -e "${GREEN}==================================${NC}"
echo -e "${GREEN}  Local Deployment Complete!${NC}"
echo -e "${GREEN}==================================${NC}"

echo ""
echo "Next steps:"
echo "1. Set up SSL certificates:"
echo "   ssh ${SERVER_USER}@${SERVER_IP}"
echo "   cd /opt/lyo"
echo "   ./scripts/setup-ssl.sh your-domain.com"
echo ""
echo "2. Configure WhatsApp webhook:"
echo "   Webhook URL: https://your-domain.com/webhooks/whatsapp"
echo "   Verify Token: (from .env file)"
echo ""
echo "3. Monitor the system:"
echo "   ssh ${SERVER_USER}@${SERVER_IP}"
echo "   cd /opt/lyo"
echo "   docker-compose -f docker-compose.production.yml logs -f"