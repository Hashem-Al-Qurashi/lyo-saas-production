import logging
import re
from typing import Optional

from app.models.database import get_connection
from app.models.schemas import Customer

logger = logging.getLogger(__name__)


class CustomerService:
    """CRUD helpers for the customers table."""

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_customer(self, business_id: int, phone: str) -> Optional[Customer]:
        phone = self._normalize_phone(phone)
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, business_id, phone, first_name, last_name,
                           platform, chatwoot_contact_id
                    FROM customers
                    WHERE business_id = %s AND phone = %s
                    """,
                    (business_id, phone),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return Customer(
                    id=row[0], business_id=row[1], phone=row[2],
                    first_name=row[3], last_name=row[4],
                    platform=row[5], chatwoot_contact_id=row[6],
                )

    # ------------------------------------------------------------------
    # Create / upsert
    # ------------------------------------------------------------------

    def get_or_create(
        self,
        business_id: int,
        phone: str,
        platform: Optional[str] = None,
        chatwoot_contact_id: Optional[int] = None,
    ) -> Customer:
        phone = self._normalize_phone(phone)
        existing = self.get_customer(business_id, phone)
        if existing:
            return existing

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO customers (business_id, phone, platform, chatwoot_contact_id)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id, business_id, phone, first_name, last_name,
                              platform, chatwoot_contact_id
                    """,
                    (business_id, phone, platform, chatwoot_contact_id),
                )
                row = cur.fetchone()
                return Customer(
                    id=row[0], business_id=row[1], phone=row[2],
                    first_name=row[3], last_name=row[4],
                    platform=row[5], chatwoot_contact_id=row[6],
                )

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    def update_name(
        self,
        business_id: int,
        phone: str,
        first_name: str,
        last_name: Optional[str] = None,
    ) -> Optional[Customer]:
        phone = self._normalize_phone(phone)
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE customers
                    SET first_name = %s, last_name = %s
                    WHERE business_id = %s AND phone = %s
                    RETURNING id, business_id, phone, first_name, last_name,
                              platform, chatwoot_contact_id
                    """,
                    (first_name, last_name, business_id, phone),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return Customer(
                    id=row[0], business_id=row[1], phone=row[2],
                    first_name=row[3], last_name=row[4],
                    platform=row[5], chatwoot_contact_id=row[6],
                )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_phone(phone: str) -> str:
        """Strip spaces/dashes, ensure leading +."""
        phone = re.sub(r"[\s\-]", "", phone)
        if not phone.startswith("+"):
            phone = "+" + phone
        return phone


# Singleton
customer_service = CustomerService()
