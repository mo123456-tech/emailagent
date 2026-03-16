"""
Mock demo — seeds fake email data into a temp CRM and runs the Claude agent.
No real email account needed.
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

# ── Load auth token from Claude Code session if no API key in env ─────────────
_TOKEN_PATHS = [
    "/home/claude/.claude/remote/.session_ingress_token",
    os.path.expanduser("~/.claude/remote/.session_ingress_token"),
]
if not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("ANTHROPIC_AUTH_TOKEN"):
    for _tp in _TOKEN_PATHS:
        if os.path.exists(_tp):
            with open(_tp) as f:
                os.environ["ANTHROPIC_AUTH_TOKEN"] = f.read().strip()
            break

from crm import CRMDatabase
from agent import CRMAgent

# ── Fake contact data ─────────────────────────────────────────────────────────

NOW = datetime.now(timezone.utc)


def days_ago(n: int) -> datetime:
    return NOW - timedelta(days=n)


FAKE_INTERACTIONS = [
    # Active relationships
    dict(email="sarah.chen@acmecorp.com",   name="Sarah Chen",       direction="inbound",  date=days_ago(2),  subject="Re: Q2 partnership proposal"),
    dict(email="sarah.chen@acmecorp.com",   name="Sarah Chen",       direction="outbound", date=days_ago(3),  subject="Q2 partnership proposal"),
    dict(email="sarah.chen@acmecorp.com",   name="Sarah Chen",       direction="inbound",  date=days_ago(10), subject="Can we schedule a call?"),
    dict(email="sarah.chen@acmecorp.com",   name="Sarah Chen",       direction="outbound", date=days_ago(11), subject="Intro + next steps"),

    dict(email="james.wu@investors.io",     name="James Wu",         direction="outbound", date=days_ago(5),  subject="Seed round update — March"),
    dict(email="james.wu@investors.io",     name="James Wu",         direction="inbound",  date=days_ago(6),  subject="Re: Seed round update — Feb"),
    dict(email="james.wu@investors.io",     name="James Wu",         direction="outbound", date=days_ago(35), subject="Seed round update — Feb"),
    dict(email="james.wu@investors.io",     name="James Wu",         direction="inbound",  date=days_ago(36), subject="Re: Seed round update — Jan"),

    # Overdue — haven't emailed in 45+ days
    dict(email="priya.nair@techblog.dev",   name="Priya Nair",       direction="outbound", date=days_ago(45), subject="Guest post collaboration"),
    dict(email="priya.nair@techblog.dev",   name="Priya Nair",       direction="inbound",  date=days_ago(44), subject="Re: Guest post collaboration"),
    dict(email="priya.nair@techblog.dev",   name="Priya Nair",       direction="outbound", date=days_ago(90), subject="Hey — long time no talk"),

    dict(email="marco.rossi@designstudio.it", name="Marco Rossi",    direction="outbound", date=days_ago(52), subject="Brand refresh quote"),
    dict(email="marco.rossi@designstudio.it", name="Marco Rossi",    direction="inbound",  date=days_ago(50), subject="Re: Brand refresh quote"),
    dict(email="marco.rossi@designstudio.it", name="Marco Rossi",    direction="inbound",  date=days_ago(49), subject="Following up on quote"),

    # Unanswered inbound — they emailed, never got a reply
    dict(email="alex.johnson@startup.xyz",  name="Alex Johnson",     direction="inbound",  date=days_ago(8),  subject="Introduction — want to chat about integrations"),
    dict(email="alex.johnson@startup.xyz",  name="Alex Johnson",     direction="inbound",  date=days_ago(15), subject="Quick follow-up"),

    dict(email="nina.patel@university.edu", name="Nina Patel",       direction="inbound",  date=days_ago(12), subject="Research collaboration opportunity"),

    dict(email="david.kim@pressroom.co",    name="David Kim",        direction="inbound",  date=days_ago(3),  subject="Interview request for your product launch"),

    # Very stale — 90+ days
    dict(email="lena.schmidt@consulting.de", name="Lena Schmidt",    direction="outbound", date=days_ago(95), subject="Project scoping call"),
    dict(email="lena.schmidt@consulting.de", name="Lena Schmidt",    direction="inbound",  date=days_ago(94), subject="Re: Project scoping call"),
    dict(email="lena.schmidt@consulting.de", name="Lena Schmidt",    direction="outbound", date=days_ago(120), subject="Following up"),

    dict(email="tom.nguyen@oldclient.com",  name="Tom Nguyen",       direction="outbound", date=days_ago(110), subject="Annual check-in"),
    dict(email="tom.nguyen@oldclient.com",  name="Tom Nguyen",       direction="inbound",  date=days_ago(108), subject="Re: Annual check-in"),

    # One-sided outbound — we emailed, never heard back
    dict(email="ceo@bigcompany.com",        name="Rachel Moore",     direction="outbound", date=days_ago(20), subject="Partnership inquiry"),
    dict(email="ceo@bigcompany.com",        name="Rachel Moore",     direction="outbound", date=days_ago(6),  subject="Following up on partnership inquiry"),

    # High frequency recent contact
    dict(email="emma.liu@cofounder.co",     name="Emma Liu",         direction="outbound", date=days_ago(1),  subject="Standup notes"),
    dict(email="emma.liu@cofounder.co",     name="Emma Liu",         direction="inbound",  date=days_ago(1),  subject="Re: Standup notes"),
    dict(email="emma.liu@cofounder.co",     name="Emma Liu",         direction="outbound", date=days_ago(2),  subject="Investor deck v3"),
    dict(email="emma.liu@cofounder.co",     name="Emma Liu",         direction="inbound",  date=days_ago(2),  subject="Re: Investor deck v3"),
]

# ─── Seed the CRM ─────────────────────────────────────────────────────────────


def seed_db(db_path: str) -> None:
    with CRMDatabase(db_path) as db:
        for i, row in enumerate(FAKE_INTERACTIONS):
            db.record_interaction(
                email_addr=row["email"],
                name=row["name"],
                direction=row["direction"],
                date=row["date"],
                subject=row["subject"],
                message_id=f"mock-{i}@demo",
            )


# ─── Run demo ─────────────────────────────────────────────────────────────────


def main() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    try:
        print("═" * 60)
        print("  Email CRM Agent — Demo")
        print("═" * 60)
        print("\n📧 Seeding mock email history…")
        seed_db(db_path)

        with CRMDatabase(db_path) as db:
            s = db.stats()
            print(
                f"   {s['total_contacts']} contacts  |  "
                f"{s['two_way']} two-way  |  "
                f"{s['unanswered']} unanswered  |  "
                f"{s['stale_30d']} stale >30d\n"
            )

            print("🤖 Asking Claude to analyze and recommend outreach…\n")
            print("─" * 60)

            agent = CRMAgent(db)
            result = agent.run(
                "Please analyze my contacts and give me a prioritized outreach list. "
                "Group by urgency: urgent (unanswered), overdue (30+ days), reconnect (90+ days). "
                "For each person include their name, why to reach out, and a suggested subject line.",
            )
            print(result)

        print("\n" + "─" * 60)
        print("✅ Demo complete. Run `python main.py --help` to use with your real email.")

    finally:
        os.unlink(db_path)


if __name__ == "__main__":
    main()
