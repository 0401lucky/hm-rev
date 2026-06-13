"""FastAPI OpenAI-compatible proxy server for DevEco Code."""

from __future__ import annotations

import json
import uuid
from typing import AsyncGenerator

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .config import DEVECO_BASE_URL, USER_AGENT
from .crypto import load_auth_data


TARGET_BASE = f"{DEVECO_BASE_URL}/sse/codeGenie/maas"


def _current_access_token() -> str | None:
    data = load_auth_data()
    deveco = data.get("deveco", {})
    access = deveco.get("access")
    return access if access else None


def build_app(api_key: str | None = None, proxy: str | None = None) -> FastAPI:
    app = FastAPI(title="hm-api", version="0.1.0")
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
        if api_key is None:
            return await call_next(request)
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or auth[7:] != api_key:
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        return await call_next(request)

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
                    models.append({"id": model_id, "object": "model", "owned_by": "deveco"})
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

        session_id = request.headers.get("x-deveco-session") or request.headers.get("x-session-affinity")
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
                async with client.stream("POST", url, headers=upstream_headers, content=body_bytes) as upstream_resp:
                    if upstream_resp.status_code != 200:
                        text = await upstream_resp.aread()
                        yield json.dumps({"error": text.decode("utf-8", errors="replace") or "Upstream error"}).encode()
                        return
                    async for chunk in upstream_resp.aiter_bytes():
                        yield chunk

            return StreamingResponse(
                streamer(),
                status_code=200,
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache"},
            )

        upstream_resp = await client.post(url, headers=upstream_headers, content=body_bytes)

        if upstream_resp.status_code != 200:
            return JSONResponse(
                content={"error": upstream_resp.text or "Upstream error"},
                status_code=upstream_resp.status_code,
            )

        return JSONResponse(
            content=upstream_resp.json()
                if upstream_resp.headers.get("content-type", "").startswith("application/json")
                else {"data": upstream_resp.text},
            status_code=upstream_resp.status_code,
        )

    return app


def run_server(host: str, port: int, api_key: str | None, proxy: str | None) -> None:
    import uvicorn

    app = build_app(api_key=api_key, proxy=proxy)
    uvicorn.run(app, host=host, port=port)
