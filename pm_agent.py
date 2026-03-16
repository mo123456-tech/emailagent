#!/usr/bin/env python3
"""
Property Manager Research Agent

Autonomously searches top property management blogs and forums, identifies
recurring themes and pain points, then saves a polished HTML report you can
open in any browser.

Usage:
    python pm_agent.py
"""

import json
import os
from datetime import datetime

import anthropic
from dotenv import load_dotenv

load_dotenv()

# ─── Auth (same pattern as agent.py) ─────────────────────────────────────────


def _make_client() -> anthropic.Anthropic:
    auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN")
    if not auth_token and not os.environ.get("ANTHROPIC_API_KEY"):
        for _tp in (
            "/home/claude/.claude/remote/.session_ingress_token",
            os.path.expanduser("~/.claude/remote/.session_ingress_token"),
        ):
            if os.path.exists(_tp):
                with open(_tp) as f:
                    auth_token = f.read().strip()
                break
    if auth_token:
        return anthropic.Anthropic(auth_token=auth_token)
    return anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env


# ─── File output ──────────────────────────────────────────────────────────────


def _save_report(title: str, html_body: str) -> dict:
    """Write a self-contained HTML file and return its path."""
    filename = f"pm_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    with open(filename, "w", encoding="utf-8") as fh:
        fh.write(
            f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
</head>
<body>
{html_body}
</body>
</html>"""
        )
    return {"success": True, "saved_to": filename}


# ─── Tool definitions ─────────────────────────────────────────────────────────

TOOLS = [
    # Server-side tools – the API executes these automatically
    {"type": "web_search_20260209", "name": "web_search"},
    {"type": "web_fetch_20260209", "name": "web_fetch"},
    # Client-side tool – triggered when Claude has finished the report
    {
        "name": "save_report",
        "description": (
            "Save the completed property management research report as a local HTML file. "
            "Call this ONLY once you have finished researching at least 5 different "
            "sources and have compiled the full report. "
            "The html_body must be a complete, polished HTML document fragment."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": (
                        "Report title used in the HTML <title> tag and as the page heading, "
                        "e.g. 'Property Manager Market Research – March 2025'"
                    ),
                },
                "html_body": {
                    "type": "string",
                    "description": "Full HTML body of the report (complete formatting).",
                },
            },
            "required": ["title", "html_body"],
        },
    },
]

# ─── System prompt ────────────────────────────────────────────────────────────

_TODAY = datetime.now().strftime("%B %d, %Y")

SYSTEM_PROMPT = f"""You are a senior market researcher specializing in property management \
software and services.

Today's date: {_TODAY}

## Mission
Research the most active property management blogs, forums, and communities RIGHT NOW. \
Identify what property managers are struggling with, what they discuss most, and what \
products would solve their biggest problems.

## Sources to check (use web_search + web_fetch)
Search and browse these communities – find the hottest, most-commented threads from the \
past 30–90 days:

- **Reddit**: r/PropertyManagement, r/landlord, r/realestateinvesting
- **BiggerPockets** forums: biggerpockets.com/forums (property management section)
- **Multifamily Insiders**: multifamilyinsiders.com/forums
- **Buildium blog**: buildium.com/blog
- **AppFolio blog & community**: appfolio.com/blog
- **Rent Manager blog**: rentmanager.com/blog
- **IREM** (Institute of Real Estate Management): irem.org
- **NAA** (National Apartment Association): naahq.org
- Any other active PM communities you discover

## Research approach
1. Search broadly first: "property manager problems 2025", "landlord pain points forum", \
"property management software complaints", etc.
2. Drill into the top threads to read actual comments and identify specific frustrations.
3. Look for patterns across multiple sources.
4. Note: software complaints, legal/compliance challenges, tenant issues, maintenance \
problems, financial management difficulties, staffing concerns.

## Report structure
Once you have researched at least 5 sources, compile the report with:

1. **Executive Summary** – 3-sentence overview
2. **Top 10 Discussion Topics** – ranked by frequency/engagement, each with:
   - Topic title
   - Brief description of the discussion
   - Representative quote or example from a real thread (with source)
3. **Recurring Themes** – cross-source patterns with frequency indicators
4. **Key Pain Points & Frustrations** – what property managers struggle with most
5. **Product Opportunity Recommendations** – 5–8 specific product/feature ideas, each with:
   - Product name / concept
   - Problem it solves (tied to pain points found)
   - Target customer segment (single-family, multifamily, commercial, etc.)
   - Why it's worth building
6. **Sources Reviewed** – list every URL/community you checked

## HTML formatting guidelines
- Use a clean, professional design with a color scheme (e.g., navy + white + amber accents)
- Clear section headers with visual separation
- Numbered lists for discussion topics; bullet points for themes
- Highlight product recommendations with a distinct card or callout style
- Include the date generated at the top
- Make it look like a real business intelligence report, not a plain document

When your research is thorough and the report is ready, call `save_report`.
"""

# ─── Agent loop ───────────────────────────────────────────────────────────────


def run() -> None:
    client = _make_client()

    print("=" * 60)
    print("  Property Manager Research Agent")
    print(f"  {_TODAY}")
    print("=" * 60)
    print()
    print("Claude will search property management forums and blogs,")
    print("analyze recurring themes, and save a report to this folder.")
    print()

    messages = [
        {
            "role": "user",
            "content": (
                "Please research the top property management forums and blogs to find "
                "the most discussed topics and pain points right now. "
                "Check at least 5 different sources, then save a comprehensive "
                "HTML report with product opportunity recommendations."
            ),
        }
    ]

    MAX_ITERATIONS = 40  # generous ceiling for thorough research

    for iteration in range(MAX_ITERATIONS):
        print(f"[Turn {iteration + 1}] Calling Claude...", flush=True)

        with client.messages.stream(
            model="claude-opus-4-6",
            max_tokens=8192,
            thinking={"type": "adaptive"},
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        ) as stream:
            response = stream.get_final_message()

        # Log what Claude is doing this turn
        for block in response.content:
            btype = getattr(block, "type", None)
            if btype == "text" and block.text.strip():
                preview = block.text.strip()[:200].replace("\n", " ")
                print(f"  Claude: {preview}{'...' if len(block.text) > 200 else ''}")
            elif btype == "thinking" and block.thinking.strip():
                preview = block.thinking.strip()[:120].replace("\n", " ")
                print(f"  [thinking] {preview}...")
            elif btype == "server_tool_use":
                tool_input = getattr(block, "input", {}) or {}
                if block.name == "web_search":
                    print(f"  Searching: {tool_input.get('query', '')}")
                elif block.name == "web_fetch":
                    print(f"  Fetching:  {tool_input.get('url', '')[:90]}")

        # Append the full assistant turn (required to preserve server tool results)
        messages.append({"role": "assistant", "content": response.content})

        # ── Handle stop reasons ────────────────────────────────────────────────

        if response.stop_reason == "end_turn":
            print("\nAgent finished.")
            break

        elif response.stop_reason == "pause_turn":
            # Server-side tool loop hit its iteration limit; resume automatically
            print("  (resuming server-side research...)")
            continue  # re-send without adding a new user message

        elif response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if getattr(block, "type", None) != "tool_use":
                    continue

                if block.name != "save_report":
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(
                                {"error": f"Unknown tool: {block.name}"}
                            ),
                            "is_error": True,
                        }
                    )
                    continue

                print("\n  Saving report...", flush=True)
                result = _save_report(block.input["title"], block.input["html_body"])
                print(f"  Report saved to: {result['saved_to']}")

                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result),
                    }
                )

            if tool_results:
                messages.append({"role": "user", "content": tool_results})
            else:
                break

        else:
            print(f"Unexpected stop reason: {response.stop_reason!r} – stopping.")
            break

    else:
        print(f"\nMax iterations ({MAX_ITERATIONS}) reached.")

    print("\nDone.")


if __name__ == "__main__":
    run()
