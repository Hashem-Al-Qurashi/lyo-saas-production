"""
Integration tests -- verify end-to-end flow without external dependencies.
These tests mock all external services (DB, OpenAI, Chatwoot) but test the
full FastAPI request/response cycle.
"""
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


class TestWebhookFlow:
    def test_incoming_message_returns_received(self):
        payload = {
            "event": "message_created",
            "id": 1,
            "content": "Ciao vorrei prenotare",
            "message_type": "incoming",
            "content_type": "text",
            "account": {"id": 1, "name": "Aura Hair Studio"},
            "conversation": {"id": 456, "inbox_id": 789},
            "inbox": {"id": 789, "name": "WhatsApp Aura"},
            "sender": {
                "id": 101,
                "name": "Maria Rossi",
                "phone_number": "+393331234567",
                "type": "contact",
            },
        }
        response = client.post("/webhook/chatwoot", json=payload)
        assert response.status_code == 200
        assert response.json()["status"] == "received"

    def test_outgoing_message_ignored(self):
        payload = {
            "event": "message_created",
            "id": 2,
            "content": "Bot reply",
            "message_type": "outgoing",
            "account": {"id": 1, "name": "Test"},
            "conversation": {"id": 456},
            "inbox": {"id": 789, "name": "WA"},
            "sender": {"id": 1, "name": "Lyo Bot", "type": "agent_bot"},
        }
        response = client.post("/webhook/chatwoot", json=payload)
        assert response.json()["status"] == "ignored"

    def test_conversation_resolved_ignored(self):
        payload = {"event": "conversation_resolved", "id": 1}
        response = client.post("/webhook/chatwoot", json=payload)
        assert response.json()["status"] == "ignored"

    def test_empty_content_ignored(self):
        payload = {
            "event": "message_created",
            "id": 3,
            "content": "",
            "message_type": "incoming",
            "account": {"id": 1, "name": "Test"},
            "conversation": {"id": 456},
            "inbox": {"id": 789, "name": "WA"},
            "sender": {"id": 101, "name": "Test", "phone_number": "+39333", "type": "contact"},
        }
        response = client.post("/webhook/chatwoot", json=payload)
        assert response.json()["status"] == "ignored"

    def test_no_account_ignored(self):
        payload = {
            "event": "message_created",
            "id": 4,
            "content": "Hello",
            "message_type": "incoming",
        }
        response = client.post("/webhook/chatwoot", json=payload)
        assert response.json()["status"] == "ignored"


class TestHealth:
    def test_health_check(self):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["version"] == "2.0.0"


class TestMultiTenantIsolation:
    """Verify webhook handles different account IDs."""

    def test_different_accounts_accepted(self):
        for account_id in [1, 2, 5, 100]:
            payload = {
                "event": "message_created",
                "id": 1,
                "content": "Ciao",
                "message_type": "incoming",
                "account": {"id": account_id, "name": f"Salon {account_id}"},
                "conversation": {"id": 456},
                "inbox": {"id": 789, "name": "WA"},
                "sender": {"id": 101, "name": "Test", "phone_number": "+39333", "type": "contact"},
            }
            response = client.post("/webhook/chatwoot", json=payload)
            assert response.status_code == 200
            assert response.json()["status"] == "received"
