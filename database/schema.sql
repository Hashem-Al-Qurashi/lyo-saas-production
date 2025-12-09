-- LYO POSTGRESQL SCHEMA
-- Simple, efficient schema for conversation memory and user management

-- Users table: Store user profiles and preferences
CREATE TABLE IF NOT EXISTS lyo_users (
    user_id VARCHAR(50) PRIMARY KEY,
    name VARCHAR(255),
    language_preference VARCHAR(10) DEFAULT 'italian',
    phone_number VARCHAR(20),
    platform VARCHAR(20) DEFAULT 'whatsapp',
    interaction_count INTEGER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_seen TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    user_data JSONB DEFAULT '{}'::JSONB
);

-- Conversations table: Store complete conversation history
CREATE TABLE IF NOT EXISTS lyo_conversations (
    session_id VARCHAR(50) PRIMARY KEY,
    user_id VARCHAR(50) REFERENCES lyo_users(user_id) ON DELETE CASCADE,
    conversation_history JSONB DEFAULT '[]'::JSONB,
    current_booking_state JSONB DEFAULT '{}'::JSONB,
    conversation_summary JSONB DEFAULT '{}'::JSONB,
    last_message_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Appointments table: Store actual appointments (integrates with Google Calendar)
CREATE TABLE IF NOT EXISTS lyo_appointments (
    id SERIAL PRIMARY KEY,
    user_id VARCHAR(50) REFERENCES lyo_users(user_id),
    customer_name VARCHAR(255),
    service_type VARCHAR(100),
    appointment_date DATE,
    appointment_time TIME,
    duration_minutes INTEGER DEFAULT 60,
    status VARCHAR(20) DEFAULT 'confirmed',
    google_calendar_event_id VARCHAR(255),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    notes JSONB DEFAULT '{}'::JSONB
);

-- Business configurations table: Support multiple businesses
CREATE TABLE IF NOT EXISTS lyo_business_configs (
    business_id VARCHAR(50) PRIMARY KEY,
    business_name VARCHAR(255) NOT NULL,
    business_type VARCHAR(50) NOT NULL, -- salon, restaurant, medical, etc.
    services JSONB NOT NULL,
    business_info JSONB NOT NULL,
    settings JSONB DEFAULT '{}'::JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_lyo_users_last_seen ON lyo_users(last_seen);
CREATE INDEX IF NOT EXISTS idx_lyo_users_language ON lyo_users(language_preference);

CREATE INDEX IF NOT EXISTS idx_lyo_conversations_user_id ON lyo_conversations(user_id);
CREATE INDEX IF NOT EXISTS idx_lyo_conversations_last_message ON lyo_conversations(last_message_at);

CREATE INDEX IF NOT EXISTS idx_lyo_appointments_user_id ON lyo_appointments(user_id);
CREATE INDEX IF NOT EXISTS idx_lyo_appointments_date ON lyo_appointments(appointment_date);
CREATE INDEX IF NOT EXISTS idx_lyo_appointments_status ON lyo_appointments(status);

-- Insert default business configuration (Salon Bella Vita)
INSERT INTO lyo_business_configs (
    business_id, 
    business_name, 
    business_type, 
    services, 
    business_info
) VALUES (
    'default_salon',
    'Salon Bella Vita',
    'beauty_salon',
    '{
        "haircut": {
            "name_en": "Women''s haircut",
            "name_it": "Taglio donna", 
            "price": "€35",
            "duration": 60,
            "description_en": "Professional haircut with style consultation",
            "description_it": "Taglio professionale con consulenza di stile"
        },
        "styling": {
            "name_en": "Hair styling",
            "name_it": "Piega",
            "price": "€20", 
            "duration": 30,
            "description_en": "Professional styling and blow-dry",
            "description_it": "Styling e piega professionale"
        },
        "coloring": {
            "name_en": "Hair coloring",
            "name_it": "Colore",
            "price": "€80",
            "duration": 120,
            "description_en": "Complete hair coloring with premium products", 
            "description_it": "Colorazione completa con prodotti premium"
        }
    }'::JSONB,
    '{
        "address": "Via Roma 123, 20121 Milano",
        "phone": "+39 02 1234567",
        "email": "info@bellasalon.it",
        "hours": {
            "monday": "09:00-19:00",
            "tuesday": "09:00-19:00", 
            "wednesday": "closed",
            "thursday": "09:00-19:00",
            "friday": "09:00-20:00",
            "saturday": "09:00-18:00",
            "sunday": "closed"
        },
        "hours_en": "Mon-Tue 9am-7pm, Wed closed, Thu-Fri 9am-8pm, Sat 9am-6pm",
        "hours_it": "Lun-Mar 9-19, Mer chiuso, Gio-Ven 9-20, Sab 9-18",
        "payment_methods": ["cash", "cards", "satispay"],
        "policies": {
            "cancellation_en": "Free cancellation up to 24 hours before",
            "cancellation_it": "Disdetta gratuita fino a 24 ore prima"
        }
    }'::JSONB
) ON CONFLICT (business_id) DO NOTHING;