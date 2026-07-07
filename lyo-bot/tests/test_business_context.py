"""Tests for business_context.py — the EC2 bot's system prompt builder.

These tests cover the UX problems found in the 2026-03-29 44-scenario investigation:
  P1: _compute_next_open_date must handle overlapping closures correctly
  P4: build_system_prompt must include permanent-closed-day reminder
  P5: resolve_operator must do fuzzy partial-name matching
  P7: build_services_dict must flag orphan services (no operator assigned)

No real database required — all functions under test are pure.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../"))

from business_context import (
    _compute_next_open_date,
    build_services_dict,
    resolve_operator,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

# Aura hours: Mon(0) closed, Tue-Fri(1-4) 09-19, Sat(5) 09-18, Sun(6) closed
_AURA_HOURS = {
    0: {"is_open": False},
    1: {"is_open": True, "open_time": "09:00", "close_time": "19:00"},
    2: {"is_open": True, "open_time": "09:00", "close_time": "19:00"},
    3: {"is_open": True, "open_time": "09:00", "close_time": "19:00"},
    4: {"is_open": True, "open_time": "09:00", "close_time": "19:00"},
    5: {"is_open": True, "open_time": "09:00", "close_time": "18:00"},
    6: {"is_open": False},
}

_OVERLAPPING_CLOSURES = [
    {"date": "2026-03-27", "end_date": "2026-04-12", "reason": "pasqua"},
    {"date": "2026-04-09", "end_date": "2026-04-16", "reason": "restauro"},
]


# ---------------------------------------------------------------------------
# P1: _compute_next_open_date — overlapping closures
# ---------------------------------------------------------------------------

class TestComputeNextOpenDate:

    def test_overlapping_closures_returns_first_open_day(self):
        """P1: pasqua (ends 12 Apr) + restauro (ends 16 Apr) → reopen Sat 17 Apr.

        The bot was returning 14 Apr (end of pasqua + 2 days) because it
        ignored that restauro closure overlaps until 16 Apr. This test
        locks the correct answer: 2026-04-17 (Saturday).
        """
        result = _compute_next_open_date(
            _OVERLAPPING_CLOSURES, _AURA_HOURS, reference_date="2026-03-29"
        )
        assert result == "2026-04-17", (
            f"Expected 2026-04-17 (sabato) but got {result}. "
            "Bot must account for ALL overlapping closure ranges, not just the first."
        )

    def test_single_closure_skips_permanently_closed_weekdays(self):
        """P1: after a Friday-ending closure, Mon and Sun are also skipped.

        Closure ends 2026-08-15 (Saturday). Sun 16 and Mon 17 are permanently
        closed. First open day must be Tuesday 2026-08-18.
        """
        closures = [
            {"date": "2026-08-01", "end_date": "2026-08-15", "reason": "ferie"},
        ]
        result = _compute_next_open_date(
            closures, _AURA_HOURS, reference_date="2026-08-14"
        )
        assert result == "2026-08-18", (
            f"Expected 2026-08-18 (martedì) but got {result}. "
            "Must skip Sun and Mon which are permanently closed."
        )

    def test_no_closures_returns_none(self):
        """When there are no closures, function returns None (salon is already open)."""
        result = _compute_next_open_date([], _AURA_HOURS, reference_date="2026-04-22")
        assert result is None

    def test_closure_on_normally_open_day_blocks_it(self):
        """A closure on a normally-open Tuesday must block that Tuesday."""
        closures = [{"date": "2026-04-21", "end_date": "2026-04-21", "reason": "test"}]
        # Reference: Mon Apr 20 (closed). Closure blocks Tue Apr 21. Wed Apr 22 is first open.
        result = _compute_next_open_date(
            closures, _AURA_HOURS, reference_date="2026-04-20"
        )
        assert result == "2026-04-22", (
            f"Expected 2026-04-22 (mercoledì) but got {result}."
        )


# ---------------------------------------------------------------------------
# P7: build_services_dict — orphan service flagging
# ---------------------------------------------------------------------------

class TestBuildServicesDictOrphanFlag:

    def test_service_with_no_operator_flagged_unavailable(self):
        """P7: a service that no operator can perform must be flagged.

        massaggio_thai is in the catalog but has zero operators assigned.
        The system prompt must expose this so the bot doesn't offer to book it.
        """
        services = {
            "taglio_donna": {
                "name_it": "Taglio Donna", "name_en": "Women's Cut",
                "price": 60, "duration": 45, "description": None,
            },
            "massaggio_thai": {
                "name_it": "Massaggio Thai", "name_en": "Thai Massage",
                "price": 80, "duration": 60, "description": None,
            },
        }
        operators = [
            {"id": 1, "display_name": "Giulia", "treatments": ["taglio_donna"]},
        ]
        result = build_services_dict(services, operators)

        # taglio_donna has an operator → should NOT be flagged
        assert "MOMENTANEAMENTE NON DISPONIBILE" not in result.split("taglio_donna")[1].split("\n")[0], (
            "taglio_donna has an operator and must NOT be flagged."
        )
        # massaggio_thai has NO operator → must be flagged
        assert "MOMENTANEAMENTE NON DISPONIBILE" in result.split("massaggio_thai")[1].split("\n")[0], (
            "massaggio_thai has no operator and MUST be flagged as unavailable."
        )

    def test_service_with_operator_not_flagged(self):
        """Services that have at least one operator must not be flagged."""
        services = {
            "taglio_donna": {
                "name_it": "Taglio Donna", "name_en": "Women's Cut",
                "price": 60, "duration": 45, "description": None,
            },
        }
        operators = [{"id": 1, "display_name": "Giulia", "treatments": ["taglio_donna"]}]
        result = build_services_dict(services, operators)
        assert "MOMENTANEAMENTE NON DISPONIBILE" not in result


# ---------------------------------------------------------------------------
# P5: resolve_operator — fuzzy partial name matching
# ---------------------------------------------------------------------------

class TestResolveOperatorFuzzy:

    _OPERATORS = [
        {"id": 1, "display_name": "Giulia", "treatments": ["taglio_donna", "colore"]},
        {"id": 2, "display_name": "Martina", "treatments": ["taglio_donna", "piega"]},
        {"id": 3, "display_name": "Federica", "treatments": ["piega"]},
    ]

    def test_partial_name_marti_resolves_to_martina(self):
        """P5: 'Marti' must match 'Martina' via substring match."""
        result = resolve_operator("Marti", "taglio_donna", self._OPERATORS)
        assert result["success"] is True, f"Expected success but got: {result}"
        assert result.get("operator_name") == "Martina", (
            f"Expected Martina but got {result.get('operator_name')}. "
            "'Marti' is an obvious abbreviation of 'Martina'."
        )

    def test_partial_name_fed_resolves_to_federica(self):
        """P5: 'Fed' must match 'Federica' via substring match."""
        result = resolve_operator("Fed", "piega", self._OPERATORS)
        assert result["success"] is True
        assert result.get("operator_name") == "Federica"

    def test_exact_match_still_works(self):
        """Exact name match must still return the correct operator."""
        result = resolve_operator("Giulia", "taglio_donna", self._OPERATORS)
        assert result["success"] is True
        assert result.get("operator_name") == "Giulia"

    def test_ambiguous_partial_name_returns_not_found(self):
        """If partial name matches multiple operators, return OPERATOR_NOT_FOUND."""
        # "Giuli" matches both "Giulia" and "Giuliana" — must not guess
        ops = [
            {"id": 1, "display_name": "Giulia", "treatments": ["taglio_donna"]},
            {"id": 2, "display_name": "Giuliana", "treatments": ["taglio_donna"]},
        ]
        result = resolve_operator("Giuli", "taglio_donna", ops)
        assert result.get("error") == "OPERATOR_NOT_FOUND", (
            "Ambiguous partial names ('Giuli' matches Giulia and Giuliana) must fail, not guess."
        )

    def test_no_match_returns_not_found(self):
        """Completely unknown name must still return OPERATOR_NOT_FOUND."""
        result = resolve_operator("Xander", "taglio_donna", self._OPERATORS)
        assert result.get("error") == "OPERATOR_NOT_FOUND"
