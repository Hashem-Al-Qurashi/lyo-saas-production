#!/bin/bash
# Auto-update ALB SSL when certificate is ready
# This script checks certificate status and updates ALB listener automatically

set -e

REGION="us-east-1"
CERT_ARN="arn:aws:acm:${REGION}:211425018318:certificate/43577b45-eadc-423d-9aa8-ce23a8f8968e"
ALB_ARN="arn:aws:elasticloadbalancing:${REGION}:211425018318:loadbalancer/app/lyo-enterprise-alb/811eddbc581e22b5"
HTTPS_LISTENER_ARN="arn:aws:elasticloadbalancing:${REGION}:211425018318:listener/app/lyo-enterprise-alb/811eddbc581e22b5/39969d1e63d9ca07"

echo "🔍 Checking SSL Certificate Status..."
CERT_STATUS=$(aws acm describe-certificate \
  --certificate-arn ${CERT_ARN} \
  --region ${REGION} \
  --query 'Certificate.Status' \
  --output text)

echo "📋 Certificate Status: ${CERT_STATUS}"

if [ "${CERT_STATUS}" == "ISSUED" ]; then
    echo "✅ Certificate is ISSUED! Updating ALB listener..."
    
    # Update ALB listener with ACM certificate
    aws elbv2 modify-listener \
      --listener-arn ${HTTPS_LISTENER_ARN} \
      --certificates CertificateArn=${CERT_ARN} \
      --region ${REGION} > /dev/null
    
    echo "✅ ALB HTTPS listener updated with ACM certificate!"
    echo ""
    echo "🧪 Testing HTTPS endpoint..."
    sleep 5
    
    if curl -s --max-time 10 "https://api.lyo-webhook.click/health" > /dev/null 2>&1; then
        echo "✅ HTTPS endpoint is working!"
        curl -s "https://api.lyo-webhook.click/health" | jq '.' 2>/dev/null || curl -s "https://api.lyo-webhook.click/health"
    else
        echo "⚠️  HTTPS endpoint test failed (DNS may still be propagating)"
    fi
else
    echo "⏳ Certificate is still ${CERT_STATUS}"
    echo "   Waiting for DNS validation to complete..."
    echo ""
    echo "📋 Current validation status:"
    aws acm describe-certificate \
      --certificate-arn ${CERT_ARN} \
      --region ${REGION} \
      --query 'Certificate.DomainValidationOptions[0].[ValidationStatus,ResourceRecord]' \
      --output json
    
    echo ""
    echo "💡 Run this script again in a few minutes:"
    echo "   ./scripts/auto-fix-ssl-when-ready.sh"
fi

