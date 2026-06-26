from __future__ import annotations

import base64
import imaplib
import smtplib
import ssl
from datetime import datetime
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser
from email.utils import formatdate, getaddresses, make_msgid, parsedate_to_datetime
from pathlib import Path
from typing import Any

from .config import EmailSettings, add_email_account, find_email_account, load_email_accounts


SENT_MAILBOX_NAMES = {"sent", "sent mail", "sent messages", "отправленные"}


def list_available_accounts() -> dict[str, Any]:
    return {"result": [account.public() for account in load_email_accounts()]}


def add_account(email: dict[str, Any]) -> dict[str, str]:
    add_email_account(email_settings_from_tool(email))
    return {"result": "Email account added successfully"}


def list_mailboxes(account_name: str = "") -> dict[str, Any]:
    with imap_connection(find_email_account(account_name)) as client:
        return {"mailboxes": client.list_mailboxes()}


def list_emails_metadata(**kwargs: Any) -> dict[str, Any]:
    account = find_email_account(kwargs.get("account_name", ""))
    mailbox = kwargs.get("mailbox") or "INBOX"
    all_mailboxes = bool(kwargs.get("all_mailboxes", False))
    page = max(int(kwargs.get("page") or 1), 1)
    page_size = int(kwargs.get("page_size") or 10)
    if page_size <= 0:
        page_size = 10
    order = (kwargs.get("order") or "desc").lower()

    with imap_connection(account) as client:
        if all_mailboxes:
            candidates = [item for item in client.list_mailboxes() if item["selectable"]]
        else:
            candidates = [client.resolve_mailbox(mailbox)]

        emails: list[dict[str, Any]] = []
        for candidate in candidates:
            try:
                emails.extend(list_metadata_from_mailbox(client, candidate, kwargs))
            except Exception:
                if not all_mailboxes:
                    raise

    emails.sort(key=lambda item: item.get("date") or "", reverse=(order != "asc"))
    total = len(emails)
    start = (page - 1) * page_size
    end = start + page_size
    return {
        "page": page,
        "page_size": page_size,
        "mailbox": "" if all_mailboxes else candidates[0]["name"],
        "all_mailboxes": all_mailboxes,
        "before": kwargs.get("before") or None,
        "since": kwargs.get("since") or None,
        "subject": kwargs.get("subject") or "",
        "emails": emails[start:end],
        "total": total,
    }


def get_emails_content(account_name: str = "", mailbox: str = "", email_ids: list[str] | None = None) -> dict[str, Any]:
    account = find_email_account(account_name)
    email_ids = email_ids or []
    with imap_connection(account) as client:
        resolved = client.resolve_mailbox(mailbox or "INBOX")
        client.select(resolved["name"])
        emails = []
        for email_id in email_ids:
            raw = client.fetch_message(email_id)
            emails.append(parse_message(email_id, raw, resolved))
    return {"emails": emails}


def send_email(**kwargs: Any) -> dict[str, Any]:
    account = find_email_account(kwargs.get("account_name", ""))
    raw, recipients = build_message(account, kwargs)
    send_smtp(account, raw, recipients)
    response: dict[str, Any] = {"result": "Email sent successfully to " + ", ".join(recipients)}
    if account.save_to_sent:
        response["sent_copy"] = save_sent(account, raw)
    else:
        response["sent_copy"] = {"saved": False, "reason": "save_to_sent is disabled"}
    return response


def delete_emails(account_name: str = "", mailbox: str = "", email_ids: list[str] | None = None) -> dict[str, str]:
    account = find_email_account(account_name)
    with imap_connection(account) as client:
        resolved = client.resolve_mailbox(mailbox or "INBOX")
        client.select(resolved["name"])
        for email_id in email_ids or []:
            client.uid("STORE", email_id, "+FLAGS.SILENT", r"(\Deleted)")
        client.expunge()
    return {"result": "Emails deleted successfully"}


def download_attachment(
    account_name: str = "",
    mailbox: str = "",
    email_id: str = "",
    attachment_name: str = "",
    save_path: str = "",
) -> dict[str, str]:
    account = find_email_account(account_name)
    if not account.enable_attachment_download:
        raise ValueError("attachment download is disabled; set EMAIL_ENABLE_ATTACHMENT_DOWNLOAD=True in constants.py")
    with imap_connection(account) as client:
        resolved = client.resolve_mailbox(mailbox or "INBOX")
        client.select(resolved["name"])
        parsed = BytesParser(policy=policy.default).parsebytes(client.fetch_message(email_id))
    for part in parsed.walk():
        if part.is_multipart():
            continue
        filename = part.get_filename()
        if filename == attachment_name:
            data = part.get_payload(decode=True) or b""
            path = Path(save_path).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            return {"result": "Attachment downloaded successfully"}
    raise ValueError(f"attachment {attachment_name!r} not found on email {email_id}")


class IMAPClient:
    def __init__(self, account: EmailSettings):
        self.account = account
        self.client = imaplib.IMAP4(account.incoming.host, account.incoming.port)
        self.client.login(account.incoming.user_name, account.incoming.password)

    def __enter__(self) -> "IMAPClient":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        try:
            self.client.logout()
        except Exception:
            pass

    def uid(self, command: str, *args: str) -> tuple[str, list[Any]]:
        status, data = self.client.uid(command, *args)
        if status != "OK":
            raise RuntimeError(f"imap UID {command} failed: {data!r}")
        return status, data

    def expunge(self) -> None:
        status, data = self.client.expunge()
        if status != "OK":
            raise RuntimeError(f"imap EXPUNGE failed: {data!r}")

    def select(self, mailbox: str) -> None:
        status, data = self.client.select(encode_modified_utf7(mailbox), readonly=False)
        if status != "OK":
            raise RuntimeError(f"imap SELECT {mailbox!r} failed: {data!r}")

    def list_mailboxes(self) -> list[dict[str, Any]]:
        status, data = self.client.list()
        if status != "OK":
            raise RuntimeError(f"imap LIST failed: {data!r}")
        mailboxes = [parse_list_response(item) for item in data or [] if item]
        mailboxes = [item for item in mailboxes if item]
        return sorted(mailboxes, key=lambda item: item.get("display_name", "").lower())

    def resolve_mailbox(self, mailbox: str, prefer_sent: bool = False) -> dict[str, Any]:
        mailbox = (mailbox or "").strip()
        mailboxes = self.list_mailboxes()
        if prefer_sent and should_auto_resolve_sent(mailbox):
            sent = find_sent_mailbox(mailboxes)
            if sent:
                return sent
        if mailbox:
            for candidate in mailboxes:
                if candidate["name"] == mailbox or candidate["display_name"] == mailbox:
                    return candidate
            for candidate in mailboxes:
                if candidate["name"].lower() == mailbox.lower() or candidate["display_name"].lower() == mailbox.lower():
                    return candidate
        if prefer_sent:
            sent = find_sent_mailbox(mailboxes)
            if sent:
                return sent
        name = mailbox or "INBOX"
        return {"name": name, "display_name": decode_modified_utf7(name), "delimiter": "/", "flags": [], "selectable": True}

    def search_uids(self, filters: dict[str, Any]) -> list[str]:
        criteria = search_criteria(filters)
        status, data = self.client.uid("SEARCH", *criteria)
        if status != "OK":
            raise RuntimeError(f"imap SEARCH failed: {data!r}")
        raw = data[0] if data else b""
        if isinstance(raw, bytes):
            raw = raw.decode("ascii", "replace")
        return raw.split()

    def fetch_header(self, uid: str) -> bytes:
        return self.fetch(uid, "(BODY.PEEK[HEADER])")

    def fetch_message(self, uid: str) -> bytes:
        return self.fetch(uid, "(BODY.PEEK[])")

    def fetch(self, uid: str, query: str) -> bytes:
        status, data = self.client.uid("FETCH", uid, query)
        if status != "OK":
            raise RuntimeError(f"imap FETCH {uid} failed: {data!r}")
        for item in data:
            if isinstance(item, tuple) and isinstance(item[1], bytes):
                return item[1]
        raise RuntimeError(f"message {uid} body not found")

    def append(self, mailbox: str, raw: bytes) -> dict[str, Any]:
        resolved = self.resolve_mailbox(mailbox, prefer_sent=True)
        status, data = self.client.append(encode_modified_utf7(resolved["name"]), r"(\Seen)", None, raw)
        if status != "OK":
            raise RuntimeError(f"imap APPEND failed: {data!r}")
        return {
            "saved": True,
            "mailbox": resolved["name"],
            "mailbox_display_name": resolved.get("display_name") or decode_modified_utf7(resolved["name"]),
        }


def imap_connection(account: EmailSettings) -> IMAPClient:
    return IMAPClient(account)


def list_metadata_from_mailbox(client: IMAPClient, mailbox: dict[str, Any], filters: dict[str, Any]) -> list[dict[str, Any]]:
    client.select(mailbox["name"])
    uids = client.search_uids(filters)
    order = (filters.get("order") or "desc").lower()
    uids = sorted(uids, key=lambda item: int(item) if item.isdigit() else item, reverse=(order != "asc"))
    return [parse_header(uid, client.fetch_header(uid), mailbox) for uid in uids]


def parse_header(uid: str, raw: bytes, mailbox: dict[str, Any]) -> dict[str, Any]:
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    return metadata_from_message(uid, msg, mailbox)


def parse_message(uid: str, raw: bytes, mailbox: dict[str, Any]) -> dict[str, Any]:
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    metadata = metadata_from_message(uid, msg, mailbox)
    text_body = message_body(msg, "plain")
    html_body = message_body(msg, "html")
    metadata["attachments"] = attachment_metadata(msg)
    metadata.update({
        "body": text_body or html_body,
        "text_body": text_body,
        "html_body": html_body,
    })
    return metadata


def metadata_from_message(uid: str, msg: Message, mailbox: dict[str, Any]) -> dict[str, Any]:
    date = parse_date(msg.get("date"))
    return {
        "email_id": uid,
        "mailbox": mailbox["name"],
        "mailbox_display_name": mailbox.get("display_name") or mailbox["name"],
        "message_id": str(msg.get("message-id") or "").strip(),
        "subject": str(msg.get("subject") or ""),
        "sender": format_address_list(msg.get_all("from", []), first=True),
        "recipients": format_address_list(msg.get_all("to", []), first=False),
        "date": date.isoformat() if date else None,
        "attachments": [],
    }


def message_body(msg: Message, subtype: str) -> str:
    if isinstance(msg, EmailMessage):
        body = msg.get_body(preferencelist=(subtype,))
        if body:
            content = body.get_content()
            return content if isinstance(content, str) else str(content)
    parts = msg.walk() if msg.is_multipart() else [msg]
    chunks: list[str] = []
    for part in parts:
        if part.is_multipart() or part.get_content_maintype() != "text" or part.get_content_subtype() != subtype:
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            text = str(part.get_payload() or "")
        else:
            charset = part.get_content_charset() or "utf-8"
            text = payload.decode(charset, "replace")
        chunks.append(text)
    return "\n".join(chunks)


def attachment_metadata(msg: Message) -> list[dict[str, Any]]:
    out = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        filename = part.get_filename()
        disposition = part.get_content_disposition()
        if not filename and disposition != "attachment":
            continue
        payload = part.get_payload(decode=True) or b""
        out.append({
            "filename": filename or "attachment",
            "content_type": part.get_content_type(),
            "size": len(payload),
        })
    return out


def build_message(account: EmailSettings, data: dict[str, Any]) -> tuple[bytes, list[str]]:
    recipients = list(data.get("recipients") or [])
    cc = list(data.get("cc") or [])
    bcc = list(data.get("bcc") or [])
    all_recipients = recipients + cc + bcc
    if not all_recipients:
        raise ValueError("recipients are required")

    msg = EmailMessage()
    msg["From"] = f"{account.full_name} <{account.email_address}>" if account.full_name else account.email_address
    msg["To"] = ", ".join(recipients)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = data.get("subject", "")
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=account.email_address.split("@")[-1])
    if data.get("in_reply_to"):
        msg["In-Reply-To"] = data["in_reply_to"]
    if data.get("references"):
        msg["References"] = data["references"]
    if data.get("html"):
        msg.set_content("")
        msg.add_alternative(data.get("body", ""), subtype="html")
    else:
        msg.set_content(data.get("body", ""))
    for filename in data.get("attachments") or []:
        path = Path(filename).expanduser()
        msg.add_attachment(path.read_bytes(), maintype="application", subtype="octet-stream", filename=path.name)
    return msg.as_bytes(policy=policy.SMTP), all_recipients


def send_smtp(account: EmailSettings, raw: bytes, recipients: list[str]) -> None:
    context = ssl_context()
    if account.outgoing.ssl:
        with smtplib.SMTP_SSL(account.outgoing.host, account.outgoing.port, context=context) as smtp:
            smtp.login(account.outgoing.user_name, account.outgoing.password)
            smtp.sendmail(account.email_address, recipients, raw)
    else:
        with smtplib.SMTP(account.outgoing.host, account.outgoing.port) as smtp:
            if account.outgoing.start_ssl:
                smtp.starttls(context=context)
            smtp.login(account.outgoing.user_name, account.outgoing.password)
            smtp.sendmail(account.email_address, recipients, raw)


def save_sent(account: EmailSettings, raw: bytes) -> dict[str, Any]:
    with imap_connection(account) as client:
        return client.append(account.sent_folder_name, raw)


def search_criteria(filters: dict[str, Any]) -> list[str]:
    criteria = ["ALL"]
    since = parse_tool_time(filters.get("since") or "")
    before = parse_tool_time(filters.get("before") or "")
    if since:
        criteria.extend(["SINCE", since.strftime("%d-%b-%Y")])
    if before:
        criteria.extend(["BEFORE", before.strftime("%d-%b-%Y")])
    for key, imap_key in [("subject", "SUBJECT"), ("from_address", "FROM"), ("to_address", "TO")]:
        value = str(filters.get(key) or "").strip()
        if value:
            criteria.extend([imap_key, quote_imap(value)])
    for key, yes, no in [("seen", "SEEN", "UNSEEN"), ("answered", "ANSWERED", "UNANSWERED"), ("flagged", "FLAGGED", "UNFLAGGED")]:
        if key in filters and filters[key] is not None:
            criteria.append(yes if bool(filters[key]) else no)
    return criteria


def parse_tool_time(raw: str) -> datetime | None:
    raw = str(raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.strptime(raw, "%Y-%m-%d")


def quote_imap(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', r"\"") + '"'


def parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw)
    except Exception:
        return None


def format_address_list(headers: list[str], first: bool) -> str | list[str]:
    addresses = []
    for name, address in getaddresses(headers):
        if name:
            addresses.append(f"{name} <{address}>")
        elif address:
            addresses.append(address)
    if first:
        return addresses[0] if addresses else ""
    return addresses


def parse_list_response(raw: bytes | str) -> dict[str, Any] | None:
    line = raw.decode("ascii", "replace") if isinstance(raw, bytes) else raw
    if not line.upper().startswith("* LIST "):
        line = "* LIST " + line
    rest = line[len("* LIST "):].strip()
    if not rest.startswith("(") or ")" not in rest:
        return None
    flags_raw, rest = rest[1:].split(")", 1)
    flags = flags_raw.split()
    delimiter, rest = parse_imap_token(rest.strip())
    name, _ = parse_imap_token(rest.strip())
    if not name:
        return None
    display_name = decode_modified_utf7(name)
    return {
        "name": display_name,
        "display_name": display_name,
        "delimiter": delimiter,
        "flags": flags,
        "selectable": not has_flag(flags, r"\Noselect"),
    }


def parse_imap_token(raw: str) -> tuple[str, str]:
    if not raw:
        return "", ""
    if raw.upper().startswith("NIL") and (len(raw) == 3 or raw[3].isspace()):
        return "", raw[3:].strip()
    if raw[0] != '"':
        parts = raw.split(maxsplit=1)
        return parts[0], parts[1].strip() if len(parts) > 1 else ""
    out = []
    escaped = False
    for index, char in enumerate(raw[1:], start=1):
        if escaped:
            out.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            return "".join(out), raw[index + 1:].strip()
        else:
            out.append(char)
    return "", ""


def decode_modified_utf7(value: str) -> str:
    out = []
    index = 0
    while index < len(value):
        if value[index] != "&":
            out.append(value[index])
            index += 1
            continue
        end = value.find("-", index)
        if end == -1:
            out.append(value[index])
            index += 1
            continue
        if end == index + 1:
            out.append("&")
            index = end + 1
            continue
        chunk = value[index + 1:end].replace(",", "/")
        chunk += "=" * ((4 - len(chunk) % 4) % 4)
        try:
            out.append(base64.b64decode(chunk).decode("utf-16-be"))
        except Exception:
            out.append(value[index:end + 1])
        index = end + 1
    return "".join(out)


def encode_modified_utf7(value: str) -> str:
    if all(ord(ch) < 128 for ch in value) and "&" not in value:
        return value
    out = []
    buf = []

    def flush() -> None:
        if not buf:
            return
        raw = "".join(buf).encode("utf-16-be")
        encoded = base64.b64encode(raw).decode("ascii").rstrip("=").replace("/", ",")
        out.append("&" + encoded + "-")
        buf.clear()

    for char in value:
        code = ord(char)
        if 0x20 <= code <= 0x7E:
            flush()
            out.append("&-" if char == "&" else char)
        else:
            buf.append(char)
    flush()
    return "".join(out)


def has_flag(flags: list[str], expected: str) -> bool:
    return any(flag.lower() == expected.lower() for flag in flags)


def find_sent_mailbox(mailboxes: list[dict[str, Any]]) -> dict[str, Any] | None:
    for candidate in mailboxes:
        if has_flag(candidate.get("flags", []), r"\Sent"):
            return candidate
    for candidate in mailboxes:
        names = {candidate.get("name", "").lower(), candidate.get("display_name", "").lower()}
        if names & SENT_MAILBOX_NAMES:
            return candidate
    return None


def should_auto_resolve_sent(mailbox: str) -> bool:
    return not mailbox or mailbox.lower() in SENT_MAILBOX_NAMES


def ssl_context() -> ssl.SSLContext:
    context = ssl._create_unverified_context()
    return context


def email_settings_from_tool(raw: dict[str, Any]) -> EmailSettings:
    incoming = raw["incoming"]
    outgoing = raw["outgoing"]
    return EmailSettings(
        account_name=raw["account_name"],
        full_name=raw["full_name"],
        email_address=raw["email_address"],
        incoming=server_from_tool(incoming),
        outgoing=server_from_tool(outgoing),
        description=raw.get("description", ""),
        enable_attachment_download=bool(raw.get("enable_attachment_download", False)),
        save_to_sent=bool(raw.get("save_to_sent", True)),
        sent_folder_name=raw.get("sent_folder_name", ""),
    )


def server_from_tool(raw: dict[str, Any]) -> Any:
    from .config import ServerSettings

    return ServerSettings(
        user_name=raw["user_name"],
        password=raw["password"],
        host=raw["host"],
        port=int(raw["port"]),
        ssl=bool(raw.get("ssl", True)),
        start_ssl=bool(raw.get("start_ssl", False)),
        verify_ssl=bool(raw.get("verify_ssl", False)),
    )
