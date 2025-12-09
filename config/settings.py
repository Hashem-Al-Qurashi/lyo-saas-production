"""
Production configuration management
"""
import os
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

class Settings:
    """
    Production settings with environment variable management
    """
    
    # OpenAI Configuration
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4-turbo")
    
    # Chatwoot Integration
    CHATWOOT_URL: str = os.getenv("CHATWOOT_URL", "")
    CHATWOOT_ACCOUNT_ID: str = os.getenv("CHATWOOT_ACCOUNT_ID", "")
    CHATWOOT_API_TOKEN: str = os.getenv("CHATWOOT_API_TOKEN", "")
    
    # Google Calendar
    GOOGLE_CALENDAR_NAME: str = os.getenv("GOOGLE_CALENDAR_NAME", "Prenotazioni Lyo")
    GOOGLE_TIMEZONE: str = os.getenv("GOOGLE_TIMEZONE", "Europe/Rome")
    
    # Application Settings
    RESPONSE_TIMER: int = int(os.getenv("RESPONSE_TIMER", "20"))
    MAX_MEMORY_MESSAGES: int = int(os.getenv("MAX_MEMORY_MESSAGES", "6"))
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"
    
    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    
    # Email
    SMTP_HOST: str = os.getenv("SMTP_HOST", "smtp.gmail.com")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER: str = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
    BUSINESS_OWNER_EMAIL: str = os.getenv("BUSINESS_OWNER_EMAIL", "")
    
    # Monitoring
    SENTRY_DSN: str = os.getenv("SENTRY_DSN", "")
    
    @property
    def is_openai_configured(self) -> bool:
        """Check if OpenAI is properly configured"""
        return bool(self.OPENAI_API_KEY and self.OPENAI_API_KEY.startswith("sk-"))
    
    @property
    def is_chatwoot_configured(self) -> bool:
        """Check if Chatwoot is properly configured"""
        return bool(self.CHATWOOT_URL and self.CHATWOOT_ACCOUNT_ID and self.CHATWOOT_API_TOKEN)
    
    @property
    def is_production_ready(self) -> bool:
        """Check if system is ready for production"""
        return self.is_openai_configured and self.is_chatwoot_configured

# Global settings instance
settings = Settings()

def get_settings() -> Settings:
    """Get settings instance"""
    return settings