from __future__ import annotations

import json
import sys
from typing import Any, Callable

from . import __version__
from . import caldav_service, email_service, webdav_service


ToolHandler = Callable[..., dict[str, Any]]


def run_stdio() -> None:
    server = MCPServer()
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        response = server.handle_line(line)
        if response is None:
            continue
        sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
        sys.stdout.flush()


class MCPServer:
    def handle_line(self, line: str) -> dict[str, Any] | None:
        try:
            request = json.loads(line)
        except json.JSONDecodeError as error:
            return self.response(None, error={"code": -32700, "message": str(error)})
        if request.get("id") is None and str(request.get("method", "")).startswith("notifications/"):
            return None
        result, error = self.handle(request)
        if request.get("id") is None:
            return None
        return self.response(request.get("id"), result=result, error=error)

    def handle(self, request: dict[str, Any]) -> tuple[Any, dict[str, Any] | None]:
        method = request.get("method")
        try:
            if method == "initialize":
                return {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "mcp-email-webdav-caldav", "version": __version__},
                }, None
            if method == "ping":
                return {}, None
            if method == "tools/list":
                return {"tools": tools()}, None
            if method == "tools/call":
                params = request.get("params") or {}
                return tool_text(call_tool(params.get("name", ""), params.get("arguments") or {})), None
            if method in {"resources/list", "prompts/list"}:
                return {method.split("/", 1)[0]: []}, None
            return None, {"code": -32601, "message": "method not found"}
        except Exception as error:
            if method == "tools/call":
                return tool_error(str(error)), None
            return None, {"code": -32603, "message": str(error)}

    def response(self, request_id: Any, result: Any = None, error: dict[str, Any] | None = None) -> dict[str, Any]:
        response: dict[str, Any] = {"jsonrpc": "2.0"}
        if request_id is not None:
            response["id"] = request_id
        if error:
            response["error"] = error
        else:
            response["result"] = result
        return response


def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    handlers: dict[str, ToolHandler] = {
        "add_email_account": lambda **kwargs: email_service.add_account(kwargs["email"]),
        "list_available_accounts": lambda **_: email_service.list_available_accounts(),
        "list_mailboxes": email_service.list_mailboxes,
        "list_emails_metadata": email_service.list_emails_metadata,
        "get_emails_content": email_service.get_emails_content,
        "send_email": email_service.send_email,
        "delete_emails": email_service.delete_emails,
        "download_attachment": email_service.download_attachment,
        "add_webdav_account": lambda **kwargs: webdav_service.add_account(kwargs["webdav"]),
        "list_webdav_accounts": lambda **_: webdav_service.list_webdav_accounts(),
        "webdav_list": webdav_service.webdav_list,
        "webdav_get_text": webdav_service.webdav_get_text,
        "webdav_download_file": webdav_service.webdav_download_file,
        "webdav_put_text": webdav_service.webdav_put_text,
        "webdav_upload_file": webdav_service.webdav_upload_file,
        "webdav_mkdir": webdav_service.webdav_mkdir,
        "webdav_delete": webdav_service.webdav_delete,
        "webdav_move": webdav_service.webdav_move,
        "webdav_copy": webdav_service.webdav_copy,
        "add_caldav_account": lambda **kwargs: caldav_service.add_account(kwargs["caldav"]),
        "list_caldav_accounts": lambda **_: caldav_service.list_caldav_accounts(),
        "caldav_list_calendars": caldav_service.caldav_list_calendars,
        "caldav_list_events": caldav_service.caldav_list_events,
        "caldav_check_availability": caldav_service.caldav_check_availability,
        "caldav_get_event": caldav_service.caldav_get_event,
        "caldav_put_event": caldav_service.caldav_put_event,
        "caldav_create_event": caldav_service.caldav_create_event,
        "caldav_delete_event": caldav_service.caldav_delete_event,
    }
    if name not in handlers:
        raise ValueError(f"unknown tool {name!r}")
    return handlers[name](**arguments)


def tool_text(value: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, separators=(",", ":"))}]}


def tool_error(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "add_email_account",
            "description": "Add or update an email account configuration.",
            "inputSchema": object_schema({"email": email_settings_schema()}, ["email"]),
        },
        {
            "name": "list_available_accounts",
            "description": "List configured email accounts without exposing credentials.",
            "inputSchema": object_schema({}, []),
        },
        {
            "name": "list_mailboxes",
            "description": "List IMAP mailboxes/folders for an account, including the exact mailbox name to use in other tools.",
            "inputSchema": object_schema({"account_name": string_schema("The configured email account name.")}, ["account_name"]),
        },
        {
            "name": "list_emails_metadata",
            "description": "List email metadata from an IMAP mailbox. Returned email_id values are mailbox-scoped IMAP UIDs; pass the returned mailbox to content/delete/download tools.",
            "inputSchema": object_schema({
                "account_name": string_schema("The configured email account name."),
                "mailbox": string_schema("Mailbox returned by list_mailboxes. Defaults to INBOX."),
                "all_mailboxes": bool_schema("Search every selectable mailbox/folder."),
                "page": integer_schema("Page number starting from 1."),
                "page_size": integer_schema("Number of emails per page."),
                "order": enum_schema("Sort order.", ["asc", "desc"]),
                "since": string_schema("RFC3339 datetime or YYYY-MM-DD date."),
                "before": string_schema("RFC3339 datetime or YYYY-MM-DD date."),
                "subject": string_schema("Filter by subject."),
                "from_address": string_schema("Filter by sender."),
                "to_address": string_schema("Filter by recipient."),
                "seen": bool_schema("Filter by read status."),
                "answered": bool_schema("Filter by replied status."),
                "flagged": bool_schema("Filter by flagged status."),
            }, ["account_name"]),
        },
        {
            "name": "get_emails_content",
            "description": "Download and parse full email content by mailbox-scoped IMAP UID.",
            "inputSchema": object_schema({
                "account_name": string_schema("The configured email account name."),
                "email_ids": array_string_schema("IMAP UID values returned by list_emails_metadata."),
                "mailbox": string_schema("Mailbox returned by list_emails_metadata or list_mailboxes. Defaults to INBOX."),
            }, ["account_name", "email_ids"]),
        },
        {
            "name": "send_email",
            "description": "Send an email through SMTP.",
            "inputSchema": object_schema({
                "account_name": string_schema("The configured email account name."),
                "recipients": array_string_schema("Recipient email addresses."),
                "cc": array_string_schema("CC email addresses."),
                "bcc": array_string_schema("BCC email addresses."),
                "subject": string_schema("Email subject."),
                "body": string_schema("Email body."),
                "html": bool_schema("Send body as HTML."),
                "attachments": array_string_schema("Absolute paths to files to attach."),
                "in_reply_to": string_schema("Message-ID for threaded replies."),
                "references": string_schema("References header for threaded replies."),
            }, ["account_name", "recipients", "subject", "body"]),
        },
        {
            "name": "delete_emails",
            "description": "Mark emails as deleted and expunge them from an IMAP mailbox.",
            "inputSchema": object_schema({
                "account_name": string_schema("The configured email account name."),
                "email_ids": array_string_schema("IMAP UID values to delete."),
                "mailbox": string_schema("Mailbox returned by list_emails_metadata or list_mailboxes. Defaults to INBOX."),
            }, ["account_name", "email_ids"]),
        },
        {
            "name": "download_attachment",
            "description": "Download an attachment to a local path when attachment downloads are enabled.",
            "inputSchema": object_schema({
                "account_name": string_schema("The configured email account name."),
                "email_id": string_schema("IMAP UID value."),
                "attachment_name": string_schema("Attachment filename from email metadata."),
                "save_path": string_schema("Absolute path where the attachment should be saved."),
                "mailbox": string_schema("Mailbox returned by list_emails_metadata or list_mailboxes. Defaults to INBOX."),
            }, ["account_name", "email_id", "attachment_name", "save_path"]),
        },
        {
            "name": "add_webdav_account",
            "description": "Add or update a WebDAV account configuration.",
            "inputSchema": object_schema({"webdav": webdav_settings_schema()}, ["webdav"]),
        },
        {
            "name": "list_webdav_accounts",
            "description": "List configured WebDAV accounts without exposing credentials.",
            "inputSchema": object_schema({}, []),
        },
        {
            "name": "webdav_list",
            "description": "List a WebDAV directory using PROPFIND.",
            "inputSchema": object_schema({
                "account_name": string_schema("The configured WebDAV account name."),
                "path": string_schema("Remote path relative to the WebDAV base URL."),
                "depth": integer_schema("PROPFIND depth. Usually 1."),
            }, ["account_name", "path"]),
        },
        {
            "name": "webdav_get_text",
            "description": "Read a WebDAV file as text.",
            "inputSchema": object_schema({
                "account_name": string_schema("The configured WebDAV account name."),
                "path": string_schema("Remote file path relative to the WebDAV base URL."),
                "encoding": string_schema("Text encoding. Defaults to utf-8."),
            }, ["account_name", "path"]),
        },
        {
            "name": "webdav_download_file",
            "description": "Download a WebDAV file to a local path when downloads are enabled.",
            "inputSchema": object_schema({
                "account_name": string_schema("The configured WebDAV account name."),
                "path": string_schema("Remote file path relative to the WebDAV base URL."),
                "save_path": string_schema("Absolute local path where the file should be saved."),
            }, ["account_name", "path", "save_path"]),
        },
        {
            "name": "webdav_put_text",
            "description": "Upload text to a WebDAV path.",
            "inputSchema": object_schema({
                "account_name": string_schema("The configured WebDAV account name."),
                "path": string_schema("Remote file path relative to the WebDAV base URL."),
                "text": string_schema("Text content to upload."),
                "encoding": string_schema("Text encoding. Defaults to utf-8."),
            }, ["account_name", "path", "text"]),
        },
        {
            "name": "webdav_upload_file",
            "description": "Upload a local file to WebDAV when uploads are enabled.",
            "inputSchema": object_schema({
                "account_name": string_schema("The configured WebDAV account name."),
                "local_path": string_schema("Absolute local file path to upload."),
                "path": string_schema("Remote file path relative to the WebDAV base URL."),
            }, ["account_name", "local_path", "path"]),
        },
        {
            "name": "webdav_mkdir",
            "description": "Create a WebDAV directory using MKCOL.",
            "inputSchema": object_schema({
                "account_name": string_schema("The configured WebDAV account name."),
                "path": string_schema("Remote directory path relative to the WebDAV base URL."),
            }, ["account_name", "path"]),
        },
        {
            "name": "webdav_delete",
            "description": "Delete a WebDAV file or directory.",
            "inputSchema": object_schema({
                "account_name": string_schema("The configured WebDAV account name."),
                "path": string_schema("Remote path relative to the WebDAV base URL."),
            }, ["account_name", "path"]),
        },
        {
            "name": "webdav_move",
            "description": "Move or rename a WebDAV path.",
            "inputSchema": object_schema({
                "account_name": string_schema("The configured WebDAV account name."),
                "source_path": string_schema("Source remote path."),
                "destination_path": string_schema("Destination remote path."),
                "overwrite": bool_schema("Overwrite destination if it exists."),
            }, ["account_name", "source_path", "destination_path"]),
        },
        {
            "name": "webdav_copy",
            "description": "Copy a WebDAV path.",
            "inputSchema": object_schema({
                "account_name": string_schema("The configured WebDAV account name."),
                "source_path": string_schema("Source remote path."),
                "destination_path": string_schema("Destination remote path."),
                "overwrite": bool_schema("Overwrite destination if it exists."),
            }, ["account_name", "source_path", "destination_path"]),
        },
        {
            "name": "add_caldav_account",
            "description": "Add or update a CalDAV account configuration.",
            "inputSchema": object_schema({"caldav": caldav_settings_schema()}, ["caldav"]),
        },
        {
            "name": "list_caldav_accounts",
            "description": "List configured CalDAV accounts without exposing credentials.",
            "inputSchema": object_schema({}, []),
        },
        {
            "name": "caldav_list_calendars",
            "description": "List CalDAV calendars using PROPFIND.",
            "inputSchema": object_schema({
                "account_name": string_schema("The configured CalDAV account name."),
                "path": string_schema("Remote path relative to the discovered CalDAV calendar-home URL."),
                "depth": integer_schema("PROPFIND depth. Usually 1."),
            }, ["account_name"]),
        },
        {
            "name": "caldav_list_events",
            "description": "List CalDAV VEVENT items using calendar-query REPORT.",
            "inputSchema": object_schema({
                "account_name": string_schema("The configured CalDAV account name."),
                "calendar_path": string_schema("Calendar collection path returned by caldav_list_calendars, relative to the discovered CalDAV calendar-home URL."),
                "start": string_schema("Start of time range, RFC3339 or YYYY-MM-DD."),
                "end": string_schema("End of time range, RFC3339 or YYYY-MM-DD."),
            }, ["account_name", "calendar_path"]),
        },
        {
            "name": "caldav_get_event",
            "description": "Read a CalDAV event .ics resource.",
            "inputSchema": object_schema({
                "account_name": string_schema("The configured CalDAV account name."),
                "path": string_schema("Remote .ics path relative to the discovered CalDAV calendar-home URL."),
            }, ["account_name", "path"]),
        },
        {
            "name": "caldav_check_availability",
            "description": "Check whether a CalDAV calendar is busy during a requested time range.",
            "inputSchema": object_schema({
                "account_name": string_schema("The configured CalDAV account name."),
                "calendar_path": string_schema("Calendar collection path returned by caldav_list_calendars, relative to the discovered CalDAV calendar-home URL."),
                "start": string_schema("Requested start, RFC3339 or YYYY-MM-DD."),
                "end": string_schema("Requested end, RFC3339 or YYYY-MM-DD."),
                "use_free_busy": bool_schema("Try CalDAV free-busy-query first, then fall back to calendar-query. Defaults to true."),
            }, ["account_name", "calendar_path", "start", "end"]),
        },
        {
            "name": "caldav_put_event",
            "description": "Create or replace a CalDAV event from raw ICS when CalDAV writes are enabled.",
            "inputSchema": object_schema({
                "account_name": string_schema("The configured CalDAV account name."),
                "path": string_schema("Remote .ics path relative to the discovered CalDAV calendar-home URL."),
                "ics": string_schema("Raw iCalendar data."),
                "overwrite": bool_schema("Overwrite an existing event. Defaults to true."),
            }, ["account_name", "path", "ics"]),
        },
        {
            "name": "caldav_create_event",
            "description": "Create a simple CalDAV VEVENT when CalDAV writes are enabled.",
            "inputSchema": object_schema({
                "account_name": string_schema("The configured CalDAV account name."),
                "calendar_path": string_schema("Calendar collection path returned by caldav_list_calendars, relative to the discovered CalDAV calendar-home URL."),
                "summary": string_schema("Event title."),
                "start": string_schema("Event start, RFC3339 datetime or YYYYMMDD all-day date."),
                "end": string_schema("Event end, RFC3339 datetime or YYYYMMDD all-day date."),
                "description": string_schema("Event description."),
                "location": string_schema("Event location."),
                "attendees": array_string_schema("Attendees as email addresses or 'Name <email>' values."),
                "participants": array_string_schema("Alias for attendees; participants as email addresses or 'Name <email>' values."),
                "organizer": string_schema("Organizer as an email address or 'Name <email>'. Defaults to the CalDAV username."),
                "uid": string_schema("Optional event UID. Generated when omitted."),
            }, ["account_name", "calendar_path", "summary", "start"]),
        },
        {
            "name": "caldav_delete_event",
            "description": "Delete a CalDAV event when CalDAV writes are enabled.",
            "inputSchema": object_schema({
                "account_name": string_schema("The configured CalDAV account name."),
                "path": string_schema("Remote .ics path relative to the discovered CalDAV calendar-home URL."),
            }, ["account_name", "path"]),
        },
    ]


def email_settings_schema() -> dict[str, Any]:
    server = object_schema({
        "user_name": string_schema("Login username."),
        "password": string_schema("Password or app password."),
        "host": string_schema("Server host."),
        "port": integer_schema("Server port."),
        "ssl": bool_schema("Use implicit SSL/TLS."),
        "start_ssl": bool_schema("Use STARTTLS where supported."),
        "verify_ssl": bool_schema("Verify TLS certificates."),
    }, ["user_name", "password", "host", "port"])
    return object_schema({
        "account_name": string_schema("Account identifier."),
        "full_name": string_schema("Display name."),
        "email_address": string_schema("Email address."),
        "incoming": server,
        "outgoing": server,
        "description": string_schema("Optional account description."),
        "enable_attachment_download": bool_schema("Allow download_attachment to write files."),
        "save_to_sent": bool_schema("Save sent mail to an IMAP Sent folder."),
        "sent_folder_name": string_schema("Custom Sent folder name."),
    }, ["account_name", "full_name", "email_address", "incoming", "outgoing"])


def webdav_settings_schema() -> dict[str, Any]:
    return object_schema({
        "account_name": string_schema("Account identifier."),
        "base_url": string_schema("Base WebDAV URL."),
        "user_name": string_schema("WebDAV username."),
        "password": string_schema("WebDAV password or app password."),
        "description": string_schema("Optional account description."),
        "verify_ssl": bool_schema("Verify TLS certificates."),
        "enable_file_download": bool_schema("Allow webdav_download_file to write local files."),
        "enable_file_upload": bool_schema("Allow webdav_upload_file to read local files."),
    }, ["account_name", "base_url"])


def caldav_settings_schema() -> dict[str, Any]:
    return object_schema({
        "account_name": string_schema("Account identifier."),
        "base_url": string_schema("Base CalDAV URL. The client discovers the calendar-home URL through /.well-known/caldav when enabled."),
        "user_name": string_schema("CalDAV username."),
        "password": string_schema("CalDAV password or app password."),
        "description": string_schema("Optional account description."),
        "verify_ssl": bool_schema("Verify TLS certificates."),
        "enable_write": bool_schema("Allow CalDAV PUT/DELETE event operations."),
    }, ["account_name", "base_url"])


def object_schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def string_schema(description: str) -> dict[str, str]:
    return {"type": "string", "description": description}


def integer_schema(description: str) -> dict[str, str]:
    return {"type": "integer", "description": description}


def bool_schema(description: str) -> dict[str, str]:
    return {"type": "boolean", "description": description}


def enum_schema(description: str, values: list[str]) -> dict[str, Any]:
    return {"type": "string", "description": description, "enum": values}


def array_string_schema(description: str) -> dict[str, Any]:
    return {"type": "array", "description": description, "items": {"type": "string"}}
