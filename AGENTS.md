# Lyo project guidance

This repository is the production codebase for the multi-tenant Lyo salon assistant. Read [docs/codex/PROJECT_CONTEXT.md](docs/codex/PROJECT_CONTEXT.md) before changing bot, dashboard, deployment, or database behavior. Read [docs/codex/AWS_ACCESS.md](docs/codex/AWS_ACCESS.md) before production diagnostics or deployment.

## Working rules

- Investigate before changing: trace the real code path, reproduce the reported behavior when practical, and distinguish verified facts from hypotheses.
- For bugs and features, add or update focused tests first when feasible, then run relevant regression tests and manually verify user-facing behavior.
- Treat production, customer conversations, appointment data, credentials, webhooks, and database contents as sensitive. Never place secrets, access tokens, passwords, private keys, or customer data in commits, logs, screenshots, or memory documents.
- Be extra careful with the production database. Prefer read-only queries for diagnosis. Do not run migrations or mutate production records unless the current request clearly authorizes the exact action and recovery path is understood.
- Do not deploy, restart production services, change Cloudflare/Meta webhook configuration, or modify DNS merely because a local fix is ready. Obtain clear authorization for the production action and target. Verify service health afterward.
- Preserve the existing Cloudflare/CloudFront webhook routing unless the user explicitly requests an infrastructure change.
- Favor scalable, safe, long-term fixes. Keep shared WhatsApp/Instagram booking logic in a common core, with platform-specific handlers and explicit override points.
- Multi-tenant operations must carry and enforce `business_id` throughout routing, availability, booking, calendar, dashboard, and integrations. Never fall back silently to a global/default tenant.
- The active production bot source is under `deployed-live/bot/`; the dashboard source is under `lyo-bot/management/`. Confirm the live destination before copying files because older duplicate bot files remain in the repository and on EC2.
- Do not commit or push unless requested. The intended remote is Hashem's `origin`; always show the branch and destination before pushing.
- When the user challenges an architectural recommendation, evaluate whether the objection actually invalidates it. Shared code can still support per-platform overrides; change direction only when the reasoning or evidence changes.

## Verification expectations

- Report exactly what was tested, what passed, known pre-existing failures, and what remains unverified.
- For dashboard/calendar changes, perform browser-level verification when possible, including refresh/resynchronization behavior and overlapping appointments.
- For production changes, verify the relevant systemd service, health endpoint, and recent logs. Never infer that copied code is active without a successful restart/reload or equivalent proof.
