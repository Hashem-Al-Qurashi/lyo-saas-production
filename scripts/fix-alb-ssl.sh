#!/bin/bash
# Fix ALB SSL Certificate Configuration
# Configures ALB HTTPS listener to use ACM certificate

set -e

echo "🔒 Fixing ALB SSL Configuration"
echo "==============================="

# Configuration
REGION="us-east-1"
ALB_NAME="lyo-enterprise-alb"
DOMAIN="api.lyo-webhook.click"

# Get ALB ARN
ALB_ARN=$(aws elbv2 describe-load-balancers \
  --region ${REGION} \
  --names ${ALB_NAME} \
  --query 'LoadBalancers[0].LoadBalancerArn' \
  --output text)

echo "📋 ALB ARN: ${ALB_ARN}"

# Get HTTPS listener ARN
HTTPS_LISTENER_ARN=$(aws elbv2 describe-listeners \
  --load-balancer-arn ${ALB_ARN} \
  --region ${REGION} \
  --query 'Listeners[?Port==`443`].ListenerArn' \
  --output text)

if [ -z "${HTTPS_LISTENER_ARN}" ]; then
    echo "❌ HTTPS listener not found. Creating one..."
    
    # Get default target group ARN
    TARGET_GROUP_ARN=$(aws elbv2 describe-target-groups \
      --region ${REGION} \
      --names lyo-enterprise-targets \
      --query 'TargetGroups[0].TargetGroupArn' \
      --output text)
    
    # Create HTTPS listener
    HTTPS_LISTENER_ARN=$(aws elbv2 create-listener \
      --load-balancer-arn ${ALB_ARN} \
      --protocol HTTPS \
      --port 443 \
      --certificates CertificateArn=arn:aws:acm:${REGION}:211425018318:certificate/f10b365c-5d9b-462f-9abd-93076af5c1b8 \
      --default-actions Type=forward,TargetGroupArn=${TARGET_GROUP_ARN} \
      --region ${REGION} \
      --query 'Listeners[0].ListenerArn' \
      --output text)
    
    echo "✅ HTTPS listener created: ${HTTPS_LISTENER_ARN}"
else
    echo "📋 HTTPS listener found: ${HTTPS_LISTENER_ARN}"
    
    # Check if certificate is already configured
    CURRENT_CERT=$(aws elbv2 describe-listeners \
      --listener-arns ${HTTPS_LISTENER_ARN} \
      --region ${REGION} \
      --query 'Listeners[0].Certificates[0].CertificateArn' \
      --output text)
    
    echo "📋 Current certificate: ${CURRENT_CERT}"
    
    # Request new certificate if old one is invalid
    echo "🔍 Checking certificate status..."
    
    # Try to get a valid certificate
    CERT_ARN=$(aws acm list-certificates \
      --region ${REGION} \
      --query "CertificateSummaryList[?DomainName=='${DOMAIN}'].CertificateArn" \
      --output text | head -1)
    
    if [ -z "${CERT_ARN}" ] || [ "${CERT_ARN}" == "None" ]; then
        echo "📝 Requesting new ACM certificate for ${DOMAIN}..."
        CERT_ARN=$(aws acm request-certificate \
          --domain-name ${DOMAIN} \
          --validation-method DNS \
          --region ${REGION} \
          --query 'CertificateArn' \
          --output text)
        
        echo "✅ Certificate requested: ${CERT_ARN}"
        echo "⏳ Waiting for DNS validation (this may take several minutes)..."
        echo "   You may need to add DNS validation records manually."
    else
        echo "✅ Found certificate: ${CERT_ARN}"
        
        # Check certificate status
        CERT_STATUS=$(aws acm describe-certificate \
          --certificate-arn ${CERT_ARN} \
          --region ${REGION} \
          --query 'Certificate.Status' \
          --output text)
        
        echo "📋 Certificate status: ${CERT_STATUS}"
        
        if [ "${CERT_STATUS}" != "ISSUED" ]; then
            echo "⚠️  Certificate is not issued yet. Status: ${CERT_STATUS}"
            echo "   You may need to complete DNS validation."
        fi
    fi
    
    # Update listener with certificate
    echo "🔧 Updating HTTPS listener with certificate..."
    aws elbv2 modify-listener \
      --listener-arn ${HTTPS_LISTENER_ARN} \
      --certificates CertificateArn=${CERT_ARN} \
      --region ${REGION} > /dev/null
    
    echo "✅ HTTPS listener updated with certificate"
fi

echo ""
echo "✅ SSL configuration complete!"
echo ""
echo "📋 Next steps:"
echo "1. If certificate is pending validation, add DNS validation records"
echo "2. Wait for certificate to be ISSUED"
echo "3. Test: curl https://${DOMAIN}/health"

