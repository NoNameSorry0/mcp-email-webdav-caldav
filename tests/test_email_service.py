import unittest

from mcp_email_webdav_caldav.email_service import decode_modified_utf7, encode_modified_utf7, parse_list_response


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
        self.assertEqual(mailbox["name"], "&BB4EQgQ,BEAEMAQyBDsENQQ9BD0ESwQ1-")
        self.assertEqual(mailbox["display_name"], "Отправленные")
        self.assertEqual(mailbox["delimiter"], "/")
        self.assertTrue(mailbox["selectable"])
        self.assertIn("\\Sent", mailbox["flags"])

    def test_parse_noselect_response(self):
        mailbox = parse_list_response(b'(\\Noselect) "/" "Projects"')
        self.assertIsNotNone(mailbox)
        self.assertFalse(mailbox["selectable"])


if __name__ == "__main__":
    unittest.main()
