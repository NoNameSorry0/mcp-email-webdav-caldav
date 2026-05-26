import unittest

from mcp_email_webdav_caldav.email_service import IMAPClient, decode_modified_utf7, encode_modified_utf7, parse_list_response


class EmailServiceTest(unittest.TestCase):
    def test_decode_modified_utf7(self):
        self.assertEqual(decode_modified_utf7("&BEIENQRBBEI-"), "тест")
        self.assertEqual(decode_modified_utf7("&BB4EQgQ,BEAEMAQyBDsENQQ9BD0ESwQ1-"), "Отправленные")
        self.assertEqual(decode_modified_utf7("Archive"), "Archive")
        self.assertEqual(decode_modified_utf7("A&-B"), "A&B")

    def test_encode_modified_utf7(self):
        self.assertEqual(encode_modified_utf7("INBOX"), "INBOX")
        self.assertEqual(encode_modified_utf7("тест"), "&BEIENQRBBEI-")
        self.assertEqual(encode_modified_utf7("A&B"), "A&-B")

    def test_parse_list_response(self):
        mailbox = parse_list_response(b'(\\Sent \\HasNoChildren) "/" "&BB4EQgQ,BEAEMAQyBDsENQQ9BD0ESwQ1-"')
        self.assertIsNotNone(mailbox)
        self.assertEqual(mailbox["name"], "Отправленные")
        self.assertEqual(mailbox["display_name"], "Отправленные")
        self.assertEqual(mailbox["delimiter"], "/")
        self.assertTrue(mailbox["selectable"])
        self.assertIn("\\Sent", mailbox["flags"])

    def test_parse_noselect_response(self):
        mailbox = parse_list_response(b'(\\Noselect) "/" "Projects"')
        self.assertIsNotNone(mailbox)
        self.assertFalse(mailbox["selectable"])

    def test_resolve_sent_prefers_sent_flag_for_auto_sent_name(self):
        client = IMAPClient.__new__(IMAPClient)
        client.list_mailboxes = lambda: [
            {"name": "Sent", "display_name": "Sent", "delimiter": "/", "flags": [], "selectable": True},
            {
                "name": "Отправленные",
                "display_name": "Отправленные",
                "delimiter": "/",
                "flags": [r"\Sent"],
                "selectable": True,
            },
        ]

        mailbox = client.resolve_mailbox("Sent", prefer_sent=True)

        self.assertEqual(mailbox["display_name"], "Отправленные")
        self.assertIn(r"\Sent", mailbox["flags"])

    def test_resolve_sent_uses_localized_name_without_sent_flag(self):
        client = IMAPClient.__new__(IMAPClient)
        client.list_mailboxes = lambda: [
            {"name": "Archive", "display_name": "Archive", "delimiter": "/", "flags": [], "selectable": True},
            {
                "name": "Отправленные",
                "display_name": "Отправленные",
                "delimiter": "/",
                "flags": [],
                "selectable": True,
            },
        ]

        mailbox = client.resolve_mailbox("", prefer_sent=True)

        self.assertEqual(mailbox["display_name"], "Отправленные")


if __name__ == "__main__":
    unittest.main()
