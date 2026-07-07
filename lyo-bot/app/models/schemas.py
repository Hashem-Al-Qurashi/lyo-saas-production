from __future__ import annotations
from typing import Optional, List
from decimal import Decimal
from datetime import date, time, datetime
from pydantic import BaseModel, Field


# --- Database models ---

class Business(BaseModel):
    id: int
    chatwoot_account_id: int
    name: str
    slug: Optional[str] = None
    timezone: str = "Europe/Rome"
    language: str = "it"
    bot_name: str = "Assistente"
    bot_persona: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    google_calendar_id: str = "primary"
    google_service_account_json: Optional[str] = None
    owner_email: Optional[str] = None
    status: str = "active"
    settings: dict = Field(default_factory=dict)
    # Loaded relations
    operators: List[Operator] = Field(default_factory=list)
    treatments: List[Treatment] = Field(default_factory=list)
    hours: List[BusinessHours] = Field(default_factory=list)
    closures: List[BusinessClosure] = Field(default_factory=list)


class OperatorHours(BaseModel):
    operator_id: int
    day_of_week: int  # 0=Monday, 6=Sunday
    is_working: bool = True
    start_time: Optional[time] = None  # None = use business default
    end_time: Optional[time] = None    # None = use business default
    break_start: Optional[time] = None  # None = no break
    break_end: Optional[time] = None    # None = no break


class BusinessClosure(BaseModel):
    business_id: int
    closure_date: date
    closure_end_date: Optional[date] = None
    reason: Optional[str] = None

    def covers(self, d: date) -> bool:
        """True if this closure covers date *d*."""
        end = self.closure_end_date or self.closure_date
        return self.closure_date <= d <= end


class Operator(BaseModel):
    id: int
    business_id: int
    technical_id: str
    display_name: str
    is_active: bool = True
    sort_order: int = 0
    notes: Optional[str] = None
    treatment_ids: List[int] = Field(default_factory=list)
    # Loaded relations
    hours: List[OperatorHours] = Field(default_factory=list)


class Treatment(BaseModel):
    id: int
    business_id: int
    code: str
    name_it: str
    name_en: Optional[str] = None
    description_it: Optional[str] = None
    description_en: Optional[str] = None
    duration_minutes: int
    price: Optional[Decimal] = None
    is_active: bool = True
    sort_order: int = 0
    notes: Optional[str] = None
    operator_ids: List[int] = Field(default_factory=list)
    auto_addon_id: Optional[int] = None


class OperatorTreatment(BaseModel):
    operator_id: int
    treatment_id: int


class BusinessHours(BaseModel):
    business_id: int
    day_of_week: int  # 0=Monday, 6=Sunday
    is_open: bool = True
    open_time: Optional[time] = None
    close_time: Optional[time] = None


class Customer(BaseModel):
    id: int
    business_id: int
    phone: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    platform: Optional[str] = None
    chatwoot_contact_id: Optional[int] = None

    @property
    def full_name(self) -> Optional[str]:
        parts = [p for p in [self.first_name, self.last_name] if p]
        return " ".join(parts) if parts else None

    @property
    def has_name(self) -> bool:
        return bool(self.first_name)


class Appointment(BaseModel):
    id: int
    business_id: int
    operator_id: Optional[int] = None
    operator_name: Optional[str] = None
    customer_phone: str
    customer_name: str
    treatment_code: str
    treatment_name: Optional[str] = None
    appointment_date: date
    appointment_time: time
    duration_minutes: int
    price: Optional[Decimal] = None
    status: str = "confirmed"
    google_event_id: Optional[str] = None
    platform: Optional[str] = None


# --- Webhook payload models ---

class WebhookAccount(BaseModel):
    id: int
    name: Optional[str] = None


class WebhookConversation(BaseModel):
    id: int
    inbox_id: Optional[int] = None
    status: Optional[str] = None
    meta: Optional[dict] = None
    labels: list[str] = []


class WebhookInbox(BaseModel):
    id: int
    name: Optional[str] = None


class WebhookSender(BaseModel):
    id: int
    name: Optional[str] = None
    phone_number: Optional[str] = None
    type: Optional[str] = None
    email: Optional[str] = None


class WebhookPayload(BaseModel):
    event: str
    id: Optional[int] = None
    content: Optional[str] = None
    message_type: Optional[str] = None
    content_type: Optional[str] = None
    account: Optional[WebhookAccount] = None
    conversation: Optional[WebhookConversation] = None
    inbox: Optional[WebhookInbox] = None
    sender: Optional[WebhookSender] = None

    def is_incoming(self) -> bool:
        return self.message_type == "incoming"

    def is_message_created(self) -> bool:
        return self.event == "message_created"


# Forward refs
Business.model_rebuild()
