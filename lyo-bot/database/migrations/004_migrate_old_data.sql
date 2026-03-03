-- Migration 004: Migrate data from salon_appointments/salon_conversations to multi-tenant tables
-- Run AFTER schema.sql and seed.sql have been applied
-- SAFE TO RE-RUN: Uses ON CONFLICT DO NOTHING / idempotent checks

BEGIN;

-- Migrate salon_appointments → appointments for Aura (business_id from businesses table)
DO $$
DECLARE
    aura_id INTEGER;
    migrated_count INTEGER;
BEGIN
    SELECT id INTO aura_id FROM businesses WHERE slug = 'aura-hair-studio';

    IF aura_id IS NULL THEN
        RAISE NOTICE 'No business found with slug aura-hair-studio. Skipping appointment migration.';
        RETURN;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'salon_appointments') THEN
        RAISE NOTICE 'salon_appointments table does not exist. Skipping.';
        RETURN;
    END IF;

    INSERT INTO appointments (
        business_id, customer_phone, customer_name,
        treatment_code, treatment_name,
        appointment_date, appointment_time,
        duration_minutes, price, status,
        google_event_id, platform,
        reminder_sent_at, reminder_confirmed, reminder_confirmed_at,
        created_at
    )
    SELECT
        aura_id,
        sa.customer_phone,
        sa.customer_name,
        sa.service_type,
        COALESCE(
            (SELECT t.name_it FROM treatments t WHERE t.code = sa.service_type AND t.business_id = aura_id),
            sa.service_type
        ),
        sa.appointment_date,
        sa.appointment_time,
        COALESCE(sa.duration_minutes, 45),
        COALESCE(sa.price, 0),
        sa.status,
        sa.google_event_id,
        COALESCE(sa.platform, 'whatsapp'),
        sa.reminder_sent_at,
        COALESCE(sa.reminder_confirmed, false),
        sa.reminder_confirmed_at,
        COALESCE(sa.created_at, NOW())
    FROM salon_appointments sa
    WHERE NOT EXISTS (
        -- Skip rows already migrated (match on date+time+phone since operator_id is NULL
        -- and NULL != NULL defeats the UNIQUE constraint for duplicate detection)
        SELECT 1 FROM appointments a
        WHERE a.business_id = aura_id
          AND a.customer_phone = sa.customer_phone
          AND a.appointment_date = sa.appointment_date
          AND a.appointment_time = sa.appointment_time
    );

    GET DIAGNOSTICS migrated_count = ROW_COUNT;
    RAISE NOTICE 'Migrated % appointments from salon_appointments to appointments', migrated_count;
END $$;

-- Migrate salon_conversations → conversations
DO $$
DECLARE
    aura_id INTEGER;
    conv_count INTEGER;
BEGIN
    SELECT id INTO aura_id FROM businesses WHERE slug = 'aura-hair-studio';

    IF aura_id IS NULL THEN
        RAISE NOTICE 'No business found. Skipping conversation migration.';
        RETURN;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'salon_conversations') THEN
        RAISE NOTICE 'salon_conversations table does not exist. Skipping.';
        RETURN;
    END IF;

    INSERT INTO conversations (business_id, customer_phone, messages, created_at, updated_at)
    SELECT
        aura_id,
        sc.phone,
        jsonb_agg(
            jsonb_build_object(
                'role', 'user', 'content', COALESCE(sc.message, '')
            ) || jsonb_build_object(
                'role', 'assistant', 'content', COALESCE(sc.response, '')
            )
            ORDER BY sc.timestamp
        ),
        MIN(sc.timestamp),
        MAX(sc.timestamp)
    FROM salon_conversations sc
    GROUP BY sc.phone
    ON CONFLICT (business_id, customer_phone) DO NOTHING;

    GET DIAGNOSTICS conv_count = ROW_COUNT;
    RAISE NOTICE 'Migrated conversations for % unique phones', conv_count;
END $$;

COMMIT;
