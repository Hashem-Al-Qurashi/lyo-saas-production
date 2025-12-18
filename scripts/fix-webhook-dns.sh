#!/bin/bash
# Fix Webhook DNS Configuration
# Creates Route53 A record pointing api.lyo-webhook.click to ALB

set -e

echo "🔧 Fixing Webhook DNS Configuration"
echo "======================================"

# Configuration
HOSTED_ZONE_ID="Z071091735JN45ZQYUP6Q"
ALB_DNS="lyo-enterprise-alb-558118620.us-east-1.elb.amazonaws.com"
ALB_HOSTED_ZONE_ID="Z35SXDOTRQ7X7K"
DOMAIN="api.lyo-webhook.click"

# Create DNS record JSON
cat > /tmp/dns-record.json <<EOF
{
  "Changes": [
    {
      "Action": "UPSERT",
      "ResourceRecordSet": {
        "Name": "${DOMAIN}",
        "Type": "A",
        "AliasTarget": {
          "DNSName": "${ALB_DNS}",
          "HostedZoneId": "${ALB_HOSTED_ZONE_ID}",
          "EvaluateTargetHealth": true
        }
      }
    }
  ]
}
EOF

echo "📝 Creating DNS A record for ${DOMAIN}..."
CHANGE_ID=$(aws route53 change-resource-record-sets \
  --hosted-zone-id ${HOSTED_ZONE_ID} \
  --change-batch file:///tmp/dns-record.json \
  --query 'ChangeInfo.Id' \
  --output text)

echo "✅ DNS change submitted: ${CHANGE_ID}"
echo "⏳ Waiting for DNS propagation (this may take a few minutes)..."

# Wait for change to be INSYNC
aws route53 wait resource-record-sets-changed --id ${CHANGE_ID}

echo "✅ DNS record created successfully!"
echo ""
echo "🔍 Verifying DNS resolution..."
sleep 5

# Test DNS resolution
if nslookup ${DOMAIN} > /dev/null 2>&1; then
    echo "✅ DNS resolution working!"
    nslookup ${DOMAIN} | grep -A 2 "Name:"
else
    echo "⚠️  DNS may still be propagating. Wait a few minutes and test again."
fi

echo ""
echo "📋 Next steps:"
echo "1. Wait 2-5 minutes for DNS propagation"
echo "2. Test: curl http://${DOMAIN}/health"
echo "3. Request new SSL certificate or re-validate existing one"

