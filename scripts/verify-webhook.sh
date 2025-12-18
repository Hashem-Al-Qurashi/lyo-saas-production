#!/bin/bash
# Verify Webhook Endpoints
# Tests DNS, HTTP, HTTPS, and webhook verification endpoints

set -e

echo "🧪 Verifying Webhook Configuration"
echo "===================================="

DOMAIN="api.lyo-webhook.click"
WEBHOOK_URL="https://${DOMAIN}/webhooks/whatsapp"
HEALTH_URL="https://${DOMAIN}/health"

# Test 1: DNS Resolution
echo ""
echo "1️⃣  Testing DNS Resolution..."
if nslookup ${DOMAIN} > /dev/null 2>&1; then
    echo "✅ DNS resolves correctly"
    nslookup ${DOMAIN} | grep -A 2 "Name:" || true
else
    echo "❌ DNS resolution failed"
    echo "   Domain may still be propagating. Wait a few minutes."
    exit 1
fi

# Test 2: HTTP Health Check
echo ""
echo "2️⃣  Testing HTTP Health Endpoint..."
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "http://${DOMAIN}/health" || echo "000")
if [ "${HTTP_STATUS}" == "200" ]; then
    echo "✅ HTTP health check passed (${HTTP_STATUS})"
    curl -s "http://${DOMAIN}/health" | jq '.' 2>/dev/null || curl -s "http://${DOMAIN}/health"
else
    echo "❌ HTTP health check failed (${HTTP_STATUS})"
fi

# Test 3: HTTPS Health Check
echo ""
echo "3️⃣  Testing HTTPS Health Endpoint..."
HTTPS_STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "https://${DOMAIN}/health" 2>/dev/null || echo "000")
if [ "${HTTPS_STATUS}" == "200" ]; then
    echo "✅ HTTPS health check passed (${HTTPS_STATUS})"
    curl -s "https://${DOMAIN}/health" | jq '.' 2>/dev/null || curl -s "https://${DOMAIN}/health"
else
    echo "⚠️  HTTPS health check failed (${HTTPS_STATUS})"
    echo "   This may be due to SSL certificate issues"
fi

# Test 4: SSL Certificate Check
echo ""
echo "4️⃣  Testing SSL Certificate..."
if echo | openssl s_client -servername ${DOMAIN} -connect ${DOMAIN}:443 2>/dev/null | grep -q "Verify return code: 0"; then
    echo "✅ SSL certificate is valid"
    CERT_INFO=$(echo | openssl s_client -servername ${DOMAIN} -connect ${DOMAIN}:443 2>/dev/null | openssl x509 -noout -subject -dates 2>/dev/null)
    echo "   ${CERT_INFO}"
else
    echo "⚠️  SSL certificate validation failed or certificate is self-signed"
    echo "   This is expected if ACM certificate is not yet issued"
fi

# Test 5: Webhook Verification Endpoint (GET)
echo ""
echo "5️⃣  Testing Webhook Verification Endpoint..."
VERIFY_URL="${WEBHOOK_URL}?hub.mode=subscribe&hub.verify_token=test&hub.challenge=test123"
VERIFY_STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "${VERIFY_URL}" 2>/dev/null || echo "000")
if [ "${VERIFY_STATUS}" == "200" ] || [ "${VERIFY_STATUS}" == "403" ]; then
    echo "✅ Webhook verification endpoint is accessible (${VERIFY_STATUS})"
    echo "   Note: 403 is expected with wrong token, 200 means verification would work"
else
    echo "⚠️  Webhook verification endpoint returned (${VERIFY_STATUS})"
fi

# Test 6: Webhook POST Endpoint
echo ""
echo "6️⃣  Testing Webhook POST Endpoint..."
POST_STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
  -H "Content-Type: application/json" \
  -d '{"object":"whatsapp_business_account","entry":[]}' \
  --max-time 10 "${WEBHOOK_URL}" 2>/dev/null || echo "000")
if [ "${POST_STATUS}" == "200" ]; then
    echo "✅ Webhook POST endpoint is accessible (${POST_STATUS})"
else
    echo "⚠️  Webhook POST endpoint returned (${POST_STATUS})"
fi

# Summary
echo ""
echo "📊 Summary"
echo "=========="
echo "Domain: ${DOMAIN}"
echo "Webhook URL: ${WEBHOOK_URL}"
echo ""
echo "✅ All critical tests completed!"
echo ""
echo "📋 Next steps:"
echo "1. If DNS is working, wait for SSL certificate validation"
echo "2. Update Meta webhook URL: ${WEBHOOK_URL}"
echo "3. Set verify token in Meta Business Manager"
echo "4. Test webhook verification from Meta"

