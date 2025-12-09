"""
Application Configuration
Using Pydantic Settings for type-safe configuration
"""

import os
from typing import List, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, validator


class Settings(BaseSettings):
    """
    Application settings with environment variable support
    """
    
    # Application
    VERSION: str = "1.0.0"
    ENVIRONMENT: str = Field("development", env="ENVIRONMENT")
    DEBUG: bool = Field(False, env="DEBUG")
    LOG_LEVEL: str = Field("INFO", env="LOG_LEVEL")
    SECRET_KEY: str = Field(..., env="SECRET_KEY")
    
    # Server
    HOST: str = Field("0.0.0.0", env="HOST")
    PORT: int = Field(8000, env="PORT")
    WORKERS: int = Field(4, env="WORKERS")
    
    # OpenAI
    OPENAI_API_KEY: str = Field(..., env="OPENAI_API_KEY")
    OPENAI_MODEL: str = Field("gpt-4-turbo-preview", env="OPENAI_MODEL")
    OPENAI_TEMPERATURE: float = Field(0.8, env="OPENAI_TEMPERATURE")
    OPENAI_MAX_TOKENS: int = Field(500, env="OPENAI_MAX_TOKENS")
    
    # Database
    DATABASE_URL: str = Field(..., env="DATABASE_URL")
    DB_POOL_MIN_SIZE: int = Field(2, env="DB_POOL_MIN_SIZE")
    DB_POOL_MAX_SIZE: int = Field(10, env="DB_POOL_MAX_SIZE")
    
    # Redis
    REDIS_URL: str = Field("redis://redis:6379/0", env="REDIS_URL")
    REDIS_TTL_HOURS: int = Field(24, env="REDIS_TTL_HOURS")
    
    # WhatsApp/Chatwoot Integration
    CHATWOOT_URL: Optional[str] = Field(None, env="CHATWOOT_URL")
    CHATWOOT_ACCOUNT_ID: Optional[str] = Field(None, env="CHATWOOT_ACCOUNT_ID")
    CHATWOOT_API_TOKEN: Optional[str] = Field(None, env="CHATWOOT_API_TOKEN")
    WHATSAPP_WEBHOOK_VERIFY_TOKEN: Optional[str] = Field(None, env="WHATSAPP_WEBHOOK_VERIFY_TOKEN")
    
    # Google Calendar
    GOOGLE_CALENDAR_ID: Optional[str] = Field("primary", env="GOOGLE_CALENDAR_ID")
    GOOGLE_CREDENTIALS_PATH: str = Field("/app/credentials.json", env="GOOGLE_CREDENTIALS_PATH")
    GOOGLE_TOKEN_PATH: str = Field("/app/token.json", env="GOOGLE_TOKEN_PATH")
    
    # Business Configuration
    BUSINESS_ID: str = Field("default_salon", env="BUSINESS_ID")
    BUSINESS_NAME: str = Field("Salon Bella Vita", env="BUSINESS_NAME")
    BUSINESS_TYPE: str = Field("beauty_salon", env="BUSINESS_TYPE")
    
    # Security
    ALLOWED_HOSTS: List[str] = Field(["*"], env="ALLOWED_HOSTS")
    CORS_ORIGINS: List[str] = Field(["*"], env="CORS_ORIGINS")
    
    # Rate Limiting
    RATE_LIMIT_PER_MINUTE: int = Field(60, env="RATE_LIMIT_PER_MINUTE")
    RATE_LIMIT_PER_HOUR: int = Field(1000, env="RATE_LIMIT_PER_HOUR")
    
    # Monitoring
    SENTRY_DSN: Optional[str] = Field(None, env="SENTRY_DSN")
    PROMETHEUS_PORT: int = Field(9090, env="PROMETHEUS_PORT")
    
    # Timezone
    TIMEZONE: str = Field("Europe/Rome", env="TZ")
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="allow"
    )
    
    @validator("ENVIRONMENT")
    def validate_environment(cls, v):
        """Validate environment value"""
        allowed = ["development", "staging", "production"]
        if v not in allowed:
            raise ValueError(f"ENVIRONMENT must be one of {allowed}")
        return v
    
    @validator("LOG_LEVEL")
    def validate_log_level(cls, v):
        """Validate log level"""
        allowed = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if v.upper() not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of {allowed}")
        return v.upper()
    
    @validator("CORS_ORIGINS", "ALLOWED_HOSTS", pre=True)
    def parse_list(cls, v):
        """Parse comma-separated list from environment variable"""
        if isinstance(v, str):
            return [item.strip() for item in v.split(",")]
        return v
    
    @property
    def is_production(self) -> bool:
        """Check if running in production"""
        return self.ENVIRONMENT == "production"
    
    @property
    def is_development(self) -> bool:
        """Check if running in development"""
        return self.ENVIRONMENT == "development"
    
    @property
    def database_url_async(self) -> str:
        """Get async database URL for SQLAlchemy"""
        if self.DATABASE_URL.startswith("postgresql://"):
            return self.DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")
        return self.DATABASE_URL


# Create settings instance
settings = Settings()

# Export configuration values
__all__ = ["settings", "Settings"]