# Webhook Troubleshooting Guide

## Overview

This guide helps diagnose and fix issues with the WhatsApp Business API webhook integration for the Lyo SaaS multi-tenant booking assistant.

## Architecture

```
Meta WhatsApp Business API
    ↓ HTTPS
api.lyo-webhook.click (Route53 → ALB)
    ↓ HTTP
Application Load Balancer (lyo-enterprise-alb)
    ↓ HTTP:8000
EC2 Instance (lyo-enterprise-final)
    ↓
FastAPI Application (/webhooks/whatsapp)
```

## Common Issues

### 1. DNS Resolution Failure

**Symptom:** `api.lyo-webhook.click` doesn't resolve (NXDOMAIN)

**Diagnosis:**
```bash
nslookup api.lyo-webhook.click
dig api.lyo-webhook.click
```

**Solution:**
1. Check Route53 hosted zone: `aws route53 list-hosted-zones`
2. Verify A record exists: `aws route53 list-resource-record-sets --hosted-zone-id Z071091735JN45ZQYUP6Q`
3. Create/update DNS record using `scripts/fix-webhook-dns.sh`
4. Wait 2-5 minutes for DNS propagation

**Verification:**
```bash
curl http://api.lyo-webhook.click/health
```

### 2. SSL Certificate Issues

**Symptom:** HTTPS endpoint fails with certificate errors

**Diagnosis:**
```bash
# Check certificate status
aws acm describe-certificate \
  --certificate-arn arn:aws:acm:us-east-1:211425018318:certificate/43577b45-eadc-423d-9aa8-ce23a8f8968e \
  --region us-east-1 \
  --query 'Certificate.Status'

# Test SSL
openssl s_client -servername api.lyo-webhook.click -connect api.lyo-webhook.click:443
```

**Solution:**
1. Ensure DNS is working first
2. Request new certificate if old one timed out:
   ```bash
   aws acm request-certificate \
     --domain-name api.lyo-webhook.click \
     --validation-method DNS \
     --region us-east-1
   ```
3. Add DNS validation CNAME record (if not auto-added)
4. Wait for certificate to be ISSUED
5. Update ALB listener: `scripts/fix-alb-ssl.sh`

**Certificate Status:**
- `PENDING_VALIDATION`: Waiting for DNS validation
- `ISSUED`: Ready to use
- `VALIDATION_TIMED_OUT`: Need to request new certificate
- `FAILED`: Certificate request failed

### 3. ALB Health Check Failing

**Symptom:** ALB shows targets as unhealthy

**Diagnosis:**
```bash
# Check target health
aws elbv2 describe-target-health \
  --target-group-arn <target-group-arn> \
  --region us-east-1

# Check EC2 instance
aws ec2 describe-instance-status \
  --instance-ids i-072d32267643624b9 \
  --region us-east-1
```

**Solution:**
1. Verify EC2 instance is running
2. Check application is listening on port 8000
3. Verify security group allows traffic from ALB
4. Test health endpoint directly: `curl http://3.239.106.181:8000/health`

### 4. Webhook Verification Failing

**Symptom:** Meta can't verify webhook (403 or timeout)

**Diagnosis:**
```bash
# Test verification endpoint
curl "https://api.lyo-webhook.click/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=YOUR_TOKEN&hub.challenge=test123"
```

**Solution:**
1. Verify DNS and SSL are working
2. Check verify token matches Meta configuration
3. Ensure endpoint returns challenge as plain text (not JSON)
4. Verify token is set in environment: `WHATSAPP_WEBHOOK_VERIFY_TOKEN`

**Expected Response:**
- Status: 200 OK
- Content-Type: text/plain
- Body: The challenge value (e.g., "test123")

### 5. Webhook Not Receiving Messages

**Symptom:** Messages sent to WhatsApp but not received by application

**Diagnosis:**
1. Check Meta webhook configuration:
   - Webhook URL: `https://api.lyo-webhook.click/webhooks/whatsapp`
   - Verify token matches
   - Webhook fields subscribed: `messages`, `message_status`
2. Check application logs on EC2
3. Verify ALB is forwarding requests correctly

**Solution:**
1. Test webhook POST endpoint:
   ```bash
   curl -X POST https://api.lyo-webhook.click/webhooks/whatsapp \
     -H "Content-Type: application/json" \
     -d '{"object":"whatsapp_business_account","entry":[]}'
   ```
2. Check CloudWatch logs (if configured)
3. SSH to EC2 and check application logs

### 6. Multi-Tenant Routing Issues

**Symptom:** Messages not routed to correct restaurant/tenant

**Current Implementation:**
The webhook handler in `app/api/webhooks.py` processes messages but doesn't identify which tenant/business the message belongs to.

**Solution:**
The WhatsApp webhook payload includes metadata with `phone_number_id` which can be used to identify the tenant:

```python
# In process_whatsapp_message function
metadata = value.get("metadata", {})
phone_number_id = metadata.get("phone_number_id")

# Look up tenant by WhatsApp phone number ID
tenant = await get_tenant_by_whatsapp_phone_id(phone_number_id)
```

**Database Query:**
```sql
SELECT * FROM lyo_tenants 
WHERE whatsapp_business_account_id = :phone_number_id;
```

## Quick Diagnostic Commands

### Full System Check
```bash
./scripts/verify-webhook.sh
```

### DNS Check
```bash
nslookup api.lyo-webhook.click
dig api.lyo-webhook.click +short
```

### HTTP Health Check
```bash
curl -v http://api.lyo-webhook.click/health
```

### HTTPS Health Check
```bash
curl -v https://api.lyo-webhook.click/health
```

### Webhook Verification Test
```bash
curl "https://api.lyo-webhook.click/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=YOUR_TOKEN&hub.challenge=test123"
```

### ALB Direct Check
```bash
curl http://lyo-enterprise-alb-558118620.us-east-1.elb.amazonaws.com/health
```

### EC2 Direct Check
```bash
curl http://3.239.106.181:8000/health
```

## AWS Resource Information

- **Route53 Hosted Zone:** `Z071091735JN45ZQYUP6Q`
- **ALB Name:** `lyo-enterprise-alb`
- **ALB DNS:** `lyo-enterprise-alb-558118620.us-east-1.elb.amazonaws.com`
- **ALB Hosted Zone ID:** `Z35SXDOTRQ7X7K`
- **EC2 Instance:** `i-072d32267643624b9` (3.239.106.181)
- **Target Group:** `lyo-enterprise-targets` (port 8000)
- **Region:** `us-east-1`

## Meta Configuration

### Webhook URL
```
https://api.lyo-webhook.click/webhooks/whatsapp
```

### Verify Token
Set in environment variable: `WHATSAPP_WEBHOOK_VERIFY_TOKEN`

### Webhook Fields
- `messages` - Incoming messages
- `message_status` - Delivery status updates

## Monitoring

### Check Application Logs
```bash
# SSH to EC2
ssh ec2-user@3.239.106.181

# Check application logs (depends on deployment method)
sudo journalctl -u lyo-app -f
# or
docker logs <container-name> -f
```

### Check ALB Access Logs
```bash
# Enable ALB access logs in S3 bucket
# Then query logs for webhook requests
```

### CloudWatch Metrics
- ALB request count
- Target response time
- Healthy/unhealthy target count
- 4xx/5xx error rates

## Escalation

If issues persist:
1. Verify all infrastructure components are running
2. Check AWS service health dashboard
3. Review recent deployments or configuration changes
4. Check Meta WhatsApp Business API status
5. Review application error logs for specific error messages

## Related Files

- `scripts/fix-webhook-dns.sh` - Fix DNS configuration
- `scripts/fix-alb-ssl.sh` - Fix SSL certificate
- `scripts/verify-webhook.sh` - Comprehensive verification
- `app/api/webhooks.py` - Webhook handler implementation
- `app/core/config.py` - Configuration settings

