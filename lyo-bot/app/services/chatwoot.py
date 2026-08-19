import logging
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.config import settings

logger = logging.getLogger(__name__)

# Retry on server errors and timeouts
_RETRY_EXCEPTIONS = (httpx.TimeoutException, httpx.ConnectError)
_RETRY_DECORATOR = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type(_RETRY_EXCEPTIONS),
    before_sleep=lambda rs: logger.warning(
        "Chatwoot call failed (attempt %d), retrying: %s", rs.attempt_number, rs.outcome.exception()
    ),
)


class ChatwootClient:
    """Async HTTP client for the Chatwoot Agent Bot API."""

    def __init__(self):
        self._client = httpx.AsyncClient(timeout=10.0)

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    @_RETRY_DECORATOR
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

    @_RETRY_DECORATOR
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

    @_RETRY_DECORATOR
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
