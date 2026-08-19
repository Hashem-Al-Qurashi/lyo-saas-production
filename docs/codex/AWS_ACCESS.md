# AWS access reference

This file records identifiers and safe connection commands only. It must never contain private-key contents, AWS secret keys, application passwords, access tokens, database passwords, or customer data.

## Main Lyo EC2

- AWS account: `211425018318`
- Region: `us-east-1`
- Instance: `i-072d32267643624b9` (`lyo-enterprise-final`)
- Private IP recorded by Claude: `10.0.1.139`
- Elastic IP: `98.89.13.25`
- SSH user: `ec2-user`
- Current key used throughout the final sessions: `/home/sakr_quraish/.ssh/lyo-saas-key.pem`
- Older sessions also used `/home/claudehashem/lyo-key.pem`; treat it as a legacy fallback, not the default.

Before SSH, ensure the private key is not group/world-readable:

```bash
chmod 600 /home/sakr_quraish/.ssh/lyo-saas-key.pem
```

Read-only connectivity check:

```bash
ssh -i /home/sakr_quraish/.ssh/lyo-saas-key.pem \
  -o IdentitiesOnly=yes \
  ec2-user@98.89.13.25 \
  'hostname; systemctl is-active lyo-bot lyo-mgmt'
```

Interactive access:

```bash
ssh -i /home/sakr_quraish/.ssh/lyo-saas-key.pem \
  -o IdentitiesOnly=yes \
  ec2-user@98.89.13.25
```

Avoid `StrictHostKeyChecking=no` for normal use. Verify and retain the host key instead.

## Production layout

- Bot service: `lyo-bot.service`, recorded listening on port 8001.
- Dashboard service: `lyo-mgmt.service`, recorded listening on `127.0.0.1:8002`.
- Live bot entry point: `/home/ec2-user/salon_bot_with_booking.py`.
- Dashboard tree: `/home/ec2-user/lyo-bot/management/`.
- Environment file used by recorded commands: `/home/ec2-user/.env`.
- Dashboard URL recorded in the session: `https://manage.lyovirtualassistant.com/manage/calendar/`.
- Instagram callback recorded in the session: `https://d34dcl62ecf71w.cloudfront.net/webhook/instagram`.

## Safety notes

- SSH access uses the PEM key; it does not require the old AWS root credential CSV.
- A historical AWS root credential CSV was referenced during Claude sessions. Do not copy its values into this repository or use root credentials for routine work. Prefer a scoped IAM identity/profile and rotate legacy credentials if still valid.
- The EC2 instance returned no IAM instance role (`IMDS .../iam/security-credentials/` returned 404 on 2026-08-17). If the workload needs AWS API access, attach a least-privilege role instead of adding static/root credentials.
- `/home/ec2-user/.env` was mode `664` on 2026-08-17. Treat this as a security observation; tighten it to least privilege after explicit authorization and verify both production services afterward.
- Production deployment, service restart, DNS changes, webhook changes, and database writes require clear current-task authorization.
- For diagnostics, begin with read-only commands such as `systemctl status`, `journalctl`, `ss`, and health checks.
- The Chatwoot host is a separate EC2 system and used different SSH keys; do not use the main Lyo key or host assumptions for Chatwoot without re-verifying its access record.
