"""DevEco OAuth login flow with local callback server."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import uuid
import webbrowser
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

import httpx

from .config import (
    DEVECO_APP_ID,
    DEVECO_AUTH_URL,
    DEVECO_BASE_URL,
    DEVECO_DEFAULT_AUTH_PORT,
    DEVECO_FAILED_REDIRECT_URL,
    DEVECO_JWT_TOKEN_CHECK_URL,
    DEVECO_SUCCESS_REDIRECT_URL,
    DEVECO_TEMP_TOKEN_CHECK_URL,
    TOKEN_FILE,
    USER_AGENT,
)
from .crypto import decrypt_value, encrypt_value, load_auth_data, save_auth_data


@dataclass
class UserInfo:
    user_id: str
    user_name: str
    access_token: str
    refresh_token: str
    jwt_token: str
    country_code: str = "CN"
    language: str = "zh_CN"
    is_real_name: bool = False


@dataclass
class LoginResult:
    success: bool
    user_info: UserInfo | None = None
    cancelled: bool = False
    unsupported_region: bool = False
    error: str | None = None


class LoginCancelledError(Exception):
    pass


class UnsupportedRegionError(Exception):
    pass


def build_login_url(port: int, client_secret: str) -> str:
    """生成 DevEco OAuth 授权地址。"""
    return (
        f"{DEVECO_BASE_URL}/{DEVECO_AUTH_URL}"
        f"?port={port}&appid={DEVECO_APP_ID}&code={client_secret}"
    )


def _parse_request(data: bytes) -> tuple[str, str, bytes]:
    """Parse a simple HTTP request; return (method, path, body)."""
    try:
        header_end = data.index(b"\r\n\r\n")
    except ValueError:
        header_end = data.index(b"\n\n") + 1
    header_lines = data[:header_end].split(b"\r\n")
    if len(header_lines) == 1:
        header_lines = data[:header_end].split(b"\n")

    first = header_lines[0].decode("utf-8", errors="replace")
    parts = first.split()
    method = parts[0] if len(parts) > 0 else "GET"
    path = parts[1] if len(parts) > 1 else "/"

    content_length = 0
    for line in header_lines[1:]:
        if line.lower().startswith(b"content-length:"):
            try:
                content_length = int(line.split(b":", 1)[1].strip())
            except ValueError:
                content_length = 0
            break

    body = data[header_end + 4 : header_end + 4 + content_length]
    return method, path, body


def _parse_callback(path: str, body: bytes) -> dict[str, str | None]:
    parsed = urlparse(path)
    params = parse_qs(parsed.query)
    if body:
        body_params = parse_qs(body.decode("utf-8"))
        for k, v in body_params.items():
            params[k] = v

    def _first(key: str) -> str | None:
        values = params.get(key)
        return values[0] if values else None

    return {
        "code": _first("code"),
        "tempToken": _first("tempToken"),
        "siteId": _first("siteId"),
        "quit": _first("quit"),
    }


def parse_callback_request(data: bytes) -> tuple[str, dict[str, str | None]]:
    """解析 DevEco 本机回调请求，供 CLI 登录和云端桥接共用。"""
    _method, path, body = _parse_request(data)
    return urlparse(path).path, _parse_callback(path, body)


async def _callback_handler(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    expected_code: str,
    future: asyncio.Future,
) -> None:
    try:
        data = await asyncio.wait_for(reader.read(65535), timeout=5.0)
    except asyncio.TimeoutError:
        writer.close()
        await writer.wait_closed()
        return

    callback_path, params = parse_callback_request(data)

    if callback_path != "/callback":
        writer.write(b"HTTP/1.1 204 No Content\r\n\r\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        return

    code = params["code"]
    temp_token = params["tempToken"]
    site_id = params["siteId"]
    quit = params["quit"]

    print(
        f"[hm-api login] callback received: code={bool(code)}, "
        f"tempToken_len={len(temp_token) if temp_token else 0}, "
        f"siteId={site_id}, quit={quit}"
    )

    response_status = 302
    location = f"{DEVECO_BASE_URL}/{DEVECO_SUCCESS_REDIRECT_URL}"

    if not code or code != expected_code:
        print("[hm-api login] code mismatch or missing")
        response_status = 400
        location = ""
    elif quit in ("true", "access_denied"):
        if not future.done():
            future.set_exception(
                LoginCancelledError(
                    "Access denied by user"
                    if quit == "access_denied"
                    else "Login cancelled by user"
                )
            )
        location = f"{DEVECO_BASE_URL}/{DEVECO_FAILED_REDIRECT_URL}"
    elif not temp_token or not site_id:
        print("[hm-api login] missing tempToken or siteId")
        if not future.done():
            future.set_exception(LoginCancelledError("Missing tempToken or siteId"))
        location = f"{DEVECO_BASE_URL}/{DEVECO_FAILED_REDIRECT_URL}"
    elif site_id != "1":
        if not future.done():
            future.set_exception(UnsupportedRegionError("Unsupported region"))
        location = f"{DEVECO_BASE_URL}/{DEVECO_FAILED_REDIRECT_URL}"
    else:
        if not future.done():
            future.set_result({"tempToken": temp_token, "siteId": site_id})

    if response_status == 400:
        writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
    else:
        writer.write(
            f"HTTP/1.1 {response_status} Found\r\n"
            f"Location: {location}\r\n"
            "Content-Length: 0\r\n\r\n".encode("utf-8")
        )
    await writer.drain()
    writer.close()
    await writer.wait_closed()


async def _open_browser(url: str) -> bool:
    try:
        return webbrowser.open(url)
    except Exception:
        return False


def _build_client(proxy: str | None = None, timeout: float = 30.0) -> httpx.AsyncClient:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept-Language": "zh-CN",
    }
    mounts = {}
    if proxy:
        mounts = {
            "http://": httpx.AsyncHTTPTransport(proxy=proxy),
            "https://": httpx.AsyncHTTPTransport(proxy=proxy),
        }
    return httpx.AsyncClient(
        headers=headers,
        timeout=httpx.Timeout(timeout),
        follow_redirects=True,
        mounts=mounts,
    )


def make_login_secret() -> str:
    return uuid.uuid4().hex.replace("-", "")


def _parse_jwt(token: str) -> dict:
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Invalid JWT format")
    payload = parts[1]
    payload = payload.replace("-", "+").replace("_", "/")
    pad = (4 - len(payload) % 4) % 4
    payload += "=" * pad
    decoded = base64.b64decode(payload).decode("utf-8")
    return json.loads(decoded)


def _save_token(jwt_token: str) -> None:
    CRED_DIR = TOKEN_FILE.parent
    CRED_DIR.mkdir(parents=True, exist_ok=True)
    blob = encrypt_value(jwt_token)
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump(blob, f)
    os.chmod(TOKEN_FILE, 0o600)


def _load_token() -> str | None:
    if not TOKEN_FILE.exists():
        return None
    with open(TOKEN_FILE, "r", encoding="utf-8") as f:
        blob = json.load(f)
    return decrypt_value(blob)


def _save_access_token(access_token: str, refresh_token: str) -> None:
    data = load_auth_data()
    data["deveco"] = {
        "type": "oauth",
        "access": access_token,
        "refresh": refresh_token,
    }
    save_auth_data(data)


async def exchange_temp_token(temp_token: str, proxy: str | None = None) -> UserInfo:
    """使用 OAuth 回调中的临时 token 换取并保存本地凭据。"""
    actual_temp_token = temp_token.split("&")[0]

    async with _build_client(proxy, timeout=30.0) as client:
        jwt_resp = await client.get(
            f"{DEVECO_BASE_URL}/{DEVECO_TEMP_TOKEN_CHECK_URL}",
            params={
                "tempToken": actual_temp_token,
                "site": "CN",
                "version": "1.0.0",
                "appid": DEVECO_APP_ID,
            },
        )
        if jwt_resp.status_code != 200:
            raise RuntimeError(f"Failed to get JWT: {jwt_resp.status_code}")
        jwt_token = jwt_resp.text.strip()
        if len(jwt_token.split(".")) != 3:
            raise ValueError("Invalid JWT format")

        info_resp = await client.get(
            f"{DEVECO_BASE_URL}/{DEVECO_JWT_TOKEN_CHECK_URL}",
            headers={"refresh": "false", "jwtToken": jwt_token},
        )
        if info_resp.status_code != 200:
            raise RuntimeError(f"Failed to check JWT: {info_resp.status_code}")
        info_data = info_resp.json()
        if not info_data.get("status") or not info_data.get("userInfo"):
            raise ValueError("Invalid JWT userInfo")

        user_info_raw = info_data["userInfo"]
        payload = _parse_jwt(jwt_token)
        user_info = UserInfo(
            user_id=payload.get("userId", ""),
            user_name=payload.get("userName", ""),
            access_token=user_info_raw.get("accessToken", ""),
            refresh_token=user_info_raw.get("refreshToken", ""),
            jwt_token=jwt_token,
            country_code="CN",
            language="zh_CN",
            is_real_name=user_info_raw.get("realName") == "true",
        )

        _save_token(jwt_token)
        _save_access_token(user_info.access_token, user_info.refresh_token)

        return user_info


async def _start_callback_server(
    port: int, expected_code: str
) -> tuple[asyncio.Server, asyncio.Future]:
    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()

    async def handler(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        await _callback_handler(reader, writer, expected_code, future)

    server = await asyncio.start_server(handler, "127.0.0.1", port)
    return server, future


async def login(
    proxy: str | None = None,
    *,
    no_browser: bool = False,
    timeout: float = 600.0,
) -> LoginResult:
    client_secret = make_login_secret()
    ports = [DEVECO_DEFAULT_AUTH_PORT, 34567, 34568, 34569, 34570]

    server: asyncio.Server | None = None
    future: asyncio.Future | None = None

    for port in ports:
        try:
            server, future = await _start_callback_server(port, client_secret)
            break
        except OSError:
            continue
    else:
        return LoginResult(success=False, error="All local ports are in use")

    try:
        login_url = build_login_url(port, client_secret)
        if no_browser:
            print(f"Please open this URL in your browser to login:\n{login_url}")
        else:
            opened = await _open_browser(login_url)
            if not opened:
                print(
                    f"Failed to open browser automatically. Please open:\n{login_url}"
                )

        result = await asyncio.wait_for(future, timeout=timeout)
        temp_token = result["tempToken"]
        user_info = await exchange_temp_token(temp_token, proxy=proxy)
        return LoginResult(success=True, user_info=user_info)
    except asyncio.TimeoutError:
        return LoginResult(success=False, error="Login timeout")
    except LoginCancelledError:
        return LoginResult(success=False, cancelled=True, error="Login cancelled")
    except UnsupportedRegionError:
        return LoginResult(
            success=False,
            unsupported_region=True,
            error="Only China site accounts are currently supported",
        )
    except Exception as exc:
        return LoginResult(success=False, error=str(exc))
    finally:
        if server:
            server.close()
            await server.wait_closed()


def is_logged_in() -> bool:
    return _load_token() is not None


async def load_session() -> dict | None:
    token = _load_token()
    if not token:
        return None
    try:
        payload = _parse_jwt(token)
        data = load_auth_data()
        deveco = data.get("deveco", {})
        return {
            "user_id": payload.get("userId", ""),
            "user_name": payload.get("userName", ""),
            "access_token": deveco.get("access", ""),
            "refresh_token": deveco.get("refresh", ""),
            "jwt_token": token,
        }
    except Exception:
        return None
