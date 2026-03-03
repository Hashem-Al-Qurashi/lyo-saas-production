"""Tests for load_operators in business_context."""

import pytest
from unittest.mock import patch, MagicMock, call

from business_context import load_operators


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
