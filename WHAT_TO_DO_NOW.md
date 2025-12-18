# What To Do Now - Step by Step Guide

## ⏱️ Timeline

**Total Wait Time: 10-35 minutes**

- **DNS Propagation:** 2-5 minutes (happening now)
- **SSL Certificate Validation:** 5-30 minutes after DNS propagates
- **Total:** ~10-35 minutes until fully working

## 🎯 What To Do RIGHT NOW

### Step 1: Wait 5 Minutes (Let DNS Propagate)

Just wait. DNS changes take 2-5 minutes to propagate globally.

### Step 2: After 5 Minutes - Run This Command

```bash
./scripts/auto-fix-ssl-when-ready.sh
```

This script will:
- ✅ Check if DNS is working
- ✅ Check if SSL certificate is ready
- ✅ Automatically update ALB when certificate is ISSUED
- ✅ Test the HTTPS endpoint

### Step 3: If Certificate Still Pending

If the script says "Certificate is still PENDING_VALIDATION", wait another 5-10 minutes and run it again:

```bash
# Wait 5-10 minutes, then:
./scripts/auto-fix-ssl-when-ready.sh
```

Repeat until you see: **"✅ Certificate is ISSUED! Updating ALB listener..."**

## 📋 Quick Status Check Commands

### Check DNS (should work after 2-5 min)
```bash
nslookup api.lyo-webhook.click
# OR
curl http://api.lyo-webhook.click/health
```

### Check SSL Certificate Status
```bash
aws acm describe-certificate \
  --certificate-arn arn:aws:acm:us-east-1:211425018318:certificate/43577b45-eadc-423d-9aa8-ce23a8f8968e \
  --region us-east-1 \
  --query 'Certificate.Status' \
  --output text
```

You want to see: **"ISSUED"**

### Full Verification (after everything is ready)
```bash
./scripts/verify-webhook.sh
```

## 🚀 Once Everything is Ready

When the auto-fix script completes successfully, you'll see:
```
✅ Certificate is ISSUED! Updating ALB listener...
✅ ALB HTTPS listener updated with ACM certificate!
✅ HTTPS endpoint is working!
```

Then:

1. **Test the webhook:**
   ```bash
   curl "https://api.lyo-webhook.click/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=YOUR_TOKEN&hub.challenge=test123"
   ```

2. **Update Meta Webhook URL:**
   - Go to Meta Business Manager
   - Set webhook URL: `https://api.lyo-webhook.click/webhooks/whatsapp`
   - Set verify token (from your environment variables)
   - Test verification

## ⚠️ Important Note

The application currently running on EC2 (`main_production.py`) has `/webhooks/chatwoot` but may not have `/webhooks/whatsapp`. 

**If webhook verification fails**, you may need to:
1. Deploy the `app/main.py` version which has the WhatsApp webhook route
2. OR add the WhatsApp route to `main_production.py`

## 📞 Summary

**Right Now:**
1. ⏳ Wait 5 minutes
2. 🔄 Run: `./scripts/auto-fix-ssl-when-ready.sh`
3. ⏳ If pending, wait 5-10 more minutes and repeat

**That's it!** The script will handle everything automatically once DNS and SSL are ready.

## 🎯 Expected Timeline

```
Now          → Wait 5 min
5 min later  → Run auto-fix script
              → If cert ready: DONE! ✅
              → If not: Wait 5-10 min, repeat
10-35 min    → Everything working! 🎉
```

