"""Tests for app.tools.definitions -- no DB or API key required."""

from app.tools.definitions import build_tools_for_business
from app.models.schemas import Business, Operator, Treatment, BusinessHours


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_business() -> Business:
    return Business(
        id=1,
        chatwoot_account_id=100,
        name="Test Salon",
        operators=[
            Operator(
                id=1, business_id=1, technical_id="op1",
                display_name="Giulia", treatment_ids=[1, 2],
            ),
            Operator(
                id=2, business_id=1, technical_id="op2",
                display_name="Marco", treatment_ids=[1],
            ),
        ],
        treatments=[
            Treatment(
                id=1, business_id=1, code="taglio_donna",
                name_it="Taglio Donna", duration_minutes=45,
                operator_ids=[1, 2],
            ),
            Treatment(
                id=2, business_id=1, code="piega",
                name_it="Piega", duration_minutes=30,
                operator_ids=[1],
            ),
            # Inactive treatment -- must NOT appear in enums
            Treatment(
                id=3, business_id=1, code="colore_old",
                name_it="Colore Vecchio", duration_minutes=60,
                is_active=False,
                operator_ids=[1],
            ),
        ],
        hours=[],
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestToolDefinitions:

    def test_tools_count(self):
        """All 9 tool names must be present."""
        tools = build_tools_for_business(_make_business())
        names = [t["function"]["name"] for t in tools]
        expected = [
            "create_appointment",
            "check_availability",
            "get_available_slots",
            "get_operators_for_treatment",
            "get_customer_appointments",
            "cancel_appointment",
            "modify_appointment",
            "confirm_reminder",
            "escalate_to_human",
        ]
        assert sorted(names) == sorted(expected)
        assert len(tools) == 9

    def test_tools_have_dynamic_treatment_enum(self):
        """Treatment code enums must come from the business's active treatments."""
        biz = _make_business()
        tools = build_tools_for_business(biz)

        # Pick check_availability -- it has treatment_code
        check_tool = next(
            t for t in tools if t["function"]["name"] == "check_availability"
        )
        tc_param = check_tool["function"]["parameters"]["properties"]["treatment_code"]
        assert "taglio_donna" in tc_param["enum"]
        assert "piega" in tc_param["enum"]
        # Inactive treatment must NOT be present
        assert "colore_old" not in tc_param["enum"]

    def test_tools_include_operator_names(self):
        """Operator name enums must come from the business's active operators."""
        biz = _make_business()
        tools = build_tools_for_business(biz)

        # Pick create_appointment -- it has operator_name
        create_tool = next(
            t for t in tools if t["function"]["name"] == "create_appointment"
        )
        op_param = create_tool["function"]["parameters"]["properties"]["operator_name"]
        assert "Giulia" in op_param["enum"]
        assert "Marco" in op_param["enum"]
        # null must be a valid option
        assert None in op_param["enum"]

    def test_tools_have_strict_true(self):
        """Every tool definition must have strict=True."""
        tools = build_tools_for_business(_make_business())
        for tool in tools:
            assert tool["function"]["strict"] is True, (
                f"Tool {tool['function']['name']} missing strict=True"
            )

    def test_empty_treatments_produce_empty_enum(self):
        """If business has no active treatments, enums should be empty."""
        biz = Business(
            id=1, chatwoot_account_id=100, name="Empty",
            operators=[], treatments=[], hours=[],
        )
        tools = build_tools_for_business(biz)
        check_tool = next(
            t for t in tools if t["function"]["name"] == "check_availability"
        )
        tc_param = check_tool["function"]["parameters"]["properties"]["treatment_code"]
        assert tc_param["enum"] == []
