"""Gate 2 (TDD) tests for human-takeover bridge logic.

Targets pure, I/O-free logic in chatwoot_bridge so it runs without a DB or network:
  - normalize_message_type
  - classify_webhook_event (forward / ignore / clear_takeover branches)
  - InMemoryTakeoverStore (set/clear/is_active + TTL expiry)
  - phone_from_conversation_payload (nested + flat + identifier fallback)
  - echo guard integration via is_bot_pushed_message
"""
import time
import importlib

import chatwoot_bridge as cb


def setup_function(_fn):
    # isolate module state between tests
    importlib.reload(cb)
    cb.use_takeover_store(cb.InMemoryTakeoverStore())


# ---------- normalize_message_type ----------

def test_normalize_message_type_strings():
    assert cb.normalize_message_type("outgoing") == "outgoing"
    assert cb.normalize_message_type("incoming") == "incoming"


def test_normalize_message_type_ints():
    # Chatwoot sometimes emits 0=incoming, 1=outgoing
    assert cb.normalize_message_type(0) == "incoming"
    assert cb.normalize_message_type(1) == "outgoing"


def test_normalize_message_type_unknown():
    assert cb.normalize_message_type(None) is None
    assert cb.normalize_message_type("weird") is None


# ---------- phone extraction ----------

def test_phone_from_nested_conversation_meta():
    payload = {"conversation": {"meta": {"sender": {"phone_number": "+393331112222"}}}}
    assert cb.phone_from_conversation_payload(payload) == "+393331112222"


def test_phone_from_flat_meta_for_conversation_events():
    payload = {"meta": {"sender": {"phone_number": "+393331112222"}}}
    assert cb.phone_from_conversation_payload(payload) == "+393331112222"


def test_phone_falls_back_to_identifier():
    payload = {"conversation": {"meta": {"sender": {"identifier": "+393331112222"}}}}
    assert cb.phone_from_conversation_payload(payload) == "+393331112222"


# ---------- classify_webhook_event ----------

def _agent_msg(content="Ciao, sono Greta", mtype="outgoing", private=False, mid=555):
    return {
        "event": "message_created",
        "id": mid,
        "content": content,
        "message_type": mtype,
        "private": private,
        "sender": {"type": "user", "name": "Greta"},
        "conversation": {"id": 42, "status": "open",
                         "meta": {"sender": {"phone_number": "+393331112222"}}},
    }


def test_classify_forwards_human_agent_message():
    res = cb.classify_webhook_event(_agent_msg())
    assert res["action"] == "forward"
    assert res["phone"] == "+393331112222"
    assert res["content"] == "Ciao, sono Greta"


def test_classify_ignores_incoming_customer_message():
    msg = _agent_msg(mtype="incoming")
    res = cb.classify_webhook_event(msg)
    assert res["action"] == "ignore"
    assert res["reason"] == "incoming"


def test_classify_ignores_private_note():
    res = cb.classify_webhook_event(_agent_msg(private=True))
    assert res["action"] == "ignore"
    assert res["reason"] == "private"


def test_classify_ignores_empty_content():
    res = cb.classify_webhook_event(_agent_msg(content="   "))
    assert res["action"] == "ignore"
    assert res["reason"] == "empty"


def test_classify_ignores_bot_pushed_echo():
    # simulate the bot having pushed message id 555
    cb._record_bot_pushed(555)
    res = cb.classify_webhook_event(_agent_msg(mid=555))
    assert res["action"] == "ignore"
    assert res["reason"] == "bot_echo"


def test_classify_clear_takeover_on_resolved():
    payload = {
        "event": "conversation_status_changed",
        "status": "resolved",
        "id": 42,
        "meta": {"sender": {"phone_number": "+393331112222"}},
    }
    res = cb.classify_webhook_event(payload)
    assert res["action"] == "clear_takeover"
    assert res["phone"] == "+393331112222"


def test_classify_open_status_change_is_ignored():
    payload = {"event": "conversation_status_changed", "status": "open",
               "meta": {"sender": {"phone_number": "+393331112222"}}}
    res = cb.classify_webhook_event(payload)
    assert res["action"] == "ignore"


def test_classify_bot_paused_label_sets_takeover():
    payload = {
        "event": "conversation_updated",
        "status": "open",
        "labels": ["bot_paused"],
        "meta": {"sender": {"phone_number": "+393331112222"}},
    }
    res = cb.classify_webhook_event(payload)
    assert res["action"] == "set_takeover"
    assert res["phone"] == "+393331112222"


def test_classify_bot_paused_label_nested_in_conversation():
    payload = {
        "event": "conversation_updated",
        "status": "open",
        "conversation": {
            "labels": ["bot_paused"],
            "meta": {"sender": {"phone_number": "+393331112222"}},
        },
    }
    res = cb.classify_webhook_event(payload)
    assert res["action"] == "set_takeover"


def test_classify_bot_paused_label_removed_clears_takeover():
    cb.mark_human_takeover("+393331112222")
    payload = {
        "event": "conversation_updated",
        "status": "open",
        "labels": ["vip"],  # bot_paused is gone
        "meta": {"sender": {"phone_number": "+393331112222"}},
    }
    res = cb.classify_webhook_event(payload)
    assert res["action"] == "clear_takeover"


def test_classify_empty_labels_clears_takeover():
    """Empty labels list [] is falsy in Python but IS valid — means all labels removed."""
    cb.mark_human_takeover("+393331112222")
    payload = {
        "event": "conversation_updated",
        "status": "open",
        "labels": [],  # all labels removed including bot_paused
        "meta": {"sender": {"phone_number": "+393331112222"}},
    }
    res = cb.classify_webhook_event(payload)
    assert res["action"] == "clear_takeover"


def test_classify_updated_without_labels_field_is_ignored():
    # No labels field at all → can't infer label state → ignore
    payload = {
        "event": "conversation_updated",
        "status": "open",
        "meta": {"sender": {"phone_number": "+393331112222"}},
    }
    res = cb.classify_webhook_event(payload)
    assert res["action"] == "ignore"


def test_classify_unhandled_event():
    res = cb.classify_webhook_event({"event": "contact_created"})
    assert res["action"] == "ignore"
    assert res["reason"] == "unhandled_event"


# ---------- takeover store ----------

def test_takeover_set_and_check():
    cb.mark_human_takeover("+393331112222")
    assert cb.has_human_takeover("+393331112222") is True
    assert cb.has_human_takeover("+390000000000") is False


def test_takeover_clear():
    cb.mark_human_takeover("+393331112222")
    cb.clear_human_takeover("+393331112222")
    assert cb.has_human_takeover("+393331112222") is False


def test_takeover_ttl_expiry():
    cb.mark_human_takeover("+393331112222")
    # within ttl -> active
    assert cb.has_human_takeover("+393331112222", ttl_seconds=100) is True
    # ttl already elapsed -> inactive (self-heal on missed resolve)
    time.sleep(0.01)
    assert cb.has_human_takeover("+393331112222", ttl_seconds=0) is False


def test_takeover_normalizes_phone():
    cb.mark_human_takeover("393331112222")  # no +
    assert cb.has_human_takeover("+393331112222") is True
