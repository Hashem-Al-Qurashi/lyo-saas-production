-- Add per-business Instagram credentials for multi-tenant IG Messaging API.
--
-- instagram_page_id   : Instagram Business Account ID (routes incoming webhooks)
-- instagram_access_token : Page Access Token with instagram_manage_messages permission
-- instagram_token_expires_at : Expiry timestamp (Page tokens are long-lived / 60 day user tokens)
--
-- Webhook routing: entry[].id in Meta webhook payload == instagram_page_id

ALTER TABLE businesses
    ADD COLUMN IF NOT EXISTS instagram_page_id VARCHAR(50),
    ADD COLUMN IF NOT EXISTS instagram_access_token TEXT,
    ADD COLUMN IF NOT EXISTS instagram_token_expires_at TIMESTAMPTZ;

CREATE UNIQUE INDEX IF NOT EXISTS idx_businesses_ig_page_id
    ON businesses (instagram_page_id)
    WHERE instagram_page_id IS NOT NULL;

-- Rollback:
--   DROP INDEX IF EXISTS idx_businesses_ig_page_id;
--   ALTER TABLE businesses
--       DROP COLUMN IF EXISTS instagram_page_id,
--       DROP COLUMN IF EXISTS instagram_access_token,
--       DROP COLUMN IF EXISTS instagram_token_expires_at;
