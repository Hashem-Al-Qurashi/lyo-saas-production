# Lyo SaaS Application Architecture

## Overview

This is a multi-tenant WhatsApp/Instagram booking assistant SaaS. Each restaurant/salon is a "tenant" with their own WhatsApp number.

## Application Files

### Currently Running on EC2: `main_production.py`

```
Endpoints:
├── /health                  ✅ Working
├── /webhooks/chatwoot       ✅ For Chatwoot integration
├── /api/test-message        ✅ Testing endpoint
└── /                        ✅ Root info

MISSING: /webhooks/whatsapp  ❌ Not available!
```

### Complete Webhook Handler: `app/api/webhooks.py`

```
Endpoints (if app/main.py is deployed):
├── /webhooks/whatsapp       ✅ GET (verification) + POST (messages)
├── /webhooks/chatwoot       ✅ Chatwoot integration  
├── /webhooks/instagram      ✅ Instagram DMs
└── All properly implemented with logging
```

### Complete App: `app/main.py`

```
Full application with all routers:
├── /webhooks/*              → app/api/webhooks.py
├── /api/*                   → app/api/chat.py
├── /admin/*                 → app/api/admin.py
├── /health                  → Health check
├── /metrics                 → Prometheus metrics
└── /                        → API info
```

## Multi-Tenant Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Meta WhatsApp Business API                        │
│                                                                      │
│  Restaurant A (phone_number_id: 123456)                             │
│  Restaurant B (phone_number_id: 789012)                             │
│  Restaurant C (phone_number_id: 345678)                             │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              │ Webhook POST to /webhooks/whatsapp
                              │ Contains: metadata.phone_number_id
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   Lyo SaaS Webhook Handler                          │
│                                                                      │
│  1. Extract phone_number_id from webhook payload                    │
│  2. Look up tenant in database:                                     │
│     SELECT * FROM lyo_tenants                                       │
│     WHERE whatsapp_business_account_id = :phone_number_id           │
│  3. Load tenant's business config (services, hours, etc.)           │
│  4. Process message with tenant context                             │
│  5. Send response using tenant's WhatsApp credentials               │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     PostgreSQL Database                              │
│                                                                      │
│  lyo_tenants:                                                       │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ tenant_id │ business_name    │ whatsapp_business_account_id │   │
│  │ uuid-1    │ Restaurant A     │ 123456                       │   │
│  │ uuid-2    │ Restaurant B     │ 789012                       │   │
│  │ uuid-3    │ Restaurant C     │ 345678                       │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                      │
│  lyo_users: (customers per tenant)                                  │
│  lyo_conversations: (chat sessions per tenant)                      │
│  lyo_appointments: (bookings per tenant)                            │
└─────────────────────────────────────────────────────────────────────┘
```

## WhatsApp Webhook Payload Structure

```json
{
  "object": "whatsapp_business_account",
  "entry": [{
    "id": "WHATSAPP_BUSINESS_ACCOUNT_ID",
    "changes": [{
      "value": {
        "messaging_product": "whatsapp",
        "metadata": {
          "display_phone_number": "15551234567",
          "phone_number_id": "123456789"  // <-- Use this to identify tenant!
        },
        "contacts": [{
          "profile": { "name": "Customer Name" },
          "wa_id": "customer_phone_number"
        }],
        "messages": [{
          "from": "customer_phone_number",
          "id": "message_id",
          "timestamp": "1234567890",
          "text": { "body": "Ciao, vorrei prenotare" },
          "type": "text"
        }]
      },
      "field": "messages"
    }]
  }]
}
```

## What Needs to Be Done

### Issue 1: Missing WhatsApp Webhook Endpoint ❌

**Current State:**
- `main_production.py` running on EC2 doesn't have `/webhooks/whatsapp`
- `app/api/webhooks.py` has complete implementation but isn't deployed

**Solutions:**

**Option A: Deploy `app/main.py` (Recommended)**
- Already has complete webhook support
- Includes `/webhooks/whatsapp` with verification + message handling
- Better code organization

**Option B: Add WhatsApp endpoint to `main_production.py`**
- Copy webhook logic from `app/api/webhooks.py`
- Quick fix but creates code duplication

### Issue 2: Multi-Tenant Routing Not Implemented ❌

**Current State:**
- Database schema supports multi-tenancy
- But webhook handler doesn't identify which tenant received the message

**Solution:**
```python
# In process_whatsapp_message():
phone_number_id = metadata.get("phone_number_id")

# Look up tenant
tenant = await db.fetchrow("""
    SELECT * FROM lyo_tenants 
    WHERE whatsapp_business_account_id = $1
""", phone_number_id)

if not tenant:
    logger.warning(f"No tenant for phone_number_id: {phone_number_id}")
    return

# Use tenant_id for all subsequent operations
tenant_id = tenant['tenant_id']
```

### Issue 3: WhatsApp Response Sending Not Implemented ❌

**Current State:**
- `send_whatsapp_reply()` is a placeholder (doesn't actually send)

**Solution:**
```python
async def send_whatsapp_reply(phone: str, message: str, tenant: dict):
    """Send reply via WhatsApp Cloud API"""
    phone_number_id = tenant['whatsapp_phone_number_id']
    access_token = tenant['whatsapp_access_token']  # From tenant settings
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"https://graph.facebook.com/v17.0/{phone_number_id}/messages",
            headers={"Authorization": f"Bearer {access_token}"},
            json={
                "messaging_product": "whatsapp",
                "to": phone,
                "type": "text",
                "text": {"body": message}
            }
        )
        return response.status_code == 200
```

## Recommended Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         AWS Infrastructure                           │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  Route53: api.lyo-webhook.click                                     │
│           ↓                                                          │
│  ALB: lyo-enterprise-alb                                            │
│           ↓                                                          │
│  EC2: lyo-enterprise-final                                          │
│           │                                                          │
│           ├── FastAPI Application (app/main.py)                     │
│           │   ├── /webhooks/whatsapp  (Meta webhook)                │
│           │   ├── /webhooks/chatwoot  (Chatwoot fallback)           │
│           │   ├── /health             (ALB health check)            │
│           │   └── /api/*              (Chat API)                    │
│           │                                                          │
│           └── Multi-tenant logic                                    │
│               ├── Identify tenant by phone_number_id                │
│               ├── Load tenant config from database                  │
│               └── Send response with tenant's credentials           │
│                                                                      │
│  RDS: lyo-enterprise-database                                       │
│       ├── lyo_tenants (restaurant configs)                          │
│       ├── lyo_users (customer data per tenant)                      │
│       ├── lyo_conversations (chat history)                          │
│       └── lyo_appointments (bookings)                               │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

## Configuration Required

Each tenant needs:
1. `whatsapp_business_account_id` - From Meta Business Manager
2. `whatsapp_phone_number` - The WhatsApp number
3. `whatsapp_access_token` - From Meta (stored securely)
4. `business_config` - Services, hours, etc.

## Files to Modify/Create

1. **Option A: Deploy `app/main.py`**
   - Already complete, just needs deployment

2. **Add multi-tenant support:**
   - `app/api/webhooks.py` - Add tenant lookup
   - `services/tenant_service.py` - Tenant management
   - Database queries for tenant operations

3. **Environment variables needed:**
   - `DATABASE_URL` - RDS connection string
   - `WHATSAPP_WEBHOOK_VERIFY_TOKEN` - For webhook verification

## Summary

| Component | Status | Action Needed |
|-----------|--------|---------------|
| WhatsApp webhook endpoint | ❌ Missing | Deploy `app/main.py` or add to `main_production.py` |
| Multi-tenant routing | ❌ Missing | Add tenant lookup by `phone_number_id` |
| WhatsApp response sending | ❌ Placeholder | Implement actual API call |
| DNS (api.lyo-webhook.click) | ⏳ Propagating | Wait ~5 minutes |
| SSL Certificate | ⏳ Pending | Wait for DNS, then auto-validates |
| ALB Configuration | ✅ Working | - |
| Database Schema | ✅ Complete | - |

