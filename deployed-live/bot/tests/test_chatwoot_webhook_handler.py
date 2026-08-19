"""Gate 2 tests for the /webhook/chatwoot route glue: auth + dispatch + send +
takeover toggle. Calls the route coroutine directly with a fake Request so no
DB/startup is needed."""
import os
import asyncio

# Module-level env the bot validates at import time.
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ["CHATWOOT_WEBHOOK_SECRET"] = "s3cret"

import salon_bot_with_booking as bot  # noqa: E402
import chatwoot_bridge as cb  # noqa: E402


class FakeRequest:
    def __init__(self, body, token=None):
        self._body = body
        self.query_params = {} if token is None else {"token": token}

    async def json(self):
        return self._body


def _setup():
    cb.use_takeover_store(cb.InMemoryTakeoverStore())
    sent = []

    async def fake_send(phone, message, business=None):
        sent.append((phone, message))
        return True

    bot.send_whatsapp_message = fake_send
    return sent


def _agent_payload(content="Ciao da Greta", mid=999):
    return {
        "event": "message_created",
        "id": mid,
        "content": content,
        "message_type": "outgoing",
        "private": False,
        "sender": {"type": "user"},
        "conversation": {"id": 1, "status": "open",
                         "meta": {"sender": {"phone_number": "+393331112222"}}},
    }


def test_rejects_bad_token():
    _setup()
    req = FakeRequest(_agent_payload(), token="wrong")
    resp = asyncio.run(bot.chatwoot_webhook(req))
    assert resp.status_code == 403


def test_rejects_missing_token():
    _setup()
    req = FakeRequest(_agent_payload(), token=None)
    resp = asyncio.run(bot.chatwoot_webhook(req))
    assert resp.status_code == 403


def test_forwards_agent_reply_and_sets_takeover():
    sent = _setup()
    req = FakeRequest(_agent_payload(content="Arrivo subito"), token="s3cret")
    resp = asyncio.run(bot.chatwoot_webhook(req))
    assert resp.status_code == 200
    assert sent == [("+393331112222", "Arrivo subito")]
    assert cb.has_human_takeover("+393331112222") is True


def test_bot_echo_not_forwarded():
    sent = _setup()
    cb._record_bot_pushed(999)  # bot pushed this message id
    req = FakeRequest(_agent_payload(mid=999), token="s3cret")
    resp = asyncio.run(bot.chatwoot_webhook(req))
    assert resp.status_code == 200
    assert sent == []  # echo guard prevented re-send
    assert cb.has_human_takeover("+393331112222") is False


def test_resolved_clears_takeover():
    _setup()
    cb.mark_human_takeover("+393331112222")
    payload = {"event": "conversation_status_changed", "status": "resolved",
               "meta": {"sender": {"phone_number": "+393331112222"}}}
    req = FakeRequest(payload, token="s3cret")
    resp = asyncio.run(bot.chatwoot_webhook(req))
    assert resp.status_code == 200
    assert cb.has_human_takeover("+393331112222") is False


def test_incoming_message_ignored():
    sent = _setup()
    p = _agent_payload()
    p["message_type"] = "incoming"
    req = FakeRequest(p, token="s3cret")
    resp = asyncio.run(bot.chatwoot_webhook(req))
    assert resp.status_code == 200
    assert sent == []


def test_process_message_suspends_ai_under_takeover():
    """Reachability proof: the chokepoint gate in process_message stops every
    AI reply path while takeover is active, but still mirrors the customer msg."""
    _setup()
    cb.use_takeover_store(cb.InMemoryTakeoverStore())
    cb.mark_human_takeover("+393331112222")

    calls = {"ai": 0, "sent": 0, "mirrored": []}

    async def fake_mark_as_read(mid, business=None):
        return True

    async def fake_send(phone, message, business=None):
        calls["sent"] += 1
        return True

    async def fake_push(direction, phone, content, name=None):
        calls["mirrored"].append((direction, content))

    def fake_ai(*a, **k):
        calls["ai"] += 1
        return "should not happen"

    bot.mark_as_read = fake_mark_as_read
    bot.send_whatsapp_message = fake_send
    bot._chatwoot_push = fake_push
    bot.get_ai_response = fake_ai

    message = {"from": "+393331112222", "id": "wamid.1", "type": "text",
               "text": {"body": "ci sei?"}}
    value = {"contacts": [{"profile": {"name": "Mario"}}]}
    asyncio.run(bot.process_message(message, value, {"business": {}}))

    assert calls["ai"] == 0          # AI never ran
    assert calls["sent"] == 0        # no bot reply sent
    assert calls["mirrored"] == [("in", "ci sei?")]  # customer msg still mirrored
