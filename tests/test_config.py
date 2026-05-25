import os
import unittest
from unittest.mock import patch

from mcp_email_webdav_caldav import constants
from mcp_email_webdav_caldav.config import caldav_account_from_env, email_account_from_env, webdav_account_from_env


class ConfigEnvTest(unittest.TestCase):
    def test_universal_server_env_creates_all_accounts(self):
        env = {
            "MCP_SERVER_FULL_NAME": "Jane Doe",
            "MCP_SERVER_EMAIL_ADDRESS": "jane@example.com",
            "MCP_SERVER_PASSWORD": "app_password_here",
        }
        with patch.dict(os.environ, env, clear=True):
            email = email_account_from_env()
            webdav = webdav_account_from_env()
            caldav = caldav_account_from_env()

        self.assertIsNotNone(email)
        self.assertEqual(email.account_name, constants.DEFAULT_ACCOUNT_NAME)
        self.assertEqual(email.full_name, "Jane Doe")
        self.assertEqual(email.email_address, "jane@example.com")
        self.assertEqual(email.incoming.user_name, "jane@example.com")
        self.assertEqual(email.incoming.password, "app_password_here")
        self.assertEqual(email.incoming.host, constants.EMAIL_IMAP_HOST)
        self.assertEqual(email.outgoing.user_name, "jane@example.com")
        self.assertEqual(email.outgoing.password, "app_password_here")
        self.assertEqual(email.outgoing.host, constants.EMAIL_SMTP_HOST)

        self.assertIsNotNone(webdav)
        self.assertEqual(webdav.user_name, "jane@example.com")
        self.assertEqual(webdav.password, "app_password_here")
        self.assertEqual(webdav.base_url, constants.WEBDAV_BASE_URL)

        self.assertIsNotNone(caldav)
        self.assertEqual(caldav.user_name, "jane@example.com")
        self.assertEqual(caldav.password, "app_password_here")
        self.assertEqual(caldav.base_url, constants.CALDAV_BASE_URL)

    def test_env_accounts_are_absent_without_universal_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(email_account_from_env())
            self.assertIsNone(webdav_account_from_env())
            self.assertIsNone(caldav_account_from_env())


if __name__ == "__main__":
    unittest.main()
