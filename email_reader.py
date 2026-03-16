"""
Email reader using IMAP to fetch and parse emails.
Extracts sender/recipient contact info, dates, and subjects.
"""

import imaplib
import email
import email.utils
import os
import re
import ssl
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Generator


@dataclass
class EmailContact:
    name: str
    address: str


@dataclass
class ParsedEmail:
    message_id: str
    date: datetime
    subject: str
    sender: EmailContact
    recipients: list[EmailContact]
    direction: str  # "sent" | "received"


def _parse_address(raw: str) -> list[EmailContact]:
    """Parse a raw address header into a list of EmailContact."""
    contacts = []
    for name, addr in email.utils.getaddresses([raw or ""]):
        addr = addr.strip().lower()
        if addr:
            contacts.append(EmailContact(name=name.strip(), address=addr))
    return contacts


def _parse_date(raw: str | None) -> datetime:
    """Parse an email date string into a UTC datetime."""
    if not raw:
        return datetime.now(timezone.utc)
    try:
        parsed = email.utils.parsedate_to_datetime(raw)
        # Ensure timezone-aware
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


class IMAPEmailReader:
    """Connects to an IMAP mailbox and yields ParsedEmail objects."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        max_per_folder: int = 500,
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.max_per_folder = max_per_folder
        self._client: imaplib.IMAP4_SSL | None = None

    def connect(self) -> None:
        ctx = ssl.create_default_context()
        self._client = imaplib.IMAP4_SSL(self.host, self.port, ssl_context=ctx)
        self._client.login(self.username, self.password)

    def disconnect(self) -> None:
        if self._client:
            try:
                self._client.logout()
            except Exception:
                pass
            self._client = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *_):
        self.disconnect()

    def _list_folders(self) -> list[str]:
        """Return all selectable mailbox folder names."""
        _, folder_list = self._client.list()
        folders = []
        for item in folder_list:
            if not item:
                continue
            decoded = item.decode() if isinstance(item, bytes) else item
            # Format: (\Flags) "delimiter" "name"
            match = re.search(r'"([^"]+)"\s*$|(\S+)$', decoded)
            if match:
                name = (match.group(1) or match.group(2)).strip('"')
                # Skip non-selectable folders
                if r"\Noselect" not in decoded:
                    folders.append(name)
        return folders

    def _fetch_folder(
        self,
        folder: str,
        direction: str,
    ) -> Generator[ParsedEmail, None, None]:
        """Select a folder and yield ParsedEmail for each message."""
        try:
            status, _ = self._client.select(f'"{folder}"', readonly=True)
            if status != "OK":
                return
        except Exception:
            return

        _, data = self._client.search(None, "ALL")
        if not data or not data[0]:
            return

        message_ids = data[0].split()
        # Take the most recent N
        message_ids = message_ids[-self.max_per_folder :]

        for mid in message_ids:
            try:
                _, msg_data = self._client.fetch(mid, "(RFC822)")
                if not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1]
                msg = email.message_from_bytes(raw)

                sender_raw = msg.get("From", "")
                senders = _parse_address(sender_raw)
                if not senders:
                    continue
                sender = senders[0]

                to_raw = msg.get("To", "") + ", " + msg.get("Cc", "")
                recipients = _parse_address(to_raw)

                date = _parse_date(msg.get("Date"))
                subject = msg.get("Subject", "(no subject)")
                msg_id = msg.get("Message-ID", str(mid))

                yield ParsedEmail(
                    message_id=msg_id,
                    date=date,
                    subject=subject,
                    sender=sender,
                    recipients=recipients,
                    direction=direction,
                )
            except Exception:
                continue

    def iter_emails(self) -> Generator[ParsedEmail, None, None]:
        """
        Yield all ParsedEmail objects from the inbox (received)
        and sent folders (sent).
        """
        folders = self._list_folders()

        # Identify inbox and sent folders heuristically
        inbox_folders = [f for f in folders if f.lower() in ("inbox",)]
        sent_folders = [
            f
            for f in folders
            if any(kw in f.lower() for kw in ("sent", "[gmail]/sent mail"))
        ]

        # Fallback: always try INBOX
        if not inbox_folders:
            inbox_folders = ["INBOX"]

        for folder in inbox_folders:
            yield from self._fetch_folder(folder, direction="received")

        for folder in sent_folders:
            yield from self._fetch_folder(folder, direction="sent")


def create_reader_from_env() -> IMAPEmailReader:
    """Build an IMAPEmailReader from environment variables."""
    return IMAPEmailReader(
        host=os.environ.get("IMAP_HOST", "imap.gmail.com"),
        port=int(os.environ.get("IMAP_PORT", "993")),
        username=os.environ["EMAIL_ADDRESS"],
        password=os.environ["EMAIL_PASSWORD"],
        max_per_folder=int(os.environ.get("MAX_EMAILS_PER_FOLDER", "500")),
    )
