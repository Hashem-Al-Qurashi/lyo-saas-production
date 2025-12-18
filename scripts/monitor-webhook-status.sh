#!/bin/bash
# Monitor Webhook DNS and SSL Status
# Continuously checks until everything is ready

set -e

REGION="us-east-1"
DOMAIN="api.lyo-webhook.click"
CERT_ARN="arn:aws:acm:${REGION}:211425018318:certificate/43577b45-eadc-423d-9aa8-ce23a8f8968e"
ALB_DNS="lyo-enterprise-alb-558118620.us-east-1.elb.amazonaws.com"

echo "🔍 Monitoring Webhook Status"
echo "============================"
echo ""
echo "Domain: ${DOMAIN}"
echo "Certificate: ${CERT_ARN}"
echo ""
echo "Press Ctrl+C to stop monitoring"
echo ""

CHECK_COUNT=0
DNS_READY=false
CERT_READY=false

while true; do
    CHECK_COUNT=$((CHECK_COUNT + 1))
    TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
    
    echo "[${TIMESTAMP}] Check #${CHECK_COUNT}"
    echo "----------------------------------------"
    
    # Check DNS Resolution
    echo -n "🌐 DNS Resolution: "
    if nslookup ${DOMAIN} > /dev/null 2>&1; then
        if [ "$DNS_READY" = false ]; then
            echo "✅ WORKING! (Just became ready)"
            DNS_READY=true
        else
            echo "✅ Working"
        fi
        
        # Try HTTP endpoint
        HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "http://${DOMAIN}/health" 2>/dev/null || echo "000")
        if [ "${HTTP_STATUS}" == "200" ]; then
            echo "   ✅ HTTP endpoint responding (${HTTP_STATUS})"
        else
            echo "   ⚠️  HTTP endpoint returned ${HTTP_STATUS}"
        fi
    else
        echo "⏳ Not ready yet (still propagating...)"
        DNS_READY=false
    fi
    
    # Check SSL Certificate Status
    echo -n "🔒 SSL Certificate: "
    CERT_STATUS=$(aws acm describe-certificate \
        --certificate-arn ${CERT_ARN} \
        --region ${REGION} \
        --query 'Certificate.Status' \
        --output text 2>/dev/null || echo "ERROR")
    
    case "${CERT_STATUS}" in
        "ISSUED")
            if [ "$CERT_READY" = false ]; then
                echo "✅ ISSUED! (Just became ready)"
                CERT_READY=true
            else
                echo "✅ Issued"
            fi
            
            # Check HTTPS endpoint
            HTTPS_STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 -k "https://${DOMAIN}/health" 2>/dev/null || echo "000")
            if [ "${HTTPS_STATUS}" == "200" ]; then
                echo "   ✅ HTTPS endpoint responding (${HTTPS_STATUS})"
            else
                echo "   ⚠️  HTTPS endpoint returned ${HTTPS_STATUS}"
            fi
            ;;
        "PENDING_VALIDATION")
            echo "⏳ Pending validation (waiting for DNS validation...)"
            CERT_READY=false
            ;;
        "VALIDATION_TIMED_OUT")
            echo "❌ Validation timed out (need to request new certificate)"
            CERT_READY=false
            ;;
        "FAILED")
            echo "❌ Failed (need to request new certificate)"
            CERT_READY=false
            ;;
        *)
            echo "❓ Unknown status: ${CERT_STATUS}"
            CERT_READY=false
            ;;
    esac
    
    # Overall Status
    echo ""
    if [ "$DNS_READY" = true ] && [ "$CERT_READY" = true ]; then
        echo "🎉 ALL SYSTEMS READY!"
        echo ""
        echo "✅ DNS: Working"
        echo "✅ SSL: Issued"
        echo ""
        echo "🚀 Next steps:"
        echo "   1. Run: ./scripts/auto-fix-ssl-when-ready.sh"
        echo "   2. Test: ./scripts/verify-webhook.sh"
        echo "   3. Update Meta webhook URL"
        echo ""
        echo "Monitoring will continue. Press Ctrl+C to stop."
    elif [ "$DNS_READY" = true ]; then
        echo "📊 Status: DNS ready, waiting for SSL certificate..."
    else
        echo "📊 Status: Waiting for DNS propagation..."
    fi
    
    echo ""
    echo "⏳ Waiting 30 seconds before next check..."
    echo ""
    
    sleep 30
done

