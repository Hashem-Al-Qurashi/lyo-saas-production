"""AI service: OpenAI chat completions with dynamic tool calling.

Builds a per-business system prompt and tool set, then runs a multi-round
tool-calling loop (up to MAX_TOOL_ROUNDS) before returning the final text.
"""

import asyncio
import json
import logging
from datetime import date, time, datetime

import openai
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.config import settings
from app.models.schemas import Business
from app.models.database import get_connection
from app.tools.definitions import build_tools_for_business
from app.utils.time_helpers import get_date_context, generate_date_calendar
from app.services.availability import availability_service
from app.services.booking import booking_service
from app.services.chatwoot import chatwoot_client

logger = logging.getLogger(__name__)

openai_client = openai.OpenAI(api_key=settings.openai_api_key)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((openai.APITimeoutError, openai.APIConnectionError, openai.RateLimitError, openai.InternalServerError)),
    before_sleep=lambda retry_state: logger.warning(
        "OpenAI call failed (attempt %d), retrying: %s", retry_state.attempt_number, retry_state.outcome.exception()
    ),
)
def _openai_create_with_retry(**kwargs):
    """Call OpenAI chat.completions.create with retry on transient errors."""
    return openai_client.chat.completions.create(**kwargs)


# ------------------------------------------------------------------
# System prompt builder
# ------------------------------------------------------------------

def build_system_prompt(business: Business, customer_name: str | None = None) -> str:
    """Construct a dynamic system prompt for *business*."""
    dates = get_date_context(business)

    # --- Services list ---
    services_lines: list[str] = []
    for t in business.treatments:
        if not t.is_active:
            continue
        # Which operators offer this treatment?
        op_names = []
        for op in business.operators:
            if op.is_active and t.id in op.treatment_ids:
                op_names.append(op.display_name)
        ops_str = ", ".join(op_names) if op_names else "all operators"
        price_str = f"EUR {t.price}" if t.price else "N/A"
        line = (
            f"- {t.name_it}: {price_str} ({t.duration_minutes} min) "
            f"[code: {t.code}] [operators: {ops_str}]"
        )
        if t.description_it:
            line += f"\n  Description: {t.description_it}"
        if t.notes:
            line += f"\n  Note: {t.notes}"
        services_lines.append(line)
    services_block = "\n".join(services_lines) or "No services configured."

    # --- Operators list ---
    day_names_op = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    operators_lines: list[str] = []
    for op in business.operators:
        if not op.is_active:
            continue
        treat_names = []
        for t in business.treatments:
            if t.is_active and t.id in op.treatment_ids:
                treat_names.append(t.name_it)
        treats_str = ", ".join(treat_names) if treat_names else "all services"
        line = f"- {op.display_name}: {treats_str}"
        if op.notes:
            line += f" — {op.notes}"
        # Per-operator working hours
        if op.hours:
            hours_parts = []
            for h in sorted(op.hours, key=lambda x: x.day_of_week):
                dn = day_names_op[h.day_of_week] if h.day_of_week < 7 else "?"
                if not h.is_working:
                    hours_parts.append(f"{dn}: OFF")
                elif h.start_time and h.end_time:
                    part = f"{dn}: {h.start_time.strftime('%H:%M')}-{h.end_time.strftime('%H:%M')}"
                    if h.break_start and h.break_end:
                        part += f" (break {h.break_start.strftime('%H:%M')}-{h.break_end.strftime('%H:%M')})"
                    hours_parts.append(part)
            if hours_parts:
                line += f"\n  Schedule: {', '.join(hours_parts)}"
        operators_lines.append(line)
    operators_block = "\n".join(operators_lines) or "No operators configured."

    # --- Business hours ---
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    hours_lines: list[str] = []
    for h in sorted(business.hours, key=lambda x: x.day_of_week):
        dname = day_names[h.day_of_week] if h.day_of_week < 7 else "?"
        if not h.is_open:
            hours_lines.append(f"- {dname}: CLOSED")
        elif h.open_time and h.close_time:
            hours_lines.append(
                f"- {dname}: {h.open_time.strftime('%H:%M')} - {h.close_time.strftime('%H:%M')}"
            )
        else:
            hours_lines.append(f"- {dname}: hours not set")
    hours_block = "\n".join(hours_lines) or "No hours configured."

    # --- Language instruction ---
    if business.language == "it":
        language_rule = (
            "LANGUAGE RULE (CRITICAL) - ITALIAN FIRST:\n"
            "- DEFAULT LANGUAGE: ITALIAN. Always reply in Italian unless the customer writes a COMPLETE sentence in English.\n"
            "- Single English words like 'ok', 'hi', 'we', 'no' -> Still reply in Italian!\n"
            "- Tool results are always in English - YOU translate to Italian for the customer.\n"
            "- NEVER show internal codes (like treatment codes) to the customer.\n"
        )
    else:
        language_rule = (
            "LANGUAGE RULE:\n"
            "- Reply in the customer's language.\n"
            "- Tool results are in English - translate if needed.\n"
            "- NEVER show internal codes (like treatment codes) to the customer.\n"
        )

    # --- Salon rules ---
    rules = business.settings.get("rules", {})
    rules_lines: list[str] = []
    if rules.get("deposit"):
        rules_lines.append(f"- Deposit/advance: {rules['deposit']}")
    if rules.get("cancellation"):
        rules_lines.append(f"- Cancellation: {rules['cancellation']}")
    if rules.get("punctuality"):
        rules_lines.append(f"- Punctuality: {rules['punctuality']}")
    if rules.get("other"):
        rules_lines.append(f"- Other: {rules['other']}")
    rules_block = "\n".join(rules_lines) if rules_lines else ""

    # --- Rules section (only included if any rules exist) ---
    rules_section = ""
    if rules_block:
        rules_section = f"\nSALON RULES:\n{rules_block}\n\n"

    # --- Special closures ---
    if business.closures:
        closure_lines = []
        for c in business.closures:
            start_str = c.closure_date.strftime("%d/%m/%Y")
            if c.closure_end_date and c.closure_end_date != c.closure_date:
                end_str = c.closure_end_date.strftime("%d/%m/%Y")
                date_range = f"{start_str} - {end_str}"
            else:
                date_range = start_str
            reason = c.reason or "chiusura speciale"
            closure_lines.append(f"  - {date_range}: {reason}")
        closures_block = "\n".join(closure_lines)
    else:
        closures_block = "  Nessuna chiusura speciale prevista."

    # --- Address block ---
    address_str = business.address or "Not configured"
    phone_str = business.phone or "Not configured"

    # --- Customer context block ---
    if customer_name:
        customer_context = f"This customer is a RETURNING client. Their name is: {customer_name}. Use this name for bookings — do NOT ask for it again."
    else:
        customer_context = "This is a NEW customer. Their name is not yet known — ask for it before booking."

    prompt = f"""You are {business.bot_name}, an employee at {business.name}.

TODAY'S DATE: {dates['display']} (Year: {dates['year']})
IMPORTANT: The current year is {dates['year']}. NEVER use any other year.

{language_rule}

"CIAO" CONTEXT RULE:
"Ciao" in Italian means BOTH hello AND goodbye. Use CONTEXT to decide:
- TREAT AS GOODBYE: If the previous message was a booking confirmation, or customer just said "grazie"/"ok"/"perfetto" -> "ciao" = goodbye. Respond briefly: "Ciao! A presto!"
- TREAT AS HELLO: If it's the first message or comes with a request -> "ciao" = hello.

BUSINESS INFO:
- Name: {business.name}
- Address: {address_str}
- Phone: {phone_str}

SERVICES:
{services_block}

OPERATORS:
{operators_block}

BUSINESS HOURS:
{hours_block}
{rules_section}UPCOMING DAYS CALENDAR:
{dates['calendar']}

SPECIAL CLOSURES (beyond regular weekly schedule):
{closures_block}
IMPORTANT: On closure dates the salon is CLOSED even if that weekday is normally open.
The calendar above already marks these dates as CHIUSO.
When asked "quando riaprite?" after a closure -> look at the FIRST APERTO date in the calendar above.
Do NOT guess or calculate reopen dates — read them directly from the calendar.

WHEN CUSTOMER SAYS A DAY NAME:
-> Look at the calendar above and use the EXACT DATE (YYYY-MM-DD).

WHEN CUSTOMER SAYS "settimana del X" / "la settimana di X" / "questa settimana" / "settimana prossima":
-> This means the FULL WEEK (Lunedi-Domenica) that contains that date — NOT just that single day.
-> Example: "settimana del 20 aprile" = the week Mon 20 Apr to Sun 26 Apr.
-> If Monday of that week is CLOSED (giorno di chiusura) or in a closure period, the first available day is Tuesday or later.
-> ASK the customer: "In quale giorno e a che ora preferisci in quella settimana?" — do NOT auto-pick Monday.
-> NEVER interpret "settimana del X" as a booking request for exactly day X without confirming.

OPERATOR RULES (CRITICAL):
- Each service lists [operators: ...] — ONLY those operators can perform that service.
- If a customer names an operator for a service they DO NOT offer -> tell the customer and suggest who does.
  Example: Federica does NOT offer taglio donna -> say so and offer Giulia/Martina/Sara/Luca.
- If customer has NO operator preference -> auto-assign (system picks the first available). Pass operator_name=null.
- If customer asks "chi e' disponibile?" -> use get_operators_for_treatment to list operators.
- If customer names a specific operator -> pass that name as operator_name.
- ALWAYS include the assigned operator's name when confirming a booking.
- If the customer SWITCHES to a different service mid-conversation -> re-verify operator compatibility for the NEW service before confirming.

CUSTOMER CONTEXT:
{customer_context}

NAME REQUIREMENT (CRITICAL):
- You MUST know the customer's name BEFORE booking.
- If CUSTOMER CONTEXT above already provides the name, use it — DO NOT ask again.
- If the customer gave their name EARLIER in this conversation, use it — DO NOT ask again.
- This applies even for a second booking within the same chat session.
- If the customer gives service + date + time but NO name has been given yet, ask for it first.
- Do NOT show a confirmation summary without the name.

BOOKING FLOW:
1. Collect: name, service, date, time.
2. Call check_availability:
   - operator_name = the operator the customer explicitly named, OR null if no preference.
3. Show summary with FULL date (day, number, month, year) and the assigned operator's name, then ask "Confermi?".
4. ONLY after customer says yes/ok/si -> call create_appointment:
   - operator_name = the operator the customer explicitly named, OR null if no preference.
   - IMPORTANT: Do NOT forward the operator name returned by check_availability. If the customer had no preference, always pass null — the system will auto-assign.
5. After booking confirmed, if customer says "ok"/"grazie"/"perfetto" -> just acknowledge, do NOT book again.

MODIFY/CANCEL:
- Use get_customer_appointments to look up bookings (the system knows the customer's phone).
- cancel_appointment / modify_appointment only need date + time — the system uses the phone number to identify the customer.

ESCALATION:
- If the customer asks to speak to a human, use escalate_to_human.
"""
    return prompt


# ------------------------------------------------------------------
# AI Service
# ------------------------------------------------------------------

class AIService:
    """Orchestrates OpenAI chat completions with tool calling."""

    MAX_TOOL_ROUNDS = 5

    async def process_message(
        self,
        business: Business,
        customer_phone: str,
        message: str,
        conversation_history: list[dict],
        customer_name: str | None = None,
        conversation_id: int | None = None,
        account_id: int | None = None,
    ) -> str:
        """Process a user message and return the bot's text reply.

        Runs up to ``MAX_TOOL_ROUNDS`` of tool-call / tool-result cycles.
        """
        system_prompt = build_system_prompt(business, customer_name=customer_name)
        tools = build_tools_for_business(business)

        messages: list[dict] = [{"role": "system", "content": system_prompt}]
        messages.extend(conversation_history)
        messages.append({"role": "user", "content": message})

        for _round in range(self.MAX_TOOL_ROUNDS):
            response = await asyncio.to_thread(
                _openai_create_with_retry,
                model=settings.openai_model,
                messages=messages,
                tools=tools,
                temperature=0.7,
            )
            choice = response.choices[0]

            if choice.finish_reason == "tool_calls" or choice.message.tool_calls:
                # Append assistant message with tool calls
                messages.append(choice.message.model_dump())

                for tool_call in choice.message.tool_calls:
                    result = await self._execute_tool(
                        business=business,
                        customer_phone=customer_phone,
                        customer_name=customer_name,
                        tool_name=tool_call.function.name,
                        arguments_json=tool_call.function.arguments,
                        conversation_id=conversation_id,
                        account_id=account_id,
                    )
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result,
                    })
            else:
                # Final text response
                return choice.message.content or ""

        # Safety: if we exhaust rounds, return whatever we have
        logger.warning(
            "Exhausted %d tool rounds for business %s, phone %s",
            self.MAX_TOOL_ROUNDS, business.id, customer_phone,
        )
        return choice.message.content or "Mi scusi, c'e' stato un problema. Riprovi tra poco."

    # ------------------------------------------------------------------
    # Tool dispatcher
    # ------------------------------------------------------------------

    async def _execute_tool(
        self,
        business: Business,
        customer_phone: str,
        customer_name: str | None,
        tool_name: str,
        arguments_json: str,
        conversation_id: int | None = None,
        account_id: int | None = None,
    ) -> str:
        """Execute a tool call and return the JSON result string."""
        try:
            args = json.loads(arguments_json)
        except json.JSONDecodeError:
            return json.dumps({"error": "INVALID_ARGUMENTS"})

        logger.info("Tool call: %s(%s) for business %s", tool_name, args, business.id)

        # Handle escalation async (needs Chatwoot API call)
        if tool_name == "escalate_to_human":
            return await self._handle_escalation(
                account_id=account_id,
                conversation_id=conversation_id,
                reason=args.get("reason", ""),
            )

        try:
            result = await asyncio.to_thread(
                self._dispatch_tool, business, customer_phone, customer_name, tool_name, args, conversation_id
            )
            return json.dumps(result, default=str)
        except Exception:
            logger.exception("Tool %s failed", tool_name)
            return json.dumps({"error": "TOOL_EXECUTION_FAILED"})

    def _dispatch_tool(
        self,
        business: Business,
        customer_phone: str,
        customer_name: str | None,
        tool_name: str,
        args: dict,
        conversation_id: int | None = None,
    ) -> dict:
        """Synchronous dispatcher to the appropriate service method."""
        if tool_name == "create_appointment":
            return booking_service.create_appointment(
                business=business,
                customer_phone=customer_phone,
                customer_name=args["customer_name"],
                treatment_code=args["treatment_code"],
                appt_date=date.fromisoformat(args["date"]),
                appt_time=time.fromisoformat(args["time"]),
                preferred_operator=args.get("operator_name"),
                chatwoot_conversation_id=conversation_id,
            )

        if tool_name == "check_availability":
            return availability_service.check_slot(
                business=business,
                treatment_code=args["treatment_code"],
                appt_date=date.fromisoformat(args["date"]),
                appt_time=time.fromisoformat(args["time"]),
                preferred_operator=args.get("operator_name"),
            )

        if tool_name == "get_available_slots":
            slots = availability_service.get_available_slots(
                business=business,
                treatment_code=args["treatment_code"],
                appt_date=date.fromisoformat(args["date"]),
                preferred_operator=args.get("operator_name"),
            )
            return {"slots": slots}

        if tool_name == "get_operators_for_treatment":
            ops = availability_service.get_operators_for_treatment(
                business=business,
                treatment_code=args["treatment_code"],
            )
            return {
                "operators": [
                    {"name": op.display_name, "id": op.id} for op in ops
                ]
            }

        if tool_name == "get_customer_appointments":
            return {
                "appointments": booking_service.get_customer_appointments(
                    business=business,
                    customer_phone=customer_phone,
                )
            }

        if tool_name == "cancel_appointment":
            return booking_service.cancel_appointment(
                business=business,
                customer_phone=customer_phone,
                appt_date=date.fromisoformat(args["date"]),
                appt_time=time.fromisoformat(args["time"]),
            )

        if tool_name == "modify_appointment":
            new_date = date.fromisoformat(args["new_date"]) if args.get("new_date") else None
            new_time = time.fromisoformat(args["new_time"]) if args.get("new_time") else None
            return booking_service.modify_appointment(
                business=business,
                customer_phone=customer_phone,
                current_date=date.fromisoformat(args["current_date"]),
                current_time=time.fromisoformat(args["current_time"]),
                new_date=new_date,
                new_time=new_time,
                new_treatment=args.get("new_treatment"),
                new_operator=args.get("new_operator"),
            )

        if tool_name == "confirm_reminder":
            return self._confirm_reminder(business, customer_phone)

        return {"error": f"UNKNOWN_TOOL: {tool_name}"}

    # ------------------------------------------------------------------
    # Escalation handler
    # ------------------------------------------------------------------

    async def _handle_escalation(
        self,
        account_id: int | None,
        conversation_id: int | None,
        reason: str,
    ) -> str:
        """Hand conversation to a human agent via Chatwoot."""
        if not account_id or not conversation_id:
            logger.warning("Cannot escalate: missing account_id=%s or conversation_id=%s", account_id, conversation_id)
            return json.dumps({"escalated": False, "reason": "MISSING_CONVERSATION_INFO"})

        try:
            # Set conversation to "open" so human agents see it in their queue
            await chatwoot_client.toggle_conversation_status(
                account_id=account_id,
                conversation_id=conversation_id,
                status="open",
            )
            logger.info(
                "Escalated conversation %s (account %s) to human. Reason: %s",
                conversation_id, account_id, reason,
            )
            return json.dumps({"escalated": True, "reason": reason})
        except Exception:
            logger.exception("Failed to escalate conversation %s", conversation_id)
            return json.dumps({"escalated": False, "reason": "CHATWOOT_API_ERROR"})

    # ------------------------------------------------------------------
    # Reminder confirmation helper
    # ------------------------------------------------------------------

    @staticmethod
    def _confirm_reminder(business: Business, customer_phone: str) -> dict:
        """Set reminder_confirmed=true on the customer's next upcoming appointment."""
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE appointments
                        SET reminder_confirmed = true,
                            reminder_confirmed_at = NOW()
                        WHERE business_id = %s
                          AND customer_phone = %s
                          AND status = 'confirmed'
                          AND appointment_date >= CURRENT_DATE
                          AND reminder_confirmed = false
                        ORDER BY appointment_date, appointment_time
                        LIMIT 1
                        RETURNING id
                        """,
                        (business.id, customer_phone),
                    )
                    row = cur.fetchone()
                    if row:
                        return {"confirmed": True, "appointment_id": row[0]}
                    return {"confirmed": False, "reason": "NO_PENDING_REMINDER"}
        except Exception:
            logger.exception("Failed to confirm reminder")
            return {"confirmed": False, "reason": "DB_ERROR"}


# Singleton
ai_service = AIService()
