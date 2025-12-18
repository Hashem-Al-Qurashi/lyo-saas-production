#!/bin/bash
# ============================================================================
# DEPLOY LYO WHATSAPP MULTI-TENANT SERVER TO EC2
# ============================================================================

set -e

echo "🚀 Deploying Lyo WhatsApp Multi-Tenant Server"
echo "=============================================="

# Configuration
EC2_INSTANCE_ID="i-0591c0c366e6eb61c"  # lyo-enterprise-final
EC2_REGION="us-east-1"

# Check AWS CLI
if ! command -v aws &> /dev/null; then
    echo "❌ AWS CLI not found. Please install it."
    exit 1
fi

# Get EC2 public IP/DNS
echo "📍 Getting EC2 instance details..."
EC2_PUBLIC_DNS=$(aws ec2 describe-instances \
    --instance-ids $EC2_INSTANCE_ID \
    --region $EC2_REGION \
    --query 'Reservations[0].Instances[0].PublicDnsName' \
    --output text)

if [ "$EC2_PUBLIC_DNS" == "None" ] || [ -z "$EC2_PUBLIC_DNS" ]; then
    echo "❌ Could not get EC2 public DNS. Instance might be stopped."
    exit 1
fi

echo "✅ EC2 DNS: $EC2_PUBLIC_DNS"

# Create deployment package
echo "📦 Creating deployment package..."
DEPLOY_DIR="deploy_package"
rm -rf $DEPLOY_DIR
mkdir -p $DEPLOY_DIR

# Copy essential files
cp main_whatsapp.py $DEPLOY_DIR/
cp requirements.txt $DEPLOY_DIR/
cp -r config $DEPLOY_DIR/ 2>/dev/null || true
cp -r services $DEPLOY_DIR/ 2>/dev/null || true
cp .env $DEPLOY_DIR/ 2>/dev/null || echo "⚠️ No .env file found"

# Create systemd service file
cat > $DEPLOY_DIR/lyo-whatsapp.service << 'EOF'
[Unit]
Description=Lyo WhatsApp Multi-Tenant Server
After=network.target

[Service]
Type=simple
User=ec2-user
WorkingDirectory=/home/ec2-user/lyo-saas
Environment=PATH=/usr/local/bin:/usr/bin:/bin
ExecStart=/usr/local/bin/python3 -m uvicorn main_whatsapp:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# Create deployment script for EC2
cat > $DEPLOY_DIR/install.sh << 'EOF'
#!/bin/bash
set -e

echo "🔧 Installing on EC2..."

cd /home/ec2-user/lyo-saas

# Install dependencies
pip3 install --user -r requirements.txt

# Copy systemd service
sudo cp lyo-whatsapp.service /etc/systemd/system/
sudo systemctl daemon-reload

# Stop old service if running
sudo systemctl stop lyo-whatsapp 2>/dev/null || true

# Start new service
sudo systemctl enable lyo-whatsapp
sudo systemctl start lyo-whatsapp

# Check status
sleep 3
sudo systemctl status lyo-whatsapp --no-pager

echo "✅ Deployment complete!"
echo "📊 Check health: curl http://localhost:8000/health"
EOF

chmod +x $DEPLOY_DIR/install.sh

echo "📤 Deployment package created in $DEPLOY_DIR/"
echo ""
echo "=============================================="
echo "📋 MANUAL DEPLOYMENT STEPS:"
echo "=============================================="
echo ""
echo "1. SSH into EC2:"
echo "   ssh -i your-key.pem ec2-user@$EC2_PUBLIC_DNS"
echo ""
echo "2. Create directory if needed:"
echo "   mkdir -p /home/ec2-user/lyo-saas"
echo ""
echo "3. Copy files (from your local machine):"
echo "   scp -i your-key.pem -r $DEPLOY_DIR/* ec2-user@$EC2_PUBLIC_DNS:/home/ec2-user/lyo-saas/"
echo ""
echo "4. Run install on EC2:"
echo "   cd /home/ec2-user/lyo-saas && ./install.sh"
echo ""
echo "5. Check it's working:"
echo "   curl http://localhost:8000/health"
echo ""
echo "=============================================="
echo "🔧 USING AWS SSM (no SSH needed):"
echo "=============================================="
echo ""
echo "# Upload main_whatsapp.py to EC2"
echo "aws ssm send-command \\"
echo "  --instance-ids $EC2_INSTANCE_ID \\"
echo "  --document-name 'AWS-RunShellScript' \\"
echo "  --parameters 'commands=[\"cat > /home/ec2-user/lyo-saas/main_whatsapp.py << '\''EOF'\''\"$(cat main_whatsapp.py)\"EOF\"]'"
echo ""
echo "=============================================="

