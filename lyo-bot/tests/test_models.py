from app.models.schemas import (
    Business, Operator, Treatment, BusinessHours,
    Customer, Appointment, WebhookPayload,
)


def test_business_schema():
    biz = Business(
        id=1, chatwoot_account_id=1, name="Test Salon",
        timezone="Europe/Rome", language="it", bot_name="Simone", status="active",
    )
    assert biz.chatwoot_account_id == 1
    assert biz.bot_name == "Simone"


def test_operator_schema():
    op = Operator(id=1, business_id=1, technical_id="op_1", display_name="Giulia", is_active=True)
    assert op.display_name == "Giulia"


def test_treatment_schema():
    t = Treatment(id=1, business_id=1, code="taglio_donna", name_it="Taglio Donna", duration_minutes=45, price=60.0)
    assert t.duration_minutes == 45


def test_customer_full_name():
    c = Customer(id=1, business_id=1, phone="+393331234567", first_name="Maria", last_name="Rossi")
    assert c.full_name == "Maria Rossi"
    assert c.has_name is True


def test_customer_no_name():
    c = Customer(id=1, business_id=1, phone="+393331234567")
    assert c.full_name is None
    assert c.has_name is False


def test_webhook_payload_parsing():
    raw = {
        "event": "message_created",
        "id": 123,
        "content": "Ciao vorrei prenotare",
        "message_type": "incoming",
        "content_type": "text",
        "account": {"id": 1, "name": "Aura Hair Studio"},
        "conversation": {"id": 456, "inbox_id": 789},
        "inbox": {"id": 789, "name": "WhatsApp"},
        "sender": {"id": 101, "name": "Maria", "phone_number": "+393331234567", "type": "contact"},
    }
    payload = WebhookPayload(**raw)
    assert payload.account.id == 1
    assert payload.content == "Ciao vorrei prenotare"
    assert payload.sender.phone_number == "+393331234567"
    assert payload.is_incoming() is True
    assert payload.is_message_created() is True


def test_webhook_payload_outgoing():
    raw = {
        "event": "message_created",
        "id": 124,
        "content": "Bot reply",
        "message_type": "outgoing",
        "account": {"id": 1, "name": "Test"},
        "conversation": {"id": 456},
        "inbox": {"id": 789, "name": "WA"},
        "sender": {"id": 1, "name": "Bot", "type": "agent_bot"},
    }
    payload = WebhookPayload(**raw)
    assert payload.is_incoming() is False
