import logging
from datetime import date, time, timedelta, datetime
from typing import Optional

from app.models.database import get_connection
from app.models.schemas import Business, Operator, Treatment, BusinessHours

logger = logging.getLogger(__name__)


class AvailabilityService:
    """Multi-operator availability logic: hours validation, overlap checks,
    operator assignment, and alternative-slot suggestions."""

    # ------------------------------------------------------------------
    # Operator lookup
    # ------------------------------------------------------------------

    def get_operators_for_treatment(
        self, business: Business, treatment_code: str
    ) -> list[Operator]:
        """Return active operators that offer *treatment_code*."""
        treatment = self._find_treatment(business, treatment_code)
        if not treatment:
            return []
        return [
            op
            for op in business.operators
            if op.id in treatment.operator_ids and op.is_active
        ]

    # ------------------------------------------------------------------
    # Single-operator calendar check (duration-aware overlap)
    # ------------------------------------------------------------------

    def is_operator_free(
        self,
        business_id: int,
        operator_id: int,
        appt_date: date,
        appt_time: time,
        duration_minutes: int,
    ) -> bool:
        """True when *operator_id* has no confirmed appointment overlapping
        the window [appt_time, appt_time + duration_minutes)."""
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*) FROM appointments
                    WHERE business_id = %s
                      AND operator_id = %s
                      AND appointment_date = %s
                      AND status = 'confirmed'
                      AND appointment_time < (%s::time + (%s || ' minutes')::interval)
                      AND (appointment_time + (duration_minutes || ' minutes')::interval) > %s::time
                    """,
                    (
                        business_id,
                        operator_id,
                        appt_date,
                        appt_time.isoformat(),
                        str(duration_minutes),
                        appt_time.isoformat(),
                    ),
                )
                count = cur.fetchone()[0]
                return count == 0

    # ------------------------------------------------------------------
    # High-level slot check
    # ------------------------------------------------------------------

    def check_slot(
        self,
        business: Business,
        treatment_code: str,
        appt_date: date,
        appt_time: time,
        preferred_operator: Optional[str] = None,
    ) -> dict:
        """Check whether the requested date/time is bookable.

        Returns a dict with at least ``available``, and on success ``operator``
        and ``operator_id``.  On failure includes ``reason`` and possibly
        ``alternatives``.
        """
        # 1. Business hours gate
        hours_check = self.validate_business_hours(business, appt_date, appt_time)
        if not hours_check["valid"]:
            return {
                "available": False,
                "reason": hours_check["reason"],
            }

        treatment = self._find_treatment(business, treatment_code)
        if not treatment:
            return {"available": False, "reason": "TREATMENT_NOT_FOUND"}

        duration = treatment.duration_minutes

        # 2. Preferred operator path
        if preferred_operator:
            operator = self._find_operator_by_name(business, preferred_operator)
            if not operator:
                return {"available": False, "reason": "OPERATOR_NOT_FOUND"}

            if operator.id not in treatment.operator_ids:
                return {
                    "available": False,
                    "reason": "OPERATOR_DOES_NOT_OFFER_TREATMENT",
                }

            if self.is_operator_free(business.id, operator.id, appt_date, appt_time, duration):
                return {
                    "available": True,
                    "operator": operator.display_name,
                    "operator_id": operator.id,
                    "treatment": treatment.name_it,
                    "treatment_code": treatment.code,
                    "duration_minutes": duration,
                    "price": str(treatment.price) if treatment.price else None,
                }

            # Preferred operator busy => alternatives for THIS operator only
            alternatives = self._find_operator_alternatives(
                business, operator.id, treatment, appt_date
            )
            return {
                "available": False,
                "reason": "PREFERRED_OPERATOR_BUSY",
                "operator": operator.display_name,
                "alternatives": alternatives,
            }

        # 3. Auto-assign: first free operator
        eligible_operators = self.get_operators_for_treatment(business, treatment_code)
        if not eligible_operators:
            return {"available": False, "reason": "NO_OPERATORS_FOR_TREATMENT"}

        for op in eligible_operators:
            if self.is_operator_free(business.id, op.id, appt_date, appt_time, duration):
                return {
                    "available": True,
                    "operator": op.display_name,
                    "operator_id": op.id,
                    "treatment": treatment.name_it,
                    "treatment_code": treatment.code,
                    "duration_minutes": duration,
                    "price": str(treatment.price) if treatment.price else None,
                }

        # All operators busy at this time => alternatives across any operator
        alternatives = self._find_any_operator_alternatives(
            business, treatment, appt_date, appt_time
        )
        return {
            "available": False,
            "reason": "ALL_OPERATORS_BUSY",
            "alternatives": alternatives,
        }

    # ------------------------------------------------------------------
    # Available slots for a full day
    # ------------------------------------------------------------------

    def get_available_slots(
        self,
        business: Business,
        treatment_code: str,
        appt_date: date,
        preferred_operator: Optional[str] = None,
    ) -> list[dict]:
        """Return every bookable slot on *appt_date* for *treatment_code*."""
        treatment = self._find_treatment(business, treatment_code)
        if not treatment:
            return []

        hours = self._get_hours_for_date(business, appt_date)
        if not hours or not hours.is_open or not hours.open_time or not hours.close_time:
            return []

        slots = self._generate_time_slots(
            hours.open_time, hours.close_time, treatment.duration_minutes
        )

        if preferred_operator:
            operator = self._find_operator_by_name(business, preferred_operator)
            if not operator or operator.id not in treatment.operator_ids:
                return []
            operators_to_check = [operator]
        else:
            operators_to_check = self.get_operators_for_treatment(business, treatment_code)

        available: list[dict] = []
        for slot_time in slots:
            for op in operators_to_check:
                if self.is_operator_free(
                    business.id, op.id, appt_date, slot_time, treatment.duration_minutes
                ):
                    available.append(
                        {
                            "time": slot_time.strftime("%H:%M"),
                            "operator": op.display_name,
                            "operator_id": op.id,
                        }
                    )
                    break  # one operator per slot is enough
        return available

    # ------------------------------------------------------------------
    # Business-hours validation
    # ------------------------------------------------------------------

    def validate_business_hours(
        self, business: Business, appt_date: date, appt_time: time
    ) -> dict:
        """Return ``{valid: bool, reason: str}``."""
        # Reject past dates
        today = date.today()
        if appt_date < today:
            return {"valid": False, "reason": "DATE_IN_PAST"}
        # Reject past times for today
        if appt_date == today and appt_time < datetime.now().time():
            return {"valid": False, "reason": "TIME_IN_PAST"}

        # Check business closures (holidays, special days)
        if self._is_closure_date(business.id, appt_date):
            return {"valid": False, "reason": "CLOSURE_DATE"}

        hours = self._get_hours_for_date(business, appt_date)
        if not hours:
            return {"valid": False, "reason": "NO_HOURS_CONFIGURED"}
        if not hours.is_open:
            return {"valid": False, "reason": "CLOSED_DAY"}
        if not hours.open_time or not hours.close_time:
            return {"valid": False, "reason": "HOURS_NOT_SET"}
        if appt_time < hours.open_time or appt_time >= hours.close_time:
            return {
                "valid": False,
                "reason": "OUTSIDE_BUSINESS_HOURS",
            }
        return {"valid": True, "reason": ""}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_closure_date(business_id: int, appt_date: date) -> bool:
        """Check if *appt_date* is a closure/holiday for this business."""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT 1 FROM business_closures WHERE business_id = %s AND closure_date = %s",
                        (business_id, appt_date),
                    )
                    return cur.fetchone() is not None
        except Exception:
            logger.exception("Failed to check closure date")
            return False

    def _get_hours_for_date(
        self, business: Business, appt_date: date
    ) -> Optional[BusinessHours]:
        dow = appt_date.weekday()  # 0 = Monday
        for h in business.hours:
            if h.day_of_week == dow:
                return h
        return None

    @staticmethod
    def _generate_time_slots(
        open_time: time, close_time: time, interval_minutes: int
    ) -> list[time]:
        """Generate time slots from *open_time* up to (but not exceeding)
        *close_time* at *interval_minutes* intervals."""
        slots: list[time] = []
        current = datetime.combine(date.today(), open_time)
        end = datetime.combine(date.today(), close_time)
        delta = timedelta(minutes=interval_minutes)
        while current + delta <= end:
            slots.append(current.time())
            current += delta
        return slots

    def _find_treatment(
        self, business: Business, treatment_code: str
    ) -> Optional[Treatment]:
        for t in business.treatments:
            if t.code == treatment_code:
                return t
        return None

    def _find_operator_by_name(
        self, business: Business, name: str
    ) -> Optional[Operator]:
        name_lower = name.lower()
        for op in business.operators:
            if op.display_name.lower() == name_lower:
                return op
        return None

    def _find_operator_alternatives(
        self,
        business: Business,
        operator_id: int,
        treatment: Treatment,
        appt_date: date,
    ) -> list[dict]:
        """Alternative time slots for a *specific* operator on *appt_date*."""
        hours = self._get_hours_for_date(business, appt_date)
        if not hours or not hours.is_open or not hours.open_time or not hours.close_time:
            return []
        slots = self._generate_time_slots(
            hours.open_time, hours.close_time, treatment.duration_minutes
        )
        alts: list[dict] = []
        for slot_time in slots:
            if self.is_operator_free(
                business.id, operator_id, appt_date, slot_time, treatment.duration_minutes
            ):
                alts.append({"time": slot_time.strftime("%H:%M")})
        return alts

    def _find_any_operator_alternatives(
        self,
        business: Business,
        treatment: Treatment,
        appt_date: date,
        requested_time: time,
    ) -> list[dict]:
        """Alternative time slots across *all* eligible operators."""
        hours = self._get_hours_for_date(business, appt_date)
        if not hours or not hours.is_open or not hours.open_time or not hours.close_time:
            return []
        slots = self._generate_time_slots(
            hours.open_time, hours.close_time, treatment.duration_minutes
        )
        eligible = self.get_operators_for_treatment(business, treatment.code)
        alts: list[dict] = []
        for slot_time in slots:
            if slot_time == requested_time:
                continue  # skip the time they already asked for
            for op in eligible:
                if self.is_operator_free(
                    business.id, op.id, appt_date, slot_time, treatment.duration_minutes
                ):
                    alts.append(
                        {
                            "time": slot_time.strftime("%H:%M"),
                            "operator": op.display_name,
                            "operator_id": op.id,
                        }
                    )
                    break
        return alts


# Singleton
availability_service = AvailabilityService()
