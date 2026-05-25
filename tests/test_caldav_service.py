import unittest

from mcp_email_webdav_caldav.caldav_service import (
    CalDAVClient,
    build_event_ics,
    caldav_time,
    parse_calendar_propfind,
    parse_calendar_query,
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


if __name__ == "__main__":
    unittest.main()
