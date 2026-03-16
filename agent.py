"""
Claude-powered CRM agent.

Tools available to Claude:
  - get_crm_stats          : high-level overview
  - list_contacts          : all contacts with last-contact dates
  - search_contacts        : find contacts by name/email
  - get_contact_detail     : recent email subjects for one contact
  - contacts_to_follow_up  : contacts not reached out to in N days
  - contacts_never_replied : people who emailed you that you've never replied to
  - update_contact_notes   : save notes/reminders on a contact

Claude analyzes the data and produces actionable outreach recommendations.
"""

import json
from datetime import datetime, timezone
from typing import Any

import anthropic

from crm import CRMDatabase, Contact

# ─── Helpers ─────────────────────────────────────────────────────────────────


def _contact_to_dict(c: Contact) -> dict:
    return {
        "email": c.email,
        "name": c.name or c.email,
        "last_inbound": c.last_inbound.isoformat() if c.last_inbound else None,
        "last_outbound": c.last_outbound.isoformat() if c.last_outbound else None,
        "days_since_last_contact": c.days_since_contact,
        "inbound_emails": c.inbound_count,
        "outbound_emails": c.outbound_count,
        "notes": c.notes,
    }


# ─── Tool definitions ─────────────────────────────────────────────────────────

TOOLS: list[dict] = [
    {
        "name": "get_crm_stats",
        "description": (
            "Get high-level statistics about your email CRM: total contacts, "
            "two-way relationships, unanswered emails, and counts of stale contacts."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "list_contacts",
        "description": (
            "List all contacts in the CRM sorted by most recently contacted. "
            "Includes last inbound/outbound date and email counts. "
            "Use limit to paginate large lists."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of contacts to return (default 50).",
                }
            },
            "required": [],
        },
    },
    {
        "name": "search_contacts",
        "description": "Search contacts by name or email address.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Name or email fragment to search for.",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_contact_detail",
        "description": (
            "Get detailed information about a specific contact including their "
            "5 most recent email subjects and any saved notes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "email": {
                    "type": "string",
                    "description": "The contact's email address.",
                }
            },
            "required": ["email"],
        },
    },
    {
        "name": "contacts_to_follow_up",
        "description": (
            "Return contacts you have previously emailed (outbound) but haven't "
            "reached out to in at least N days. Use this to find people who may "
            "need a check-in."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Minimum days since last outbound email (default 30).",
                }
            },
            "required": [],
        },
    },
    {
        "name": "contacts_never_replied",
        "description": (
            "Return contacts who have sent you emails but you have never replied to. "
            "These are unanswered inbound messages that may need a response."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "update_contact_notes",
        "description": (
            "Save notes or a reminder for a contact. "
            "Useful for recording why you should follow up or important context."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "email": {
                    "type": "string",
                    "description": "The contact's email address.",
                },
                "notes": {
                    "type": "string",
                    "description": "Free-text notes to save on the contact record.",
                },
            },
            "required": ["email", "notes"],
        },
    },
]

# ─── Tool executor ────────────────────────────────────────────────────────────


def execute_tool(
    tool_name: str,
    tool_input: dict[str, Any],
    db: CRMDatabase,
) -> str:
    """Execute a tool call and return a JSON string result."""

    if tool_name == "get_crm_stats":
        return json.dumps(db.stats())

    elif tool_name == "list_contacts":
        limit = tool_input.get("limit", 50)
        contacts = db.get_all_contacts()[:limit]
        return json.dumps([_contact_to_dict(c) for c in contacts])

    elif tool_name == "search_contacts":
        contacts = db.search_contacts(tool_input["query"])
        return json.dumps([_contact_to_dict(c) for c in contacts])

    elif tool_name == "get_contact_detail":
        email_addr = tool_input["email"]
        contact = db.get_contact(email_addr)
        if not contact:
            return json.dumps({"error": f"Contact '{email_addr}' not found."})
        subjects = db.get_recent_subjects(email_addr, limit=5)
        result = _contact_to_dict(contact)
        result["recent_subjects"] = subjects
        return json.dumps(result)

    elif tool_name == "contacts_to_follow_up":
        days = tool_input.get("days", 30)
        contacts = db.get_contacts_not_contacted_since(days)
        return json.dumps([_contact_to_dict(c) for c in contacts])

    elif tool_name == "contacts_never_replied":
        contacts = db.get_contacts_never_replied_to()
        return json.dumps([_contact_to_dict(c) for c in contacts])

    elif tool_name == "update_contact_notes":
        ok = db.update_notes(tool_input["email"], tool_input["notes"])
        return json.dumps({"success": ok})

    else:
        return json.dumps({"error": f"Unknown tool: {tool_name}"})


# ─── Agent ───────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a personal CRM assistant. Your job is to help the user:

1. Understand who they have been in contact with via email.
2. Identify people they should reach out to — because it's been too long,
   because those contacts emailed them and got no reply, or because the
   relationship seems important based on frequency.
3. Generate a prioritized, actionable outreach list.

When producing recommendations:
- Be specific: name the person, the relationship context (if inferable),
  and WHY you suggest reaching out.
- Group suggestions (e.g. "Urgent – no reply", "Check-in overdue",
  "Reconnect – haven't talked in a while").
- Keep it concise and scannable.

Today's date: {today}
"""


class CRMAgent:
    """Runs the agentic CRM analysis loop using Claude."""

    def __init__(self, db: CRMDatabase, model: str = "claude-opus-4-6"):
        self.db = db
        self.client = anthropic.Anthropic()
        self.model = model

    def run(self, prompt: str, *, verbose: bool = False) -> str:
        """
        Send a prompt to Claude, let it explore the CRM via tools,
        and return the final text response.
        """
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        system = SYSTEM_PROMPT.format(today=today)

        messages: list[dict] = [{"role": "user", "content": prompt}]

        while True:
            with self.client.messages.stream(
                model=self.model,
                max_tokens=4096,
                thinking={"type": "adaptive"},
                system=system,
                tools=TOOLS,
                messages=messages,
            ) as stream:
                response = stream.get_final_message()

            if verbose:
                for block in response.content:
                    if block.type == "thinking":
                        print(f"\n[thinking] {block.thinking[:200]}…")

            # Collect tool calls
            tool_uses = [b for b in response.content if b.type == "tool_use"]

            # Append assistant turn
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn" or not tool_uses:
                # Return the final text
                texts = [b.text for b in response.content if b.type == "text"]
                return "\n".join(texts)

            # Execute all tool calls and feed results back
            tool_results = []
            for tool_use in tool_uses:
                if verbose:
                    print(f"[tool] {tool_use.name}({tool_use.input})")
                result = execute_tool(tool_use.name, tool_use.input, self.db)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use.id,
                        "content": result,
                    }
                )

            messages.append({"role": "user", "content": tool_results})
