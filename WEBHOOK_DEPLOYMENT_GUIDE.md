# WhatsApp Webhook Deployment Guide

## Quick Start Deployment

### Step 1: Prepare GitHub Repository

1. **Create a new branch for webhook deployment**:
```bash
git checkout -b webhook-production
```

2. **Copy production files**:
```bash
# Use the production webhook handler
mv api/webhook_production.py api/webhook.py

# Use production Vercel config
mv vercel.production.json vercel.json
```

3. **Commit and push**:
```bash
git add .
git commit -m "Deploy production webhook infrastructure"
git push origin webhook-production
```

### Step 2: Deploy to Vercel

1. **Go to Vercel Dashboard**: https://vercel.com/dashboard

2. **Import Project**:
   - Click "New Project"
   - Import from GitHub
   - Select repository: `Hashem-Al-Qurashi/lyo-saas-production`
   - Choose branch: `webhook-production`

3. **Configure Project**:
   - Project Name: `lyo-webhook`
   - Framework Preset: Other
   - Root Directory: ./
   - Build Command: (leave empty)
   - Output Directory: (leave empty)

4. **Set Environment Variables** (IMPORTANT):
   ```
   WEBHOOK_VERIFY_TOKEN = lyo_verify_2024
   HETZNER_SERVER_URL = http://135.181.249.116:8000
   MONITORING_ENABLED = true
   MAX_RETRIES = 3
   RETRY_DELAY = 1.0
   REQUEST_TIMEOUT = 10
   ```

5. **Deploy**:
   - Click "Deploy"
   - Wait for deployment (usually < 1 minute)

### Step 3: Get Your Permanent Webhook URL

After deployment, your webhook will be available at:
```
https://lyo-webhook.vercel.app/webhook
```

This URL is PERMANENT and will never change!

### Step 4: Configure WhatsApp Business API

1. **Go to Meta for Developers**: https://developers.facebook.com

2. **Navigate to your WhatsApp Business App**

3. **Configure Webhook**:
   - Callback URL: `https://lyo-webhook.vercel.app/webhook`
   - Verify Token: `lyo_verify_2024`
   - Click "Verify and Save"

4. **Subscribe to Webhook Fields**:
   - messages
   - messaging_postbacks
   - messaging_optins
   - message_deliveries
   - message_reads

### Step 5: Test the Webhook

1. **Test Verification**:
```bash
curl "https://lyo-webhook.vercel.app/webhook?hub.mode=subscribe&hub.verify_token=lyo_verify_2024&hub.challenge=test123"
```
Expected response: `test123`

2. **Test Health Check**:
```bash
curl https://lyo-webhook.vercel.app/health
```

3. **Test Message Forward**:
```bash
curl -X POST https://lyo-webhook.vercel.app/webhook \
  -H "Content-Type: application/json" \
  -d '{"object":"whatsapp_business_account","entry":[{"changes":[{"value":{"messages":[{"from":"1234567890","text":{"body":"Test message"}}]}}]}]}'
```

## Production Checklist

### Pre-Deployment
- [ ] Environment variables configured in Vercel
- [ ] Hetzner server accessible from internet
- [ ] WhatsApp Business API credentials ready
- [ ] Backup webhook solution prepared (optional)

### Deployment
- [ ] Code pushed to GitHub
- [ ] Vercel deployment successful
- [ ] Webhook URL obtained
- [ ] Health check passing

### Post-Deployment
- [ ] WhatsApp webhook verified
- [ ] Test message sent and received
- [ ] Monitoring dashboard accessible
- [ ] Alerts configured (optional)

## Monitoring Your Webhook

### Health Check
Monitor webhook health at:
```
https://lyo-webhook.vercel.app/health
```

### Metrics Dashboard
View operational metrics at:
```
https://lyo-webhook.vercel.app/metrics
```

### Vercel Analytics
1. Go to Vercel Dashboard
2. Select your project
3. Click "Analytics" tab
4. Monitor:
   - Request volume
   - Error rate
   - Response times
   - Geographic distribution

## Troubleshooting

### Issue: Webhook verification fails
**Solution**:
1. Check verify token matches exactly
2. Ensure GET requests are allowed
3. Check Vercel logs for errors

### Issue: Messages not forwarding to Hetzner
**Solution**:
1. Verify Hetzner server is running
2. Check firewall allows connections
3. Review Vercel function logs
4. Test direct connection to Hetzner

### Issue: High latency
**Solution**:
1. Check Vercel region (should be closest to your users)
2. Optimize Hetzner server response time
3. Enable Vercel Edge caching if applicable

### Issue: Rate limiting
**Solution**:
1. Upgrade to Vercel Pro if needed
2. Implement request queuing
3. Add caching layer

## Scaling Considerations

### Current Limits (Free Tier)
- 100,000 requests/month
- 100GB bandwidth
- 10 second timeout
- 1,000 concurrent executions

### When to Upgrade
- > 50 restaurants active
- > 10,000 messages/day
- Need SLA guarantees
- Require custom domain

### Upgrade Path
1. **Vercel Pro** ($20/month):
   - 1,000,000 requests
   - 1TB bandwidth
   - Priority support

2. **Vercel Enterprise**:
   - Unlimited requests
   - SLA guarantees
   - Dedicated support

## Security Best Practices

1. **Never commit secrets to GitHub**
2. **Use environment variables for all sensitive data**
3. **Enable webhook signature verification**
4. **Regularly rotate access tokens**
5. **Monitor for unusual activity**
6. **Keep dependencies updated**

## Backup Strategy

### Primary: Vercel
- URL: https://lyo-webhook.vercel.app/webhook
- Uptime: 99.99%

### Backup: Railway (Optional)
- URL: https://lyo-backup.railway.app/webhook
- Auto-failover configuration

### Disaster Recovery
1. If Vercel is down, update WhatsApp to backup URL
2. Monitor backup webhook health
3. Switch back when primary recovers

## Support and Maintenance

### Daily Tasks
- Check health endpoint
- Review metrics dashboard
- Monitor error logs

### Weekly Tasks
- Review performance metrics
- Check for security updates
- Test backup webhook

### Monthly Tasks
- Rotate access tokens
- Review scaling needs
- Update documentation

## Conclusion

Your WhatsApp webhook is now:
- ✅ Permanently hosted at a stable URL
- ✅ Protected with valid SSL certificates
- ✅ Professionally monitored and maintained
- ✅ Scalable to handle growth
- ✅ Backed up for disaster recovery

The webhook URL `https://lyo-webhook.vercel.app/webhook` will NEVER change, eliminating the need to regenerate WhatsApp tokens ever again!