from __future__ import annotations

import base64
import ssl
import uuid
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any

from .config import CalDAVSettings, add_caldav_account, caldav_settings_from_dict, find_caldav_account, load_caldav_accounts


DAV_NS = "{DAV:}"
CAL_NS = "{urn:ietf:params:xml:ns:caldav}"


def list_caldav_accounts() -> dict[str, Any]:
    return {"result": [account.public() for account in load_caldav_accounts()]}


def add_account(caldav: dict[str, Any]) -> dict[str, str]:
    add_caldav_account(caldav_settings_from_dict(caldav))
    return {"result": "CalDAV account added successfully"}


def caldav_list_calendars(account_name: str = "", path: str = "", depth: int = 1) -> dict[str, Any]:
    client = CalDAVClient(find_caldav_account(account_name))
    xml = client.request("PROPFIND", path, headers={"Depth": str(depth)}, data=calendar_propfind_body())
    calendars = parse_calendar_propfind(xml, client.url(path))
    return {"calendars": calendars}


def caldav_list_events(
    account_name: str = "",
    calendar_path: str = "",
    start: str = "",
    end: str = "",
) -> dict[str, Any]:
    client = CalDAVClient(find_caldav_account(account_name))
    xml = client.request(
        "REPORT",
        calendar_path,
        headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
        data=calendar_query_body(start=start, end=end),
    )
    events = parse_calendar_query(xml, client.url(calendar_path))
    return {"events": events}


def caldav_get_event(account_name: str = "", path: str = "") -> dict[str, Any]:
    client = CalDAVClient(find_caldav_account(account_name))
    ics = client.request("GET", path).decode("utf-8", "replace")
    return {"path": path, "ics": ics, "event": parse_ics_event(ics)}


def caldav_put_event(account_name: str = "", path: str = "", ics: str = "", overwrite: bool = True) -> dict[str, str]:
    account = find_caldav_account(account_name)
    ensure_write_enabled(account)
    headers = {"Content-Type": "text/calendar; charset=utf-8"}
    if not overwrite:
        headers["If-None-Match"] = "*"
    CalDAVClient(account).request("PUT", path, data=ics.encode("utf-8"), headers=headers)
    return {"result": "CalDAV event saved successfully", "path": path}


def caldav_create_event(
    account_name: str = "",
    calendar_path: str = "",
    summary: str = "",
    start: str = "",
    end: str = "",
    description: str = "",
    location: str = "",
    uid: str = "",
) -> dict[str, str]:
    account = find_caldav_account(account_name)
    ensure_write_enabled(account)
    if not uid:
        uid = f"{uuid.uuid4()}@mcp-email-webdav-caldav"
    ics = build_event_ics(
        uid=uid,
        summary=summary,
        start=start,
        end=end,
        description=description,
        location=location,
    )
    filename = uid if uid.endswith(".ics") else f"{uid}.ics"
    path = join_remote_path(calendar_path, filename)
    CalDAVClient(account).request("PUT", path, data=ics.encode("utf-8"), headers={"Content-Type": "text/calendar; charset=utf-8"})
    return {"result": "CalDAV event created successfully", "path": path, "uid": uid}


def caldav_delete_event(account_name: str = "", path: str = "") -> dict[str, str]:
    account = find_caldav_account(account_name)
    ensure_write_enabled(account)
    CalDAVClient(account).request("DELETE", path)
    return {"result": "CalDAV event deleted successfully"}


class CalDAVClient:
    def __init__(self, account: CalDAVSettings):
        self.account = account
        self.base_url = account.base_url.rstrip("/") + "/"
        self.context = ssl.create_default_context() if account.verify_ssl else ssl._create_unverified_context()

    def url(self, path: str = "") -> str:
        path = (path or "").strip()
        if urllib.parse.urlsplit(path).scheme:
            raise ValueError("path must be relative to the configured CalDAV base_url")
        quoted = "/".join(urllib.parse.quote(part) for part in path.strip("/").split("/") if part)
        if path.endswith("/") and quoted:
            quoted += "/"
        return urllib.parse.urljoin(self.base_url, quoted)

    def request(
        self,
        method: str,
        path: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> bytes:
        request = urllib.request.Request(self.url(path), data=data, method=method.upper())
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        if self.account.user_name or self.account.password:
            token = f"{self.account.user_name}:{self.account.password}".encode("utf-8")
            request.add_header("Authorization", "Basic " + base64.b64encode(token).decode("ascii"))
        if data is not None and "Content-Length" not in request.headers:
            request.add_header("Content-Length", str(len(data)))
        try:
            with urllib.request.urlopen(request, context=self.context) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")
            raise RuntimeError(f"CalDAV {method.upper()} {path!r} failed: HTTP {error.code} {error.reason}: {detail}") from error


def ensure_write_enabled(account: CalDAVSettings) -> None:
    if not account.enable_write:
        raise ValueError("CalDAV write operations are disabled; set CALDAV_ENABLE_WRITE=True in constants.py")


def calendar_propfind_body() -> bytes:
    return b"""<?xml version="1.0" encoding="utf-8" ?>
<D:propfind xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:prop>
    <D:displayname/>
    <D:resourcetype/>
    <C:supported-calendar-component-set/>
    <C:calendar-description/>
    <C:calendar-timezone/>
  </D:prop>
</D:propfind>"""


def calendar_query_body(start: str = "", end: str = "") -> bytes:
    time_range = ""
    if start or end:
        attrs = []
        if start:
            attrs.append(f'start="{xml_escape(caldav_time(start))}"')
        if end:
            attrs.append(f'end="{xml_escape(caldav_time(end))}"')
        time_range = f"<C:time-range {' '.join(attrs)}/>"
    return f"""<?xml version="1.0" encoding="utf-8" ?>
<C:calendar-query xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:prop>
    <D:getetag/>
    <C:calendar-data/>
  </D:prop>
  <C:filter>
    <C:comp-filter name="VCALENDAR">
      <C:comp-filter name="VEVENT">
        {time_range}
      </C:comp-filter>
    </C:comp-filter>
  </C:filter>
</C:calendar-query>""".encode("utf-8")


def parse_calendar_propfind(raw: bytes, base_url: str) -> list[dict[str, Any]]:
    root = ET.fromstring(raw)
    calendars = []
    base_path = urllib.parse.urlsplit(base_url).path.rstrip("/") + "/"
    for response in root.findall(f"{DAV_NS}response"):
        href = text_of(response, f"{DAV_NS}href")
        prop = first_prop(response)
        if prop is None:
            continue
        is_calendar = prop.find(f"{DAV_NS}resourcetype/{CAL_NS}calendar") is not None
        if not is_calendar:
            continue
        href_path = urllib.parse.unquote(urllib.parse.urlsplit(href).path)
        rel_path = href_path[len(base_path):] if href_path.startswith(base_path) else href_path
        rel_path = rel_path.lstrip("/")
        calendars.append({
            "path": rel_path,
            "display_name": text_of(prop, f"{DAV_NS}displayname") or PurePosixPath(rel_path.rstrip("/")).name,
            "description": text_of(prop, f"{CAL_NS}calendar-description"),
            "components": supported_components(prop),
            "href": href,
        })
    return calendars


def parse_calendar_query(raw: bytes, base_url: str) -> list[dict[str, Any]]:
    root = ET.fromstring(raw)
    events = []
    base_path = urllib.parse.urlsplit(base_url).path.rstrip("/") + "/"
    for response in root.findall(f"{DAV_NS}response"):
        href = text_of(response, f"{DAV_NS}href")
        prop = first_prop(response)
        if prop is None:
            continue
        ics = text_of(prop, f"{CAL_NS}calendar-data")
        event = parse_ics_event(ics)
        href_path = urllib.parse.unquote(urllib.parse.urlsplit(href).path)
        rel_path = href_path[len(base_path):] if href_path.startswith(base_path) else href_path
        rel_path = rel_path.lstrip("/")
        events.append({
            "path": rel_path,
            "href": href,
            "etag": text_of(prop, f"{DAV_NS}getetag"),
            "ics": ics,
            "event": event,
        })
    return events


def parse_ics_event(ics: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    in_event = False
    for line in unfold_ics(ics):
        upper = line.upper()
        if upper == "BEGIN:VEVENT":
            in_event = True
            continue
        if upper == "END:VEVENT":
            break
        if not in_event or ":" not in line:
            continue
        name, value = line.split(":", 1)
        name = name.split(";", 1)[0].upper()
        if name in {"UID", "SUMMARY", "DTSTART", "DTEND", "DESCRIPTION", "LOCATION", "STATUS"}:
            fields[name.lower()] = unescape_ics(value)
    return fields


def unfold_ics(ics: str) -> list[str]:
    lines: list[str] = []
    for raw in ics.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        elif raw:
            lines.append(raw)
    return lines


def build_event_ics(
    uid: str,
    summary: str,
    start: str,
    end: str,
    description: str = "",
    location: str = "",
) -> str:
    if not summary:
        raise ValueError("summary is required")
    if not start:
        raise ValueError("start is required")
    dtstamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//mcp-email-webdav-caldav//caldav//EN",
        "BEGIN:VEVENT",
        f"UID:{escape_ics(uid)}",
        f"DTSTAMP:{dtstamp}",
        f"DTSTART:{caldav_time(start)}",
    ]
    if end:
        lines.append(f"DTEND:{caldav_time(end)}")
    lines.append(f"SUMMARY:{escape_ics(summary)}")
    if description:
        lines.append(f"DESCRIPTION:{escape_ics(description)}")
    if location:
        lines.append(f"LOCATION:{escape_ics(location)}")
    lines.extend(["END:VEVENT", "END:VCALENDAR", ""])
    return "\r\n".join(lines)


def caldav_time(raw: str) -> str:
    raw = str(raw or "").strip()
    if not raw:
        return ""
    if len(raw) == 8 and raw.isdigit():
        return raw
    if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
        return raw.replace("-", "") + "T000000Z"
    value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if value.tzinfo is None:
        return value.strftime("%Y%m%dT%H%M%S")
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def supported_components(prop: ET.Element) -> list[str]:
    components = []
    for comp in prop.findall(f"{CAL_NS}supported-calendar-component-set/{CAL_NS}comp"):
        name = comp.attrib.get("name")
        if name:
            components.append(name)
    return components


def first_prop(response: ET.Element) -> ET.Element | None:
    for propstat in response.findall(f"{DAV_NS}propstat"):
        status = text_of(propstat, f"{DAV_NS}status")
        if not status or " 200 " in status:
            return propstat.find(f"{DAV_NS}prop")
    return None


def text_of(element: ET.Element, path: str) -> str:
    found = element.find(path)
    if found is None or found.text is None:
        return ""
    return found.text.strip()


def join_remote_path(base: str, name: str) -> str:
    base = (base or "").strip("/")
    name = (name or "").strip("/")
    if not base:
        return name
    return f"{base}/{name}"


def escape_ics(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace(";", r"\;").replace(",", r"\,").replace("\n", r"\n")


def unescape_ics(value: str) -> str:
    return value.replace(r"\n", "\n").replace(r"\,", ",").replace(r"\;", ";").replace(r"\\", "\\")


def xml_escape(value: str) -> str:
    return value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
