from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from email.utils import parseaddr
from pathlib import Path
from typing import Any

from . import constants


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ServerSettings:
    user_name: str
    password: str
    host: str
    port: int
    ssl: bool = True
    start_ssl: bool = False
    verify_ssl: bool = True


@dataclass
class EmailSettings:
    account_name: str
    full_name: str
    email_address: str
    incoming: ServerSettings
    outgoing: ServerSettings
    description: str = ""
    enable_attachment_download: bool = False
    save_to_sent: bool = True
    sent_folder_name: str = ""
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def public(self) -> dict[str, Any]:
        return {
            "account_name": self.account_name,
            "description": self.description,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class WebDAVSettings:
    account_name: str
    base_url: str
    user_name: str = ""
    password: str = ""
    description: str = ""
    verify_ssl: bool = True
    enable_file_download: bool = False
    enable_file_upload: bool = False
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def public(self) -> dict[str, Any]:
        return {
            "account_name": self.account_name,
            "base_url": self.base_url,
            "description": self.description,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class CalDAVSettings:
    account_name: str
    base_url: str
    user_name: str = ""
    password: str = ""
    description: str = ""
    verify_ssl: bool = True
    enable_write: bool = False
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def public(self) -> dict[str, Any]:
        return {
            "account_name": self.account_name,
            "base_url": self.base_url,
            "description": self.description,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def config_path() -> Path:
    override = os.getenv("MCP_EMAIL_WEBDAV_CALDAV_CONFIG_PATH")
    if override:
        return Path(override).expanduser()
    config_home = os.getenv("XDG_CONFIG_HOME")
    root = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    return root / "zerolib" / "mcp_email_webdav_caldav" / "config.json"


def load_email_accounts() -> list[EmailSettings]:
    accounts: list[EmailSettings] = []
    env_account = email_account_from_env()
    if env_account:
        accounts = upsert(accounts, env_account, "account_name")
    for account in load_file().get("emails", []):
        accounts = upsert(accounts, email_settings_from_dict(account), "account_name")
    return accounts


def load_webdav_accounts() -> list[WebDAVSettings]:
    accounts: list[WebDAVSettings] = []
    env_account = webdav_account_from_env()
    if env_account:
        accounts = upsert(accounts, env_account, "account_name")
    for account in load_file().get("webdav", []):
        accounts = upsert(accounts, webdav_settings_from_dict(account), "account_name")
    return accounts


def load_caldav_accounts() -> list[CalDAVSettings]:
    accounts: list[CalDAVSettings] = []
    env_account = caldav_account_from_env()
    if env_account:
        accounts = upsert(accounts, env_account, "account_name")
    for account in load_file().get("caldav", []):
        accounts = upsert(accounts, caldav_settings_from_dict(account), "account_name")
    return accounts


def find_email_account(name: str = "") -> EmailSettings:
    accounts = load_email_accounts()
    if name:
        for account in accounts:
            if account.account_name == name:
                return account
        raise ValueError(f"email account {name!r} not found")
    if len(accounts) == 1:
        return accounts[0]
    raise ValueError("email account name is required")


def find_webdav_account(name: str = "") -> WebDAVSettings:
    accounts = load_webdav_accounts()
    if name:
        for account in accounts:
            if account.account_name == name:
                return account
        raise ValueError(f"webdav account {name!r} not found")
    if len(accounts) == 1:
        return accounts[0]
    raise ValueError("webdav account name is required")


def find_caldav_account(name: str = "") -> CalDAVSettings:
    accounts = load_caldav_accounts()
    if name:
        for account in accounts:
            if account.account_name == name:
                return account
        raise ValueError(f"caldav account {name!r} not found")
    if len(accounts) == 1:
        return accounts[0]
    raise ValueError("caldav account name is required")


def add_email_account(account: EmailSettings) -> None:
    validate_email_account(account)
    data = load_file()
    account.updated_at = utc_now()
    if not account.created_at:
        account.created_at = account.updated_at
    data["emails"] = [asdict(item) for item in upsert(
        [email_settings_from_dict(item) for item in data.get("emails", [])],
        account,
        "account_name",
    )]
    save_file(data)


def add_webdav_account(account: WebDAVSettings) -> None:
    validate_webdav_account(account)
    data = load_file()
    account.updated_at = utc_now()
    if not account.created_at:
        account.created_at = account.updated_at
    data["webdav"] = [asdict(item) for item in upsert(
        [webdav_settings_from_dict(item) for item in data.get("webdav", [])],
        account,
        "account_name",
    )]
    save_file(data)


def add_caldav_account(account: CalDAVSettings) -> None:
    validate_caldav_account(account)
    data = load_file()
    account.updated_at = utc_now()
    if not account.created_at:
        account.created_at = account.updated_at
    data["caldav"] = [asdict(item) for item in upsert(
        [caldav_settings_from_dict(item) for item in data.get("caldav", [])],
        account,
        "account_name",
    )]
    save_file(data)


def load_file() -> dict[str, Any]:
    path = config_path()
    if not path.exists():
        return {"emails": [], "webdav": [], "caldav": []}
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return {"emails": [], "webdav": [], "caldav": []}
    data = json.loads(raw)
    data.setdefault("emails", [])
    data.setdefault("webdav", [])
    data.setdefault("caldav", [])
    return data


def save_file(data: dict[str, Any]) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


def email_account_from_env() -> EmailSettings | None:
    email_address = os.getenv("MCP_SERVER_EMAIL_ADDRESS", "").strip()
    password = os.getenv("MCP_SERVER_PASSWORD", "")
    if not any([email_address, password]):
        return None

    full_name = os.getenv("MCP_SERVER_FULL_NAME", "").strip()
    if not full_name and email_address:
        full_name = email_address.split("@", 1)[0]
    account = EmailSettings(
        account_name=constants.EMAIL_ACCOUNT_NAME,
        full_name=full_name,
        email_address=email_address,
        incoming=ServerSettings(
            user_name=email_address,
            password=password,
            host=constants.EMAIL_IMAP_HOST,
            port=constants.EMAIL_IMAP_PORT,
            ssl=constants.EMAIL_IMAP_SSL,
            verify_ssl=constants.EMAIL_IMAP_VERIFY_SSL,
        ),
        outgoing=ServerSettings(
            user_name=email_address,
            password=password,
            host=constants.EMAIL_SMTP_HOST,
            port=constants.EMAIL_SMTP_PORT,
            ssl=constants.EMAIL_SMTP_SSL,
            start_ssl=constants.EMAIL_SMTP_START_SSL,
            verify_ssl=constants.EMAIL_SMTP_VERIFY_SSL,
        ),
        enable_attachment_download=constants.EMAIL_ENABLE_ATTACHMENT_DOWNLOAD,
        save_to_sent=constants.EMAIL_SAVE_TO_SENT,
        sent_folder_name=constants.EMAIL_SENT_FOLDER_NAME,
    )
    validate_email_account(account)
    return account


def webdav_account_from_env() -> WebDAVSettings | None:
    user_name = os.getenv("MCP_SERVER_EMAIL_ADDRESS", "").strip()
    password = os.getenv("MCP_SERVER_PASSWORD", "")
    if not any([user_name, password]):
        return None
    account = WebDAVSettings(
        account_name=constants.WEBDAV_ACCOUNT_NAME,
        base_url=constants.WEBDAV_BASE_URL,
        user_name=user_name,
        password=password,
        description=constants.WEBDAV_DESCRIPTION,
        verify_ssl=constants.WEBDAV_VERIFY_SSL,
        enable_file_download=constants.WEBDAV_ENABLE_FILE_DOWNLOAD,
        enable_file_upload=constants.WEBDAV_ENABLE_FILE_UPLOAD,
    )
    validate_webdav_account(account)
    return account


def caldav_account_from_env() -> CalDAVSettings | None:
    user_name = os.getenv("MCP_SERVER_EMAIL_ADDRESS", "").strip()
    password = os.getenv("MCP_SERVER_PASSWORD", "")
    if not any([user_name, password]):
        return None
    account = CalDAVSettings(
        account_name=constants.CALDAV_ACCOUNT_NAME,
        base_url=constants.CALDAV_BASE_URL,
        user_name=user_name,
        password=password,
        description=constants.CALDAV_DESCRIPTION,
        verify_ssl=constants.CALDAV_VERIFY_SSL,
        enable_write=constants.CALDAV_ENABLE_WRITE,
    )
    validate_caldav_account(account)
    return account


def email_settings_from_dict(raw: dict[str, Any]) -> EmailSettings:
    incoming = server_settings_from_dict(raw["incoming"])
    outgoing = server_settings_from_dict(raw["outgoing"])
    account = EmailSettings(
        account_name=raw.get("account_name", "default"),
        full_name=raw.get("full_name", ""),
        email_address=raw.get("email_address", ""),
        incoming=incoming,
        outgoing=outgoing,
        description=raw.get("description", ""),
        enable_attachment_download=bool(raw.get("enable_attachment_download", False)),
        save_to_sent=bool(raw.get("save_to_sent", True)),
        sent_folder_name=raw.get("sent_folder_name", ""),
        created_at=raw.get("created_at") or utc_now(),
        updated_at=raw.get("updated_at") or raw.get("created_at") or utc_now(),
    )
    validate_email_account(account)
    return account


def server_settings_from_dict(raw: dict[str, Any]) -> ServerSettings:
    return ServerSettings(
        user_name=raw.get("user_name", ""),
        password=raw.get("password", ""),
        host=raw.get("host", ""),
        port=int(raw.get("port") or 0),
        ssl=bool(raw.get("ssl", True)),
        start_ssl=bool(raw.get("start_ssl", False)),
        verify_ssl=bool(raw.get("verify_ssl", True)),
    )


def webdav_settings_from_dict(raw: dict[str, Any]) -> WebDAVSettings:
    account = WebDAVSettings(
        account_name=raw.get("account_name", "default"),
        base_url=raw.get("base_url", ""),
        user_name=raw.get("user_name", ""),
        password=raw.get("password", ""),
        description=raw.get("description", ""),
        verify_ssl=bool(raw.get("verify_ssl", True)),
        enable_file_download=bool(raw.get("enable_file_download", False)),
        enable_file_upload=bool(raw.get("enable_file_upload", False)),
        created_at=raw.get("created_at") or utc_now(),
        updated_at=raw.get("updated_at") or raw.get("created_at") or utc_now(),
    )
    validate_webdav_account(account)
    return account


def caldav_settings_from_dict(raw: dict[str, Any]) -> CalDAVSettings:
    account = CalDAVSettings(
        account_name=raw.get("account_name", "default"),
        base_url=raw.get("base_url", ""),
        user_name=raw.get("user_name", ""),
        password=raw.get("password", ""),
        description=raw.get("description", ""),
        verify_ssl=bool(raw.get("verify_ssl", True)),
        enable_write=bool(raw.get("enable_write", False)),
        created_at=raw.get("created_at") or utc_now(),
        updated_at=raw.get("updated_at") or raw.get("created_at") or utc_now(),
    )
    validate_caldav_account(account)
    return account


def validate_email_account(account: EmailSettings) -> None:
    if not account.account_name:
        raise ValueError("account_name is required")
    if not account.email_address or not parseaddr(account.email_address)[1]:
        raise ValueError("email_address is required")
    for label, server in [("incoming", account.incoming), ("outgoing", account.outgoing)]:
        if not server.user_name:
            raise ValueError(f"{label}.user_name is required")
        if not server.password:
            raise ValueError(f"{label}.password is required")
        if not server.host:
            raise ValueError(f"{label}.host is required")
        if not server.port:
            raise ValueError(f"{label}.port is required")


def validate_webdav_account(account: WebDAVSettings) -> None:
    if not account.account_name:
        raise ValueError("account_name is required")
    if not account.base_url:
        raise ValueError("base_url is required")


def validate_caldav_account(account: CalDAVSettings) -> None:
    if not account.account_name:
        raise ValueError("account_name is required")
    if not account.base_url:
        raise ValueError("base_url is required")


def upsert(items: list[Any], item: Any, key: str) -> list[Any]:
    value = getattr(item, key)
    for index, existing in enumerate(items):
        if getattr(existing, key) == value:
            if hasattr(existing, "created_at") and not getattr(item, "created_at", ""):
                item.created_at = existing.created_at
            items[index] = item
            return items
    return [*items, item]

