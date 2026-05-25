import unittest

from mcp_email_webdav_caldav.config import WebDAVSettings
from mcp_email_webdav_caldav.webdav_service import WebDAVClient, parse_propfind


class WebDAVServiceTest(unittest.TestCase):
    def test_relative_url_quoting(self):
        client = WebDAVClient(WebDAVSettings(account_name="cloud", base_url="https://example.com/dav/root"))
        self.assertEqual(client.url("Папка/file name.txt"), "https://example.com/dav/root/%D0%9F%D0%B0%D0%BF%D0%BA%D0%B0/file%20name.txt")
        with self.assertRaises(ValueError):
            client.url("https://evil.example/file.txt")

    def test_parse_propfind(self):
        raw = b"""<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:">
  <D:response>
    <D:href>/dav/root/docs/</D:href>
    <D:propstat>
      <D:prop>
        <D:displayname>docs</D:displayname>
        <D:resourcetype><D:collection/></D:resourcetype>
      </D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
  <D:response>
    <D:href>/dav/root/docs/a.txt</D:href>
    <D:propstat>
      <D:prop>
        <D:displayname>a.txt</D:displayname>
        <D:getcontentlength>12</D:getcontentlength>
        <D:getcontenttype>text/plain</D:getcontenttype>
      </D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
</D:multistatus>"""
        items = parse_propfind(raw, "https://example.com/dav/root/")
        self.assertEqual(items[0]["path"], "docs/")
        self.assertTrue(items[0]["is_collection"])
        self.assertEqual(items[1]["path"], "docs/a.txt")
        self.assertEqual(items[1]["content_length"], 12)


if __name__ == "__main__":
    unittest.main()
