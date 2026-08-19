-- Seed data: Aura Hair Studio as first business (chatwoot_account_id = 1)

BEGIN;

INSERT INTO businesses (chatwoot_account_id, name, slug, timezone, language, bot_name, bot_persona, address, phone, email, owner_email, google_calendar_id, whatsapp_phone_number_id)
VALUES (1, 'Aura Hair Studio', 'aura-hair-studio', 'Europe/Rome', 'it', 'Simone',
        'You are Simone, a HUMAN employee at Aura Hair Studio. You are friendly, professional, and always reply in Italian unless the customer writes in English. Never reveal you are AI.',
        'Via Example 123, 20121 Milano', '+39 02 1234567', 'info@aurahair.it',
        'notifiche.lyo@gmail.com', 'primary', '961636900357709')
ON CONFLICT (chatwoot_account_id) DO UPDATE SET
    whatsapp_phone_number_id = EXCLUDED.whatsapp_phone_number_id,
    bot_persona = EXCLUDED.bot_persona;

INSERT INTO operators (business_id, technical_id, display_name, sort_order) VALUES
    ((SELECT id FROM businesses WHERE slug='aura-hair-studio'), 'operatore_1', 'Giulia', 1),
    ((SELECT id FROM businesses WHERE slug='aura-hair-studio'), 'operatore_2', 'Martina', 2),
    ((SELECT id FROM businesses WHERE slug='aura-hair-studio'), 'operatore_3', 'Sara', 3),
    ((SELECT id FROM businesses WHERE slug='aura-hair-studio'), 'operatore_4', 'Luca', 4)
ON CONFLICT (business_id, technical_id) DO NOTHING;

INSERT INTO treatments (business_id, code, name_it, name_en, duration_minutes, price, sort_order) VALUES
    ((SELECT id FROM businesses WHERE slug='aura-hair-studio'), 'taglio_donna', 'Taglio Donna', 'Women''s Haircut', 45, 60, 1),
    ((SELECT id FROM businesses WHERE slug='aura-hair-studio'), 'taglio_uomo', 'Taglio Uomo', 'Men''s Haircut', 45, 40, 2),
    ((SELECT id FROM businesses WHERE slug='aura-hair-studio'), 'piega', 'Piega', 'Styling/Blow-dry', 30, 30, 3),
    ((SELECT id FROM businesses WHERE slug='aura-hair-studio'), 'colore_base', 'Colore Base', 'Basic Color', 90, 70, 4),
    ((SELECT id FROM businesses WHERE slug='aura-hair-studio'), 'balayage', 'Balayage/Schiariture', 'Balayage/Highlights', 150, 130, 5),
    ((SELECT id FROM businesses WHERE slug='aura-hair-studio'), 'trattamento_ristrutturante', 'Trattamento Ristrutturante', 'Restructuring Treatment', 45, 45, 6),
    ((SELECT id FROM businesses WHERE slug='aura-hair-studio'), 'trattamento_cute', 'Trattamento Cute', 'Scalp Treatment', 30, 40, 7)
ON CONFLICT (business_id, code) DO NOTHING;

-- Operator-Treatment mappings
-- Giulia: all treatments
INSERT INTO operator_treatments (operator_id, treatment_id)
SELECT o.id, t.id FROM operators o, treatments t
WHERE o.technical_id = 'operatore_1' AND o.business_id = t.business_id
ON CONFLICT DO NOTHING;

-- Martina: all treatments
INSERT INTO operator_treatments (operator_id, treatment_id)
SELECT o.id, t.id FROM operators o, treatments t
WHERE o.technical_id = 'operatore_2' AND o.business_id = t.business_id
ON CONFLICT DO NOTHING;

-- Sara: taglio_donna, taglio_uomo, piega, trattamento_ristrutturante, trattamento_cute
INSERT INTO operator_treatments (operator_id, treatment_id)
SELECT o.id, t.id FROM operators o, treatments t
WHERE o.technical_id = 'operatore_3' AND o.business_id = t.business_id
AND t.code IN ('taglio_donna', 'taglio_uomo', 'piega', 'trattamento_ristrutturante', 'trattamento_cute')
ON CONFLICT DO NOTHING;

-- Luca: taglio_donna, taglio_uomo, piega
INSERT INTO operator_treatments (operator_id, treatment_id)
SELECT o.id, t.id FROM operators o, treatments t
WHERE o.technical_id = 'operatore_4' AND o.business_id = t.business_id
AND t.code IN ('taglio_donna', 'taglio_uomo', 'piega')
ON CONFLICT DO NOTHING;

-- Business hours (Tue-Sat open, Mon+Sun closed)
INSERT INTO business_hours (business_id, day_of_week, is_open, open_time, close_time) VALUES
    ((SELECT id FROM businesses WHERE slug='aura-hair-studio'), 0, false, NULL, NULL),
    ((SELECT id FROM businesses WHERE slug='aura-hair-studio'), 1, true, '09:00', '19:00'),
    ((SELECT id FROM businesses WHERE slug='aura-hair-studio'), 2, true, '09:00', '19:00'),
    ((SELECT id FROM businesses WHERE slug='aura-hair-studio'), 3, true, '09:00', '19:00'),
    ((SELECT id FROM businesses WHERE slug='aura-hair-studio'), 4, true, '09:00', '20:00'),
    ((SELECT id FROM businesses WHERE slug='aura-hair-studio'), 5, true, '09:00', '18:00'),
    ((SELECT id FROM businesses WHERE slug='aura-hair-studio'), 6, false, NULL, NULL)
ON CONFLICT (business_id, day_of_week) DO NOTHING;

-- Management user for Aura Hair Studio (password: admin123)
INSERT INTO management_users (business_id, email, password_hash, name, role)
VALUES (
    (SELECT id FROM businesses WHERE slug='aura-hair-studio'),
    'admin@aura.it',
    '$2b$12$5a6FUaAULPkIB/DWxdp7iucURkwKrwPhhrta4CmNz1UKOxqXpJMcS',
    'Admin',
    'owner'
) ON CONFLICT (email) DO NOTHING;

COMMIT;
