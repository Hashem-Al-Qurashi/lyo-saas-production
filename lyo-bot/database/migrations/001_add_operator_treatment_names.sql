-- Migration 001: Add operator_name and treatment_name to appointments
-- These columns are referenced by booking.py but were missing from the original schema.

ALTER TABLE appointments ADD COLUMN IF NOT EXISTS operator_name VARCHAR(255);
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS treatment_name VARCHAR(255);
