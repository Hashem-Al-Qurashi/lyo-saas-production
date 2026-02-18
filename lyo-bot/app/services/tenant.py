import logging
import time
from typing import Optional

from app.models.database import get_connection
from app.models.schemas import Business, Operator, Treatment, BusinessHours

logger = logging.getLogger(__name__)

CACHE_TTL = 300  # seconds


class TenantService:
    """Loads and caches Business objects (with relations) keyed by chatwoot_account_id."""

    def __init__(self):
        self._cache: dict[int, tuple[Business, float]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_business(self, chatwoot_account_id: int) -> Optional[Business]:
        """Return a fully-loaded Business or None.  Uses in-memory cache."""
        cached = self._cache.get(chatwoot_account_id)
        if cached:
            business, ts = cached
            if time.time() - ts < CACHE_TTL:
                logger.debug("Cache hit for account %s", chatwoot_account_id)
                return business
            # expired
            del self._cache[chatwoot_account_id]

        business = self._load_from_db(chatwoot_account_id)
        if business:
            self._cache[chatwoot_account_id] = (business, time.time())
        return business

    def invalidate(self, chatwoot_account_id: int) -> None:
        self._cache.pop(chatwoot_account_id, None)

    def invalidate_all(self) -> None:
        self._cache.clear()

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _load_from_db(self, chatwoot_account_id: int) -> Optional[Business]:
        """Query DB and build a Business with operators, treatments, hours."""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    # 1. Business row
                    cur.execute(
                        """
                        SELECT id, chatwoot_account_id, name, slug, timezone,
                               language, bot_name, bot_persona, address, phone,
                               email, google_calendar_id,
                               google_service_account_json, owner_email,
                               status, settings
                        FROM businesses
                        WHERE chatwoot_account_id = %s AND status = 'active'
                        """,
                        (chatwoot_account_id,),
                    )
                    row = cur.fetchone()
                    if not row:
                        logger.warning("No active business for account %s", chatwoot_account_id)
                        return None

                    business = Business(
                        id=row[0],
                        chatwoot_account_id=row[1],
                        name=row[2],
                        slug=row[3],
                        timezone=row[4],
                        language=row[5],
                        bot_name=row[6],
                        bot_persona=row[7],
                        address=row[8],
                        phone=row[9],
                        email=row[10],
                        google_calendar_id=row[11] or "primary",
                        google_service_account_json=row[12],
                        owner_email=row[13],
                        status=row[14],
                        settings=row[15] if isinstance(row[15], dict) else {},
                    )
                    bid = business.id

                    # 2. Treatments
                    cur.execute(
                        """
                        SELECT id, business_id, code, name_it, name_en,
                               description_it, description_en,
                               duration_minutes, price, is_active, sort_order, notes
                        FROM treatments
                        WHERE business_id = %s AND is_active = true
                        ORDER BY sort_order
                        """,
                        (bid,),
                    )
                    treatments = []
                    treatment_map: dict[int, Treatment] = {}
                    for t in cur.fetchall():
                        treat = Treatment(
                            id=t[0], business_id=t[1], code=t[2],
                            name_it=t[3], name_en=t[4],
                            description_it=t[5], description_en=t[6],
                            duration_minutes=t[7], price=t[8],
                            is_active=t[9], sort_order=t[10], notes=t[11],
                        )
                        treatments.append(treat)
                        treatment_map[treat.id] = treat

                    # 3. Operators
                    cur.execute(
                        """
                        SELECT id, business_id, technical_id, display_name,
                               is_active, sort_order, notes
                        FROM operators
                        WHERE business_id = %s AND is_active = true
                        ORDER BY sort_order
                        """,
                        (bid,),
                    )
                    operators = []
                    operator_map: dict[int, Operator] = {}
                    for o in cur.fetchall():
                        op = Operator(
                            id=o[0], business_id=o[1], technical_id=o[2],
                            display_name=o[3], is_active=o[4],
                            sort_order=o[5], notes=o[6],
                        )
                        operators.append(op)
                        operator_map[op.id] = op

                    # 4. Operator-treatment links
                    cur.execute(
                        """
                        SELECT operator_id, treatment_id
                        FROM operator_treatments
                        WHERE operator_id IN (
                            SELECT id FROM operators WHERE business_id = %s
                        )
                        """,
                        (bid,),
                    )
                    for ot in cur.fetchall():
                        op_id, treat_id = ot[0], ot[1]
                        if op_id in operator_map:
                            operator_map[op_id].treatment_ids.append(treat_id)
                        if treat_id in treatment_map:
                            treatment_map[treat_id].operator_ids.append(op_id)

                    # 5. Business hours
                    cur.execute(
                        """
                        SELECT business_id, day_of_week, is_open, open_time, close_time
                        FROM business_hours
                        WHERE business_id = %s
                        ORDER BY day_of_week
                        """,
                        (bid,),
                    )
                    hours = []
                    for h in cur.fetchall():
                        hours.append(
                            BusinessHours(
                                business_id=h[0], day_of_week=h[1],
                                is_open=h[2], open_time=h[3], close_time=h[4],
                            )
                        )

                    business.operators = operators
                    business.treatments = treatments
                    business.hours = hours

                    logger.info(
                        "Loaded business '%s' (id=%s): %d operators, %d treatments, %d hour-rules",
                        business.name, bid, len(operators), len(treatments), len(hours),
                    )
                    return business

        except Exception:
            logger.exception("Failed to load business for account %s", chatwoot_account_id)
            return None


# Singleton
tenant_service = TenantService()
