# mcp-email-webdav-caldav

Python MCP server for IMAP/SMTP email, WebDAV, and CalDAV.

## Package Run

Publish the package to PyPI as `mcp-email-webdav-caldav`, then run it without cloning this repository:

For Claude:
```bash
claude-glm-5 mcp add --scope user mail-webdav-caldav \
--env MCP_SERVER_FULL_NAME=personal \
--env MCP_SERVER_FULL_NAME=Иван Иванов \
--env MCP_SERVER_EMAIL_ADDRESS=ivan@inbox.ru \
--env MCP_SERVER_PASSWORD=token \
-- uvx mcp-email-webdav-caldav
```

For Codex:

```toml
[mcp_servers.mail-webdav-python]
command = "uvx"
args = ["mcp-email-webdav-caldav"]
enabled = true

[mcp_servers.mail-webdav-python.env]
MCP_SERVER_FULL_NAME = "Jane Doe"
MCP_SERVER_EMAIL_ADDRESS = "jane@example.com"
MCP_SERVER_PASSWORD = "app_password_here"
```

Equivalent one-off run:

```bash
uvx mcp-email-webdav-caldav
```

Or install the package once and run the console script:

```bash
pipx install mcp-email-webdav-caldav
```

Then Codex can use:

```toml
command = "mcp-email-webdav-caldav"
args = []
```

Ready-made examples:

- [Codex uvx config](examples/codex.uvx.config.toml)
- [Codex installed-script config](examples/codex.installed.config.toml)

## Run From Source

Use this only while developing locally:

```bash
cd /Users/maksim.vlasov/GolandProjects/mcp-email-webdav-caldav-python
PYTHONPATH=src python3 -m mcp_email_webdav_caldav
```

For an editable local install:

```bash
python3 -m pip install -e /fullpath/mcp-email-webdav-caldav-python
```

## Build And Publish

Build source and wheel distributions:

```bash
uv build
```

Publish to PyPI:

```bash
uv publish
```

After publishing a new version, restart Codex so `uvx` creates or refreshes the package environment. To pin a specific version:

```toml
args = ["mcp-email-webdav-caldav==0.2.0"]
```

## Runtime Environment Variables

Required:

- `MCP_SERVER_FULL_NAME`
- `MCP_SERVER_EMAIL_ADDRESS`
- `MCP_SERVER_PASSWORD`

`MCP_SERVER_EMAIL_ADDRESS` and `MCP_SERVER_PASSWORD` are reused as credentials for Email, WebDAV, and CalDAV.

## Service Constants

Provider-specific settings live in `src/mcp_email_webdav_caldav/constants.py`:

- Email IMAP/SMTP host, port, SSL, sent-folder, and attachment-download settings.
- WebDAV account name, base URL, description, SSL verification, download, and upload settings.
- CalDAV account name, base URL, description, SSL verification, and write settings.

Email, WebDAV, and CalDAV accounts can also be saved to `~/.config/zerolib/mcp_email_webdav_caldav/config.json` via the `add_email_account`, `add_webdav_account`, and `add_caldav_account` tools. Override the file location with `MCP_EMAIL_WEBDAV_CALDAV_CONFIG_PATH`.

## Tools

Email:

- `add_email_account`
- `list_available_accounts`
- `list_mailboxes`
- `list_emails_metadata`
- `get_emails_content`
- `send_email`
- `delete_emails`
- `download_attachment`

WebDAV:

- `add_webdav_account`
- `list_webdav_accounts`
- `webdav_list`
- `webdav_get_text`
- `webdav_download_file`
- `webdav_put_text`
- `webdav_upload_file`
- `webdav_mkdir`
- `webdav_delete`
- `webdav_move`
- `webdav_copy`

CalDAV:

- `add_caldav_account`
- `list_caldav_accounts`
- `caldav_list_calendars`
- `caldav_list_events`
- `caldav_get_event`
- `caldav_put_event`
- `caldav_create_event`
- `caldav_delete_event`

`list_emails_metadata` returns `mailbox` together with each `email_id`; pass the same `mailbox` to `get_emails_content`, `delete_emails`, and `download_attachment`. Set `all_mailboxes=true` to search every selectable IMAP folder.

`webdav_download_file` and `webdav_upload_file` are disabled by default because they read/write local files. Enable them explicitly in `constants.py`.

`caldav_put_event`, `caldav_create_event`, and `caldav_delete_event` are disabled by default because they modify remote calendars. Enable them explicitly in `constants.py`.

## Test

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```
