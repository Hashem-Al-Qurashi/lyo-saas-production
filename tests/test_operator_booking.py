"""Tests for operator-aware bookings in business_context."""

import pytest
from unittest.mock import patch, MagicMock, call

from business_context import load_operators, resolve_operator, build_system_prompt, build_booking_tools


# ---- Fixtures / helpers ----

def _make_operators():
    """Two operators with different treatment sets."""
    return [
        {"id": 1, "display_name": "Giulia", "is_active": True,
         "treatments": ["taglio_donna", "balayage", "colore_base"]},
        {"id": 2, "display_name": "Luca", "is_active": True,
         "treatments": ["taglio_donna", "taglio_uomo", "piega"]},
    ]


def _make_biz_context(operators=None):
    """Build a minimal biz_context for testing."""
    return {
        "business": {
            "id": 1, "name": "Test Salon", "bot_name": "TestBot",
            "bot_persona": "You are TestBot at Test Salon.",
            "address": "Via Test 1", "phone": "+39 000", "email": "test@test.it",
            "timezone": "Europe/Rome", "language": "it",
            "owner_email": "owner@test.it",
        },
        "services": {
            "taglio_donna": {"name_it": "Taglio Donna", "name_en": "Haircut", "price": 60, "duration": 45, "description": None},
        },
        "hours": {
            0: {"is_open": False, "open_time": None, "close_time": None},
            1: {"is_open": True, "open_time": "09:00", "close_time": "19:00"},
        },
        "closures": [{"date": "2026-12-25", "reason": "Natale"}],
        "operators": operators if operators is not None else [],
    }


class TestLoadOperators:
    """Load active operators with their treatment codes."""

    @patch("business_context.get_db_connection")
    def test_load_operators_returns_list_of_operator_dicts(self, mock_conn):
        """Mock DB to return 2 operators with treatment mappings."""
        mock_cur = MagicMock()

        # First execute -> operators query; second execute -> treatments query
        mock_cur.fetchall.side_effect = [
            # Operators: (id, display_name, is_active)
            [
                (1, "Giulia", True),
                (2, "Marco", True),
            ],
            # Treatments: (operator_id, code)
            [
                (1, "taglio_donna"),
                (1, "balayage"),
                (2, "taglio_uomo"),
            ],
        ]

        mock_conn.return_value.__enter__ = lambda s: s
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.return_value.cursor.return_value = mock_cur

        result = load_operators(business_id=1)

        assert len(result) == 2

        # First operator
        assert result[0]["id"] == 1
        assert result[0]["display_name"] == "Giulia"
        assert result[0]["is_active"] is True
        assert result[0]["treatments"] == ["taglio_donna", "balayage"]

        # Second operator
        assert result[1]["id"] == 2
        assert result[1]["display_name"] == "Marco"
        assert result[1]["is_active"] is True
        assert result[1]["treatments"] == ["taglio_uomo"]

    @patch("business_context.get_db_connection")
    def test_load_operators_empty_business_returns_empty_list(self, mock_conn):
        """Business with no operators returns []."""
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = []

        mock_conn.return_value.__enter__ = lambda s: s
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.return_value.cursor.return_value = mock_cur

        result = load_operators(business_id=999)

        assert result == []
        # Should only execute the first query (operators), not the treatments query
        assert mock_cur.execute.call_count == 1

    @patch("business_context.get_db_connection")
    def test_load_operators_only_active(self, mock_conn):
        """Verify the SQL filters by is_active=true."""
        mock_cur = MagicMock()
        mock_cur.fetchall.side_effect = [
            [(1, "Giulia", True)],
            [(1, "taglio_donna")],
        ]

        mock_conn.return_value.__enter__ = lambda s: s
        mock_conn.return_value.__exit__ = MagicMock(return_value=False)
        mock_conn.return_value.cursor.return_value = mock_cur

        load_operators(business_id=1)

        # Check the first execute call contains is_active = true
        first_call_sql = mock_cur.execute.call_args_list[0][0][0]
        assert "is_active = true" in first_call_sql.lower()
        assert "business_id = %s" in first_call_sql.lower().replace("\n", " ")


# ===========================================================================
# Task 3: Operators in system prompt
# ===========================================================================

class TestSystemPromptOperators:
    """System prompt includes operator information."""

    def test_prompt_includes_operator_names(self):
        """Multiple operators listed with treatments."""
        biz_context = _make_biz_context(operators=_make_operators())
        prompt = build_system_prompt(biz_context)
        assert "Giulia" in prompt
        assert "Luca" in prompt
        assert "STYLISTS/OPERATORS" in prompt
        assert "preferenza" in prompt.lower()

    def test_prompt_without_operators_has_no_stylist_section(self):
        """Empty operators list = no stylist section."""
        biz_context = _make_biz_context(operators=[])
        prompt = build_system_prompt(biz_context)
        assert "STYLISTS/OPERATORS" not in prompt

    def test_prompt_single_operator_no_preference_question(self):
        """Single operator: name mentioned, no preference question."""
        single = [{"id": 1, "display_name": "Giulia", "is_active": True, "treatments": ["taglio_donna"]}]
        biz_context = _make_biz_context(operators=single)
        prompt = build_system_prompt(biz_context)
        assert "Giulia" in prompt
        assert "STYLISTS/OPERATORS" in prompt
        assert "preferenza" not in prompt.lower()


# ===========================================================================
# Task 5: resolve_operator()
# ===========================================================================

class TestResolveOperator:
    """Resolve operator name to operator_id with validation."""

    def test_resolve_specific_operator(self):
        operators = _make_operators()
        result = resolve_operator("Giulia", "taglio_donna", operators)
        assert result["success"] is True
        assert result["operator_id"] == 1
        assert result["operator_name"] == "Giulia"

    def test_resolve_null_returns_auto_assign_flag(self):
        operators = _make_operators()
        result = resolve_operator(None, "taglio_donna", operators)
        assert result["success"] is True
        assert result["auto_assign"] is True
        # Both Giulia and Luca do taglio_donna
        assert 1 in result["eligible_operator_ids"]
        assert 2 in result["eligible_operator_ids"]

    def test_resolve_operator_wrong_service(self):
        operators = _make_operators()
        # Luca doesn't do balayage
        result = resolve_operator("Luca", "balayage", operators)
        assert result["success"] is False
        assert result["error"] == "OPERATOR_SERVICE_MISMATCH"
        assert "Giulia" in result["alternatives"]

    def test_resolve_operator_not_found(self):
        operators = _make_operators()
        result = resolve_operator("Marco", "taglio_donna", operators)
        assert result["success"] is False
        assert result["error"] == "OPERATOR_NOT_FOUND"

    def test_resolve_no_operators_configured(self):
        result = resolve_operator("Giulia", "taglio_donna", [])
        assert result["success"] is True
        assert result["operator_id"] is None
        assert result["operator_name"] is None

    def test_resolve_case_insensitive(self):
        operators = _make_operators()
        result = resolve_operator("giulia", "taglio_donna", operators)
        assert result["success"] is True
        assert result["operator_id"] == 1
        assert result["operator_name"] == "Giulia"

    def test_resolve_empty_service_code_skips_treatment_check(self):
        """Empty service_code should resolve operator without treatment validation.
        Used by check_availability and get_available_slots which don't know the service."""
        operators = _make_operators()
        # Luca only does taglio_donna/uomo/piega, but "" should skip the check
        result = resolve_operator("Luca", "", operators)
        assert result["success"] is True
        assert result["operator_id"] == 2

    def test_resolve_none_service_code_skips_treatment_check(self):
        operators = _make_operators()
        result = resolve_operator("Luca", None, operators)
        assert result["success"] is True
        assert result["operator_id"] == 2


# ===========================================================================
# Task 4: operator_name in tool definitions
# ===========================================================================

def _services():
    return {"taglio_donna": {"name_it": "Taglio Donna", "name_en": "Haircut", "price": 60, "duration": 45}}


class TestBookingToolsOperator:
    """build_booking_tools adds operator_name when operators present."""

    def test_create_appointment_has_operator_name_param(self):
        tools = build_booking_tools(_services(), operators=_make_operators())
        create = next(t for t in tools if t["function"]["name"] == "create_appointment")
        props = create["function"]["parameters"]["properties"]
        assert "operator_name" in props
        assert None in props["operator_name"]["enum"]
        assert "Giulia" in props["operator_name"]["enum"]
        assert "operator_name" in create["function"]["parameters"]["required"]

    def test_check_availability_has_operator_name_param(self):
        tools = build_booking_tools(_services(), operators=_make_operators())
        check = next(t for t in tools if t["function"]["name"] == "check_availability")
        props = check["function"]["parameters"]["properties"]
        assert "operator_name" in props

    def test_get_available_slots_has_operator_name_param(self):
        tools = build_booking_tools(_services(), operators=_make_operators())
        slots = next(t for t in tools if t["function"]["name"] == "get_available_slots")
        props = slots["function"]["parameters"]["properties"]
        assert "operator_name" in props

    def test_modify_appointment_has_new_operator_param(self):
        tools = build_booking_tools(_services(), operators=_make_operators())
        modify = next(t for t in tools if t["function"]["name"] == "modify_appointment")
        props = modify["function"]["parameters"]["properties"]
        assert "new_operator" in props
        assert None in props["new_operator"]["enum"]
        assert "new_operator" in modify["function"]["parameters"]["required"]

    def test_no_operators_means_no_operator_name_param(self):
        tools = build_booking_tools(_services(), operators=[])
        create = next(t for t in tools if t["function"]["name"] == "create_appointment")
        props = create["function"]["parameters"]["properties"]
        assert "operator_name" not in props
        check = next(t for t in tools if t["function"]["name"] == "check_availability")
        assert "operator_name" not in check["function"]["parameters"]["properties"]
        modify = next(t for t in tools if t["function"]["name"] == "modify_appointment")
        assert "new_operator" not in modify["function"]["parameters"]["properties"]
