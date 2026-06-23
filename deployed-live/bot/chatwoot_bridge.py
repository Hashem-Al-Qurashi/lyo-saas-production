"""WhatsApp ↔ Chatwoot bridge.

Forwards customer + bot messages into Chatwoot API channel inbox so agents can
view the conversation in the dashboard, and forwards agent replies from Chatwoot
back to the customer over WhatsApp.

Handoff: when a human agent posts a message from the dashboard, the conversation
is marked as 'human takeover' and the bot AI stops responding until the
conversation is resolved.
"""
import os
import time
import logging
import requests
from typing import Optional, Dict, Any

logger = logging.getLogger("chatwoot_bridge")

CHATWOOT_BASE = (os.getenv("CHATWOOT_BASE_URL") or "").rstrip("/")
CHATWOOT_TOKEN = os.getenv("CHATWOOT_API_TOKEN", "")
CHATWOOT_ACCOUNT_ID = int(os.getenv("CHATWOOT_ACCOUNT_ID", "1"))
CHATWOOT_INBOX_ID = int(os.getenv("CHATWOOT_INBOX_ID", "1"))

# In-memory cache: phone -> {"contact_id": int, "conversation_id": int}
_phone_cache: Dict[str, Dict[str, int]] = {}

# Human-takeover state is kept in a pluggable store (see TakeoverStore below).
# Default is in-memory; the bot swaps in a Postgres-backed store at startup so
# takeover survives a process restart (a restart must NOT let the AI talk over
# a human agent mid-handoff). A TTL self-heals a missed "resolved" webhook.
DEFAULT_TAKEOVER_TTL_SECONDS = int(os.getenv("CHATWOOT_TAKEOVER_TTL_SECONDS", str(6 * 3600)))

# Track IDs of outgoing messages WE pushed (avoid webhook echo loop).
# Bounded to last 200 to cap memory.
from collections import deque
_bot_pushed_msg_ids: deque = deque(maxlen=200)
_bot_pushed_msg_id_set: set = set()


def _record_bot_pushed(msg_id):
    if msg_id is None:
        return
    if len(_bot_pushed_msg_ids) >= 200:
        evicted = _bot_pushed_msg_ids.popleft()
        _bot_pushed_msg_id_set.discard(evicted)
    _bot_pushed_msg_ids.append(msg_id)
    _bot_pushed_msg_id_set.add(msg_id)


def is_enabled() -> bool:
    return bool(CHATWOOT_BASE and CHATWOOT_TOKEN and CHATWOOT_ACCOUNT_ID and CHATWOOT_INBOX_ID)


def _headers() -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
        "api_access_token": CHATWOOT_TOKEN,
    }


def _normalize_phone(phone: str) -> str:
    """Chatwoot wants E.164 with leading +."""
    p = "".join(ch for ch in (phone or "") if ch.isdigit() or ch == "+")
    if p and not p.startswith("+"):
        p = "+" + p
    return p


def _find_or_create_contact(phone: str, name: Optional[str] = None) -> Optional[int]:
    phone_e164 = _normalize_phone(phone)
    if not phone_e164:
        return None

    cached = _phone_cache.get(phone_e164)
    if cached and cached.get("contact_id"):
        return cached["contact_id"]

    # search by phone
    try:
        r = requests.get(
            f"{CHATWOOT_BASE}/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}/contacts/search",
            params={"q": phone_e164, "include": "contact_inboxes"},
            headers=_headers(),
            timeout=8,
        )
        if r.ok:
            payload = r.json().get("payload") or []
            for c in payload:
                if c.get("phone_number") == phone_e164:
                    _phone_cache.setdefault(phone_e164, {})["contact_id"] = c["id"]
                    return c["id"]
    except Exception as e:
        logger.warning(f"contact search failed for {phone_e164}: {e}")

    # create
    try:
        r = requests.post(
            f"{CHATWOOT_BASE}/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}/contacts",
            json={
                "inbox_id": CHATWOOT_INBOX_ID,
                "name": name or phone_e164,
                "phone_number": phone_e164,
                "identifier": phone_e164,
            },
            headers=_headers(),
            timeout=8,
        )
        if r.ok:
            data = r.json().get("payload") or r.json()
            contact = (data.get("contact") if isinstance(data, dict) else None) or data
            cid = contact.get("id")
            if cid:
                _phone_cache.setdefault(phone_e164, {})["contact_id"] = cid
                return cid
        else:
            logger.warning(f"contact create failed {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.warning(f"contact create exception: {e}")

    return None


def _find_or_create_conversation(contact_id: int, phone_e164: str) -> Optional[int]:
    cached = _phone_cache.get(phone_e164, {})
    if cached.get("conversation_id"):
        return cached["conversation_id"]

    # list conversations for this contact
    try:
        r = requests.get(
            f"{CHATWOOT_BASE}/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}/contacts/{contact_id}/conversations",
            headers=_headers(),
            timeout=8,
        )
        if r.ok:
            payload = r.json().get("payload") or []
            # prefer open conversation in our inbox
            for conv in payload:
                if conv.get("inbox_id") == CHATWOOT_INBOX_ID and conv.get("status") in ("open", "pending"):
                    _phone_cache.setdefault(phone_e164, {})["conversation_id"] = conv["id"]
                    return conv["id"]
    except Exception as e:
        logger.warning(f"conversation lookup failed for contact {contact_id}: {e}")

    # create new conversation
    try:
        r = requests.post(
            f"{CHATWOOT_BASE}/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}/conversations",
            json={
                "source_id": phone_e164,
                "inbox_id": CHATWOOT_INBOX_ID,
                "contact_id": contact_id,
                "status": "open",
            },
            headers=_headers(),
            timeout=8,
        )
        if r.ok:
            cid = r.json().get("id")
            if cid:
                _phone_cache.setdefault(phone_e164, {})["conversation_id"] = cid
                return cid
        else:
            logger.warning(f"conversation create failed {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.warning(f"conversation create exception: {e}")

    return None


def push_incoming(phone: str, content: str, name: Optional[str] = None) -> None:
    """Customer (WhatsApp) message → Chatwoot inbox as 'incoming'."""
    if not is_enabled() or not content:
        return
    try:
        phone_e164 = _normalize_phone(phone)
        contact_id = _find_or_create_contact(phone_e164, name)
        if not contact_id:
            return
        conv_id = _find_or_create_conversation(contact_id, phone_e164)
        if not conv_id:
            return
        requests.post(
            f"{CHATWOOT_BASE}/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}/conversations/{conv_id}/messages",
            json={"content": content, "message_type": "incoming"},
            headers=_headers(),
            timeout=8,
        )
    except Exception as e:
        logger.warning(f"push_incoming failed: {e}")


def push_outgoing(phone: str, content: str) -> None:
    """Bot reply → Chatwoot inbox as 'outgoing' (so it appears in the conversation)."""
    if not is_enabled() or not content:
        return
    try:
        phone_e164 = _normalize_phone(phone)
        contact_id = _find_or_create_contact(phone_e164, None)
        if not contact_id:
            return
        conv_id = _find_or_create_conversation(contact_id, phone_e164)
        if not conv_id:
            return
        # Chatwoot does NOT preserve custom content_attributes via API.
        # Instead capture the returned message id and remember it; the webhook
        # handler skips any message id we pushed ourselves.
        r = requests.post(
            f"{CHATWOOT_BASE}/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}/conversations/{conv_id}/messages",
            json={"content": content, "message_type": "outgoing", "private": False},
            headers=_headers(),
            timeout=8,
        )
        if r.ok:
            _record_bot_pushed((r.json() or {}).get("id"))
    except Exception as e:
        logger.warning(f"push_outgoing failed: {e}")


def is_bot_pushed_message(msg: dict) -> bool:
    """Webhook loop guard: did we push this message ourselves?"""
    try:
        mid = msg.get("id")
        return mid is not None and mid in _bot_pushed_msg_id_set
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Takeover store (pluggable: in-memory for tests/fallback, Postgres for prod)
# ---------------------------------------------------------------------------
class InMemoryTakeoverStore:
    """Process-local store. Lost on restart — used for tests and as a safe
    fallback if the DB is unavailable at startup."""

    def __init__(self):
        self._active: Dict[str, float] = {}  # phone_e164 -> epoch seconds set

    def set(self, phone_e164: str) -> None:
        self._active[phone_e164] = time.time()

    def clear(self, phone_e164: str) -> None:
        self._active.pop(phone_e164, None)

    def is_active(self, phone_e164: str, ttl_seconds: int) -> bool:
        ts = self._active.get(phone_e164)
        if ts is None:
            return False
        if time.time() - ts > ttl_seconds:
            # expired — self-heal a missed resolve event
            self._active.pop(phone_e164, None)
            return False
        return True


class PostgresTakeoverStore:
    """Durable store backed by the bot's existing RDS Postgres. The table is
    created idempotently at startup so no separate migration step is needed."""

    def __init__(self, conn_factory):
        # conn_factory: zero-arg callable returning a psycopg2 connection
        self._conn_factory = conn_factory
        self._ensure_table()

    def _ensure_table(self) -> None:
        with self._conn_factory() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS chatwoot_takeover (
                        phone       TEXT PRIMARY KEY,
                        active      BOOLEAN NOT NULL DEFAULT TRUE,
                        updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
            conn.commit()

    def set(self, phone_e164: str) -> None:
        with self._conn_factory() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO chatwoot_takeover (phone, active, updated_at)
                    VALUES (%s, TRUE, NOW())
                    ON CONFLICT (phone)
                    DO UPDATE SET active = TRUE, updated_at = NOW()
                    """,
                    (phone_e164,),
                )
            conn.commit()

    def clear(self, phone_e164: str) -> None:
        with self._conn_factory() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE chatwoot_takeover SET active = FALSE, updated_at = NOW() WHERE phone = %s",
                    (phone_e164,),
                )
            conn.commit()

    def is_active(self, phone_e164: str, ttl_seconds: int) -> bool:
        with self._conn_factory() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT 1 FROM chatwoot_takeover
                    WHERE phone = %s AND active = TRUE
                      AND updated_at > NOW() - (%s * INTERVAL '1 second')
                    """,
                    (phone_e164, ttl_seconds),
                )
                return cur.fetchone() is not None


_takeover_store = InMemoryTakeoverStore()


def use_takeover_store(store) -> None:
    """Swap the active takeover store (tests inject in-memory; bot injects Postgres)."""
    global _takeover_store
    _takeover_store = store


def init_takeover_store(conn_factory) -> bool:
    """Try to install the durable Postgres store. Falls back to in-memory on
    failure so the bot never crashes at startup over this. Returns True if the
    durable store was installed."""
    try:
        use_takeover_store(PostgresTakeoverStore(conn_factory))
        logger.info("takeover store: Postgres (durable)")
        return True
    except Exception as e:
        logger.error(f"takeover store: Postgres init failed ({e}); using in-memory fallback")
        return False


def has_human_takeover(phone: str, ttl_seconds: int = DEFAULT_TAKEOVER_TTL_SECONDS) -> bool:
    try:
        return _takeover_store.is_active(_normalize_phone(phone), ttl_seconds)
    except Exception as e:
        logger.warning(f"has_human_takeover check failed for {phone}: {e}")
        return False  # fail open: bot keeps answering rather than going silent


def mark_human_takeover(phone: str) -> None:
    p = _normalize_phone(phone)
    try:
        _takeover_store.set(p)
        logger.info(f"human takeover ON for {p}")
    except Exception as e:
        logger.error(f"mark_human_takeover failed for {p}: {e}")


def clear_human_takeover(phone: str) -> None:
    p = _normalize_phone(phone)
    try:
        _takeover_store.clear(p)
        logger.info(f"human takeover CLEARED for {p}")
    except Exception as e:
        logger.error(f"clear_human_takeover failed for {p}: {e}")


# ---------------------------------------------------------------------------
# Inbound webhook classification (pure, I/O-free — unit tested)
# ---------------------------------------------------------------------------
def normalize_message_type(value) -> Optional[str]:
    """Chatwoot emits message_type as 'incoming'/'outgoing' or 0/1."""
    if value in ("outgoing", 1):
        return "outgoing"
    if value in ("incoming", 0):
        return "incoming"
    return None


def phone_from_conversation_payload(payload: dict) -> Optional[str]:
    """Extract customer phone from a Chatwoot webhook payload.

    Handles both message_created (conversation nested) and conversation_*
    events (meta at top level), and falls back to identifier when
    phone_number is absent.
    """
    try:
        for meta_holder in (payload.get("conversation") or {}, payload):
            sender = ((meta_holder.get("meta") or {}).get("sender")) or {}
            phone = sender.get("phone_number") or sender.get("identifier")
            if phone:
                return phone
        sender2 = payload.get("sender") or {}
        return sender2.get("phone_number") or sender2.get("identifier")
    except Exception:
        return None


def classify_webhook_event(payload: dict) -> Dict[str, Any]:
    """Decide what to do with a Chatwoot webhook event.

    Returns a dict with an 'action':
      - {"action": "forward", "phone", "content"}  human agent reply -> WhatsApp + takeover
      - {"action": "clear_takeover", "phone"}      conversation resolved -> resume AI
      - {"action": "ignore", "reason"}             everything else
    """
    event = payload.get("event")

    if event in ("conversation_status_changed", "conversation_updated", "conversation_resolved"):
        status = payload.get("status") or (payload.get("conversation") or {}).get("status")
        if status == "resolved":
            return {"action": "clear_takeover", "phone": phone_from_conversation_payload(payload)}

        # Label-based pause: if the conversation has 'bot_paused' label, suspend AI immediately
        # without waiting for an agent to type a reply. Labels live at payload["labels"] or
        # payload["conversation"]["labels"] depending on Chatwoot version.
        conv_labels = (
            payload.get("labels")
            or (payload.get("conversation") or {}).get("labels")
            or []
        )
        if "bot_paused" in conv_labels:
            return {"action": "set_takeover", "phone": phone_from_conversation_payload(payload)}

        return {"action": "ignore", "reason": "status_not_resolved"}

    if event != "message_created":
        return {"action": "ignore", "reason": "unhandled_event"}

    if payload.get("private"):
        return {"action": "ignore", "reason": "private"}

    if normalize_message_type(payload.get("message_type")) != "outgoing":
        # incoming = customer's own message (already mirrored); never echo back
        return {"action": "ignore", "reason": "incoming"}

    if is_bot_pushed_message(payload):
        return {"action": "ignore", "reason": "bot_echo"}

    content = (payload.get("content") or "").strip()
    if not content:
        return {"action": "ignore", "reason": "empty"}

    phone = phone_from_conversation_payload(payload)
    if not phone:
        return {"action": "ignore", "reason": "no_phone"}

    return {"action": "forward", "phone": phone, "content": content}
