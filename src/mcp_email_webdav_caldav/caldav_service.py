from __future__ import annotations

import base64
import ssl
import uuid
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parseaddr
from pathlib import PurePosixPath
from typing import Any

from . import constants
from .config import CalDAVSettings, add_caldav_account, caldav_settings_from_dict, find_caldav_account, load_caldav_accounts


DAV_NS = "{DAV:}"
CAL_NS = "{urn:ietf:params:xml:ns:caldav}"
REDIRECT_CODES = {301, 302, 303, 307, 308}
DISCOVERY_FALLBACK_CODES = {404, 405, 501}


def list_caldav_accounts() -> dict[str, Any]:
    return {"result": [account.public() for account in load_caldav_accounts()]}


def add_account(caldav: dict[str, Any]) -> dict[str, str]:
    add_caldav_account(caldav_settings_from_dict(caldav))
    return {"result": "CalDAV account added successfully"}


def caldav_list_calendars(account_name: str = "", path: str = "", depth: int = 1) -> dict[str, Any]:
    client = CalDAVClient(find_caldav_account(account_name))
    xml = client.request("PROPFIND", path, headers={"Depth": str(depth)}, data=calendar_propfind_body())
    calendars = parse_calendar_propfind(xml, client.calendar_url(path))
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
    events = parse_calendar_query(xml, client.calendar_url(calendar_path), root_url=client.calendar_home_url())
    return {"events": events}


def caldav_check_availability(
    account_name: str = "",
    calendar_path: str = "",
    start: str = "",
    end: str = "",
    use_free_busy: bool = True,
) -> dict[str, Any]:
    if not calendar_path:
        raise ValueError("calendar_path is required")
    if not start:
        raise ValueError("start is required")
    if not end:
        raise ValueError("end is required")

    client = CalDAVClient(find_caldav_account(account_name))
    if use_free_busy:
        try:
            raw = client.request(
                "REPORT",
                calendar_path,
                headers={"Depth": "0", "Content-Type": "application/xml; charset=utf-8"},
                data=free_busy_query_body(start=start, end=end),
            )
            busy = parse_free_busy(raw.decode("utf-8", "replace"))
            return availability_result(calendar_path, start, end, busy, "free_busy_query")
        except (CalDAVHTTPError, ValueError):
            pass

    xml = client.request(
        "REPORT",
        calendar_path,
        headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
        data=calendar_query_body(start=start, end=end),
    )
    events = parse_calendar_query(xml, client.calendar_url(calendar_path), root_url=client.calendar_home_url())
    busy = busy_from_events(events)
    return availability_result(calendar_path, start, end, busy, "calendar_query")


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
    attendees: list[str] | None = None,
    participants: list[str] | None = None,
    organizer: str = "",
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
        attendees=[*(attendees or []), *(participants or [])],
        organizer=organizer or account.user_name,
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
        self._calendar_home_url: str | None = None
        self._principal_url: str | None = None
        self._discovery_url: str | None = None

    def url(self, path: str = "", base_url: str | None = None) -> str:
        path = (path or "").strip()
        if urllib.parse.urlsplit(path).scheme:
            raise ValueError("path must be relative to the configured CalDAV base_url")
        base = (base_url or self.base_url).rstrip("/") + "/"
        quoted = "/".join(urllib.parse.quote(part) for part in path.strip("/").split("/") if part)
        if path.endswith("/") and quoted:
            quoted += "/"
        return urllib.parse.urljoin(base, quoted)

    def calendar_url(self, path: str = "") -> str:
        return self.url(path, base_url=self.calendar_home_url())

    def calendar_home_url(self) -> str:
        if self._calendar_home_url:
            return self._calendar_home_url
        if not constants.CALDAV_USE_WELL_KNOWN_DISCOVERY:
            self._calendar_home_url = self.base_url
            return self._calendar_home_url
        try:
            self._calendar_home_url = self.discover_calendar_home_url()
        except CalDAVHTTPError as error:
            if error.code not in DISCOVERY_FALLBACK_CODES:
                raise
            self._calendar_home_url = self.base_url
        except (ET.ParseError, ValueError):
            self._calendar_home_url = self.base_url
        return self._calendar_home_url

    def discover_calendar_home_url(self) -> str:
        well_known_url = self.origin_url(constants.CALDAV_WELL_KNOWN_PATH)
        raw, discovery_url = self.request_url(
            "PROPFIND",
            well_known_url,
            headers={"Depth": "0", "Content-Type": "application/xml; charset=utf-8"},
            data=current_user_principal_body(),
        )
        self._discovery_url = discovery_url
        principal_href = prop_href(raw, f"{DAV_NS}current-user-principal")
        if not principal_href:
            raise ValueError("CalDAV current-user-principal was not found during discovery")

        principal_url = normalize_collection_url(resolve_href(discovery_url, principal_href))
        self._principal_url = principal_url
        raw, principal_url = self.request_url(
            "PROPFIND",
            principal_url,
            headers={"Depth": "0", "Content-Type": "application/xml; charset=utf-8"},
            data=calendar_home_set_body(),
        )
        self._principal_url = principal_url
        home_href = prop_href(raw, f"{CAL_NS}calendar-home-set")
        if not home_href:
            return normalize_collection_url(urllib.parse.urljoin(principal_url.rstrip("/") + "/", "calendars/"))
        return normalize_collection_url(resolve_href(principal_url, home_href))

    def origin_url(self, path: str) -> str:
        parsed = urllib.parse.urlsplit(self.base_url)
        origin = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "/", "", ""))
        return urllib.parse.urljoin(origin, path.lstrip("/"))

    def request(
        self,
        method: str,
        path: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> bytes:
        raw, _ = self.request_url(method, self.calendar_url(path), data=data, headers=headers)
        return raw

    def request_url(
        self,
        method: str,
        url: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        max_redirects: int = 10,
    ) -> tuple[bytes, str]:
        method = method.upper()
        for _ in range(max_redirects + 1):
            request = urllib.request.Request(url, data=data, method=method)
            for key, value in (headers or {}).items():
                request.add_header(key, value)
            if self.account.user_name or self.account.password:
                token = f"{self.account.user_name}:{self.account.password}".encode("utf-8")
                request.add_header("Authorization", "Basic " + base64.b64encode(token).decode("ascii"))
            if data is not None and "Content-Length" not in request.headers:
                request.add_header("Content-Length", str(len(data)))
            try:
                with urllib.request.urlopen(request, context=self.context) as response:
                    return response.read(), response.geturl()
            except urllib.error.HTTPError as error:
                if error.code in REDIRECT_CODES and error.headers.get("Location"):
                    error.read()
                    error.close()
                    url = urllib.parse.urljoin(url, error.headers["Location"])
                    continue
                detail = error.read().decode("utf-8", "replace")
                error.close()
                raise CalDAVHTTPError(method, url, error.code, error.reason, detail) from error
        raise RuntimeError(f"CalDAV {method} {url!r} failed: too many redirects")


class CalDAVHTTPError(RuntimeError):
    def __init__(self, method: str, url: str, code: int, reason: str, detail: str):
        self.method = method
        self.url = url
        self.code = code
        self.reason = reason
        self.detail = detail
        super().__init__(f"CalDAV {method} {url!r} failed: HTTP {code} {reason}: {detail}")


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


def current_user_principal_body() -> bytes:
    return b"""<?xml version="1.0" encoding="utf-8" ?>
<D:propfind xmlns:D="DAV:">
  <D:prop>
    <D:current-user-principal/>
  </D:prop>
</D:propfind>"""


def calendar_home_set_body() -> bytes:
    return b"""<?xml version="1.0" encoding="utf-8" ?>
<D:propfind xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:prop>
    <C:calendar-home-set/>
  </D:prop>
</D:propfind>"""


def free_busy_query_body(start: str = "", end: str = "") -> bytes:
    return f"""<?xml version="1.0" encoding="utf-8" ?>
<C:free-busy-query xmlns:C="urn:ietf:params:xml:ns:caldav">
  <C:time-range start="{xml_escape(caldav_time(start))}" end="{xml_escape(caldav_time(end))}"/>
</C:free-busy-query>""".encode("utf-8")


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


def parse_calendar_query(raw: bytes, base_url: str, root_url: str | None = None) -> list[dict[str, Any]]:
    root = ET.fromstring(raw)
    events = []
    base_path = urllib.parse.urlsplit(root_url or base_url).path.rstrip("/") + "/"
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


def parse_ics_event(ics: str) -> dict[str, Any]:
    fields: dict[str, Any] = {}
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
        name, params, value = parse_ics_property(line)
        if name in {"UID", "SUMMARY", "DTSTART", "DTEND", "DESCRIPTION", "LOCATION", "STATUS", "TRANSP"}:
            fields[name.lower()] = unescape_ics(value)
        elif name == "ORGANIZER":
            fields["organizer"] = attendee_value(value, params)
        elif name == "ATTENDEE":
            fields.setdefault("attendees", []).append(attendee_value(value, params))
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
    attendees: list[str] | None = None,
    organizer: str = "",
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
    if organizer:
        lines.append(organizer_line(organizer))
    for attendee in attendees or []:
        lines.append(attendee_line(attendee))
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


def availability_result(calendar_path: str, start: str, end: str, busy: list[dict[str, Any]], source: str) -> dict[str, Any]:
    return {
        "available": not busy,
        "calendar_path": calendar_path,
        "start": start,
        "end": end,
        "source": source,
        "busy": busy,
    }


def busy_from_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    busy = []
    for item in events:
        event = item.get("event") or {}
        if str(event.get("status", "")).upper() == "CANCELLED":
            continue
        if str(event.get("transp", "")).upper() == "TRANSPARENT":
            continue
        busy.append({
            "path": item.get("path", ""),
            "uid": event.get("uid", ""),
            "summary": event.get("summary", ""),
            "start": event.get("dtstart", ""),
            "end": event.get("dtend", ""),
            "status": event.get("status", ""),
        })
    return busy


def parse_free_busy(ics: str) -> list[dict[str, Any]]:
    if "BEGIN:VCALENDAR" not in ics.upper():
        raise ValueError("CalDAV free-busy response is not an iCalendar object")
    busy = []
    in_freebusy = False
    for line in unfold_ics(ics):
        upper = line.upper()
        if upper == "BEGIN:VFREEBUSY":
            in_freebusy = True
            continue
        if upper == "END:VFREEBUSY":
            break
        if not in_freebusy or ":" not in line:
            continue
        name, params, value = parse_ics_property(line)
        if name != "FREEBUSY":
            continue
        fbtype = params.get("FBTYPE", "BUSY")
        for period in value.split(","):
            if "/" not in period:
                continue
            period_start, period_end = period.split("/", 1)
            busy.append({"start": period_start, "end": period_end, "type": fbtype})
    return busy


def parse_ics_property(line: str) -> tuple[str, dict[str, str], str]:
    raw_name, value = line.split(":", 1)
    parts = raw_name.split(";")
    name = parts[0].upper()
    params: dict[str, str] = {}
    for raw_param in parts[1:]:
        if "=" not in raw_param:
            continue
        key, raw_value = raw_param.split("=", 1)
        params[key.upper()] = raw_value.strip('"')
    return name, params, value


def organizer_line(organizer: str) -> str:
    name, email = contact_parts(organizer)
    params = f";CN={ics_param_value(name)}" if name else ""
    return f"ORGANIZER{params}:mailto:{email}"


def attendee_line(attendee: str) -> str:
    name, email = contact_parts(attendee)
    params = ["ROLE=REQ-PARTICIPANT", "PARTSTAT=NEEDS-ACTION", "RSVP=TRUE"]
    if name:
        params.insert(0, f"CN={ics_param_value(name)}")
    return f"ATTENDEE;{';'.join(params)}:mailto:{email}"


def attendee_value(value: str, params: dict[str, str]) -> dict[str, Any]:
    email = value[7:] if value.lower().startswith("mailto:") else value
    return {
        "email": email,
        "name": params.get("CN", ""),
        "role": params.get("ROLE", ""),
        "partstat": params.get("PARTSTAT", ""),
        "rsvp": params.get("RSVP", "").upper() == "TRUE",
    }


def contact_parts(value: str) -> tuple[str, str]:
    value = str(value or "").strip()
    name, email = parseaddr(value)
    if not email:
        email = value[7:] if value.lower().startswith("mailto:") else value
        name = ""
    if not email:
        raise ValueError("attendee email is required")
    return name.strip(), email.strip()


def ics_param_value(value: str) -> str:
    escaped = str(value).replace("\\", "\\\\").replace('"', r"\"")
    if any(char in escaped for char in (";", ":", ",")):
        return f'"{escaped}"'
    return escaped


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


def prop_href(raw: bytes, prop_name: str) -> str:
    root = ET.fromstring(raw)
    for response in root.findall(f"{DAV_NS}response"):
        prop = first_prop(response)
        if prop is None:
            continue
        container = prop.find(prop_name)
        if container is None:
            continue
        href = text_of(container, f"{DAV_NS}href")
        if href:
            return href
    return ""


def resolve_href(base_url: str, href: str) -> str:
    return urllib.parse.urljoin(base_url, href)


def normalize_collection_url(url: str) -> str:
    return url.rstrip("/") + "/"


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
