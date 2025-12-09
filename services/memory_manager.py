"""
MEMORY MANAGER: Two-tier system for WhatsApp/Instagram chatbots
- Redis: Active conversations (24h expiry)
- PostgreSQL: Customer profiles (permanent)
"""
import asyncio
import json
import redis
import asyncpg
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import logging

logger = logging.getLogger(__name__)

class RedisSessionManager:
    """
    Tier 1: Active conversation sessions
    Stores current conversation state with 24-hour expiry
    """
    
    def __init__(self, redis_url: str = "redis://localhost:6379"):
        self.redis = redis.from_url(redis_url, decode_responses=True)
    
    def save_session(self, phone: str, session_data: dict, ttl_hours: int = 24) -> bool:
        """Save active session with expiry"""
        try:
            ttl_seconds = ttl_hours * 3600
            key = f"session:{phone}"
            
            self.redis.setex(key, ttl_seconds, json.dumps(session_data))
            logger.info(f"Saved session for {phone}, expires in {ttl_hours}h")
            return True
            
        except Exception as e:
            logger.error(f"Failed to save session: {e}")
            return False
    
    def get_session(self, phone: str) -> dict:
        """Get active session"""
        try:
            key = f"session:{phone}"
            data = self.redis.get(key)
            
            if data:
                session = json.loads(data)
                # Extend expiry on access
                self.redis.expire(key, 86400)  # Reset to 24 hours
                return session
            else:
                return {}
                
        except Exception as e:
            logger.error(f"Failed to get session: {e}")
            return {}
    
    def clear_session(self, phone: str) -> bool:
        """Clear active session"""
        try:
            key = f"session:{phone}"
            self.redis.delete(key)
            return True
        except Exception as e:
            logger.error(f"Failed to clear session: {e}")
            return False
    
    def get_active_sessions_count(self) -> int:
        """Get count of active sessions"""
        try:
            return len(self.redis.keys("session:*"))
        except:
            return 0

class PostgreSQLCustomerManager:
    """
    Tier 2: Long-term customer profiles
    Stores customer relationship data permanently
    """
    
    def __init__(self, database_url: str):
        self.database_url = database_url
        self.pool = None
    
    async def initialize(self):
        """Initialize PostgreSQL connection"""
        try:
            self.pool = await asyncpg.create_pool(
                self.database_url,
                min_size=2,
                max_size=10
            )
            
            # Create tables if not exist
            await self._create_tables()
            logger.info("PostgreSQL customer manager initialized")
            
        except Exception as e:
            logger.error(f"Failed to initialize PostgreSQL: {e}")
            raise
    
    async def _create_tables(self):
        """Create necessary tables"""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS customers (
                    phone VARCHAR(20) PRIMARY KEY,
                    name VARCHAR(100),
                    language_preference VARCHAR(10) DEFAULT 'italian',
                    service_history JSONB DEFAULT '[]'::JSONB,
                    preferences JSONB DEFAULT '{}'::JSONB,
                    last_visit TIMESTAMP WITH TIME ZONE,
                    total_visits INTEGER DEFAULT 0,
                    vip_status BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                    notes TEXT
                )
            """)
            
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS business_configs (
                    business_id VARCHAR(50) PRIMARY KEY,
                    business_name VARCHAR(255) NOT NULL,
                    business_type VARCHAR(50) NOT NULL,
                    custom_prompt TEXT,
                    staff_schedule JSONB DEFAULT '{}'::JSONB,
                    services JSONB NOT NULL,
                    google_calendar_config JSONB DEFAULT '{}'::JSONB,
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
                )
            """)
    
    async def get_customer_profile(self, phone: str) -> Dict[str, Any]:
        """Get customer long-term profile"""
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT * FROM customers WHERE phone = $1
                """, phone)
                
                if row:
                    return dict(row)
                else:
                    return None
                    
        except Exception as e:
            logger.error(f"Error getting customer profile: {e}")
            return None
    
    async def save_customer_profile(self, phone: str, profile_data: dict) -> bool:
        """Save/update customer profile"""
        try:
            async with self.pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO customers (phone, name, language_preference, last_visit, total_visits)
                    VALUES ($1, $2, $3, NOW(), 1)
                    ON CONFLICT (phone) 
                    DO UPDATE SET 
                        name = COALESCE($2, customers.name),
                        language_preference = COALESCE($3, customers.language_preference),
                        last_visit = NOW(),
                        total_visits = customers.total_visits + 1
                """, phone, profile_data.get("name"), profile_data.get("language"))
                
            logger.info(f"Saved customer profile for {phone}")
            return True
            
        except Exception as e:
            logger.error(f"Error saving customer profile: {e}")
            return False
    
    async def add_appointment_to_history(self, phone: str, appointment: dict) -> bool:
        """Add completed appointment to customer history"""
        try:
            async with self.pool.acquire() as conn:
                # Get current history
                current_history = await conn.fetchval("""
                    SELECT service_history FROM customers WHERE phone = $1
                """, phone) or []
                
                # Add new appointment
                current_history.append({
                    "date": appointment.get("date"),
                    "service": appointment.get("service"),
                    "time": appointment.get("time"),
                    "staff": appointment.get("staff"),
                    "completed_at": datetime.now().isoformat()
                })
                
                # Keep last 50 appointments
                if len(current_history) > 50:
                    current_history = current_history[-50:]
                
                # Update database
                await conn.execute("""
                    UPDATE customers 
                    SET service_history = $2
                    WHERE phone = $1
                """, phone, json.dumps(current_history))
                
            return True
            
        except Exception as e:
            logger.error(f"Error adding appointment to history: {e}")
            return False
    
    async def get_business_config(self, business_id: str = "default") -> Dict[str, Any]:
        """Get business configuration"""
        try:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT * FROM business_configs WHERE business_id = $1
                """, business_id)
                
                if row:
                    return dict(row)
                else:
                    return None
                    
        except Exception as e:
            logger.error(f"Error getting business config: {e}")
            return None

class MemoryManager:
    """
    COMPLETE MEMORY MANAGER: Redis + PostgreSQL
    Handles both active sessions and long-term customer relationships
    """
    
    def __init__(self, redis_url: str = "redis://localhost:6379", postgres_url: str = None):
        self.redis_sessions = RedisSessionManager(redis_url)
        self.postgres_customers = PostgreSQLCustomerManager(
            postgres_url or "postgresql://lyo:password@localhost:5432/lyo_production"
        )
    
    async def initialize(self):
        """Initialize both memory systems"""
        await self.postgres_customers.initialize()
        logger.info("Complete memory manager initialized")
    
    async def start_conversation(self, phone: str) -> dict:
        """
        Start new conversation: Load customer profile + create session
        """
        # Get long-term customer profile
        customer_profile = await self.postgres_customers.get_customer_profile(phone)
        
        # Create active session
        session_data = {
            "phone": phone,
            "customer_name": customer_profile.get("name") if customer_profile else None,
            "language_preference": customer_profile.get("language_preference") if customer_profile else None,
            "conversation_messages": [],
            "current_booking": {},
            "session_start": datetime.now().isoformat(),
            "message_count": 0
        }
        
        # Save to Redis
        self.redis_sessions.save_session(phone, session_data)
        
        return {
            "session": session_data,
            "customer_profile": customer_profile,
            "is_returning_customer": bool(customer_profile)
        }
    
    async def save_message(self, phone: str, role: str, content: str, language: str = "italian") -> bool:
        """
        Save message to active session
        """
        session = self.redis_sessions.get_session(phone)
        
        if not session:
            # Create new session if doesn't exist
            context = await self.start_conversation(phone)
            session = context["session"]
        
        # Add message
        session["conversation_messages"].append({
            "role": role,
            "content": content,
            "language": language,
            "timestamp": datetime.now().isoformat()
        })
        
        # Keep last 20 messages in session
        if len(session["conversation_messages"]) > 20:
            session["conversation_messages"] = session["conversation_messages"][-15:]
        
        session["message_count"] += 1
        
        # Update Redis
        return self.redis_sessions.save_session(phone, session)
    
    async def save_customer_name(self, phone: str, name: str, language: str = "italian") -> bool:
        """
        Save customer name to both session and profile
        """
        # Update active session
        session = self.redis_sessions.get_session(phone)
        if session:
            session["customer_name"] = name
            self.redis_sessions.save_session(phone, session)
        
        # Update long-term profile
        return await self.postgres_customers.save_customer_profile(
            phone, {"name": name, "language": language}
        )
    
    async def get_conversation_context(self, phone: str) -> dict:
        """
        Get complete conversation context: session + customer profile
        """
        # Get active session
        session = self.redis_sessions.get_session(phone)
        
        # Get customer profile
        customer_profile = await self.postgres_customers.get_customer_profile(phone)
        
        if not session:
            # Create new session for existing customer
            context = await self.start_conversation(phone)
            session = context["session"]
        
        return {
            "active_session": session,
            "customer_profile": customer_profile,
            "recent_messages": session.get("conversation_messages", [])[-10:],
            "customer_name": session.get("customer_name") or (customer_profile.get("name") if customer_profile else None),
            "language_preference": session.get("language_preference") or (customer_profile.get("language_preference") if customer_profile else "italian"),
            "is_returning_customer": bool(customer_profile),
            "total_visits": customer_profile.get("total_visits", 0) if customer_profile else 0
        }

# MOCK IMPLEMENTATION FOR TESTING
class MockMemoryManager:
    """
    Mock memory manager for testing without Redis/PostgreSQL
    But with proper persistence simulation
    """
    
    def __init__(self):
        self.sessions = {}  # Active sessions
        self.customers = {}  # Customer profiles
        self.session_file = "/tmp/lyo_sessions.json"
        self.customers_file = "/tmp/lyo_customers.json"
        
        # Load from files on startup
        self._load_from_files()
    
    def _load_from_files(self):
        """Load data from files (simulates database persistence)"""
        try:
            import os
            if os.path.exists(self.session_file):
                with open(self.session_file, 'r') as f:
                    self.sessions = json.load(f)
            
            if os.path.exists(self.customers_file):
                with open(self.customers_file, 'r') as f:
                    self.customers = json.load(f)
                    
            logger.info(f"Loaded {len(self.customers)} customer profiles and {len(self.sessions)} sessions")
            
        except Exception as e:
            logger.warning(f"Could not load from files: {e}")
    
    def _save_to_files(self):
        """Save data to files (simulates database persistence)"""
        try:
            with open(self.session_file, 'w') as f:
                json.dump(self.sessions, f)
            
            with open(self.customers_file, 'w') as f:
                json.dump(self.customers, f)
                
        except Exception as e:
            logger.error(f"Could not save to files: {e}")
    
    async def initialize(self):
        """Initialize mock manager"""
        logger.info("Mock memory manager initialized with file persistence")
    
    async def start_conversation(self, phone: str) -> dict:
        """Start conversation with persistence"""
        customer_profile = self.customers.get(phone)
        
        session_data = {
            "phone": phone,
            "customer_name": customer_profile.get("name") if customer_profile else None,
            "language_preference": customer_profile.get("language_preference") if customer_profile else None,
            "conversation_messages": [],
            "current_booking": {},
            "session_start": datetime.now().isoformat(),
            "message_count": 0
        }
        
        self.sessions[phone] = session_data
        self._save_to_files()
        
        return {
            "session": session_data,
            "customer_profile": customer_profile,
            "is_returning_customer": bool(customer_profile)
        }
    
    async def save_customer_name(self, phone: str, name: str, language: str = "italian") -> bool:
        """Save customer name with persistence"""
        # Update session
        if phone in self.sessions:
            self.sessions[phone]["customer_name"] = name
        
        # Update customer profile
        if phone not in self.customers:
            self.customers[phone] = {
                "phone": phone,
                "name": name,
                "language_preference": language,
                "service_history": [],
                "total_visits": 0,
                "created_at": datetime.now().isoformat()
            }
        else:
            self.customers[phone]["name"] = name
            self.customers[phone]["language_preference"] = language
        
        self.customers[phone]["last_visit"] = datetime.now().isoformat()
        self.customers[phone]["total_visits"] = self.customers[phone].get("total_visits", 0) + 1
        
        self._save_to_files()
        logger.info(f"💾 PERSISTENT: Saved {name} for {phone}")
        return True
    
    async def save_message(self, phone: str, role: str, content: str, language: str = "italian") -> bool:
        """Save message with persistence"""
        if phone not in self.sessions:
            await self.start_conversation(phone)
        
        session = self.sessions[phone]
        
        session["conversation_messages"].append({
            "role": role,
            "content": content,
            "language": language,
            "timestamp": datetime.now().isoformat()
        })
        
        # Keep last 15 messages
        if len(session["conversation_messages"]) > 15:
            session["conversation_messages"] = session["conversation_messages"][-10:]
        
        session["message_count"] += 1
        
        self._save_to_files()
        return True
    
    async def get_conversation_context(self, phone: str) -> dict:
        """Get complete context with persistence"""
        if phone not in self.sessions:
            await self.start_conversation(phone)
        
        session = self.sessions[phone]
        customer = self.customers.get(phone)
        
        return {
            "active_session": session,
            "customer_profile": customer,
            "recent_messages": session.get("conversation_messages", [])[-8:],
            "customer_name": session.get("customer_name") or (customer.get("name") if customer else None),
            "language_preference": session.get("language_preference") or (customer.get("language_preference") if customer else "italian"),
            "is_returning_customer": bool(customer),
            "total_visits": customer.get("total_visits", 0) if customer else 0
        }

# TEST PERSISTENCE
async def test_memory_persistence():
    """
    Test memory persistence across 'server restarts'
    """
    print("🧪 TESTING MEMORY PERSISTENCE")
    print("=" * 40)
    
    # Phase 1: Create memory and save data
    print("1️⃣ Creating memory and saving customer data")
    memory1 = MockMemoryManager()
    await memory1.initialize()
    
    # Save customer interaction
    await memory1.save_customer_name("+39123456789", "Marco Rossi", "italian")
    await memory1.save_message("+39123456789", "user", "Vorrei un taglio", "italian")
    await memory1.save_message("+39123456789", "assistant", "Perfetto Marco! Quando?", "italian")
    
    print("   ✅ Saved: Marco Rossi with conversation")
    
    # Phase 2: Simulate server restart (new memory instance)
    print("\n2️⃣ Simulating server restart (new memory instance)")
    memory2 = MockMemoryManager()  # Fresh instance
    await memory2.initialize()
    
    # Check if data survived
    context = await memory2.get_conversation_context("+39123456789")
    
    print(f"   Customer name: {context['customer_name']}")
    print(f"   Is returning customer: {context['is_returning_customer']}")
    print(f"   Total visits: {context['total_visits']}")
    print(f"   Recent messages: {len(context['recent_messages'])}")
    
    if context["customer_name"] == "Marco Rossi":
        print("   ✅ PERSISTENCE WORKING: Customer data survived restart!")
        return True
    else:
        print("   ❌ PERSISTENCE FAILED: Data lost on restart")
        return False

if __name__ == "__main__":
    asyncio.run(test_memory_persistence())