-- ============================================================================
-- LYO SAAS MULTI-TENANT DATABASE SCHEMA
-- Enterprise-grade PostgreSQL schema for scalable SaaS platform
-- ============================================================================

-- Enable UUID extension for unique identifiers
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_stat_statements";

-- ============================================================================
-- TENANTS & ORGANIZATIONS (Business-level isolation)
-- ============================================================================

-- Tenants table: Each restaurant/salon is a tenant
CREATE TABLE IF NOT EXISTS lyo_tenants (
    tenant_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    business_name VARCHAR(255) NOT NULL,
    business_type VARCHAR(50) NOT NULL, -- salon, restaurant, medical, etc.
    subdomain VARCHAR(50) UNIQUE NOT NULL, -- e.g., "bellavita" for bellavita.lyo.com
    status VARCHAR(20) DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'trial', 'cancelled')),
    plan VARCHAR(50) DEFAULT 'starter' CHECK (plan IN ('trial', 'starter', 'professional', 'enterprise')),
    
    -- Contact & billing info
    owner_name VARCHAR(255),
    owner_email VARCHAR(255) UNIQUE NOT NULL,
    phone_number VARCHAR(20),
    
    -- Business details
    address TEXT,
    city VARCHAR(100),
    country VARCHAR(100) DEFAULT 'Italy',
    timezone VARCHAR(50) DEFAULT 'Europe/Rome',
    language VARCHAR(10) DEFAULT 'it',
    
    -- Subscription & billing
    subscription_starts_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    subscription_ends_at TIMESTAMP WITH TIME ZONE,
    monthly_price DECIMAL(8,2) DEFAULT 79.00,
    billing_cycle VARCHAR(20) DEFAULT 'monthly',
    
    -- API & webhook configuration  
    webhook_url TEXT,
    whatsapp_phone_number VARCHAR(20),
    whatsapp_business_account_id VARCHAR(100),
    
    -- System fields
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    -- Business configuration (services, hours, etc.)
    business_config JSONB DEFAULT '{}'::JSONB,
    settings JSONB DEFAULT '{}'::JSONB
);

-- ============================================================================
-- USERS (Customer/end-user data with tenant isolation)
-- ============================================================================

CREATE TABLE IF NOT EXISTS lyo_users (
    user_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES lyo_tenants(tenant_id) ON DELETE CASCADE,
    
    -- User identification (phone is primary identifier for WhatsApp)
    phone_number VARCHAR(20) NOT NULL,
    name VARCHAR(255),
    email VARCHAR(255),
    
    -- Platform & preferences
    platform VARCHAR(20) DEFAULT 'whatsapp' CHECK (platform IN ('whatsapp', 'instagram', 'telegram', 'web')),
    language_preference VARCHAR(10) DEFAULT 'it',
    
    -- Engagement tracking
    interaction_count INTEGER DEFAULT 0,
    total_bookings INTEGER DEFAULT 0,
    last_booking_date TIMESTAMP WITH TIME ZONE,
    
    -- Customer lifecycle
    customer_segment VARCHAR(50) DEFAULT 'new' CHECK (customer_segment IN ('new', 'regular', 'vip', 'inactive')),
    customer_value DECIMAL(10,2) DEFAULT 0.00,
    
    -- System fields
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_seen TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    -- Additional user data (preferences, notes, etc.)
    user_data JSONB DEFAULT '{}'::JSONB,
    
    -- Composite unique constraint: one user per phone per tenant
    UNIQUE(tenant_id, phone_number)
);

-- ============================================================================
-- CONVERSATIONS (Chat sessions with memory persistence)
-- ============================================================================

CREATE TABLE IF NOT EXISTS lyo_conversations (
    conversation_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES lyo_tenants(tenant_id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES lyo_users(user_id) ON DELETE CASCADE,
    
    -- Session management
    session_id VARCHAR(100) NOT NULL, -- External session ID (WhatsApp/platform specific)
    platform VARCHAR(20) NOT NULL DEFAULT 'whatsapp',
    
    -- Conversation state
    conversation_status VARCHAR(20) DEFAULT 'active' CHECK (conversation_status IN ('active', 'completed', 'abandoned')),
    conversation_context JSONB DEFAULT '{}'::JSONB, -- Current booking state, preferences
    
    -- Memory & history
    conversation_history JSONB DEFAULT '[]'::JSONB, -- Full message history
    conversation_summary JSONB DEFAULT '{}'::JSONB, -- AI-generated summary
    
    -- Performance tracking
    message_count INTEGER DEFAULT 0,
    response_time_avg DECIMAL(8,2) DEFAULT 0.0, -- Average response time in seconds
    
    -- Timing
    started_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_message_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    ended_at TIMESTAMP WITH TIME ZONE,
    
    -- Composite index for efficient lookups
    UNIQUE(tenant_id, session_id)
);

-- ============================================================================
-- APPOINTMENTS (Booking data with Calendar integration)
-- ============================================================================

CREATE TABLE IF NOT EXISTS lyo_appointments (
    appointment_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES lyo_tenants(tenant_id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES lyo_users(user_id) ON DELETE CASCADE,
    conversation_id UUID REFERENCES lyo_conversations(conversation_id),
    
    -- Appointment details
    customer_name VARCHAR(255) NOT NULL,
    customer_phone VARCHAR(20) NOT NULL,
    customer_email VARCHAR(255),
    
    -- Service details
    service_type VARCHAR(100) NOT NULL,
    service_name VARCHAR(255) NOT NULL,
    service_price DECIMAL(8,2),
    service_duration INTEGER NOT NULL DEFAULT 60, -- minutes
    
    -- Schedule
    appointment_date DATE NOT NULL,
    appointment_time TIME NOT NULL,
    appointment_timezone VARCHAR(50) DEFAULT 'Europe/Rome',
    
    -- Status tracking
    status VARCHAR(20) DEFAULT 'confirmed' CHECK (status IN ('pending', 'confirmed', 'completed', 'cancelled', 'no_show')),
    cancellation_reason VARCHAR(255),
    
    -- External system integration
    google_calendar_event_id VARCHAR(255),
    external_booking_ref VARCHAR(100),
    
    -- Payment tracking
    payment_status VARCHAR(20) DEFAULT 'pending' CHECK (payment_status IN ('pending', 'paid', 'partial', 'refunded')),
    payment_amount DECIMAL(8,2),
    payment_method VARCHAR(50),
    
    -- System fields
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    -- Additional notes and metadata
    notes JSONB DEFAULT '{}'::JSONB,
    metadata JSONB DEFAULT '{}'::JSONB
);

-- ============================================================================
-- BUSINESS SERVICES (Per-tenant service catalog)
-- ============================================================================

CREATE TABLE IF NOT EXISTS lyo_business_services (
    service_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES lyo_tenants(tenant_id) ON DELETE CASCADE,
    
    -- Service details
    service_code VARCHAR(50) NOT NULL, -- haircut, coloring, manicure
    service_name_en VARCHAR(255) NOT NULL,
    service_name_it VARCHAR(255) NOT NULL,
    description_en TEXT,
    description_it TEXT,
    
    -- Pricing & duration
    base_price DECIMAL(8,2) NOT NULL,
    currency VARCHAR(3) DEFAULT 'EUR',
    duration_minutes INTEGER NOT NULL,
    
    -- Availability
    is_active BOOLEAN DEFAULT true,
    requires_consultation BOOLEAN DEFAULT false,
    advance_booking_hours INTEGER DEFAULT 24, -- Minimum advance booking time
    
    -- Category & tags
    category VARCHAR(100), -- hair, nails, facial, massage
    tags TEXT[], -- trending, premium, quick
    
    -- System fields
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    -- Business-specific service code must be unique per tenant
    UNIQUE(tenant_id, service_code)
);

-- ============================================================================
-- BUSINESS HOURS (Operating schedule per tenant)
-- ============================================================================

CREATE TABLE IF NOT EXISTS lyo_business_hours (
    schedule_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES lyo_tenants(tenant_id) ON DELETE CASCADE,
    
    -- Day of week (0 = Sunday, 1 = Monday, etc.)
    day_of_week INTEGER NOT NULL CHECK (day_of_week >= 0 AND day_of_week <= 6),
    
    -- Operating hours
    is_open BOOLEAN DEFAULT true,
    open_time TIME,
    close_time TIME,
    
    -- Break times (lunch, etc.)
    break_start_time TIME,
    break_end_time TIME,
    
    -- Special notes
    notes VARCHAR(255),
    
    -- Effective dates (for seasonal changes)
    effective_from DATE DEFAULT CURRENT_DATE,
    effective_until DATE,
    
    -- System fields  
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    -- One schedule per day per tenant (can have multiple for date ranges)
    UNIQUE(tenant_id, day_of_week, effective_from)
);

-- ============================================================================
-- ANALYTICS & METRICS (Business intelligence)
-- ============================================================================

CREATE TABLE IF NOT EXISTS lyo_analytics (
    metric_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID NOT NULL REFERENCES lyo_tenants(tenant_id) ON DELETE CASCADE,
    
    -- Metric identification
    metric_name VARCHAR(100) NOT NULL, -- conversations_started, appointments_booked, revenue
    metric_category VARCHAR(50) NOT NULL, -- engagement, conversion, revenue, performance
    
    -- Metric value & dimensions
    metric_value DECIMAL(12,4) NOT NULL,
    metric_unit VARCHAR(20), -- count, percentage, euros, seconds
    
    -- Time dimensions
    recorded_date DATE NOT NULL DEFAULT CURRENT_DATE,
    recorded_hour INTEGER, -- For hourly metrics
    
    -- Additional dimensions
    dimensions JSONB DEFAULT '{}'::JSONB, -- service_type, user_segment, etc.
    
    -- System fields
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    -- Prevent duplicate metrics for same time period
    UNIQUE(tenant_id, metric_name, recorded_date, recorded_hour)
);

-- ============================================================================
-- SYSTEM AUDIT LOG (Track all changes for compliance)
-- ============================================================================

CREATE TABLE IF NOT EXISTS lyo_audit_log (
    audit_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id UUID REFERENCES lyo_tenants(tenant_id),
    
    -- Event details
    event_type VARCHAR(50) NOT NULL, -- CREATE, UPDATE, DELETE, LOGIN, etc.
    table_name VARCHAR(100) NOT NULL,
    record_id UUID,
    
    -- Actor (who made the change)
    actor_type VARCHAR(20) NOT NULL, -- system, user, admin
    actor_id VARCHAR(100),
    
    -- Change details
    old_values JSONB,
    new_values JSONB,
    
    -- Context
    user_agent TEXT,
    ip_address INET,
    request_id UUID,
    
    -- Timing
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ============================================================================
-- INDEXES FOR PERFORMANCE
-- ============================================================================

-- Tenants
CREATE INDEX IF NOT EXISTS idx_lyo_tenants_status ON lyo_tenants(status);
CREATE INDEX IF NOT EXISTS idx_lyo_tenants_plan ON lyo_tenants(plan);
CREATE INDEX IF NOT EXISTS idx_lyo_tenants_subdomain ON lyo_tenants(subdomain);

-- Users
CREATE INDEX IF NOT EXISTS idx_lyo_users_tenant_phone ON lyo_users(tenant_id, phone_number);
CREATE INDEX IF NOT EXISTS idx_lyo_users_last_seen ON lyo_users(last_seen);
CREATE INDEX IF NOT EXISTS idx_lyo_users_language ON lyo_users(language_preference);
CREATE INDEX IF NOT EXISTS idx_lyo_users_segment ON lyo_users(customer_segment);

-- Conversations
CREATE INDEX IF NOT EXISTS idx_lyo_conversations_tenant_user ON lyo_conversations(tenant_id, user_id);
CREATE INDEX IF NOT EXISTS idx_lyo_conversations_session ON lyo_conversations(session_id);
CREATE INDEX IF NOT EXISTS idx_lyo_conversations_last_message ON lyo_conversations(last_message_at);
CREATE INDEX IF NOT EXISTS idx_lyo_conversations_status ON lyo_conversations(conversation_status);

-- Appointments
CREATE INDEX IF NOT EXISTS idx_lyo_appointments_tenant ON lyo_appointments(tenant_id);
CREATE INDEX IF NOT EXISTS idx_lyo_appointments_date ON lyo_appointments(appointment_date);
CREATE INDEX IF NOT EXISTS idx_lyo_appointments_status ON lyo_appointments(status);
CREATE INDEX IF NOT EXISTS idx_lyo_appointments_customer ON lyo_appointments(tenant_id, customer_phone);
CREATE INDEX IF NOT EXISTS idx_lyo_appointments_service ON lyo_appointments(service_type);

-- Services
CREATE INDEX IF NOT EXISTS idx_lyo_services_tenant_active ON lyo_business_services(tenant_id, is_active);
CREATE INDEX IF NOT EXISTS idx_lyo_services_category ON lyo_business_services(category);

-- Business Hours
CREATE INDEX IF NOT EXISTS idx_lyo_hours_tenant_day ON lyo_business_hours(tenant_id, day_of_week);
CREATE INDEX IF NOT EXISTS idx_lyo_hours_effective ON lyo_business_hours(effective_from, effective_until);

-- Analytics
CREATE INDEX IF NOT EXISTS idx_lyo_analytics_tenant_date ON lyo_analytics(tenant_id, recorded_date);
CREATE INDEX IF NOT EXISTS idx_lyo_analytics_metric ON lyo_analytics(metric_name, recorded_date);

-- Audit Log
CREATE INDEX IF NOT EXISTS idx_lyo_audit_tenant_date ON lyo_audit_log(tenant_id, created_at);
CREATE INDEX IF NOT EXISTS idx_lyo_audit_table_record ON lyo_audit_log(table_name, record_id);

-- ============================================================================
-- ROW LEVEL SECURITY (RLS) for multi-tenant isolation
-- ============================================================================

-- Enable RLS on all tenant-specific tables
ALTER TABLE lyo_users ENABLE ROW LEVEL SECURITY;
ALTER TABLE lyo_conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE lyo_appointments ENABLE ROW LEVEL SECURITY;
ALTER TABLE lyo_business_services ENABLE ROW LEVEL SECURITY;
ALTER TABLE lyo_business_hours ENABLE ROW LEVEL SECURITY;
ALTER TABLE lyo_analytics ENABLE ROW LEVEL SECURITY;

-- Create tenant isolation policies (applications must set current_tenant_id)
CREATE POLICY tenant_isolation_users ON lyo_users
    USING (tenant_id = current_setting('app.current_tenant_id')::UUID);

CREATE POLICY tenant_isolation_conversations ON lyo_conversations
    USING (tenant_id = current_setting('app.current_tenant_id')::UUID);

CREATE POLICY tenant_isolation_appointments ON lyo_appointments
    USING (tenant_id = current_setting('app.current_tenant_id')::UUID);

CREATE POLICY tenant_isolation_services ON lyo_business_services
    USING (tenant_id = current_setting('app.current_tenant_id')::UUID);

CREATE POLICY tenant_isolation_hours ON lyo_business_hours
    USING (tenant_id = current_setting('app.current_tenant_id')::UUID);

CREATE POLICY tenant_isolation_analytics ON lyo_analytics
    USING (tenant_id = current_setting('app.current_tenant_id')::UUID);

-- ============================================================================
-- FUNCTIONS & TRIGGERS for automated maintenance
-- ============================================================================

-- Update timestamp trigger function
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Add updated_at triggers to relevant tables
CREATE TRIGGER update_lyo_tenants_updated_at BEFORE UPDATE ON lyo_tenants
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_lyo_appointments_updated_at BEFORE UPDATE ON lyo_appointments
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_lyo_services_updated_at BEFORE UPDATE ON lyo_business_services
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_lyo_hours_updated_at BEFORE UPDATE ON lyo_business_hours
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Customer value calculation function
CREATE OR REPLACE FUNCTION calculate_customer_value(p_user_id UUID, p_tenant_id UUID)
RETURNS DECIMAL(10,2) AS $$
DECLARE
    total_value DECIMAL(10,2) := 0.00;
BEGIN
    SELECT COALESCE(SUM(service_price), 0.00)
    INTO total_value
    FROM lyo_appointments
    WHERE user_id = p_user_id 
    AND tenant_id = p_tenant_id
    AND status IN ('completed', 'confirmed');
    
    -- Update user record
    UPDATE lyo_users
    SET customer_value = total_value,
        customer_segment = CASE
            WHEN total_value = 0 THEN 'new'
            WHEN total_value < 200 THEN 'regular'
            WHEN total_value >= 200 THEN 'vip'
            ELSE 'regular'
        END
    WHERE user_id = p_user_id AND tenant_id = p_tenant_id;
    
    RETURN total_value;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- INSERT SAMPLE DATA for testing
-- ============================================================================

-- Insert demo tenant (Salon Bella Vita)
INSERT INTO lyo_tenants (
    tenant_id,
    business_name,
    business_type,
    subdomain,
    owner_name,
    owner_email,
    phone_number,
    address,
    city,
    country,
    whatsapp_phone_number,
    business_config
) VALUES (
    'a0000000-0000-0000-0000-000000000001'::UUID,
    'Salon Bella Vita',
    'beauty_salon',
    'bellavita',
    'Maria Rossi',
    'maria@bellavita.it',
    '+39 02 1234567',
    'Via Roma 123',
    'Milano',
    'Italy',
    '+39 345 1234567',
    '{
        "services": {
            "haircut": {
                "name_en": "Women''s haircut",
                "name_it": "Taglio donna",
                "price": 35.00,
                "duration": 60
            },
            "styling": {
                "name_en": "Hair styling",
                "name_it": "Piega",
                "price": 20.00,
                "duration": 30
            },
            "coloring": {
                "name_en": "Hair coloring",
                "name_it": "Colore",
                "price": 80.00,
                "duration": 120
            }
        },
        "hours": {
            "monday": "09:00-19:00",
            "tuesday": "09:00-19:00",
            "wednesday": "closed",
            "thursday": "09:00-19:00",
            "friday": "09:00-20:00",
            "saturday": "09:00-18:00",
            "sunday": "closed"
        },
        "contact": {
            "address": "Via Roma 123, 20121 Milano",
            "phone": "+39 02 1234567",
            "email": "info@bellavita.it"
        }
    }'::JSONB
) ON CONFLICT (subdomain) DO NOTHING;

-- Insert business services for demo tenant
INSERT INTO lyo_business_services (tenant_id, service_code, service_name_en, service_name_it, base_price, duration_minutes, category) VALUES
('a0000000-0000-0000-0000-000000000001'::UUID, 'haircut', 'Women''s Haircut', 'Taglio Donna', 35.00, 60, 'hair'),
('a0000000-0000-0000-0000-000000000001'::UUID, 'styling', 'Hair Styling', 'Piega', 20.00, 30, 'hair'),
('a0000000-0000-0000-0000-000000000001'::UUID, 'coloring', 'Hair Coloring', 'Colore', 80.00, 120, 'hair'),
('a0000000-0000-0000-0000-000000000001'::UUID, 'highlights', 'Highlights', 'Colpi di Sole', 60.00, 90, 'hair'),
('a0000000-0000-0000-0000-000000000001'::UUID, 'treatment', 'Hair Treatment', 'Trattamento', 25.00, 30, 'hair')
ON CONFLICT (tenant_id, service_code) DO NOTHING;

-- Insert business hours for demo tenant
INSERT INTO lyo_business_hours (tenant_id, day_of_week, is_open, open_time, close_time) VALUES
('a0000000-0000-0000-0000-000000000001'::UUID, 1, true, '09:00', '19:00'), -- Monday
('a0000000-0000-0000-0000-000000000001'::UUID, 2, true, '09:00', '19:00'), -- Tuesday
('a0000000-0000-0000-0000-000000000001'::UUID, 3, false, null, null),    -- Wednesday (closed)
('a0000000-0000-0000-0000-000000000001'::UUID, 4, true, '09:00', '19:00'), -- Thursday
('a0000000-0000-0000-0000-000000000001'::UUID, 5, true, '09:00', '20:00'), -- Friday
('a0000000-0000-0000-0000-000000000001'::UUID, 6, true, '09:00', '18:00'), -- Saturday
('a0000000-0000-0000-0000-000000000001'::UUID, 0, false, null, null)      -- Sunday (closed)
ON CONFLICT (tenant_id, day_of_week, effective_from) DO NOTHING;

-- ============================================================================
-- PERFORMANCE MONITORING VIEWS
-- ============================================================================

-- Tenant performance summary
CREATE OR REPLACE VIEW lyo_tenant_metrics AS
SELECT 
    t.tenant_id,
    t.business_name,
    t.plan,
    COUNT(DISTINCT u.user_id) as total_customers,
    COUNT(DISTINCT c.conversation_id) as total_conversations,
    COUNT(DISTINCT a.appointment_id) as total_appointments,
    COALESCE(SUM(a.service_price), 0) as total_revenue,
    COUNT(DISTINCT CASE WHEN u.last_seen >= CURRENT_DATE - INTERVAL '30 days' THEN u.user_id END) as active_customers,
    COUNT(DISTINCT CASE WHEN a.appointment_date >= CURRENT_DATE - INTERVAL '30 days' AND a.status = 'completed' THEN a.appointment_id END) as recent_appointments
FROM lyo_tenants t
LEFT JOIN lyo_users u ON t.tenant_id = u.tenant_id
LEFT JOIN lyo_conversations c ON t.tenant_id = c.tenant_id
LEFT JOIN lyo_appointments a ON t.tenant_id = a.tenant_id
GROUP BY t.tenant_id, t.business_name, t.plan;

-- Daily appointment summary
CREATE OR REPLACE VIEW lyo_daily_appointments AS
SELECT 
    tenant_id,
    appointment_date,
    COUNT(*) as total_appointments,
    COUNT(CASE WHEN status = 'confirmed' THEN 1 END) as confirmed,
    COUNT(CASE WHEN status = 'completed' THEN 1 END) as completed,
    COUNT(CASE WHEN status = 'cancelled' THEN 1 END) as cancelled,
    COALESCE(SUM(service_price), 0) as daily_revenue
FROM lyo_appointments
WHERE appointment_date >= CURRENT_DATE - INTERVAL '90 days'
GROUP BY tenant_id, appointment_date
ORDER BY tenant_id, appointment_date DESC;

-- Customer engagement levels
CREATE OR REPLACE VIEW lyo_customer_engagement AS
SELECT 
    u.tenant_id,
    u.customer_segment,
    COUNT(*) as customer_count,
    AVG(u.interaction_count) as avg_interactions,
    AVG(u.total_bookings) as avg_bookings,
    AVG(u.customer_value) as avg_customer_value,
    COUNT(CASE WHEN u.last_seen >= CURRENT_DATE - INTERVAL '7 days' THEN 1 END) as active_weekly,
    COUNT(CASE WHEN u.last_seen >= CURRENT_DATE - INTERVAL '30 days' THEN 1 END) as active_monthly
FROM lyo_users u
GROUP BY u.tenant_id, u.customer_segment
ORDER BY u.tenant_id, u.customer_segment;