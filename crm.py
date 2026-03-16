"""
SQLite-backed CRM that stores contacts and their email interaction history.
Tracks last inbound/outbound contact date and message counts.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass
class Contact:
    email: str
    name: str
    last_inbound: Optional[datetime]   # Last time they emailed us
    last_outbound: Optional[datetime]  # Last time we emailed them
    inbound_count: int
    outbound_count: int
    notes: str

    @property
    def last_contact(self) -> Optional[datetime]:
        """Most recent interaction (either direction)."""
        dates = [d for d in (self.last_inbound, self.last_outbound) if d]
        return max(dates) if dates else None

    @property
    def days_since_contact(self) -> Optional[int]:
        lc = self.last_contact
        if lc is None:
            return None
        now = datetime.now(timezone.utc)
        if lc.tzinfo is None:
            lc = lc.replace(tzinfo=timezone.utc)
        return (now - lc).days

    @property
    def total_emails(self) -> int:
        return self.inbound_count + self.outbound_count


CREATE_CONTACTS_SQL = """
CREATE TABLE IF NOT EXISTS contacts (
    email           TEXT PRIMARY KEY,
    name            TEXT NOT NULL DEFAULT '',
    last_inbound    TEXT,
    last_outbound   TEXT,
    inbound_count   INTEGER NOT NULL DEFAULT 0,
    outbound_count  INTEGER NOT NULL DEFAULT 0,
    notes           TEXT NOT NULL DEFAULT ''
)
"""

CREATE_INTERACTIONS_SQL = """
CREATE TABLE IF NOT EXISTS interactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    email       TEXT NOT NULL,
    direction   TEXT NOT NULL,   -- 'inbound' | 'outbound'
    date        TEXT NOT NULL,
    subject     TEXT NOT NULL DEFAULT '',
    message_id  TEXT UNIQUE,
    FOREIGN KEY (email) REFERENCES contacts(email)
)
"""


def _dt_to_str(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _str_to_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


class CRMDatabase:
    """Manages all CRM read/write operations."""

    def __init__(self, db_path: str = "crm.db"):
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def open(self) -> None:
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(CREATE_CONTACTS_SQL)
        self._conn.execute(CREATE_INTERACTIONS_SQL)
        self._conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *_):
        self.close()

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    def record_interaction(
        self,
        email_addr: str,
        name: str,
        direction: str,  # "inbound" | "outbound"
        date: datetime,
        subject: str,
        message_id: str,
    ) -> None:
        """
        Upsert a contact and record a single email interaction.
        Skips duplicate message_ids.
        """
        assert self._conn, "Database not open"

        # Upsert contact (insert or ignore, then update name if better)
        self._conn.execute(
            """
            INSERT INTO contacts (email, name) VALUES (?, ?)
            ON CONFLICT(email) DO UPDATE SET
                name = CASE WHEN excluded.name != '' THEN excluded.name ELSE name END
            """,
            (email_addr, name),
        )

        # Try to insert interaction; skip duplicates
        try:
            self._conn.execute(
                """
                INSERT INTO interactions (email, direction, date, subject, message_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (email_addr, direction, _dt_to_str(date), subject, message_id),
            )
        except sqlite3.IntegrityError:
            # Duplicate message_id — already recorded
            return

        # Update aggregated stats on contacts
        if direction == "inbound":
            self._conn.execute(
                """
                UPDATE contacts SET
                    inbound_count = inbound_count + 1,
                    last_inbound = CASE
                        WHEN last_inbound IS NULL OR last_inbound < ? THEN ?
                        ELSE last_inbound
                    END
                WHERE email = ?
                """,
                (_dt_to_str(date), _dt_to_str(date), email_addr),
            )
        else:
            self._conn.execute(
                """
                UPDATE contacts SET
                    outbound_count = outbound_count + 1,
                    last_outbound = CASE
                        WHEN last_outbound IS NULL OR last_outbound < ? THEN ?
                        ELSE last_outbound
                    END
                WHERE email = ?
                """,
                (_dt_to_str(date), _dt_to_str(date), email_addr),
            )

        self._conn.commit()

    # ------------------------------------------------------------------
    # Queries (used as Claude tools)
    # ------------------------------------------------------------------

    def get_all_contacts(self) -> list[Contact]:
        """Return all contacts sorted by last contact date (most recent first)."""
        assert self._conn
        rows = self._conn.execute(
            """
            SELECT email, name, last_inbound, last_outbound,
                   inbound_count, outbound_count, notes
            FROM contacts
            ORDER BY COALESCE(last_inbound, ''), COALESCE(last_outbound, '') DESC
            """
        ).fetchall()
        return [_row_to_contact(r) for r in rows]

    def get_contact(self, email_addr: str) -> Optional[Contact]:
        """Return a single contact by email address."""
        assert self._conn
        row = self._conn.execute(
            """
            SELECT email, name, last_inbound, last_outbound,
                   inbound_count, outbound_count, notes
            FROM contacts WHERE email = ?
            """,
            (email_addr,),
        ).fetchone()
        return _row_to_contact(row) if row else None

    def search_contacts(self, query: str) -> list[Contact]:
        """Full-text search on name and email."""
        assert self._conn
        like = f"%{query.lower()}%"
        rows = self._conn.execute(
            """
            SELECT email, name, last_inbound, last_outbound,
                   inbound_count, outbound_count, notes
            FROM contacts
            WHERE LOWER(name) LIKE ? OR LOWER(email) LIKE ?
            ORDER BY COALESCE(last_inbound, last_outbound) DESC
            """,
            (like, like),
        ).fetchall()
        return [_row_to_contact(r) for r in rows]

    def get_recent_subjects(self, email_addr: str, limit: int = 5) -> list[dict]:
        """Return the N most recent email subjects for a contact."""
        assert self._conn
        rows = self._conn.execute(
            """
            SELECT direction, date, subject
            FROM interactions
            WHERE email = ?
            ORDER BY date DESC
            LIMIT ?
            """,
            (email_addr, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_contacts_not_contacted_since(self, days: int) -> list[Contact]:
        """Return contacts whose last outbound email is older than N days."""
        assert self._conn
        rows = self._conn.execute(
            """
            SELECT email, name, last_inbound, last_outbound,
                   inbound_count, outbound_count, notes
            FROM contacts
            WHERE last_outbound IS NOT NULL
              AND DATE(last_outbound) <= DATE('now', ? || ' days')
            ORDER BY last_outbound ASC
            """,
            (f"-{days}",),
        ).fetchall()
        return [_row_to_contact(r) for r in rows]

    def get_contacts_never_replied_to(self) -> list[Contact]:
        """Return contacts who have emailed us but we've never replied to."""
        assert self._conn
        rows = self._conn.execute(
            """
            SELECT email, name, last_inbound, last_outbound,
                   inbound_count, outbound_count, notes
            FROM contacts
            WHERE inbound_count > 0 AND outbound_count = 0
            ORDER BY last_inbound DESC
            """
        ).fetchall()
        return [_row_to_contact(r) for r in rows]

    def update_notes(self, email_addr: str, notes: str) -> bool:
        """Update notes for a contact. Returns True if found."""
        assert self._conn
        cur = self._conn.execute(
            "UPDATE contacts SET notes = ? WHERE email = ?",
            (notes, email_addr),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def stats(self) -> dict:
        """Return high-level CRM statistics."""
        assert self._conn
        row = self._conn.execute(
            """
            SELECT
                COUNT(*) as total_contacts,
                COUNT(CASE WHEN outbound_count > 0 THEN 1 END) as two_way,
                COUNT(CASE WHEN inbound_count  > 0 AND outbound_count = 0 THEN 1 END) as unanswered,
                COUNT(CASE WHEN outbound_count > 0 AND last_outbound < DATE('now', '-30 days') THEN 1 END) as stale_30d,
                COUNT(CASE WHEN outbound_count > 0 AND last_outbound < DATE('now', '-90 days') THEN 1 END) as stale_90d
            FROM contacts
            """
        ).fetchone()
        return dict(row)


def _row_to_contact(row: sqlite3.Row) -> Contact:
    return Contact(
        email=row["email"],
        name=row["name"],
        last_inbound=_str_to_dt(row["last_inbound"]),
        last_outbound=_str_to_dt(row["last_outbound"]),
        inbound_count=row["inbound_count"],
        outbound_count=row["outbound_count"],
        notes=row["notes"],
    )
