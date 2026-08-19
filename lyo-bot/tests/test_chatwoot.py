"""Tests for ChatwootClient -- async, using httpx mock."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from app.services.chatwoot import ChatwootClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(json_data=None, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSendMessage:

    @patch("app.services.chatwoot.settings")
    async def test_send_message_calls_correct_url(self, mock_settings):
        mock_settings.chatwoot_base_url = "https://chat.example.com"
        mock_settings.chatwoot_bot_token = "tok_123"

        client = ChatwootClient()
        mock_resp = _mock_response({"id": 1, "content": "hello"})
        client._client = AsyncMock()
        client._client.post = AsyncMock(return_value=mock_resp)

        result = await client.send_message(
            account_id=5,
            conversation_id=42,
            content="Ciao!",
        )

        client._client.post.assert_called_once_with(
            "https://chat.example.com/api/v1/accounts/5/conversations/42/messages",
            json={"content": "Ciao!", "message_type": "outgoing"},
            headers={"api_access_token": "tok_123"},
        )
        assert result == {"id": 1, "content": "hello"}

    @patch("app.services.chatwoot.settings")
    async def test_send_message_custom_type(self, mock_settings):
        mock_settings.chatwoot_base_url = "https://chat.example.com"
        mock_settings.chatwoot_bot_token = "tok_123"

        client = ChatwootClient()
        mock_resp = _mock_response({})
        client._client = AsyncMock()
        client._client.post = AsyncMock(return_value=mock_resp)

        await client.send_message(
            account_id=5, conversation_id=42, content="hi", message_type="incoming"
        )

        call_kwargs = client._client.post.call_args
        assert call_kwargs[1]["json"]["message_type"] == "incoming"


class TestToggleStatus:

    @patch("app.services.chatwoot.settings")
    async def test_toggle_status_calls_correct_url(self, mock_settings):
        mock_settings.chatwoot_base_url = "https://chat.example.com"
        mock_settings.chatwoot_bot_token = "tok_123"

        client = ChatwootClient()
        mock_resp = _mock_response({"status": "resolved"})
        client._client = AsyncMock()
        client._client.post = AsyncMock(return_value=mock_resp)

        result = await client.toggle_conversation_status(5, 42, "resolved")

        client._client.post.assert_called_once_with(
            "https://chat.example.com/api/v1/accounts/5/conversations/42/toggle_status",
            json={"status": "resolved"},
            headers={"api_access_token": "tok_123"},
        )
        assert result["status"] == "resolved"


class TestAssignConversation:

    @patch("app.services.chatwoot.settings")
    async def test_assign_calls_correct_url(self, mock_settings):
        mock_settings.chatwoot_base_url = "https://chat.example.com"
        mock_settings.chatwoot_bot_token = "tok_123"

        client = ChatwootClient()
        mock_resp = _mock_response({"assignee_id": 7})
        client._client = AsyncMock()
        client._client.post = AsyncMock(return_value=mock_resp)

        result = await client.assign_conversation(5, 42, 7)

        client._client.post.assert_called_once_with(
            "https://chat.example.com/api/v1/accounts/5/conversations/42/assignments",
            json={"assignee_id": 7},
            headers={"api_access_token": "tok_123"},
        )
        assert result["assignee_id"] == 7
