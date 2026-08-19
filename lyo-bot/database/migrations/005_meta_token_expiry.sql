-- Track when the Meta 60-day long-lived token expires so we can warn tenants
ALTER TABLE businesses
    ADD COLUMN IF NOT EXISTS meta_token_expires_at TIMESTAMPTZ;
