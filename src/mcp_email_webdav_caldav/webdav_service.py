from __future__ import annotations

import base64
import mimetypes
import ssl
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .config import WebDAVSettings, add_webdav_account, find_webdav_account, load_webdav_accounts, webdav_settings_from_dict


DAV_NS = "{DAV:}"


def list_webdav_accounts() -> dict[str, Any]:
    return {"result": [account.public() for account in load_webdav_accounts()]}


def add_account(webdav: dict[str, Any]) -> dict[str, str]:
    add_webdav_account(webdav_settings_from_dict(webdav))
    return {"result": "WebDAV account added successfully"}


def webdav_list(account_name: str = "", path: str = "", depth: int = 1) -> dict[str, Any]:
    client = WebDAVClient(find_webdav_account(account_name))
    xml = client.request("PROPFIND", path, headers={"Depth": str(depth)}, data=propfind_body())
    return {"items": parse_propfind(xml, client.url(path))}


def webdav_get_text(account_name: str = "", path: str = "", encoding: str = "utf-8") -> dict[str, Any]:
    client = WebDAVClient(find_webdav_account(account_name))
    data = client.request("GET", path)
    return {"path": path, "text": data.decode(encoding or "utf-8", "replace")}


def webdav_download_file(account_name: str = "", path: str = "", save_path: str = "") -> dict[str, str]:
    account = find_webdav_account(account_name)
    if not account.enable_file_download:
        raise ValueError("WebDAV file download is disabled; set WEBDAV_ENABLE_FILE_DOWNLOAD=True in constants.py")
    client = WebDAVClient(account)
    data = client.request("GET", path)
    target = Path(save_path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return {"result": "WebDAV file downloaded successfully", "save_path": str(target)}


def webdav_put_text(account_name: str = "", path: str = "", text: str = "", encoding: str = "utf-8") -> dict[str, str]:
    client = WebDAVClient(find_webdav_account(account_name))
    client.request("PUT", path, data=text.encode(encoding or "utf-8"), headers={"Content-Type": f"text/plain; charset={encoding or 'utf-8'}"})
    return {"result": "WebDAV text uploaded successfully"}


def webdav_upload_file(account_name: str = "", local_path: str = "", path: str = "") -> dict[str, str]:
    account = find_webdav_account(account_name)
    if not account.enable_file_upload:
        raise ValueError("WebDAV file upload is disabled; set WEBDAV_ENABLE_FILE_UPLOAD=True in constants.py")
    source = Path(local_path).expanduser()
    content_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    WebDAVClient(account).request("PUT", path, data=source.read_bytes(), headers={"Content-Type": content_type})
    return {"result": "WebDAV file uploaded successfully"}


def webdav_mkdir(account_name: str = "", path: str = "") -> dict[str, str]:
    WebDAVClient(find_webdav_account(account_name)).request("MKCOL", path)
    return {"result": "WebDAV directory created successfully"}


def webdav_delete(account_name: str = "", path: str = "") -> dict[str, str]:
    WebDAVClient(find_webdav_account(account_name)).request("DELETE", path)
    return {"result": "WebDAV path deleted successfully"}


def webdav_move(account_name: str = "", source_path: str = "", destination_path: str = "", overwrite: bool = False) -> dict[str, str]:
    client = WebDAVClient(find_webdav_account(account_name))
    headers = {
        "Destination": client.url(destination_path),
        "Overwrite": "T" if overwrite else "F",
    }
    client.request("MOVE", source_path, headers=headers)
    return {"result": "WebDAV path moved successfully"}


def webdav_copy(account_name: str = "", source_path: str = "", destination_path: str = "", overwrite: bool = False) -> dict[str, str]:
    client = WebDAVClient(find_webdav_account(account_name))
    headers = {
        "Destination": client.url(destination_path),
        "Overwrite": "T" if overwrite else "F",
    }
    client.request("COPY", source_path, headers=headers)
    return {"result": "WebDAV path copied successfully"}


class WebDAVClient:
    def __init__(self, account: WebDAVSettings):
        self.account = account
        self.base_url = account.base_url.rstrip("/") + "/"
        self.context = ssl.create_default_context() if account.verify_ssl else ssl._create_unverified_context()

    def url(self, path: str = "") -> str:
        path = (path or "").strip()
        if urllib.parse.urlsplit(path).scheme:
            raise ValueError("path must be relative to the configured WebDAV base_url")
        quoted = "/".join(urllib.parse.quote(part) for part in path.strip("/").split("/") if part)
        if path.endswith("/") and quoted:
            quoted += "/"
        return urllib.parse.urljoin(self.base_url, quoted)

    def request(
        self,
        method: str,
        path: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> bytes:
        request = urllib.request.Request(self.url(path), data=data, method=method.upper())
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        if self.account.user_name or self.account.password:
            token = f"{self.account.user_name}:{self.account.password}".encode("utf-8")
            request.add_header("Authorization", "Basic " + base64.b64encode(token).decode("ascii"))
        if data is not None and "Content-Length" not in request.headers:
            request.add_header("Content-Length", str(len(data)))
        try:
            with urllib.request.urlopen(request, context=self.context) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", "replace")
            raise RuntimeError(f"WebDAV {method.upper()} {path!r} failed: HTTP {error.code} {error.reason}: {detail}") from error


def propfind_body() -> bytes:
    return b"""<?xml version="1.0" encoding="utf-8" ?>
<D:propfind xmlns:D="DAV:">
  <D:prop>
    <D:displayname/>
    <D:getcontentlength/>
    <D:getcontenttype/>
    <D:getlastmodified/>
    <D:resourcetype/>
  </D:prop>
</D:propfind>"""


def parse_propfind(raw: bytes, base_url: str) -> list[dict[str, Any]]:
    root = ET.fromstring(raw)
    items = []
    base_path = urllib.parse.urlsplit(base_url).path.rstrip("/") + "/"
    for response in root.findall(f"{DAV_NS}response"):
        href = text_of(response, f"{DAV_NS}href")
        prop = first_prop(response)
        if prop is None:
            continue
        href_path = urllib.parse.unquote(urllib.parse.urlsplit(href).path)
        rel_path = href_path
        if href_path.startswith(base_path):
            rel_path = href_path[len(base_path):]
        rel_path = rel_path.lstrip("/")
        is_collection = prop.find(f"{DAV_NS}resourcetype/{DAV_NS}collection") is not None
        items.append({
            "path": rel_path,
            "display_name": text_of(prop, f"{DAV_NS}displayname") or Path(rel_path.rstrip("/")).name,
            "is_collection": is_collection,
            "content_length": int(text_of(prop, f"{DAV_NS}getcontentlength") or 0),
            "content_type": text_of(prop, f"{DAV_NS}getcontenttype"),
            "last_modified": text_of(prop, f"{DAV_NS}getlastmodified"),
            "href": href,
        })
    return items


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
