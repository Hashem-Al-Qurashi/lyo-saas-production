import logging
import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class ChatwootClient:
    """Async HTTP client for the Chatwoot Agent Bot API."""

    def __init__(self):
        self._client = httpx.AsyncClient(timeout=10.0)

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    async def send_message(
        self,
        account_id: int,
        conversation_id: int,
        content: str,
        message_type: str = "outgoing",
    ) -> dict:
        """Send a message to a Chatwoot conversation."""
        url = (
            f"{settings.chatwoot_base_url}/api/v1/accounts/{account_id}"
            f"/conversations/{conversation_id}/messages"
        )
        headers = {"api_access_token": settings.chatwoot_bot_token}
        payload = {"content": content, "message_type": message_type}

        resp = await self._client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Conversation management
    # ------------------------------------------------------------------

    async def toggle_conversation_status(
        self,
        account_id: int,
        conversation_id: int,
        status: str,
    ) -> dict:
        """Toggle conversation status (open / resolved / pending)."""
        url = (
            f"{settings.chatwoot_base_url}/api/v1/accounts/{account_id}"
            f"/conversations/{conversation_id}/toggle_status"
        )
        headers = {"api_access_token": settings.chatwoot_bot_token}
        payload = {"status": status}

        resp = await self._client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()

    async def assign_conversation(
        self,
        account_id: int,
        conversation_id: int,
        assignee_id: int,
    ) -> dict:
        """Assign a conversation to a human agent."""
        url = (
            f"{settings.chatwoot_base_url}/api/v1/accounts/{account_id}"
            f"/conversations/{conversation_id}/assignments"
        )
        headers = {"api_access_token": settings.chatwoot_bot_token}
        payload = {"assignee_id": assignee_id}

        resp = await self._client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        await self._client.aclose()


# Singleton
chatwoot_client = ChatwootClient()
