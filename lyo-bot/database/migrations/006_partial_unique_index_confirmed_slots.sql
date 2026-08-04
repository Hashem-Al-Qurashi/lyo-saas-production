-- Fix: unique constraint on appointment slots must only apply to confirmed rows.
--
-- The original full unique index treated cancelled appointments as still occupying
-- their slot, causing create_appointment to return SLOT_JUST_BOOKED whenever a
-- customer tried to rebook a slot they had just cancelled. check_availability
-- correctly filtered by status='confirmed' and returned available:True, but the
-- DB-level INSERT hit the full index and raised UniqueViolation.
--
-- Safe migration order: CREATE partial index first (no protection gap),
-- then DROP the old full index.

CREATE UNIQUE INDEX IF NOT EXISTS appointments_operator_slot_unique_confirmed
    ON appointments (business_id, operator_id, appointment_date, appointment_time)
    WHERE status = 'confirmed';

-- The original constraint was created via ADD CONSTRAINT (not standalone index),
-- so it must be dropped via ALTER TABLE, not DROP INDEX.
ALTER TABLE appointments
    DROP CONSTRAINT IF EXISTS appointments_business_id_operator_id_appointment_date_appoi_key;


-- Rollback (if needed):
--   ALTER TABLE appointments
--       ADD CONSTRAINT appointments_business_id_operator_id_appointment_date_appoi_key
--       UNIQUE (business_id, operator_id, appointment_date, appointment_time);
--   DROP INDEX IF EXISTS appointments_operator_slot_unique_confirmed;
