"""FastAPI OpenAI-compatible proxy server for DevEco Code."""

from __future__ import annotations

import json
import time
import uuid
from html import escape
from pathlib import Path
from typing import AsyncGenerator
from urllib.parse import parse_qs

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
)


TARGET_BASE = f"{DEVECO_BASE_URL}/sse/codeGenie/maas"
WEB_DIR = Path(__file__).resolve().parent / "web"


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


def _current_access_token() -> str | None:
    data = load_auth_data()
    deveco = data.get("deveco", {})
    access = deveco.get("access")
    return access if access else None


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
        timeout=httpx.Timeout(600.0),
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
        resp = await client.get(
            f"{DEVECO_BASE_URL}/codeGenie/modelConfig?localVersion=0&pluginVersion=CLI.0.1.0",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=resp.text)
        data = resp.json()
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
        token = _current_access_token()
        if not token:
            raise HTTPException(status_code=401, detail="Not logged in")

        body_bytes = await request.body()
        if not body_bytes:
            body_bytes = b"{}"
        try:
            body_json = json.loads(body_bytes)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON body")

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

        if stream:

            async def streamer() -> AsyncGenerator[bytes, None]:
                async with client.stream(
                    "POST", url, headers=upstream_headers, content=body_bytes
                ) as upstream_resp:
                    if upstream_resp.status_code != 200:
                        text = await upstream_resp.aread()
                        yield json.dumps(
                            {
                                "error": text.decode("utf-8", errors="replace")
                                or "Upstream error"
                            }
                        ).encode()
                        return
                    async for chunk in upstream_resp.aiter_bytes():
                        yield chunk

            return StreamingResponse(
                streamer(),
                status_code=200,
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache"},
            )

        upstream_resp = await client.post(
            url, headers=upstream_headers, content=body_bytes
        )

        if upstream_resp.status_code != 200:
            return JSONResponse(
                content={"error": upstream_resp.text or "Upstream error"},
                status_code=upstream_resp.status_code,
            )

        return JSONResponse(
            content=upstream_resp.json()
            if upstream_resp.headers.get("content-type", "").startswith(
                "application/json"
            )
            else {"data": upstream_resp.text},
            status_code=upstream_resp.status_code,
        )

    return app


def run_server(host: str, port: int, api_key: str | None, proxy: str | None) -> None:
    import uvicorn

    app = build_app(api_key=api_key, proxy=proxy)
    uvicorn.run(app, host=host, port=port)
