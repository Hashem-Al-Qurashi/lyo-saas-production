"""Tests for management authentication module."""

from unittest.mock import patch, MagicMock
from contextlib import contextmanager

from management.auth import (
    hash_password,
    verify_password,
    create_token,
    decode_token,
)


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

class TestPasswordHashing:

    def test_hash_and_verify(self):
        plain = "MySuperSecretPassword123!"
        hashed = hash_password(plain)
        assert hashed != plain
        assert verify_password(plain, hashed) is True

    def test_wrong_password_fails(self):
        hashed = hash_password("correct_password")
        assert verify_password("wrong_password", hashed) is False

    def test_hash_is_different_each_time(self):
        h1 = hash_password("same_password")
        h2 = hash_password("same_password")
        assert h1 != h2  # bcrypt uses random salt


# ---------------------------------------------------------------------------
# JWT tokens
# ---------------------------------------------------------------------------

class TestTokens:

    def test_create_and_decode_roundtrip(self):
        token = create_token("admin@salon.it", business_id=42)
        payload = decode_token(token)
        assert payload is not None
        assert payload["sub"] == "admin@salon.it"
        assert payload["business_id"] == 42

    def test_decode_invalid_token_returns_none(self):
        result = decode_token("this.is.not.a.valid.jwt")
        assert result is None

    def test_decode_tampered_token_returns_none(self):
        token = create_token("admin@salon.it", business_id=1)
        # Tamper with the token
        parts = token.split(".")
        parts[1] = parts[1][:-3] + "xyz"
        tampered = ".".join(parts)
        assert decode_token(tampered) is None

    def test_token_contains_exp_claim(self):
        token = create_token("test@example.com", business_id=5)
        payload = decode_token(token)
        assert "exp" in payload


# ---------------------------------------------------------------------------
# authenticate_user (with mocked DB)
# ---------------------------------------------------------------------------

class TestAuthenticateUser:

    @patch("management.auth.get_connection")
    def test_valid_credentials(self, mock_get_conn):
        from management.auth import authenticate_user

        hashed = hash_password("password123")
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (1, 42, "admin@salon.it", hashed, "Admin User", "admin")
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        @contextmanager
        def _ctx():
            yield mock_conn

        mock_get_conn.return_value = _ctx()

        result = authenticate_user("admin@salon.it", "password123")
        assert result is not None
        assert result["id"] == 1
        assert result["business_id"] == 42
        assert result["email"] == "admin@salon.it"
        assert result["role"] == "admin"

    @patch("management.auth.get_connection")
    def test_wrong_password(self, mock_get_conn):
        from management.auth import authenticate_user

        hashed = hash_password("correct_password")
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (1, 42, "admin@salon.it", hashed, "Admin", "admin")
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        @contextmanager
        def _ctx():
            yield mock_conn

        mock_get_conn.return_value = _ctx()

        result = authenticate_user("admin@salon.it", "wrong_password")
        assert result is None

    @patch("management.auth.get_connection")
    def test_user_not_found(self, mock_get_conn):
        from management.auth import authenticate_user

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        @contextmanager
        def _ctx():
            yield mock_conn

        mock_get_conn.return_value = _ctx()

        result = authenticate_user("nobody@example.com", "whatever")
        assert result is None
