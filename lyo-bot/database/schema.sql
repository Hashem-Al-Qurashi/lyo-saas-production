-- Lyo SaaS Multi-Tenant Schema
-- Each business is linked to a Chatwoot account via chatwoot_account_id

BEGIN;

-- 1. Businesses (core tenant table)
CREATE TABLE IF NOT EXISTS businesses (
    id SERIAL PRIMARY KEY,
    chatwoot_account_id INTEGER UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    slug VARCHAR(100) UNIQUE,
    timezone VARCHAR(50) NOT NULL DEFAULT 'Europe/Rome',
    language VARCHAR(10) NOT NULL DEFAULT 'it',
    bot_name VARCHAR(100) NOT NULL DEFAULT 'Assistente',
    bot_persona TEXT,
    address TEXT,
    phone VARCHAR(50),
    email VARCHAR(255),
    google_calendar_id VARCHAR(255) DEFAULT 'primary',
    google_service_account_json TEXT,
    owner_email VARCHAR(255),
    status VARCHAR(20) NOT NULL DEFAULT 'active',
    settings JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2. Operators (stylists/staff per business)
CREATE TABLE IF NOT EXISTS operators (
    id SERIAL PRIMARY KEY,
    business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    technical_id VARCHAR(50) NOT NULL,
    display_name VARCHAR(100) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT true,
    sort_order INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(business_id, technical_id)
);

-- 3. Treatments (services offered per business)
CREATE TABLE IF NOT EXISTS treatments (
    id SERIAL PRIMARY KEY,
    business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    code VARCHAR(50) NOT NULL,
    name_it VARCHAR(100) NOT NULL,
    name_en VARCHAR(100),
    description_it TEXT,
    description_en TEXT,
    duration_minutes INTEGER NOT NULL,
    price DECIMAL(10,2),
    is_active BOOLEAN NOT NULL DEFAULT true,
    sort_order INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(business_id, code)
);

-- 4. Operator-Treatment mapping
CREATE TABLE IF NOT EXISTS operator_treatments (
    id SERIAL PRIMARY KEY,
    operator_id INTEGER NOT NULL REFERENCES operators(id) ON DELETE CASCADE,
    treatment_id INTEGER NOT NULL REFERENCES treatments(id) ON DELETE CASCADE,
    UNIQUE(operator_id, treatment_id)
);

-- 5. Business hours (per day of week)
CREATE TABLE IF NOT EXISTS business_hours (
    id SERIAL PRIMARY KEY,
    business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    day_of_week INTEGER NOT NULL CHECK (day_of_week BETWEEN 0 AND 6),
    is_open BOOLEAN NOT NULL DEFAULT true,
    open_time TIME,
    close_time TIME,
    UNIQUE(business_id, day_of_week)
);

-- 6. Business closures (holidays, special days)
CREATE TABLE IF NOT EXISTS business_closures (
    id SERIAL PRIMARY KEY,
    business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    closure_date DATE NOT NULL,
    reason VARCHAR(255),
    UNIQUE(business_id, closure_date)
);

-- 7. Customers (mandatory name DB per business)
CREATE TABLE IF NOT EXISTS customers (
    id SERIAL PRIMARY KEY,
    business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    phone VARCHAR(50) NOT NULL,
    first_name VARCHAR(100),
    last_name VARCHAR(100),
    platform VARCHAR(20),
    chatwoot_contact_id INTEGER,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(business_id, phone)
);

-- 8. Appointments (with operator assignment)
CREATE TABLE IF NOT EXISTS appointments (
    id SERIAL PRIMARY KEY,
    business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    operator_id INTEGER REFERENCES operators(id) ON DELETE SET NULL,
    customer_id INTEGER REFERENCES customers(id) ON DELETE SET NULL,
    customer_phone VARCHAR(50) NOT NULL,
    customer_name VARCHAR(255) NOT NULL,
    treatment_id INTEGER REFERENCES treatments(id) ON DELETE SET NULL,
    treatment_code VARCHAR(50) NOT NULL,
    appointment_date DATE NOT NULL,
    appointment_time TIME NOT NULL,
    duration_minutes INTEGER NOT NULL,
    price DECIMAL(10,2),
    status VARCHAR(20) NOT NULL DEFAULT 'confirmed',
    google_event_id VARCHAR(255),
    platform VARCHAR(20),
    chatwoot_conversation_id INTEGER,
    reminder_sent_at TIMESTAMPTZ,
    reminder_confirmed BOOLEAN NOT NULL DEFAULT false,
    reminder_confirmed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(business_id, operator_id, appointment_date, appointment_time)
);

-- 9. Conversations (bot context per customer per business)
CREATE TABLE IF NOT EXISTS conversations (
    id SERIAL PRIMARY KEY,
    business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    customer_id INTEGER REFERENCES customers(id) ON DELETE SET NULL,
    customer_phone VARCHAR(50) NOT NULL,
    chatwoot_conversation_id INTEGER,
    messages JSONB NOT NULL DEFAULT '[]',
    booking_state JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(business_id, customer_phone)
);

-- 10. Management users (for management page login)
CREATE TABLE IF NOT EXISTS management_users (
    id SERIAL PRIMARY KEY,
    business_id INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    name VARCHAR(255),
    role VARCHAR(20) NOT NULL DEFAULT 'owner',
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_operators_business ON operators(business_id);
CREATE INDEX IF NOT EXISTS idx_treatments_business ON treatments(business_id);
CREATE INDEX IF NOT EXISTS idx_customers_business_phone ON customers(business_id, phone);
CREATE INDEX IF NOT EXISTS idx_appointments_business_date ON appointments(business_id, appointment_date);
CREATE INDEX IF NOT EXISTS idx_appointments_operator_date ON appointments(operator_id, appointment_date);
CREATE INDEX IF NOT EXISTS idx_appointments_customer_phone ON appointments(customer_phone);
CREATE INDEX IF NOT EXISTS idx_appointments_status ON appointments(status);
CREATE INDEX IF NOT EXISTS idx_conversations_business_phone ON conversations(business_id, customer_phone);
CREATE INDEX IF NOT EXISTS idx_business_hours_business ON business_hours(business_id);
CREATE INDEX IF NOT EXISTS idx_business_closures_date ON business_closures(business_id, closure_date);

COMMIT;
