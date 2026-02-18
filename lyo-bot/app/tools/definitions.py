"""Dynamic OpenAI function-calling tool definitions.

``build_tools_for_business`` inspects the tenant's active treatments and
operators so the enum values in each tool schema are always up-to-date.
"""

from app.models.schemas import Business


def build_tools_for_business(business: Business) -> list[dict]:
    """Build the 9 OpenAI function-calling tool definitions for *business*."""

    treatment_codes = [t.code for t in business.treatments if t.is_active]
    operator_names = [o.display_name for o in business.operators if o.is_active]

    # Helper to build an operator_name parameter (nullable enum)
    def _operator_param(description: str = "Operator name, or null for auto-assign") -> dict:
        return {
            "type": ["string", "null"],
            "enum": operator_names + [None],
            "description": description,
        }

    tools: list[dict] = [
        # 1. create_appointment
        {
            "type": "function",
            "function": {
                "name": "create_appointment",
                "description": "Book a new appointment for the customer.",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "customer_name": {
                            "type": "string",
                            "description": "Full name of the customer.",
                        },
                        "treatment_code": {
                            "type": "string",
                            "enum": treatment_codes,
                            "description": "Internal treatment code.",
                        },
                        "date": {
                            "type": "string",
                            "description": "Appointment date in YYYY-MM-DD format.",
                        },
                        "time": {
                            "type": "string",
                            "description": "Appointment time in HH:MM 24h format.",
                        },
                        "operator_name": _operator_param(),
                    },
                    "required": [
                        "customer_name", "treatment_code", "date", "time", "operator_name",
                    ],
                    "additionalProperties": False,
                },
            },
        },
        # 2. check_availability
        {
            "type": "function",
            "function": {
                "name": "check_availability",
                "description": "Check whether a specific date/time slot is available.",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "treatment_code": {
                            "type": "string",
                            "enum": treatment_codes,
                            "description": "Internal treatment code.",
                        },
                        "date": {
                            "type": "string",
                            "description": "Date in YYYY-MM-DD format.",
                        },
                        "time": {
                            "type": "string",
                            "description": "Time in HH:MM 24h format.",
                        },
                        "operator_name": _operator_param(),
                    },
                    "required": ["treatment_code", "date", "time", "operator_name"],
                    "additionalProperties": False,
                },
            },
        },
        # 3. get_available_slots
        {
            "type": "function",
            "function": {
                "name": "get_available_slots",
                "description": "List all available time slots for a treatment on a given date.",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "treatment_code": {
                            "type": "string",
                            "enum": treatment_codes,
                            "description": "Internal treatment code.",
                        },
                        "date": {
                            "type": "string",
                            "description": "Date in YYYY-MM-DD format.",
                        },
                        "operator_name": _operator_param(),
                    },
                    "required": ["treatment_code", "date", "operator_name"],
                    "additionalProperties": False,
                },
            },
        },
        # 4. get_operators_for_treatment
        {
            "type": "function",
            "function": {
                "name": "get_operators_for_treatment",
                "description": "List operators who can perform a given treatment.",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "treatment_code": {
                            "type": "string",
                            "enum": treatment_codes,
                            "description": "Internal treatment code.",
                        },
                    },
                    "required": ["treatment_code"],
                    "additionalProperties": False,
                },
            },
        },
        # 5. get_customer_appointments
        {
            "type": "function",
            "function": {
                "name": "get_customer_appointments",
                "description": "Retrieve the customer's existing appointments.",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
            },
        },
        # 6. cancel_appointment
        {
            "type": "function",
            "function": {
                "name": "cancel_appointment",
                "description": "Cancel an existing appointment.",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "customer_name": {
                            "type": "string",
                            "description": "Name of the customer.",
                        },
                        "date": {
                            "type": "string",
                            "description": "Appointment date in YYYY-MM-DD format.",
                        },
                        "time": {
                            "type": "string",
                            "description": "Appointment time in HH:MM 24h format.",
                        },
                    },
                    "required": ["customer_name", "date", "time"],
                    "additionalProperties": False,
                },
            },
        },
        # 7. modify_appointment
        {
            "type": "function",
            "function": {
                "name": "modify_appointment",
                "description": "Modify/reschedule an existing appointment.",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "customer_name": {
                            "type": "string",
                            "description": "Name of the customer.",
                        },
                        "current_date": {
                            "type": "string",
                            "description": "Current appointment date in YYYY-MM-DD.",
                        },
                        "current_time": {
                            "type": "string",
                            "description": "Current appointment time in HH:MM.",
                        },
                        "new_date": {
                            "type": ["string", "null"],
                            "description": "New date (YYYY-MM-DD), or null to keep current.",
                        },
                        "new_time": {
                            "type": ["string", "null"],
                            "description": "New time (HH:MM), or null to keep current.",
                        },
                        "new_treatment": {
                            "type": ["string", "null"],
                            "enum": treatment_codes + [None],
                            "description": "New treatment code, or null to keep current.",
                        },
                        "new_operator": _operator_param("New operator, or null to keep current."),
                    },
                    "required": [
                        "customer_name", "current_date", "current_time",
                        "new_date", "new_time", "new_treatment", "new_operator",
                    ],
                    "additionalProperties": False,
                },
            },
        },
        # 8. confirm_reminder
        {
            "type": "function",
            "function": {
                "name": "confirm_reminder",
                "description": "Mark the customer's upcoming appointment reminder as confirmed.",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
            },
        },
        # 9. escalate_to_human
        {
            "type": "function",
            "function": {
                "name": "escalate_to_human",
                "description": "Escalate the conversation to a human agent.",
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reason": {
                            "type": "string",
                            "description": "Reason for escalation.",
                        },
                    },
                    "required": ["reason"],
                    "additionalProperties": False,
                },
            },
        },
    ]
    return tools
