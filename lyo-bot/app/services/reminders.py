import logging
from app.models.database import get_connection
from app.services.chatwoot import chatwoot_client

logger = logging.getLogger(__name__)


class ReminderService:
    async def send_daily_reminders(self):
        """Send reminders for tomorrow's appointments across all businesses."""
        try:
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    """SELECT a.id, a.business_id, a.customer_phone, a.customer_name,
                              a.treatment_code, t.name_it, a.appointment_date::text,
                              a.appointment_time::text, o.display_name,
                              a.chatwoot_conversation_id, b.chatwoot_account_id
                       FROM appointments a
                       JOIN businesses b ON a.business_id = b.id
                       LEFT JOIN treatments t ON a.treatment_id = t.id
                       LEFT JOIN operators o ON a.operator_id = o.id
                       WHERE a.appointment_date = CURRENT_DATE + INTERVAL '1 day'
                         AND a.status = 'confirmed'
                         AND a.reminder_sent_at IS NULL
                         AND a.chatwoot_conversation_id IS NOT NULL""",
                )
                appointments = cur.fetchall()

            for appt in appointments:
                (appt_id, biz_id, phone, name, treatment_code, treatment_name,
                 appt_date, appt_time, operator_name, conv_id, account_id) = appt

                message = (
                    f"Ciao {name}! Ti ricordiamo il tuo appuntamento di domani:\n"
                    f"\U0001f4cb {treatment_name or treatment_code}\n"
                    f"\U0001f550 Ore {appt_time[:5]}\n"
                )
                if operator_name:
                    message += f"\U0001f469\u200d\U0001f4bc Con {operator_name}\n"
                message += "\nPuoi confermare rispondendo 'Confermo' o contattarci per modificare."

                try:
                    await chatwoot_client.send_message(
                        account_id=account_id,
                        conversation_id=conv_id,
                        content=message,
                    )
                    with get_connection() as conn:
                        cur = conn.cursor()
                        cur.execute(
                            "UPDATE appointments SET reminder_sent_at = NOW() WHERE id = %s",
                            (appt_id,),
                        )
                    logger.info(f"Reminder sent for appointment #{appt_id} to {phone}")
                except Exception as e:
                    logger.error(f"Failed to send reminder for #{appt_id}: {e}")

        except Exception as e:
            logger.error(f"Error in daily reminders: {e}")

    async def check_unconfirmed(self):
        """Check for unconfirmed appointments tomorrow. Placeholder for email notification."""
        logger.info("Checking unconfirmed appointments (placeholder)")


reminder_service = ReminderService()
