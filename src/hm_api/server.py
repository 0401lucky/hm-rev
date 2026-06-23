"""FastAPI OpenAI-compatible proxy server for DevEco Code."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from html import escape
from pathlib import Path
from typing import Any, AsyncGenerator
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles

from .config import DEFAULT_PORT, DEVECO_BASE_URL, USER_AGENT
from .crypto import load_auth_data
from .login import (
    build_login_url,
    exchange_temp_token,
    is_logged_in,
    load_session,
    make_login_secret,
    refresh_access_token,
)
from .stats import extract_usage, get_stats_snapshot, record_chat_completion


TARGET_BASE = f"{DEVECO_BASE_URL}/sse/codeGenie/maas"
WEB_DIR = Path(__file__).resolve().parent / "web"
ACCESS_TOKEN_ERROR_CODES = {4016}
UPSTREAM_RETRY_ATTEMPTS = 3
UPSTREAM_RETRY_STATUSES = {408, 429, 500, 502, 503, 504}
UPSTREAM_TIMEOUT = httpx.Timeout(connect=15.0, read=600.0, write=60.0, pool=15.0)
UPSTREAM_ERROR_BODY_LIMIT = 2000


def _public_session(session: dict | None) -> dict | None:
    if not session:
        return None
    return {
        "user_id": session.get("user_id") or "",
        "user_name": session.get("user_name") or "",
    }


def _infer_public_port(request: Request) -> int:
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    if ":" in host:
        port_text = host.rsplit(":", 1)[-1]
        if port_text.isdigit():
            return int(port_text)
    if request.url.port:
        return request.url.port
    return DEFAULT_PORT


def _auth_result_page(title: str, message: str, ok: bool) -> HTMLResponse:
    accent = "#107c41" if ok else "#9f1d20"
    safe_title = escape(title)
    safe_message = escape(message)
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="1.8; url=/">
  <title>{safe_title}</title>
  <style>
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      background: #f3f5f1;
      color: #171a1f;
      font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
    }}
    main {{
      width: min(480px, calc(100vw - 40px));
      border: 1px solid #d7ddd4;
      background: #fff;
      padding: 32px;
      box-shadow: 0 24px 80px rgba(31, 42, 33, .12);
    }}
    .mark {{
      width: 44px;
      height: 6px;
      background: {accent};
      margin-bottom: 28px;
    }}
    h1 {{
      margin: 0 0 12px;
      font-size: 26px;
      line-height: 1.2;
    }}
    p {{
      margin: 0;
      color: #566057;
      line-height: 1.7;
    }}
    a {{
      color: {accent};
      font-weight: 700;
    }}
  </style>
</head>
<body>
  <main>
    <div class="mark"></div>
    <h1>{safe_title}</h1>
    <p>{safe_message} 即将返回控制台，也可以 <a href="/">立即返回</a>。</p>
  </main>
</body>
</html>"""
    return HTMLResponse(html)


def _extract_manual_auth_params(value: str) -> dict[str, str | None]:
    text = value.strip()
    if not text:
        return {"code": None, "tempToken": None, "siteId": None, "quit": None}

    parsed = urlparse(text)
    query = parsed.query
    raw_query = query or text.lstrip("?")
    params = parse_qs(raw_query)

    def _first(key: str) -> str | None:
        values = params.get(key)
        return values[0] if values else None

    known_keys = {"code", "tempToken", "siteId", "quit"}
    temp_token = _first("tempToken")
    if not temp_token and not known_keys.intersection(params):
        temp_token = text

    return {
        "code": _first("code"),
        "tempToken": temp_token,
        "siteId": _first("siteId"),
        "quit": _first("quit"),
    }


def _current_access_token() -> str | None:
    data = load_auth_data()
    deveco = data.get("deveco", {})
    access = deveco.get("access")
    return access if access else None


def _business_error(payload: Any) -> tuple[int | None, str] | None:
    if not isinstance(payload, dict) or "errorCode" not in payload:
        return None
    raw_code = payload.get("errorCode")
    code = raw_code if isinstance(raw_code, int) else None
    message = payload.get("errorMsg") or payload.get("message") or "Upstream error"
    return code, str(message)


def _business_error_status(error: tuple[int | None, str]) -> int:
    code, _message = error
    if code in ACCESS_TOKEN_ERROR_CODES:
        return 401
    if code in UPSTREAM_RETRY_STATUSES:
        return 502
    return 502


def _is_retryable_status(status_code: int) -> bool:
    return status_code in UPSTREAM_RETRY_STATUSES


def _is_retryable_error(exc: Exception) -> bool:
    return isinstance(
        exc,
        (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.PoolTimeout,
            httpx.ReadError,
            httpx.RemoteProtocolError,
            httpx.WriteError,
        ),
    )


def _trim_error_text(text: str) -> str:
    stripped = text.strip()
    if len(stripped) <= UPSTREAM_ERROR_BODY_LIMIT:
        return stripped
    return stripped[:UPSTREAM_ERROR_BODY_LIMIT] + "...(truncated)"


def _upstream_transport_error(exc: Exception) -> tuple[int | None, str]:
    return None, f"DevEco upstream connection failed: {type(exc).__name__}: {exc}"


def _openai_error_content(error: tuple[int | None, str]) -> dict[str, dict[str, Any]]:
    code, message = error
    return {
        "error": {
            "message": message,
            "type": "upstream_error",
            "code": code,
        }
    }


def _sse_error(error: tuple[int | None, str]) -> bytes:
    content = _openai_error_content(error)
    data = json.dumps(content, ensure_ascii=False)
    return f"data: {data}\n\ndata: [DONE]\n\n".encode("utf-8")


def _json_payload(resp: httpx.Response) -> Any:
    content_type = resp.headers.get("content-type", "")
    if not content_type.startswith("application/json"):
        return None
    try:
        return resp.json()
    except json.JSONDecodeError:
        return None


def _required_json_payload(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=502,
            detail=_trim_error_text(resp.text) or "Upstream returned invalid JSON",
        ) from exc


def _chat_model(body_json: dict) -> str:
    model = body_json.get("model")
    return model if isinstance(model, str) and model else "unknown"


def _duration_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _usage_from_sse_line(line: str) -> dict[str, int] | None:
    stripped = line.strip()
    if not stripped.startswith("data:"):
        return None
    data = stripped[5:].strip()
    if not data or data == "[DONE]":
        return None
    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        return None
    usage = extract_usage(payload)
    return usage if usage["total_tokens"] > 0 else None


async def _sleep_before_retry(attempt: int) -> None:
    await asyncio.sleep(min(0.4 * (2**attempt), 2.0))


async def _request_with_retries(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    retry_statuses: bool = True,
    **kwargs: Any,
) -> httpx.Response:
    last_exc: Exception | None = None
    for attempt in range(UPSTREAM_RETRY_ATTEMPTS):
        try:
            resp = await client.request(method, url, **kwargs)
        except Exception as exc:
            if not _is_retryable_error(exc) or attempt == UPSTREAM_RETRY_ATTEMPTS - 1:
                raise
            last_exc = exc
            await _sleep_before_retry(attempt)
            continue

        if (
            retry_statuses
            and _is_retryable_status(resp.status_code)
            and attempt < UPSTREAM_RETRY_ATTEMPTS - 1
        ):
            await resp.aclose()
            await _sleep_before_retry(attempt)
            continue
        return resp

    if last_exc:
        raise last_exc
    raise RuntimeError("DevEco upstream request retry exhausted")


def build_app(api_key: str | None = None, proxy: str | None = None) -> FastAPI:
    app = FastAPI(title="hm-api", version="0.1.0")
    auth_state: dict[str, str | float | None] = {
        "secret": None,
        "proxy": proxy,
        "created_at": 0.0,
    }
    mounts: dict[str, httpx.AsyncHTTPTransport] | None = None
    if proxy:
        transport = httpx.AsyncHTTPTransport(proxy=proxy)
        mounts = {"http://": transport, "https://": transport}

    client = httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-CN"},
        timeout=UPSTREAM_TIMEOUT,
        follow_redirects=True,
        mounts=mounts,
    )

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        if api_key is None or not request.url.path.startswith("/v1/"):
            return await call_next(request)
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or auth[7:] != api_key:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        return await call_next(request)

    app.mount("/assets", StaticFiles(directory=WEB_DIR), name="assets")

    @app.get("/", response_model=None, include_in_schema=False)
    async def dashboard() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/api/status", response_model=None)
    async def auth_status() -> JSONResponse:
        session = await load_session()
        return JSONResponse(
            {
                "logged_in": is_logged_in(),
                "user": _public_session(session),
                "api_key_enabled": api_key is not None,
            }
        )

    @app.get("/api/stats", response_model=None)
    async def stats_snapshot() -> JSONResponse:
        return JSONResponse(get_stats_snapshot())

    @app.post("/api/auth/start", response_model=None)
    async def start_auth(request: Request) -> JSONResponse:
        body = {}
        try:
            body = await request.json()
        except Exception:
            body = {}

        override_proxy = body.get("proxy") if isinstance(body, dict) else None
        if not isinstance(override_proxy, str):
            override_proxy = None
        callback_port = _infer_public_port(request)
        secret = make_login_secret()
        auth_state.update(
            {
                "secret": secret,
                "proxy": override_proxy or proxy,
                "created_at": time.time(),
            }
        )
        return JSONResponse(
            {
                "login_url": build_login_url(callback_port, secret),
                "callback_port": callback_port,
                "expires_in": 600,
            }
        )

    @app.post("/api/auth/import", response_model=None)
    async def import_auth(request: Request) -> JSONResponse:
        body = {}
        try:
            body = await request.json()
        except Exception:
            body = {}

        raw_value = body.get("callback") if isinstance(body, dict) else None
        if not isinstance(raw_value, str) or not raw_value.strip():
            return JSONResponse(
                {"error": "请粘贴回调 URL 或 tempToken"},
                status_code=400,
            )

        override_proxy = body.get("proxy") if isinstance(body, dict) else None
        if not isinstance(override_proxy, str):
            override_proxy = None

        params = _extract_manual_auth_params(raw_value)
        code = params["code"]
        temp_token = params["tempToken"]
        site_id = params["siteId"]
        quit_value = params["quit"]
        expected_code = auth_state.get("secret")
        created_at = auth_state.get("created_at")

        if quit_value in ("true", "access_denied"):
            return JSONResponse({"error": "当前账号没有完成授权"}, status_code=400)
        if not temp_token:
            return JSONResponse({"error": "未找到 tempToken"}, status_code=400)
        if site_id and site_id != "1":
            return JSONResponse({"error": "当前仅支持中国站账号"}, status_code=400)
        if (
            code
            and isinstance(expected_code, str)
            and expected_code
            and isinstance(created_at, (int, float))
            and time.time() - created_at <= 600
            and code != expected_code
        ):
            return JSONResponse({"error": "回调校验未通过"}, status_code=400)

        try:
            state_proxy = override_proxy or auth_state.get("proxy")
            proxy_value = (
                state_proxy if isinstance(state_proxy, str) and state_proxy else None
            )
            user_info = await exchange_temp_token(temp_token, proxy=proxy_value)
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

        auth_state["secret"] = None
        return JSONResponse(
            {
                "success": True,
                "user": {
                    "user_id": user_info.user_id,
                    "user_name": user_info.user_name,
                },
            }
        )

    @app.api_route("/callback", methods=["GET", "POST"], response_model=None)
    async def auth_callback(request: Request) -> HTMLResponse:
        params = dict(request.query_params)
        if request.method == "POST":
            body = await request.body()
            for key, values in parse_qs(body.decode("utf-8", errors="replace")).items():
                if values:
                    params[key] = values[0]

        code = params.get("code")
        temp_token = params.get("tempToken")
        site_id = params.get("siteId")
        quit_value = params.get("quit")
        expected_code = auth_state.get("secret")
        created_at = auth_state.get("created_at")

        if not isinstance(expected_code, str) or not expected_code:
            return _auth_result_page(
                "授权未开始",
                "请先在 hm-api 控制台中点击授权登录",
                ok=False,
            )
        if not isinstance(created_at, (int, float)) or time.time() - created_at > 600:
            auth_state["secret"] = None
            return _auth_result_page("授权已过期", "请回到控制台重新发起授权", ok=False)
        if not code or code != expected_code:
            return _auth_result_page("授权失败", "回调校验未通过", ok=False)
        if quit_value in ("true", "access_denied"):
            auth_state["secret"] = None
            return _auth_result_page("授权已取消", "当前账号没有完成授权", ok=False)
        if not temp_token or not site_id:
            auth_state["secret"] = None
            return _auth_result_page("授权失败", "回调缺少必要参数", ok=False)
        if site_id != "1":
            auth_state["secret"] = None
            return _auth_result_page("暂不支持该区域", "当前仅支持中国站账号", ok=False)

        try:
            state_proxy = auth_state.get("proxy")
            proxy_value = (
                state_proxy if isinstance(state_proxy, str) and state_proxy else None
            )
            await exchange_temp_token(temp_token, proxy=proxy_value)
        except Exception as exc:
            auth_state["secret"] = None
            return _auth_result_page("授权失败", str(exc), ok=False)

        auth_state["secret"] = None
        return _auth_result_page("授权成功", "hm-api 已完成 DevEco 登录", ok=True)

    @app.get("/v1/models", response_model=None)
    async def list_models() -> JSONResponse:
        token = _current_access_token()
        if not token:
            raise HTTPException(status_code=401, detail="Not logged in")

        async def request_models(access_token: str) -> httpx.Response:
            return await _request_with_retries(
                client,
                "GET",
                f"{DEVECO_BASE_URL}/codeGenie/modelConfig?localVersion=0&pluginVersion=CLI.0.1.0",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
            )

        try:
            resp = await request_models(token)
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502,
                detail=_upstream_transport_error(exc)[1],
            ) from exc
        if resp.status_code != 200:
            raise HTTPException(
                status_code=resp.status_code,
                detail=_trim_error_text(resp.text) or "Upstream error",
            )
        data = _required_json_payload(resp)
        error = _business_error(data)
        if error and error[0] in ACCESS_TOKEN_ERROR_CODES:
            refreshed_token = await refresh_access_token(proxy=proxy)
            if refreshed_token:
                try:
                    resp = await request_models(refreshed_token)
                except httpx.HTTPError as exc:
                    raise HTTPException(
                        status_code=502,
                        detail=_upstream_transport_error(exc)[1],
                    ) from exc
                if resp.status_code != 200:
                    raise HTTPException(
                        status_code=resp.status_code,
                        detail=_trim_error_text(resp.text) or "Upstream error",
                    )
                data = _required_json_payload(resp)
                error = _business_error(data)
        if error:
            raise HTTPException(
                status_code=_business_error_status(error),
                detail=error[1],
            )
        models: list[dict] = []
        for group in data.get("body", {}).get("inner_models", []):
            for cfg in group.get("model_configs", []):
                model_id = cfg.get("model_id")
                if model_id:
                    models.append(
                        {"id": model_id, "object": "model", "owned_by": "deveco"}
                    )
        return JSONResponse({"object": "list", "data": models})

    @app.post("/v1/chat/completions", response_model=None)
    async def chat_completions(request: Request) -> StreamingResponse | JSONResponse:
        started_at = time.perf_counter()
        token = _current_access_token()
        if not token:
            record_chat_completion(
                model="unknown",
                stream=False,
                status_code=401,
                duration_ms=_duration_ms(started_at),
                error="Not logged in",
            )
            raise HTTPException(status_code=401, detail="Not logged in")

        body_bytes = await request.body()
        if not body_bytes:
            body_bytes = b"{}"
        try:
            body_json = json.loads(body_bytes)
        except json.JSONDecodeError:
            record_chat_completion(
                model="unknown",
                stream=False,
                status_code=400,
                duration_ms=_duration_ms(started_at),
                error="Invalid JSON body",
            )
            raise HTTPException(status_code=400, detail="Invalid JSON body")

        model = _chat_model(body_json)
        stream = bool(body_json.get("stream"))
        target_path = "v2/chat/completions"
        if not stream:
            target_path = "v2/no-stream/chat/completions"
        url = f"{TARGET_BASE}/{target_path}"

        session_id = request.headers.get("x-deveco-session") or request.headers.get(
            "x-session-affinity"
        )
        chat_id = uuid.uuid4().hex.replace("-", "")

        upstream_headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "lang": "en",
            "Chat-Id": chat_id,
        }
        if session_id:
            upstream_headers["Session-Id"] = session_id

        for key, value in request.headers.items():
            lower = key.lower()
            if lower in {
                "host",
                "authorization",
                "content-length",
                "content-type",
                "connection",
                "accept-encoding",
            }:
                continue
            upstream_headers[key] = value

        def headers_with_token(access_token: str) -> dict[str, str]:
            headers = dict(upstream_headers)
            headers["Authorization"] = f"Bearer {access_token}"
            return headers

        if stream:

            async def streamer() -> AsyncGenerator[bytes, None]:
                access_token = token
                usage: dict[str, int] | None = None
                line_buffer = ""
                recorded = False
                has_sent_chunk = False
                refreshed_once = False
                try:
                    for attempt in range(UPSTREAM_RETRY_ATTEMPTS):
                        try:
                            async with client.stream(
                                "POST",
                                url,
                                headers=headers_with_token(access_token),
                                content=body_bytes,
                            ) as upstream_resp:
                                if upstream_resp.status_code != 200:
                                    text = await upstream_resp.aread()
                                    error_text = (
                                        _trim_error_text(
                                            text.decode("utf-8", errors="replace")
                                        )
                                        or "Upstream error"
                                    )
                                    if (
                                        _is_retryable_status(upstream_resp.status_code)
                                        and attempt < UPSTREAM_RETRY_ATTEMPTS - 1
                                    ):
                                        await _sleep_before_retry(attempt)
                                        continue
                                    error = (upstream_resp.status_code, error_text)
                                    record_chat_completion(
                                        model=model,
                                        stream=True,
                                        status_code=upstream_resp.status_code,
                                        duration_ms=_duration_ms(started_at),
                                        error=error_text,
                                    )
                                    recorded = True
                                    yield _sse_error(error)
                                    return

                                content_type = upstream_resp.headers.get(
                                    "content-type", ""
                                )
                                if not content_type.startswith("text/event-stream"):
                                    raw_body = await upstream_resp.aread()
                                    error_payload = None
                                    if content_type.startswith("application/json"):
                                        try:
                                            error_payload = json.loads(raw_body)
                                        except json.JSONDecodeError:
                                            error_payload = None
                                    error = _business_error(error_payload)
                                    if (
                                        error
                                        and error[0] in ACCESS_TOKEN_ERROR_CODES
                                        and not refreshed_once
                                    ):
                                        refreshed_token = await refresh_access_token(
                                            proxy=proxy
                                        )
                                        if refreshed_token:
                                            access_token = refreshed_token
                                            refreshed_once = True
                                            continue
                                    if not error:
                                        text = (
                                            _trim_error_text(
                                                raw_body.decode(
                                                    "utf-8", errors="replace"
                                                )
                                            )
                                            or "Upstream error"
                                        )
                                        error = (None, text)
                                    status_code = _business_error_status(error)
                                    message = error[1]
                                    record_chat_completion(
                                        model=model,
                                        stream=True,
                                        status_code=status_code,
                                        duration_ms=_duration_ms(started_at),
                                        error=message,
                                    )
                                    recorded = True
                                    yield _sse_error(error)
                                    return

                                async for chunk in upstream_resp.aiter_bytes():
                                    has_sent_chunk = True
                                    line_buffer += chunk.decode(
                                        "utf-8", errors="ignore"
                                    )
                                    while "\n" in line_buffer:
                                        line, line_buffer = line_buffer.split("\n", 1)
                                        parsed_usage = _usage_from_sse_line(line)
                                        if parsed_usage:
                                            usage = parsed_usage
                                    yield chunk
                                record_chat_completion(
                                    model=model,
                                    stream=True,
                                    status_code=200,
                                    duration_ms=_duration_ms(started_at),
                                    usage=usage,
                                )
                                recorded = True
                                return
                        except httpx.HTTPError as exc:
                            if (
                                _is_retryable_error(exc)
                                and not has_sent_chunk
                                and attempt < UPSTREAM_RETRY_ATTEMPTS - 1
                            ):
                                await _sleep_before_retry(attempt)
                                continue
                            error = _upstream_transport_error(exc)
                            record_chat_completion(
                                model=model,
                                stream=True,
                                status_code=502,
                                duration_ms=_duration_ms(started_at),
                                usage=usage,
                                error=error[1],
                            )
                            recorded = True
                            yield _sse_error(error)
                            return
                finally:
                    if not recorded:
                        record_chat_completion(
                            model=model,
                            stream=True,
                            status_code=499,
                            duration_ms=_duration_ms(started_at),
                            usage=usage,
                            error="Stream closed before completion",
                        )

            return StreamingResponse(
                streamer(),
                status_code=200,
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache"},
            )

        access_token = token
        try:
            upstream_resp = await _request_with_retries(
                client,
                "POST",
                url,
                headers=headers_with_token(access_token),
                content=body_bytes,
            )
        except httpx.HTTPError as exc:
            error = _upstream_transport_error(exc)
            record_chat_completion(
                model=model,
                stream=False,
                status_code=502,
                duration_ms=_duration_ms(started_at),
                error=error[1],
            )
            return JSONResponse(
                content=_openai_error_content(error),
                status_code=502,
            )

        if upstream_resp.status_code != 200:
            error_text = _trim_error_text(upstream_resp.text) or "Upstream error"
            record_chat_completion(
                model=model,
                stream=False,
                status_code=upstream_resp.status_code,
                duration_ms=_duration_ms(started_at),
                error=error_text,
            )
            return JSONResponse(
                content=_openai_error_content((upstream_resp.status_code, error_text)),
                status_code=upstream_resp.status_code,
            )

        response_payload = _json_payload(upstream_resp)
        error = _business_error(response_payload)
        if error and error[0] in ACCESS_TOKEN_ERROR_CODES:
            refreshed_token = await refresh_access_token(proxy=proxy)
            if refreshed_token:
                access_token = refreshed_token
                try:
                    upstream_resp = await _request_with_retries(
                        client,
                        "POST",
                        url,
                        retry_statuses=True,
                        headers=headers_with_token(access_token),
                        content=body_bytes,
                    )
                except httpx.HTTPError as exc:
                    error = _upstream_transport_error(exc)
                    record_chat_completion(
                        model=model,
                        stream=False,
                        status_code=502,
                        duration_ms=_duration_ms(started_at),
                        error=error[1],
                    )
                    return JSONResponse(
                        content=_openai_error_content(error),
                        status_code=502,
                    )

                if upstream_resp.status_code != 200:
                    error_text = (
                        _trim_error_text(upstream_resp.text) or "Upstream error"
                    )
                    record_chat_completion(
                        model=model,
                        stream=False,
                        status_code=upstream_resp.status_code,
                        duration_ms=_duration_ms(started_at),
                        error=error_text,
                    )
                    return JSONResponse(
                        content=_openai_error_content(
                            (upstream_resp.status_code, error_text)
                        ),
                        status_code=upstream_resp.status_code,
                    )

                response_payload = _json_payload(upstream_resp)
                error = _business_error(response_payload)

        if error:
            status_code = _business_error_status(error)
            record_chat_completion(
                model=model,
                stream=False,
                status_code=status_code,
                duration_ms=_duration_ms(started_at),
                error=error[1],
            )
            return JSONResponse(
                content=_openai_error_content(error),
                status_code=status_code,
            )

        if response_payload is not None:
            response_content = response_payload
        else:
            response_content = {"data": upstream_resp.text}

        record_chat_completion(
            model=model,
            stream=False,
            status_code=upstream_resp.status_code,
            duration_ms=_duration_ms(started_at),
            usage=extract_usage(response_payload),
        )

        return JSONResponse(
            content=response_content,
            status_code=upstream_resp.status_code,
        )

    return app


def run_server(host: str, port: int, api_key: str | None, proxy: str | None) -> None:
    import uvicorn

    app = build_app(api_key=api_key, proxy=proxy)
    uvicorn.run(app, host=host, port=port)
