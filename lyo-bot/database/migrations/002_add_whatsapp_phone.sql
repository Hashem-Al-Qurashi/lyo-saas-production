-- Migration 002: Add whatsapp_phone column to businesses
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS whatsapp_phone VARCHAR(50);
