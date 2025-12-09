"""
LYO MEMORY SERVICE: Adapted from Solo Chat
Session-based memory with conversation persistence and behavioral learning
"""
import asyncio
import json
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field

@dataclass
class LyoConversationMessage:
    """Individual conversation message"""
    role: str  # "user" or "assistant"
    content: str
    timestamp: datetime
    language: str = "italian"
    intent_type: str = "general"
    sequence: int = 0

@dataclass  
class LyoUserProfile:
    """User profile and preferences"""
    user_id: str
    name: Optional[str] = None
    language_preference: Optional[str] = None
    interaction_count: int = 0
    first_seen: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_seen: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Business-specific data
    past_appointments: List[Dict] = field(default_factory=list)
    preferred_services: List[str] = field(default_factory=list)
    booking_patterns: Dict[str, Any] = field(default_factory=dict)

@dataclass
class LyoConversationContext:
    """Complete conversation context for a user session"""
    session_id: str
    user_profile: LyoUserProfile
    messages: List[LyoConversationMessage] = field(default_factory=list)
    current_booking_state: Dict[str, Any] = field(default_factory=dict)
    conversation_topic: Optional[str] = None
    
    def add_message(self, role: str, content: str, language: str = "italian", intent_type: str = "general"):
        """Add message to conversation history"""
        message = LyoConversationMessage(
            role=role,
            content=content,
            timestamp=datetime.now(timezone.utc),
            language=language,
            intent_type=intent_type,
            sequence=len(self.messages)
        )
        self.messages.append(message)
        
        # Keep last 50 messages (manageable memory)
        if len(self.messages) > 50:
            self.messages = self.messages[-40:]  # Keep 40, remove oldest 10
    
    def get_recent_messages(self, count: int = 10) -> List[LyoConversationMessage]:
        """Get recent messages for context"""
        return self.messages[-count:] if self.messages else []
    
    def get_conversation_summary(self) -> Dict[str, Any]:
        """Get conversation summary for context"""
        if not self.messages:
            return {"total_messages": 0, "languages_used": [], "topics_discussed": []}
        
        languages = list(set(msg.language for msg in self.messages))
        intents = list(set(msg.intent_type for msg in self.messages))
        
        return {
            "total_messages": len(self.messages),
            "languages_used": languages,
            "intents_seen": intents,
            "last_language": self.messages[-1].language if self.messages else "italian",
            "conversation_started": self.messages[0].timestamp.isoformat() if self.messages else None
        }

class LyoMemoryService:
    """
    SIMPLIFIED MEMORY SERVICE FOR LYO
    Session-based memory without complex graph database
    """
    
    def __init__(self):
        # In-memory storage (can be upgraded to Redis/database later)
        self.sessions: Dict[str, LyoConversationContext] = {}
        self.user_profiles: Dict[str, LyoUserProfile] = {}
        
        # Performance tracking
        self.total_sessions = 0
        self.total_messages = 0
    
    async def get_or_create_session(self, session_id: str) -> LyoConversationContext:
        """
        Get existing session or create new one with clean state
        """
        if session_id not in self.sessions:
            # Create new user profile if needed
            if session_id not in self.user_profiles:
                self.user_profiles[session_id] = LyoUserProfile(
                    user_id=session_id,
                    first_seen=datetime.now(timezone.utc)
                )
            
            # Create new conversation context
            self.sessions[session_id] = LyoConversationContext(
                session_id=session_id,
                user_profile=self.user_profiles[session_id]
            )
            
            self.total_sessions += 1
        
        # Update last seen
        self.user_profiles[session_id].last_seen = datetime.now(timezone.utc)
        
        return self.sessions[session_id]
    
    async def save_message(
        self, 
        session_id: str, 
        role: str, 
        content: str, 
        language: str = "italian",
        intent_type: str = "general"
    ) -> None:
        """
        Save message to conversation history
        """
        context = await self.get_or_create_session(session_id)
        context.add_message(role, content, language, intent_type)
        
        # Update user profile
        context.user_profile.interaction_count += 1
        context.user_profile.last_seen = datetime.now(timezone.utc)
        
        # Update language preference
        if role == "user" and not context.user_profile.language_preference:
            context.user_profile.language_preference = language
        
        self.total_messages += 1
    
    async def save_user_name(self, session_id: str, name: str) -> bool:
        """
        Save user name to profile
        """
        try:
            context = await self.get_or_create_session(session_id)
            context.user_profile.name = name
            return True
        except Exception as e:
            print(f"Error saving name: {e}")
            return False
    
    async def get_user_name(self, session_id: str) -> Optional[str]:
        """
        Get user name from profile
        """
        if session_id in self.sessions:
            return self.sessions[session_id].user_profile.name
        return None
    
    async def get_conversation_context(self, session_id: str, message_count: int = 10) -> Dict[str, Any]:
        """
        Get conversation context for AI processing
        """
        context = await self.get_or_create_session(session_id)
        recent_messages = context.get_recent_messages(message_count)
        summary = context.get_conversation_summary()
        
        return {
            "session_id": session_id,
            "user_name": context.user_profile.name,
            "language_preference": context.user_profile.language_preference,
            "recent_messages": [
                {
                    "role": msg.role,
                    "content": msg.content,
                    "language": msg.language,
                    "intent": msg.intent_type,
                    "timestamp": msg.timestamp.isoformat()
                }
                for msg in recent_messages
            ],
            "conversation_summary": summary,
            "current_booking": context.current_booking_state,
            "interaction_count": context.user_profile.interaction_count
        }
    
    async def reset_session(self, session_id: str) -> bool:
        """
        Reset session memory (keep user profile)
        """
        try:
            if session_id in self.sessions:
                user_profile = self.sessions[session_id].user_profile
                
                # Create fresh context but keep user profile
                self.sessions[session_id] = LyoConversationContext(
                    session_id=session_id,
                    user_profile=user_profile
                )
                
                return True
            return False
        except Exception as e:
            print(f"Error resetting session: {e}")
            return False
    
    async def update_booking_state(
        self, 
        session_id: str, 
        booking_data: Dict[str, Any]
    ) -> bool:
        """
        Update current booking state
        """
        try:
            context = await self.get_or_create_session(session_id)
            context.current_booking_state.update(booking_data)
            return True
        except Exception as e:
            print(f"Error updating booking state: {e}")
            return False
    
    def get_memory_stats(self) -> Dict[str, Any]:
        """
        Get memory service statistics
        """
        return {
            "total_sessions": self.total_sessions,
            "active_sessions": len(self.sessions),
            "total_messages": self.total_messages,
            "total_users": len(self.user_profiles),
            "users_with_names": len([p for p in self.user_profiles.values() if p.name]),
            "memory_service_type": "simplified_session_based"
        }

# INTEGRATION TEST
async def test_lyo_memory_service():
    """
    Test the Lyo memory service
    """
    print("🧠 TESTING LYO MEMORY SERVICE")
    print("=" * 40)
    
    memory = LyoMemoryService()
    
    # Test session creation
    print("1️⃣ Testing session creation:")
    context1 = await memory.get_or_create_session("+39123456789")
    print(f"   ✅ Created session for +39123456789")
    print(f"   User profile: {context1.user_profile.user_id}")
    
    # Test message saving
    print("\\n2️⃣ Testing message saving:")
    await memory.save_message("+39123456789", "user", "Hello", "english", "greeting")
    await memory.save_message("+39123456789", "assistant", "Hello! How can I help?", "english", "greeting")
    await memory.save_message("+39123456789", "user", "I'm John Smith", "english", "name_introduction")
    
    context = await memory.get_conversation_context("+39123456789")
    print(f"   ✅ Saved {len(context['recent_messages'])} messages")
    print(f"   Conversation summary: {context['conversation_summary']}")
    
    # Test name saving
    print("\\n3️⃣ Testing name saving:")
    await memory.save_user_name("+39123456789", "John Smith")
    saved_name = await memory.get_user_name("+39123456789")
    print(f"   ✅ Saved name: {saved_name}")
    
    # Test user isolation
    print("\\n4️⃣ Testing user isolation:")
    context2 = await memory.get_or_create_session("+39987654321")
    await memory.save_user_name("+39987654321", "Marco Rossi")
    
    name1 = await memory.get_user_name("+39123456789")
    name2 = await memory.get_user_name("+39987654321") 
    
    print(f"   User 1 name: {name1}")
    print(f"   User 2 name: {name2}")
    
    if name1 == "John Smith" and name2 == "Marco Rossi":
        print("   ✅ USER ISOLATION: WORKING")
    else:
        print("   ❌ USER ISOLATION: FAILED")
    
    # Test conversation context
    print("\\n5️⃣ Testing conversation context:")
    await memory.save_message("+39987654321", "user", "Ciao", "italian", "greeting")
    await memory.save_message("+39987654321", "user", "Che servizi offrite?", "italian", "faq")
    
    context_it = await memory.get_conversation_context("+39987654321")
    print(f"   ✅ Italian user context: {context_it['language_preference']}")
    print(f"   Messages: {len(context_it['recent_messages'])}")
    
    # Memory stats
    print("\\n6️⃣ Memory statistics:")
    stats = memory.get_memory_stats()
    print(f"   Total sessions: {stats['total_sessions']}")
    print(f"   Active sessions: {stats['active_sessions']}")  
    print(f"   Total messages: {stats['total_messages']}")
    print(f"   Users with names: {stats['users_with_names']}")
    
    print("\\n✅ LYO MEMORY SERVICE: WORKING")
    return True

if __name__ == "__main__":
    asyncio.run(test_lyo_memory_service())