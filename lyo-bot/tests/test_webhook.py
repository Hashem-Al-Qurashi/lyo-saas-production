from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def _make_incoming_payload(content="Ciao", account_id=1, conv_id=456):
    return {
        "event": "message_created",
        "id": 123,
        "content": content,
        "message_type": "incoming",
        "content_type": "text",
        "account": {"id": account_id, "name": "Test Salon"},
        "conversation": {"id": conv_id, "inbox_id": 789},
        "inbox": {"id": 789, "name": "WhatsApp"},
        "sender": {"id": 101, "name": "Maria", "phone_number": "+393331234567", "type": "contact"},
    }


def test_webhook_returns_200_immediately():
    response = client.post("/webhook/chatwoot", json=_make_incoming_payload())
    assert response.status_code == 200
    assert response.json()["status"] == "received"


def test_webhook_ignores_outgoing():
    payload = _make_incoming_payload()
    payload["message_type"] = "outgoing"
    response = client.post("/webhook/chatwoot", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


def test_webhook_ignores_non_message_events():
    payload = _make_incoming_payload()
    payload["event"] = "conversation_resolved"
    response = client.post("/webhook/chatwoot", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


def test_webhook_rejects_empty_content():
    payload = _make_incoming_payload(content="")
    response = client.post("/webhook/chatwoot", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
