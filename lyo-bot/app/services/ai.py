"""AI service: OpenAI chat completions with dynamic tool calling.

Builds a per-business system prompt and tool set, then runs a multi-round
tool-calling loop (up to MAX_TOOL_ROUNDS) before returning the final text.
"""

import asyncio
import json
import logging
from datetime import date, time, datetime

import openai

from app.config import settings
from app.models.schemas import Business
from app.models.database import get_connection
from app.tools.definitions import build_tools_for_business
from app.utils.time_helpers import get_date_context, generate_date_calendar
from app.services.availability import availability_service
from app.services.booking import booking_service

logger = logging.getLogger(__name__)

openai_client = openai.OpenAI(api_key=settings.openai_api_key)


# ------------------------------------------------------------------
# System prompt builder
# ------------------------------------------------------------------

def build_system_prompt(business: Business) -> str:
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
        services_lines.append(
            f"- {t.name_it}: {price_str} ({t.duration_minutes} min) "
            f"[code: {t.code}] [operators: {ops_str}]"
        )
    services_block = "\n".join(services_lines) or "No services configured."

    # --- Operators list ---
    operators_lines: list[str] = []
    for op in business.operators:
        if not op.is_active:
            continue
        treat_names = []
        for t in business.treatments:
            if t.is_active and t.id in op.treatment_ids:
                treat_names.append(t.name_it)
        treats_str = ", ".join(treat_names) if treat_names else "all services"
        operators_lines.append(f"- {op.display_name}: {treats_str}")
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

    # --- Address block ---
    address_str = business.address or "Not configured"
    phone_str = business.phone or "Not configured"

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

UPCOMING DAYS CALENDAR:
{dates['calendar']}

WHEN CUSTOMER SAYS A DAY NAME:
-> Look at the calendar above and use the EXACT DATE (YYYY-MM-DD).

OPERATOR RULES:
- If customer has NO operator preference -> auto-assign (system picks the first available).
- If customer asks "chi e' disponibile?" or "who is available?" -> use get_operators_for_treatment to list operators.
- If customer names a specific operator -> check only that operator's availability.
- ALWAYS include the assigned operator's name when confirming a booking.

NAME REQUIREMENT (CRITICAL):
- You MUST know the customer's name BEFORE booking.
- If the customer gives service + date + time but NO name, ask for it first.
- Do NOT show a confirmation summary without the name.

BOOKING FLOW:
1. Collect: name, service, date, time.
2. BEFORE asking "Confermi?", call check_availability to verify the slot is free.
3. Show summary with FULL date (day, number, month, year) and ask for confirmation.
4. ONLY after customer says yes/ok/si -> call create_appointment.
5. After booking confirmed, if customer says "ok"/"grazie"/"perfetto" -> just acknowledge, do NOT book again.

MODIFY/CANCEL:
- Use get_customer_appointments to look up bookings (no need to ask for phone).
- cancel_appointment / modify_appointment use name + date + time (no IDs needed).

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
    ) -> str:
        """Process a user message and return the bot's text reply.

        Runs up to ``MAX_TOOL_ROUNDS`` of tool-call / tool-result cycles.
        """
        system_prompt = build_system_prompt(business)
        tools = build_tools_for_business(business)

        messages: list[dict] = [{"role": "system", "content": system_prompt}]
        messages.extend(conversation_history)
        messages.append({"role": "user", "content": message})

        for _round in range(self.MAX_TOOL_ROUNDS):
            response = await asyncio.to_thread(
                openai_client.chat.completions.create,
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
    ) -> str:
        """Execute a tool call and return the JSON result string."""
        try:
            args = json.loads(arguments_json)
        except json.JSONDecodeError:
            return json.dumps({"error": "INVALID_ARGUMENTS"})

        logger.info("Tool call: %s(%s) for business %s", tool_name, args, business.id)

        try:
            result = await asyncio.to_thread(
                self._dispatch_tool, business, customer_phone, customer_name, tool_name, args
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
                customer_name=args["customer_name"],
                appt_date=date.fromisoformat(args["date"]),
                appt_time=time.fromisoformat(args["time"]),
            )

        if tool_name == "modify_appointment":
            new_date = date.fromisoformat(args["new_date"]) if args.get("new_date") else None
            new_time = time.fromisoformat(args["new_time"]) if args.get("new_time") else None
            return booking_service.modify_appointment(
                business=business,
                customer_name=args["customer_name"],
                current_date=date.fromisoformat(args["current_date"]),
                current_time=time.fromisoformat(args["current_time"]),
                new_date=new_date,
                new_time=new_time,
                new_treatment=args.get("new_treatment"),
                new_operator=args.get("new_operator"),
            )

        if tool_name == "confirm_reminder":
            return self._confirm_reminder(business, customer_phone)

        if tool_name == "escalate_to_human":
            return {"escalated": True, "reason": args.get("reason", "")}

        return {"error": f"UNKNOWN_TOOL: {tool_name}"}

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
