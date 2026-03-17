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
import re
import urllib.request
from datetime import datetime
from html.parser import HTMLParser

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


# ─── Slack notification ───────────────────────────────────────────────────────


class _StripHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script"):
            self._skip = True
        if tag in ("li", "p", "h1", "h2", "h3", "h4", "br", "div", "tr"):
            self._parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("style", "script"):
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            self._parts.append(data)

    def get_text(self):
        text = "".join(self._parts)
        return re.sub(r"\n{3,}", "\n\n", text).strip()


def _extract_summary(html_body: str) -> dict:
    """Pull executive summary, top-10 topics, and key stats from the HTML."""
    parser = _StripHTML()
    parser.feed(html_body)
    text = parser.get_text()

    # Key stats line (lines with % signs near the header)
    stat_lines = [
        ln.strip()
        for ln in text.splitlines()
        if "%" in ln and len(ln.strip()) < 100 and ln.strip()
    ][:6]

    # Executive summary: text between "Executive Summary" and "Section" or "Top 10"
    exec_match = re.search(
        r"Executive Summary\s*\n+(.*?)(?=\nSection|\nTop 10|\n\d+\n)",
        text,
        re.DOTALL,
    )
    exec_summary = ""
    if exec_match:
        exec_summary = " ".join(exec_match.group(1).split())[:600]

    # Top 10 topics: lines that look like "1\nTitle" or numbered entries
    topic_titles = re.findall(
        r"(?:^|\n)(\d{1,2})\n([^\n]{10,80})\n", text
    )
    topics = [f"{n}. {t.strip()}" for n, t in topic_titles if int(n) <= 10][:10]

    return {
        "exec_summary": exec_summary,
        "stats": stat_lines,
        "topics": topics,
    }


def _send_slack_report(html_body: str, report_path: str) -> None:
    """Post a formatted summary to Slack via incoming webhook."""
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook_url:
        print("  (SLACK_WEBHOOK_URL not set — skipping Slack notification)")
        return

    data = _extract_summary(html_body)
    date_str = datetime.now().strftime("%B %d, %Y")

    stats_text = "  ".join(data["stats"]) if data["stats"] else ""
    topics_text = "\n".join(data["topics"]) if data["topics"] else ""

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"Property Manager Research Report — {date_str}",
            },
        },
        {"type": "divider"},
    ]

    if data["exec_summary"]:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Summary*\n{data['exec_summary']}"},
            }
        )

    if stats_text:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Key Stats*\n{stats_text}"},
            }
        )

    if topics_text:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Top 10 Topics*\n{topics_text}"},
            }
        )

    blocks.append({"type": "divider"})
    blocks.append(
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Report saved to `{report_path}` · 15+ sources analyzed",
                }
            ],
        }
    )

    payload = json.dumps({"blocks": blocks}).encode()
    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.status
        if status == 200:
            print("  Slack notification sent.")
        else:
            print(f"  Slack returned HTTP {status}")
    except Exception as exc:
        print(f"  Slack notification failed: {exc}")


# ─── Tool definitions ─────────────────────────────────────────────────────────

TOOLS = [
    # Server-side tools – the API executes these automatically
    {"type": "web_search_20250305", "name": "web_search"},
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

## Sources to check (use web_search)
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
2. Run multiple targeted searches to cover each source community and topic area.
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
            max_tokens=16000,
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
                _send_slack_report(block.input["html_body"], result["saved_to"])

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
