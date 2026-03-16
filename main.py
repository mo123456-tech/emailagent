#!/usr/bin/env python3
"""
Email CRM Agent — CLI entry point.

Commands:
  sync      Scan your mailbox and populate the CRM database.
  analyze   Ask Claude to analyze your contacts and suggest who to reach out to.
  ask       Ask Claude a free-form question about your contacts.
  stats     Print CRM statistics.
  list      List contacts.
  search    Search contacts by name or email.

Usage examples:
  python main.py sync
  python main.py analyze
  python main.py ask "Who are my most important contacts that I haven't emailed in 60 days?"
  python main.py stats
  python main.py list --limit 20
  python main.py search "alice"
"""

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from crm import CRMDatabase
from email_reader import IMAPEmailReader, create_reader_from_env
from agent import CRMAgent

load_dotenv()

DB_PATH = os.environ.get("CRM_DB_PATH", "crm.db")

# ─── Sync ─────────────────────────────────────────────────────────────────────


def cmd_sync(args) -> None:
    """Read all emails from IMAP and build/update the CRM database."""
    try:
        reader = create_reader_from_env()
    except KeyError as e:
        print(f"Error: missing environment variable {e}")
        print("Copy .env.example to .env and fill in your credentials.")
        sys.exit(1)

    own_address = os.environ["EMAIL_ADDRESS"].strip().lower()

    print(f"Connecting to {os.environ.get('IMAP_HOST', 'imap.gmail.com')}…")

    with CRMDatabase(DB_PATH) as db:
        with reader:
            count = 0
            for parsed in reader.iter_emails():
                count += 1
                if count % 50 == 0:
                    print(f"  Processed {count} emails…", end="\r", flush=True)

                if parsed.direction == "received":
                    # Email came from someone else to us
                    db.record_interaction(
                        email_addr=parsed.sender.address,
                        name=parsed.sender.name,
                        direction="inbound",
                        date=parsed.date,
                        subject=parsed.subject,
                        message_id=parsed.message_id,
                    )
                else:
                    # Email sent by us — record each recipient
                    for recipient in parsed.recipients:
                        if recipient.address and recipient.address != own_address:
                            db.record_interaction(
                                email_addr=recipient.address,
                                name=recipient.name,
                                direction="outbound",
                                date=parsed.date,
                                subject=parsed.subject,
                                message_id=f"{parsed.message_id}:{recipient.address}",
                            )

        print(f"\nSync complete. Processed {count} emails.")
        s = db.stats()
        print(
            f"CRM: {s['total_contacts']} contacts  |  "
            f"{s['two_way']} two-way  |  "
            f"{s['unanswered']} unanswered  |  "
            f"{s['stale_30d']} stale >30d"
        )


# ─── Analyze ──────────────────────────────────────────────────────────────────

DEFAULT_ANALYZE_PROMPT = """
Please analyze my email contacts and give me a prioritized list of people I should reach out to.

For each suggestion:
1. Name / email
2. Why I should reach out (no reply, long time since contact, etc.)
3. A suggested opening line or topic based on context

Group them into:
- 🔴 Urgent: people who emailed me and never got a reply
- 🟡 Overdue: people I regularly email but haven't contacted in 30+ days
- 🔵 Reconnect: people I've lost touch with (90+ days since any contact)
"""


def cmd_analyze(args) -> None:
    with CRMDatabase(DB_PATH) as db:
        agent = CRMAgent(db)
        print("Analyzing your contacts with Claude…\n")
        result = agent.run(DEFAULT_ANALYZE_PROMPT, verbose=args.verbose)
        print(result)


# ─── Ask ──────────────────────────────────────────────────────────────────────


def cmd_ask(args) -> None:
    question = " ".join(args.question)
    with CRMDatabase(DB_PATH) as db:
        agent = CRMAgent(db)
        print("Claude is thinking…\n")
        result = agent.run(question, verbose=args.verbose)
        print(result)


# ─── Stats ────────────────────────────────────────────────────────────────────


def cmd_stats(args) -> None:
    with CRMDatabase(DB_PATH) as db:
        s = db.stats()
    print("─── CRM Statistics ───────────────────────────────────────")
    print(f"  Total contacts       : {s['total_contacts']}")
    print(f"  Two-way relationships: {s['two_way']}")
    print(f"  Unanswered inbound   : {s['unanswered']}")
    print(f"  Stale >30 days       : {s['stale_30d']}")
    print(f"  Stale >90 days       : {s['stale_90d']}")
    print("──────────────────────────────────────────────────────────")


# ─── List ─────────────────────────────────────────────────────────────────────


def cmd_list(args) -> None:
    with CRMDatabase(DB_PATH) as db:
        contacts = db.get_all_contacts()[: args.limit]

    if not contacts:
        print("No contacts yet. Run `python main.py sync` first.")
        return

    fmt = "{:<30} {:<8} {:<20} {:<20}"
    print(fmt.format("Name/Email", "Emails", "Last Inbound", "Last Outbound"))
    print("─" * 82)
    for c in contacts:
        label = (c.name or c.email)[:29]
        total = f"{c.inbound_count}in/{c.outbound_count}out"
        inb = c.last_inbound.strftime("%Y-%m-%d") if c.last_inbound else "—"
        out = c.last_outbound.strftime("%Y-%m-%d") if c.last_outbound else "—"
        print(fmt.format(label, total, inb, out))


# ─── Search ───────────────────────────────────────────────────────────────────


def cmd_search(args) -> None:
    query = " ".join(args.query)
    with CRMDatabase(DB_PATH) as db:
        contacts = db.search_contacts(query)

    if not contacts:
        print(f"No contacts matching '{query}'.")
        return

    for c in contacts:
        days = c.days_since_contact
        days_str = f"{days}d ago" if days is not None else "never"
        print(
            f"  {c.name or '(no name)':<25}  {c.email:<35}  "
            f"{c.total_emails} emails  last: {days_str}"
        )


# ─── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Email CRM Agent — review your emails and manage relationships."
    )
    parser.add_argument(
        "--db", default=DB_PATH, help="Path to CRM SQLite database (default: crm.db)"
    )
    subparsers = parser.add_subparsers(dest="command")

    # sync
    p_sync = subparsers.add_parser("sync", help="Scan mailbox and populate CRM.")
    p_sync.set_defaults(func=cmd_sync)

    # analyze
    p_analyze = subparsers.add_parser(
        "analyze", help="Ask Claude to recommend who to reach out to."
    )
    p_analyze.add_argument("--verbose", action="store_true", help="Show Claude's thinking.")
    p_analyze.set_defaults(func=cmd_analyze)

    # ask
    p_ask = subparsers.add_parser("ask", help="Ask Claude a free-form question.")
    p_ask.add_argument("question", nargs="+", help="Your question.")
    p_ask.add_argument("--verbose", action="store_true")
    p_ask.set_defaults(func=cmd_ask)

    # stats
    p_stats = subparsers.add_parser("stats", help="Show CRM statistics.")
    p_stats.set_defaults(func=cmd_stats)

    # list
    p_list = subparsers.add_parser("list", help="List contacts.")
    p_list.add_argument("--limit", type=int, default=50, help="Max rows to show.")
    p_list.set_defaults(func=cmd_list)

    # search
    p_search = subparsers.add_parser("search", help="Search contacts.")
    p_search.add_argument("query", nargs="+", help="Name or email to search for.")
    p_search.set_defaults(func=cmd_search)

    args = parser.parse_args()

    # Override DB path if provided
    global DB_PATH
    DB_PATH = args.db

    if not args.command:
        parser.print_help()
        sys.exit(0)

    args.func(args)


if __name__ == "__main__":
    main()
