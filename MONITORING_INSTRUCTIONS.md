# Monitoring Instructions

## 🔍 Quick Status Check (One-Time)

Run this anytime to check current status:

```bash
./scripts/check-status.sh
```

This shows:
- ✅ DNS status
- ✅ SSL certificate status
- ✅ What to do next

## 📊 Continuous Monitoring

To continuously monitor until everything is ready:

```bash
./scripts/monitor-webhook-status.sh
```

This will:
- ✅ Check every 30 seconds
- ✅ Show real-time status updates
- ✅ Alert you when DNS is ready
- ✅ Alert you when SSL certificate is issued
- ✅ Tell you when everything is ready

**Press Ctrl+C to stop monitoring**

## 🎯 What You'll See

### While Waiting:
```
🌐 DNS Resolution: ⏳ Not ready yet (still propagating...)
🔒 SSL Certificate: ⏳ Pending validation (waiting for DNS validation...)
```

### When DNS is Ready:
```
🌐 DNS Resolution: ✅ WORKING! (Just became ready)
   ✅ HTTP endpoint responding (200)
🔒 SSL Certificate: ⏳ Pending validation (waiting for DNS validation...)
```

### When Everything is Ready:
```
🌐 DNS Resolution: ✅ Working
   ✅ HTTP endpoint responding (200)
🔒 SSL Certificate: ✅ ISSUED! (Just became ready)
   ✅ HTTPS endpoint responding (200)

🎉 ALL SYSTEMS READY!
```

## ⚡ Quick Commands

```bash
# One-time check
./scripts/check-status.sh

# Continuous monitoring (runs until ready)
./scripts/monitor-webhook-status.sh

# Auto-fix when ready
./scripts/auto-fix-ssl-when-ready.sh

# Full verification
./scripts/verify-webhook.sh
```

## 💡 Recommended Workflow

1. **Start monitoring:**
   ```bash
   ./scripts/monitor-webhook-status.sh
   ```

2. **Leave it running** - it will check every 30 seconds

3. **When you see "ALL SYSTEMS READY":**
   - Press Ctrl+C to stop monitoring
   - Run: `./scripts/auto-fix-ssl-when-ready.sh`
   - This will automatically complete the setup

4. **Test everything:**
   ```bash
   ./scripts/verify-webhook.sh
   ```

That's it! The monitoring will tell you exactly when everything is ready.

