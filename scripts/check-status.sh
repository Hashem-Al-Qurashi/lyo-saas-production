#!/bin/bash
# Quick one-time status check

REGION="us-east-1"
DOMAIN="api.lyo-webhook.click"
CERT_ARN="arn:aws:acm:${REGION}:211425018318:certificate/43577b45-eadc-423d-9aa8-ce23a8f8968e"

echo "🔍 Quick Status Check"
echo "===================="
echo ""

# Check DNS
echo -n "🌐 DNS: "
if nslookup ${DOMAIN} > /dev/null 2>&1; then
    echo "✅ Working"
    HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "http://${DOMAIN}/health" 2>/dev/null || echo "000")
    if [ "${HTTP_STATUS}" == "200" ]; then
        echo "   ✅ HTTP endpoint: OK"
    fi
else
    echo "⏳ Not ready (propagating...)"
fi

# Check Certificate
echo -n "🔒 SSL Certificate: "
CERT_STATUS=$(aws acm describe-certificate \
    --certificate-arn ${CERT_ARN} \
    --region ${REGION} \
    --query 'Certificate.Status' \
    --output text 2>/dev/null || echo "ERROR")

case "${CERT_STATUS}" in
    "ISSUED")
        echo "✅ Issued"
        ;;
    "PENDING_VALIDATION")
        echo "⏳ Pending validation"
        ;;
    *)
        echo "❌ ${CERT_STATUS}"
        ;;
esac

echo ""
if [ "${CERT_STATUS}" == "ISSUED" ] && nslookup ${DOMAIN} > /dev/null 2>&1; then
    echo "🎉 Ready! Run: ./scripts/auto-fix-ssl-when-ready.sh"
else
    echo "⏳ Still waiting... Run this again in a few minutes"
fi

