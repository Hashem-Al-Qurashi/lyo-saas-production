"""WhatsApp ↔ Chatwoot bridge.

Forwards customer + bot messages into Chatwoot API channel inbox so agents can
view the conversation in the dashboard, and forwards agent replies from Chatwoot
back to the customer over WhatsApp.

Handoff: when a human agent posts a message from the dashboard, the conversation
is marked as 'human takeover' and the bot AI stops responding until the
conversation is resolved.
"""
import os
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

# In-memory set of phones where human agent has taken over (bot AI suspended)
_human_takeover: set = set()

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


def has_human_takeover(phone: str) -> bool:
    return _normalize_phone(phone) in _human_takeover


def mark_human_takeover(phone: str) -> None:
    _human_takeover.add(_normalize_phone(phone))
    logger.info(f"human takeover ON for {_normalize_phone(phone)}")


def clear_human_takeover(phone: str) -> None:
    _human_takeover.discard(_normalize_phone(phone))
    logger.info(f"human takeover CLEARED for {_normalize_phone(phone)}")


def phone_from_conversation_payload(payload: dict) -> Optional[str]:
    """Extract customer phone from a Chatwoot webhook payload."""
    try:
        # webhook may include conversation -> meta -> sender
        conv = payload.get("conversation") or {}
        meta = conv.get("meta") or {}
        sender = meta.get("sender") or {}
        phone = sender.get("phone_number") or sender.get("identifier")
        if phone:
            return phone
        # alternate locations
        sender2 = payload.get("sender") or {}
        return sender2.get("phone_number") or sender2.get("identifier")
    except Exception:
        return None
