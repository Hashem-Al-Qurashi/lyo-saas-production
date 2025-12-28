#!/usr/bin/env python3
"""
Comprehensive Test Suite for Aura Hair Studio WhatsApp Bot
Tests 20 edge cases including language handling, booking, modification, and identity
"""

import os
import sys
import json
import logging
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta

# Suppress OpenAI API key requirement for testing
os.environ["OPENAI_API_KEY"] = "test-key-for-testing"

# Import after setting env
from salon_bot_with_booking import (
    get_ai_response,
    conversation_history,
    SYSTEM_PROMPT,
    SALON_SERVICES,
    normalize_phone,
    validate_business_day_and_time
)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

# Test phone number
TEST_PHONE = "+393312671591"

# Track tools called for each test
tools_called = []

def reset_test_state():
    """Reset test state between tests"""
    global tools_called
    tools_called = []
    if TEST_PHONE in conversation_history:
        del conversation_history[TEST_PHONE]

def create_mock_response(content, tool_calls=None):
    """Create a mock OpenAI response"""
    mock_message = MagicMock()
    mock_message.content = content
    mock_message.tool_calls = tool_calls

    mock_choice = MagicMock()
    mock_choice.message = mock_message

    mock_response = MagicMock()
    mock_response.choices = [mock_choice]

    return mock_response

def create_tool_call(name, arguments, call_id="call_123"):
    """Create a mock tool call object"""
    mock_tool_call = MagicMock()
    mock_tool_call.id = call_id
    mock_tool_call.function = MagicMock()
    mock_tool_call.function.name = name
    mock_tool_call.function.arguments = json.dumps(arguments) if isinstance(arguments, dict) else arguments
    return mock_tool_call

# ============================================================================
# TEST CASES
# ============================================================================

class TestResults:
    passed = 0
    failed = 0
    results = []

def run_test(name, test_func):
    """Run a test and track results"""
    reset_test_state()
    try:
        test_func()
        TestResults.passed += 1
        TestResults.results.append((name, "PASS", None))
        logger.info(f"  ✅ {name}")
        return True
    except AssertionError as e:
        TestResults.failed += 1
        TestResults.results.append((name, "FAIL", str(e)))
        logger.info(f"  ❌ {name}: {str(e)}")
        return False
    except Exception as e:
        TestResults.failed += 1
        TestResults.results.append((name, "ERROR", str(e)))
        logger.info(f"  ❌ {name}: {str(e)}")
        return False

# ============================================================================
# 1. LANGUAGE TESTS
# ============================================================================

def test_english_input_english_response():
    """Test: English input should get English response (not Italian)"""
    # Check that system prompt has proper English handling
    assert "REPLY IN ENGLISH ONLY" in SYSTEM_PROMPT, "System prompt should instruct English response"
    assert "Book haircut Monday" in SYSTEM_PROMPT, "System prompt should have English example"

def test_italian_input_italian_response():
    """Test: Italian input should get Italian response"""
    assert "REPLY IN ITALIAN ONLY" in SYSTEM_PROMPT, "System prompt should instruct Italian response"
    assert "Prenota taglio lunedì" in SYSTEM_PROMPT, "System prompt should have Italian example"

def test_language_rule_is_most_important():
    """Test: Language rule should be clearly marked as #1"""
    assert "LANGUAGE RULE #1" in SYSTEM_PROMPT, "Language should be rule #1"
    assert "MOST IMPORTANT RULE" in SYSTEM_PROMPT, "Language rule should be marked most important"

# ============================================================================
# 2. BUSINESS HOURS TESTS
# ============================================================================

def test_closed_days_clear():
    """Test: Closed days (Monday, Sunday) are clearly specified"""
    assert "CLOSED DAYS: Monday and Sunday ONLY" in SYSTEM_PROMPT, "Closed days not clearly specified"

def test_open_days_clear():
    """Test: Open days are clearly specified"""
    assert "OPEN DAYS: Tuesday, Wednesday, Thursday, Friday, Saturday" in SYSTEM_PROMPT, "Open days not clear"

def test_friday_is_open():
    """Test: Friday is explicitly marked as OPEN (was causing confusion)"""
    assert "FRIDAY IS DEFINITELY OPEN" in SYSTEM_PROMPT, "Friday should be explicitly marked as open"

def test_dec_26_is_normal_friday():
    """Test: December 26 should be marked as a normal open day"""
    assert "December 26" in SYSTEM_PROMPT, "Dec 26 should be mentioned"
    assert "NORMAL Friday" in SYSTEM_PROMPT or "normal Friday" in SYSTEM_PROMPT.lower(), "Dec 26 should be marked as normal Friday"

def test_holiday_closures_limited():
    """Test: Only Christmas and New Year are holidays"""
    assert "December 25" in SYSTEM_PROMPT and "CLOSED" in SYSTEM_PROMPT, "Christmas should be closed"
    assert "January 1" in SYSTEM_PROMPT and "CLOSED" in SYSTEM_PROMPT, "New Year should be closed"

# ============================================================================
# 3. PHONE NUMBER LOOKUP TESTS
# ============================================================================

def test_phone_first_lookup_rule():
    """Test: System should use phone number directly for modify/cancel"""
    assert "PHONE NUMBER RULE" in SYSTEM_PROMPT, "Phone number rule should exist"
    assert "ALREADY have the customer's phone number" in SYSTEM_PROMPT, "Should state phone is already available"
    assert "NEVER ask \"what's your name?\"" in SYSTEM_PROMPT, "Should not ask for name"

def test_immediate_appointment_lookup():
    """Test: Should immediately call get_customer_appointments for modify/cancel"""
    assert "IMMEDIATELY call get_customer_appointments" in SYSTEM_PROMPT, "Should immediately look up appointments"

# ============================================================================
# 4. IDENTITY TESTS
# ============================================================================

def test_identity_as_simone():
    """Test: Bot should identify as Simone, a human employee"""
    assert "You are Simone" in SYSTEM_PROMPT, "Should be Simone"
    assert "HUMAN employee" in SYSTEM_PROMPT, "Should be human"
    assert "NEVER reveal you are AI" in SYSTEM_PROMPT, "Should never reveal AI"

def test_forbidden_ai_words():
    """Test: Should have forbidden words list without 'AI' (causes false positives with 'hair')"""
    assert "FORBIDDEN WORDS:" in SYSTEM_PROMPT, "Should have forbidden words"
    # "AI" should NOT be in forbidden words (causes issues with "Aura Hair Studio")
    # Check that specific terms are forbidden instead
    assert "artificial" in SYSTEM_PROMPT.lower(), "Should forbid 'artificial'"
    assert "chatbot" in SYSTEM_PROMPT.lower(), "Should forbid 'chatbot'"

def test_no_ai_in_forbidden():
    """Test: 'AI' should NOT be in forbidden list (conflicts with 'hair')"""
    # Find the forbidden words line
    import re
    match = re.search(r'FORBIDDEN WORDS:([^\n]+)', SYSTEM_PROMPT)
    if match:
        forbidden_line = match.group(1).lower()
        # "ai" as a standalone word should not be there (to avoid "hair" false positive)
        # We're checking that the line doesn't contain just " ai" or "ai," as a standalone word
        assert " ai" not in forbidden_line.split(","), "'AI' should not be a standalone forbidden word"

# ============================================================================
# 5. MODIFY BEHAVIOR TESTS
# ============================================================================

def test_modify_rule_no_confirmation_when_time_specified():
    """Test: When customer says 'reschedule to 4pm', just do it - don't ask again"""
    assert "MODIFY RULE" in SYSTEM_PROMPT, "Should have modify rule"
    assert "reschedule to 4pm" in SYSTEM_PROMPT or "move it to 3pm" in SYSTEM_PROMPT, "Should have example"
    assert "Call modify_appointment IMMEDIATELY" in SYSTEM_PROMPT, "Should call immediately"
    assert "DON'T ask" in SYSTEM_PROMPT, "Should not ask for confirmation when time given"

def test_conflicting_times_use_last():
    """Test: When customer says '3pm no 4pm actually 5pm', use the LAST time (5pm)"""
    assert "CONFLICTING TIMES" in SYSTEM_PROMPT, "Should have conflicting times rule"
    assert "LAST time mentioned" in SYSTEM_PROMPT, "Should use last time"
    assert "Don't check all times" in SYSTEM_PROMPT, "Should not check all times"

# ============================================================================
# 6. CRITICAL RULE TESTS
# ============================================================================

def test_never_lie_rule():
    """Test: Should have 'NEVER LIE' rule about tool calling"""
    assert "NEVER LIE" in SYSTEM_PROMPT or "NEVER say \"done\"" in SYSTEM_PROMPT, "Should have never lie rule"
    assert "MUST call the tool" in SYSTEM_PROMPT, "Should require tool call"

def test_action_equals_tool_call():
    """Test: Should have clear 'ACTION = TOOL CALL' instruction"""
    assert "ACTION = TOOL CALL" in SYSTEM_PROMPT, "Should have action = tool call"
    assert "TALKING about doing something is NOT the same as DOING it" in SYSTEM_PROMPT, "Should emphasize doing vs talking"

# ============================================================================
# 7. SERVICE TESTS
# ============================================================================

def test_services_defined():
    """Test: All services should be defined"""
    expected_services = ["taglio_donna", "taglio_uomo", "piega", "colore_base", "balayage"]
    for service in expected_services:
        assert service in SALON_SERVICES, f"Service {service} should be defined"

def test_invalid_service_lists_alternatives():
    """Test: System prompt should tell AI to list services when invalid service requested"""
    assert "service we DON'T offer" in SYSTEM_PROMPT or "don't offer" in SYSTEM_PROMPT.lower(), "Should handle invalid services"
    assert "list" in SYSTEM_PROMPT.lower() or "we have:" in SYSTEM_PROMPT.lower(), "Should list alternatives"

# ============================================================================
# 8. NORMALIZE PHONE TEST
# ============================================================================

def test_normalize_phone():
    """Test: Phone normalization function"""
    assert normalize_phone("+393312671591") == "393312671591", "Should strip +"
    assert normalize_phone("393312671591") == "393312671591", "Should keep digits"
    assert normalize_phone("03312671591") == "3312671591", "Should strip leading zeros"

# ============================================================================
# 9. BUSINESS HOURS VALIDATION TESTS (NEW)
# ============================================================================

def test_monday_booking_rejected():
    """Test: Booking on Monday should be rejected"""
    result = validate_business_day_and_time("2025-12-29", "10:00")  # Monday
    assert not result["valid"], "Monday booking should fail"
    assert result["error_code"] == "CLOSED_MONDAY", "Should return CLOSED_MONDAY"

def test_sunday_booking_rejected():
    """Test: Booking on Sunday should be rejected"""
    result = validate_business_day_and_time("2025-12-28", "10:00")  # Sunday
    assert not result["valid"], "Sunday booking should fail"
    assert result["error_code"] == "CLOSED_SUNDAY", "Should return CLOSED_SUNDAY"

def test_christmas_booking_rejected():
    """Test: Booking on Christmas should be rejected"""
    result = validate_business_day_and_time("2025-12-25", "10:00")
    assert not result["valid"], "Christmas booking should fail"
    assert result["error_code"] == "CLOSED_HOLIDAY_CHRISTMAS", "Should return CLOSED_HOLIDAY_CHRISTMAS"

def test_new_year_booking_rejected():
    """Test: Booking on New Year should be rejected"""
    result = validate_business_day_and_time("2026-01-01", "10:00")
    assert not result["valid"], "New Year booking should fail"
    assert result["error_code"] == "CLOSED_HOLIDAY_NEWYEAR", "Should return CLOSED_HOLIDAY_NEWYEAR"

# Business hours tests - COMMENTED OUT until client confirms hours
# def test_after_hours_booking_rejected():
#     """Test: Booking at 8pm (after hours) should be rejected"""
#     result = validate_business_day_and_time("2025-12-30", "20:00")  # Tuesday 8pm
#     assert not result["valid"], "8pm booking should fail"
#     assert result["error_code"] == "OUTSIDE_BUSINESS_HOURS", "Should return OUTSIDE_BUSINESS_HOURS"
#
# def test_saturday_after_5pm_rejected():
#     """Test: Booking on Saturday after 5pm should be rejected"""
#     result = validate_business_day_and_time("2025-12-27", "17:30")  # Saturday 5:30pm
#     assert not result["valid"], "Saturday 5:30pm should fail"
#     assert result["error_code"] == "OUTSIDE_SATURDAY_HOURS", "Should return OUTSIDE_SATURDAY_HOURS"

def test_valid_weekday_booking():
    """Test: Valid weekday booking should succeed"""
    result = validate_business_day_and_time("2025-12-30", "10:00")  # Tuesday 10am
    assert result["valid"], "Tuesday 10am should be valid"

def test_valid_saturday_booking():
    """Test: Valid Saturday booking should succeed"""
    result = validate_business_day_and_time("2025-12-27", "10:00")  # Saturday 10am
    assert result["valid"], "Saturday 10am should be valid"

def test_dec_26_friday_is_open():
    """Test: December 26 (Friday) should be a normal open day"""
    result = validate_business_day_and_time("2025-12-26", "14:00")
    assert result["valid"], "Dec 26 (Friday) should be valid"

# ============================================================================
# RUN ALL TESTS
# ============================================================================

def run_all_tests():
    """Run all tests and report results"""
    logger.info("\n" + "="*60)
    logger.info("🧪 COMPREHENSIVE BOT TEST SUITE")
    logger.info("="*60)

    tests = [
        # Language tests
        ("English input → English response", test_english_input_english_response),
        ("Italian input → Italian response", test_italian_input_italian_response),
        ("Language rule is #1 priority", test_language_rule_is_most_important),

        # Business hours tests
        ("Closed days clearly specified", test_closed_days_clear),
        ("Open days clearly specified", test_open_days_clear),
        ("Friday explicitly marked OPEN", test_friday_is_open),
        ("Dec 26 is normal Friday", test_dec_26_is_normal_friday),
        ("Holidays limited to Christmas/New Year", test_holiday_closures_limited),

        # Phone lookup tests
        ("Phone-first lookup rule exists", test_phone_first_lookup_rule),
        ("Immediate appointment lookup", test_immediate_appointment_lookup),

        # Identity tests
        ("Identity as Simone (human)", test_identity_as_simone),
        ("Forbidden AI words defined", test_forbidden_ai_words),
        ("No 'AI' in forbidden (avoids 'hair' conflict)", test_no_ai_in_forbidden),

        # Modify behavior tests
        ("Modify: no confirmation when time specified", test_modify_rule_no_confirmation_when_time_specified),
        ("Conflicting times: use LAST one", test_conflicting_times_use_last),

        # Critical rule tests
        ("Never lie rule (must call tools)", test_never_lie_rule),
        ("Action = Tool call emphasized", test_action_equals_tool_call),

        # Service tests
        ("All services defined", test_services_defined),
        ("Invalid service lists alternatives", test_invalid_service_lists_alternatives),

        # Utility tests
        ("Phone normalization works", test_normalize_phone),

        # Business validation tests (closed days + holidays)
        ("Monday booking rejected", test_monday_booking_rejected),
        ("Sunday booking rejected", test_sunday_booking_rejected),
        ("Christmas booking rejected", test_christmas_booking_rejected),
        ("New Year booking rejected", test_new_year_booking_rejected),
        # Business hours tests commented out until client confirms
        # ("After hours booking (8pm) rejected", test_after_hours_booking_rejected),
        # ("Saturday after 5pm rejected", test_saturday_after_5pm_rejected),
        ("Valid weekday booking accepted", test_valid_weekday_booking),
        ("Valid Saturday booking accepted", test_valid_saturday_booking),
        ("Dec 26 Friday is open", test_dec_26_friday_is_open),
    ]

    logger.info(f"\n📋 Running {len(tests)} tests...\n")

    for name, test_func in tests:
        run_test(name, test_func)

    logger.info("\n" + "="*60)
    logger.info("📊 RESULTS")
    logger.info("="*60)

    total = TestResults.passed + TestResults.failed
    percentage = (TestResults.passed / total * 100) if total > 0 else 0

    logger.info(f"\n✅ Passed: {TestResults.passed}/{total}")
    logger.info(f"❌ Failed: {TestResults.failed}/{total}")
    logger.info(f"📈 Success Rate: {percentage:.1f}%\n")

    if TestResults.failed > 0:
        logger.info("Failed tests:")
        for name, status, error in TestResults.results:
            if status != "PASS":
                logger.info(f"  - {name}: {error}")

    logger.info("="*60)

    return TestResults.failed == 0

if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
