import io
import unittest
from email.message import Message
from urllib.error import HTTPError
from unittest.mock import patch

from mcp_email_webdav_caldav.caldav_service import (
    CalDAVClient,
    build_event_ics,
    caldav_check_availability,
    caldav_time,
    current_user_principal_body,
    free_busy_query_body,
    parse_calendar_propfind,
    parse_calendar_query,
    parse_free_busy,
    parse_ics_event,
)
from mcp_email_webdav_caldav.config import CalDAVSettings


class CalDAVServiceTest(unittest.TestCase):
    def test_relative_url_quoting(self):
        client = CalDAVClient(CalDAVSettings(account_name="calendar", base_url="https://example.com/caldav/root"))
        self.assertEqual(client.url("Работа/main.ics"), "https://example.com/caldav/root/%D0%A0%D0%B0%D0%B1%D0%BE%D1%82%D0%B0/main.ics")
        with self.assertRaises(ValueError):
            client.url("https://evil.example/event.ics")

    def test_parse_calendar_propfind(self):
        raw = b"""<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:response>
    <D:href>/caldav/root/work/</D:href>
    <D:propstat>
      <D:prop>
        <D:displayname>Work</D:displayname>
        <D:resourcetype><D:collection/><C:calendar/></D:resourcetype>
        <C:calendar-description>Work calendar</C:calendar-description>
        <C:supported-calendar-component-set><C:comp name="VEVENT"/></C:supported-calendar-component-set>
      </D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
</D:multistatus>"""
        calendars = parse_calendar_propfind(raw, "https://example.com/caldav/root/")
        self.assertEqual(calendars[0]["path"], "work/")
        self.assertEqual(calendars[0]["display_name"], "Work")
        self.assertEqual(calendars[0]["components"], ["VEVENT"])

    def test_parse_calendar_query(self):
        raw = b"""<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:response>
    <D:href>/caldav/root/work/event-1.ics</D:href>
    <D:propstat>
      <D:prop>
        <D:getetag>"abc"</D:getetag>
        <C:calendar-data>BEGIN:VCALENDAR&#13;
BEGIN:VEVENT&#13;
UID:1@example.com&#13;
DTSTART:20260525T090000Z&#13;
SUMMARY:Planning&#13;
END:VEVENT&#13;
END:VCALENDAR</C:calendar-data>
      </D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
</D:multistatus>"""
        events = parse_calendar_query(raw, "https://example.com/caldav/root/work/")
        self.assertEqual(events[0]["path"], "event-1.ics")
        self.assertEqual(events[0]["etag"], '"abc"')
        self.assertEqual(events[0]["event"]["summary"], "Planning")

    def test_parse_calendar_query_relative_to_calendar_home(self):
        raw = b"""<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:response>
    <D:href>/principals/mail.ru/geraldy12319/calendars/personal/event-1.ics</D:href>
    <D:propstat>
      <D:prop>
        <D:getetag>"abc"</D:getetag>
        <C:calendar-data>BEGIN:VCALENDAR&#13;
BEGIN:VEVENT&#13;
UID:1@example.com&#13;
SUMMARY:Planning&#13;
END:VEVENT&#13;
END:VCALENDAR</C:calendar-data>
      </D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
</D:multistatus>"""
        events = parse_calendar_query(
            raw,
            "https://calendar.mail.ru/principals/mail.ru/geraldy12319/calendars/personal/",
            root_url="https://calendar.mail.ru/principals/mail.ru/geraldy12319/calendars/",
        )
        self.assertEqual(events[0]["path"], "personal/event-1.ics")

    def test_discovers_mail_ru_calendar_home_before_listing_calendars(self):
        account = CalDAVSettings(
            account_name="calendar",
            base_url="https://calendar.mail.ru/",
            user_name="geraldy12319@mail.ru",
            password="token",
            verify_ssl=False,
        )
        calls = []

        def fake_urlopen(request, context):
            calls.append((request.get_method(), request.full_url, dict(request.header_items())))
            if request.full_url == "https://calendar.mail.ru/.well-known/caldav":
                headers = Message()
                headers.add_header("Location", "/dav/")
                raise HTTPError(request.full_url, 301, "Moved Permanently", headers, io.BytesIO(b""))
            if request.full_url == "https://calendar.mail.ru/dav/":
                self.assertEqual(request.data, current_user_principal_body())
                return FakeResponse(b"""<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:">
  <D:response>
    <D:href>/dav/</D:href>
    <D:propstat>
      <D:prop>
        <D:current-user-principal>
          <D:href>/principals/mail.ru/geraldy12319/</D:href>
        </D:current-user-principal>
      </D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
</D:multistatus>""", request.full_url)
            if request.full_url == "https://calendar.mail.ru/principals/mail.ru/geraldy12319/":
                return FakeResponse(b"""<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:response>
    <D:href>/principals/mail.ru/geraldy12319/</D:href>
    <D:propstat>
      <D:prop>
        <C:calendar-home-set>
          <D:href>/principals/mail.ru/geraldy12319/calendars/</D:href>
        </C:calendar-home-set>
      </D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
</D:multistatus>""", request.full_url)
            if request.full_url == "https://calendar.mail.ru/principals/mail.ru/geraldy12319/calendars/":
                return FakeResponse(b"""<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:response>
    <D:href>/principals/mail.ru/geraldy12319/calendars/personal/</D:href>
    <D:propstat>
      <D:prop>
        <D:displayname>Personal</D:displayname>
        <D:resourcetype><D:collection/><C:calendar/></D:resourcetype>
      </D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
</D:multistatus>""", request.full_url)
            raise AssertionError(f"unexpected CalDAV URL {request.full_url}")

        with patch("mcp_email_webdav_caldav.caldav_service.urllib.request.urlopen", side_effect=fake_urlopen):
            client = CalDAVClient(account)
            xml = client.request("PROPFIND", "", headers={"Depth": "1"}, data=b"<propfind/>")
            calendars = parse_calendar_propfind(xml, client.calendar_url(""))

        self.assertEqual(calendars[0]["path"], "personal/")
        self.assertEqual(client.calendar_home_url(), "https://calendar.mail.ru/principals/mail.ru/geraldy12319/calendars/")
        self.assertEqual([url for _, url, _ in calls], [
            "https://calendar.mail.ru/.well-known/caldav",
            "https://calendar.mail.ru/dav/",
            "https://calendar.mail.ru/principals/mail.ru/geraldy12319/",
            "https://calendar.mail.ru/principals/mail.ru/geraldy12319/calendars/",
        ])

    def test_create_event_ics_includes_organizer_and_attendees(self):
        ics = build_event_ics(
            uid="uid@example.com",
            summary="Demo",
            start="2026-05-25T09:00:00+03:00",
            end="2026-05-25T10:00:00+03:00",
            organizer="Jane Doe <jane@example.com>",
            attendees=["John Smith <john@example.com>", "team@example.com"],
        )
        self.assertIn("ORGANIZER;CN=Jane Doe:mailto:jane@example.com", ics)
        self.assertIn("ATTENDEE;CN=John Smith;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:john@example.com", ics)
        self.assertIn("ATTENDEE;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:team@example.com", ics)

        event = parse_ics_event(ics)
        self.assertEqual(event["organizer"]["email"], "jane@example.com")
        self.assertEqual(event["organizer"]["name"], "Jane Doe")
        self.assertEqual(event["attendees"][0]["email"], "john@example.com")
        self.assertEqual(event["attendees"][0]["name"], "John Smith")
        self.assertTrue(event["attendees"][0]["rsvp"])

    def test_create_event_accepts_participants_alias(self):
        captured: dict[str, bytes] = {}

        def fake_request(self, method, path, data=None, headers=None):
            captured["data"] = data or b""
            return b""

        account = CalDAVSettings(
            account_name="calendar",
            base_url="https://example.com/caldav/",
            user_name="jane@example.com",
            password="token",
            enable_write=True,
        )

        with patch("mcp_email_webdav_caldav.caldav_service.find_caldav_account", return_value=account):
            with patch.object(CalDAVClient, "request", fake_request):
                from mcp_email_webdav_caldav.caldav_service import caldav_create_event

                caldav_create_event(
                    account_name="calendar",
                    calendar_path="work/",
                    summary="Demo",
                    start="2026-05-25T09:00:00+03:00",
                    participants=["Alex Example <alex@example.com>"],
                )

        ics = captured["data"].decode("utf-8")
        self.assertIn("ATTENDEE;CN=Alex Example;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:alex@example.com", ics)

    def test_parse_free_busy(self):
        busy = parse_free_busy("""BEGIN:VCALENDAR
BEGIN:VFREEBUSY
FREEBUSY;FBTYPE=BUSY:20260525T060000Z/20260525T070000Z,20260525T080000Z/20260525T083000Z
END:VFREEBUSY
END:VCALENDAR
""")
        self.assertEqual(busy, [
            {"start": "20260525T060000Z", "end": "20260525T070000Z", "type": "BUSY"},
            {"start": "20260525T080000Z", "end": "20260525T083000Z", "type": "BUSY"},
        ])

    def test_check_availability_uses_free_busy_query(self):
        account = CalDAVSettings(
            account_name="calendar",
            base_url="https://example.com/caldav/",
            user_name="jane@example.com",
            password="token",
        )
        calls = []

        def fake_urlopen(request, context):
            calls.append((request.get_method(), request.full_url, request.data))
            self.assertEqual(request.full_url, "https://example.com/caldav/work/")
            self.assertEqual(request.data, free_busy_query_body("2026-05-25T09:00:00+03:00", "2026-05-25T10:00:00+03:00"))
            return FakeResponse(b"""BEGIN:VCALENDAR
BEGIN:VFREEBUSY
FREEBUSY:20260525T060000Z/20260525T070000Z
END:VFREEBUSY
END:VCALENDAR
""", request.full_url)

        with patch("mcp_email_webdav_caldav.caldav_service.find_caldav_account", return_value=account):
            with patch("mcp_email_webdav_caldav.caldav_service.constants.CALDAV_USE_WELL_KNOWN_DISCOVERY", False):
                with patch("mcp_email_webdav_caldav.caldav_service.urllib.request.urlopen", side_effect=fake_urlopen):
                    result = caldav_check_availability(
                        account_name="calendar",
                        calendar_path="work/",
                        start="2026-05-25T09:00:00+03:00",
                        end="2026-05-25T10:00:00+03:00",
                    )

        self.assertFalse(result["available"])
        self.assertEqual(result["source"], "free_busy_query")
        self.assertEqual(result["busy"][0]["start"], "20260525T060000Z")
        self.assertEqual(len(calls), 1)

    def test_ics_helpers(self):
        ics = build_event_ics(
            uid="uid@example.com",
            summary="Demo, planning",
            start="2026-05-25T09:00:00+03:00",
            end="2026-05-25T10:00:00+03:00",
            description="Line 1\nLine 2",
            location="Room 1",
        )
        event = parse_ics_event(ics)
        self.assertEqual(event["uid"], "uid@example.com")
        self.assertEqual(event["summary"], "Demo, planning")
        self.assertEqual(event["dtstart"], "20260525T060000Z")
        self.assertEqual(event["description"], "Line 1\nLine 2")
        self.assertEqual(caldav_time("2026-05-25"), "20260525T000000Z")
        self.assertEqual(caldav_time("20260525"), "20260525")

class FakeResponse:
    def __init__(self, body: bytes, url: str):
        self.body = body
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def read(self):
        return self.body

    def geturl(self):
        return self.url


if __name__ == "__main__":
    unittest.main()
