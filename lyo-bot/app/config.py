import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    db_host: str = "lyo-enterprise-database.cixc4kiw6r00.us-east-1.rds.amazonaws.com"
    db_port: int = 5432
    db_name: str = "lyo_production"
    db_user: str = "lyoadmin"
    db_password: str = ""
    db_sslmode: str = "require"

    # OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"

    # Chatwoot
    chatwoot_base_url: str = "http://localhost:3000"
    chatwoot_bot_token: str = ""

    # Google Calendar (default service account path)
    google_service_account_file: str = ""

    # Management Page
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480

    # Webhook security
    webhook_secret: str = ""

    # Bot defaults
    default_timezone: str = "Europe/Rome"
    message_batch_delay_seconds: int = 15

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()
