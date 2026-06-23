import logging
from datetime import date, time
from typing import Optional

import psycopg2

from app.models.database import get_connection
from app.models.schemas import Business
from app.services.availability import availability_service
from app.services.customer import customer_service
from app.services import calendar as cal_module

logger = logging.getLogger(__name__)


class BookingService:
    """Create, cancel, modify, and query appointments."""

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def create_appointment(
        self,
        business: Business,
        customer_phone: str,
        customer_name: str,
        treatment_code: str,
        appt_date: date,
        appt_time: time,
        preferred_operator: Optional[str] = None,
        platform: Optional[str] = None,
        chatwoot_conversation_id: Optional[int] = None,
    ) -> dict:
        # Validate name
        if not customer_name or not customer_name.strip():
            return {"success": False, "error": "CUSTOMER_NAME_REQUIRED"}

        # Check slot
        slot = availability_service.check_slot(
            business, treatment_code, appt_date, appt_time, preferred_operator
        )
        # If the preferred operator became busy between check_availability and booking
        # (TOCTOU race, or AI echoed an auto-assigned operator name), fall back to
        # auto-assign so the customer is not left with a failed booking they already confirmed.
        if not slot.get("available") and slot.get("reason") == "PREFERRED_OPERATOR_BUSY" and preferred_operator:
            logger.warning(
                "Preferred operator %r busy at booking time — falling back to auto-assign "
                "(business_id=%s, date=%s, time=%s)",
                preferred_operator, business.id, appt_date, appt_time,
            )
            slot = availability_service.check_slot(
                business, treatment_code, appt_date, appt_time, preferred_operator=None
            )
        if not slot.get("available"):
            return {
                "success": False,
                "error": slot.get("reason", "SLOT_UNAVAILABLE"),
                "alternatives": slot.get("alternatives", []),
            }

        operator_name = slot["operator"]
        operator_id = slot["operator_id"]
        treatment_name = slot["treatment"]
        duration = slot["duration_minutes"]
        price = slot.get("price")

        # Google Calendar event (best-effort)
        event_id = cal_module.create_calendar_event(
            business,
            customer_name=customer_name,
            treatment_name=treatment_name,
            date_str=appt_date.isoformat(),
            time_str=appt_time.strftime("%H:%M"),
            duration_minutes=duration,
            operator_name=operator_name,
            customer_phone=customer_phone,
        )

        # Persist (handle concurrent booking via unique constraint)
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO appointments
                            (business_id, operator_id, operator_name,
                             customer_phone, customer_name,
                             treatment_code, treatment_name,
                             appointment_date, appointment_time,
                             duration_minutes, price, status,
                             google_event_id, platform,
                             chatwoot_conversation_id)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'confirmed',%s,%s,%s)
                        RETURNING id
                        """,
                        (
                            business.id,
                            operator_id,
                            operator_name,
                            customer_phone,
                            customer_name.strip(),
                            treatment_code,
                            treatment_name,
                            appt_date,
                            appt_time,
                            duration,
                            price,
                            event_id,
                            platform,
                            chatwoot_conversation_id,
                        ),
                    )
                    appt_id = cur.fetchone()[0]
        except psycopg2.errors.UniqueViolation:
            logger.warning(
                "Concurrent booking conflict: operator %s on %s at %s",
                operator_name, appt_date, appt_time,
            )
            return {
                "success": False,
                "error": "SLOT_JUST_TAKEN",
                "message": "This slot was just booked by another customer. Please try a different time.",
            }

        logger.info(
            "Appointment #%s created: %s with %s on %s at %s",
            appt_id, treatment_name, operator_name, appt_date, appt_time,
        )
        return {
            "success": True,
            "appointment_id": appt_id,
            "operator": operator_name,
            "operator_id": operator_id,
            "treatment": treatment_name,
            "treatment_code": treatment_code,
            "date": appt_date.isoformat(),
            "time": appt_time.strftime("%H:%M"),
            "duration_minutes": duration,
            "price": price,
            "google_event_id": event_id,
        }

    # ------------------------------------------------------------------
    # Cancel
    # ------------------------------------------------------------------

    def cancel_appointment(
        self,
        business: Business,
        customer_phone: str,
        appt_date: date,
        appt_time: time,
    ) -> dict:
        phone = customer_service._normalize_phone(customer_phone)
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE appointments
                    SET status = 'cancelled'
                    WHERE business_id = %s
                      AND customer_phone = %s
                      AND appointment_date = %s
                      AND appointment_time = %s
                      AND status = 'confirmed'
                    RETURNING id, google_event_id
                    """,
                    (business.id, phone, appt_date, appt_time),
                )
                row = cur.fetchone()
                if not row:
                    return {"success": False, "error": "APPOINTMENT_NOT_FOUND"}

                appt_id, event_id = row
                if event_id:
                    cal_module.delete_calendar_event(business, event_id)

                logger.info("Appointment #%s cancelled", appt_id)
                return {"success": True, "appointment_id": appt_id}

    # ------------------------------------------------------------------
    # Modify
    # ------------------------------------------------------------------

    def modify_appointment(
        self,
        business: Business,
        customer_phone: str,
        current_date: date,
        current_time: time,
        new_date: Optional[date] = None,
        new_time: Optional[time] = None,
        new_treatment: Optional[str] = None,
        new_operator: Optional[str] = None,
    ) -> dict:
        phone = customer_service._normalize_phone(customer_phone)
        # 1. Look up the original appointment BEFORE cancelling
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, treatment_code, customer_phone, google_event_id, customer_name
                    FROM appointments
                    WHERE business_id = %s
                      AND customer_phone = %s
                      AND appointment_date = %s
                      AND appointment_time = %s
                      AND status = 'confirmed'
                    """,
                    (business.id, phone, current_date, current_time),
                )
                row = cur.fetchone()
                if not row:
                    return {"success": False, "error": "APPOINTMENT_NOT_FOUND"}

                original_id = row[0]
                original_treatment = row[1]
                customer_phone = row[2]
                original_event_id = row[3]
                customer_name = row[4]

        target_date = new_date or current_date
        target_time = new_time or current_time
        treatment_code = new_treatment or original_treatment

        # 2. Check if new slot is available BEFORE cancelling old
        slot = availability_service.check_slot(
            business, treatment_code, target_date, target_time, new_operator
        )
        if not slot.get("available"):
            return {
                "success": False,
                "error": slot.get("reason", "NEW_SLOT_UNAVAILABLE"),
                "alternatives": slot.get("alternatives", []),
            }

        # 3. Now safe to cancel old and create new in one flow
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE appointments SET status = 'cancelled' WHERE id = %s",
                    (original_id,),
                )

        if original_event_id:
            cal_module.delete_calendar_event(business, original_event_id)

        logger.info("Appointment #%s cancelled for modification", original_id)

        return self.create_appointment(
            business=business,
            customer_phone=customer_phone,
            customer_name=customer_name,
            treatment_code=treatment_code,
            appt_date=target_date,
            appt_time=target_time,
            preferred_operator=new_operator,
        )

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get_customer_appointments(
        self, business: Business, customer_phone: str
    ) -> list[dict]:
        phone = customer_service._normalize_phone(customer_phone)
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, operator_name, treatment_name, treatment_code,
                           appointment_date, appointment_time, duration_minutes,
                           price, status
                    FROM appointments
                    WHERE business_id = %s AND customer_phone = %s
                      AND status = 'confirmed'
                    ORDER BY appointment_date, appointment_time
                    """,
                    (business.id, phone),
                )
                rows = cur.fetchall()
                return [
                    {
                        "appointment_id": r[0],
                        "operator": r[1],
                        "treatment": r[2],
                        "treatment_code": r[3],
                        "date": r[4].isoformat() if r[4] else None,
                        "time": r[5].strftime("%H:%M") if r[5] else None,
                        "duration_minutes": r[6],
                        "price": str(r[7]) if r[7] else None,
                        "status": r[8],
                    }
                    for r in rows
                ]


# Singleton
booking_service = BookingService()
