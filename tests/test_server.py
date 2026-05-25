import json
import unittest

from mcp_email_webdav_caldav.server import MCPServer


class ServerTest(unittest.TestCase):
    def test_initialize(self):
        server = MCPServer()
        response = server.handle_line('{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}')
        self.assertEqual(response["result"]["serverInfo"]["name"], "mcp-email-webdav-caldav")

    def test_tools_list_contains_email_and_webdav(self):
        server = MCPServer()
        response = server.handle_line('{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}')
        names = {tool["name"] for tool in response["result"]["tools"]}
        self.assertIn("list_emails_metadata", names)
        self.assertIn("list_mailboxes", names)
        self.assertIn("webdav_list", names)
        self.assertIn("webdav_put_text", names)
        self.assertIn("caldav_list_calendars", names)
        self.assertIn("caldav_list_events", names)

        metadata_tool = next(tool for tool in response["result"]["tools"] if tool["name"] == "list_emails_metadata")
        properties = metadata_tool["inputSchema"]["properties"]
        self.assertIn("all_mailboxes", properties)

    def test_tool_call_returns_text_content(self):
        server = MCPServer()
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "list_available_accounts", "arguments": {}},
        }
        response = server.handle_line(json.dumps(payload))
        text = response["result"]["content"][0]["text"]
        self.assertEqual(json.loads(text), {"result": []})


if __name__ == "__main__":
    unittest.main()
