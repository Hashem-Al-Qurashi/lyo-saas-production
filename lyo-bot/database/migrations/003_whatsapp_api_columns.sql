-- Migration 003: Add WhatsApp Business API columns for multi-tenant routing
BEGIN;

ALTER TABLE businesses ADD COLUMN IF NOT EXISTS whatsapp_phone_number_id VARCHAR(50);
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS waba_id VARCHAR(50);
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS meta_access_token TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_businesses_wa_phone_id
    ON businesses(whatsapp_phone_number_id) WHERE whatsapp_phone_number_id IS NOT NULL;

COMMIT;
