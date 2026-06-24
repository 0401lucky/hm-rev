"""CLI entry point."""

from __future__ import annotations

import asyncio
from html import escape
from typing import Optional
from urllib.parse import urlencode, urlparse

import httpx
import typer
from rich.console import Console

from .config import DEFAULT_HOST, DEFAULT_PORT
from .login import is_logged_in, load_session, login, parse_callback_request
from .server import run_server

app = typer.Typer(help="hm-api - DevEco Code OpenAI-compatible API CLI")
console = Console()


def _empty_as_none(value: str | None) -> str | None:
    return value if value else None


def _bridge_page(title: str, message: str, ok: bool) -> bytes:
    accent = "#107c41" if ok else "#9f1d20"
    safe_title = escape(title)
    safe_message = escape(message)
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title}</title>
  <style>
    body {{
      min-height: 100vh;
      margin: 0;
      display: grid;
      place-items: center;
      background: #f4f6f1;
      color: #171a1f;
      font-family: "Microsoft YaHei", "PingFang SC", sans-serif;
    }}
    main {{
      width: min(480px, calc(100vw - 40px));
      border: 1px solid #d6ddd2;
      background: #fff;
      padding: 30px;
      box-shadow: 0 24px 80px rgba(21, 31, 23, .12);
    }}
    .mark {{
      width: 46px;
      height: 6px;
      margin-bottom: 24px;
      background: {accent};
    }}
    h1 {{
      margin: 0 0 12px;
      font-size: 26px;
    }}
    p {{
      margin: 0;
      color: #5f685f;
      line-height: 1.7;
    }}
  </style>
</head>
<body>
  <main>
    <div class="mark"></div>
    <h1>{safe_title}</h1>
    <p>{safe_message}</p>
  </main>
</body>
</html>"""
    return html.encode("utf-8")


async def _write_bridge_response(
    writer: asyncio.StreamWriter, title: str, message: str, ok: bool
) -> None:
    body = _bridge_page(title, message, ok)
    writer.write(
        (
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: text/html; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\n"
            "\r\n"
        ).encode("utf-8")
        + body
    )
    await writer.drain()
    writer.close()
    await writer.wait_closed()


async def _bridge_callback_handler(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    target: str,
    proxy: str | None,
    key: str | None,
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

    values = {key: value for key, value in params.items() if value}
    if not values.get("tempToken"):
        if not future.done():
            future.set_exception(RuntimeError("回调里没有 tempToken"))
        await _write_bridge_response(
            writer, "桥接失败", "DevEco 回调里没有 tempToken。", ok=False
        )
        return

    mounts = None
    if proxy:
        transport = httpx.AsyncHTTPTransport(proxy=proxy)
        mounts = {"http://": transport, "https://": transport}

    try:
        headers = {"Authorization": f"Bearer {key}"} if key else None
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30.0), follow_redirects=True, mounts=mounts
        ) as client:
            resp = await client.post(
                f"{target.rstrip('/')}/api/auth/import",
                json={"callback": urlencode(values)},
                headers=headers,
            )
        if resp.status_code != 200:
            raise RuntimeError(resp.text or f"HTTP {resp.status_code}")
    except Exception as exc:
        if not future.done():
            future.set_exception(exc)
        await _write_bridge_response(writer, "桥接失败", str(exc), ok=False)
        return

    if not future.done():
        future.set_result(True)
    await _write_bridge_response(
        writer,
        "桥接成功",
        "授权凭据已转发到云端服务，可以回到 hm-api 控制台查看状态。",
        ok=True,
    )


@app.command("login")
def login_cmd(
    proxy: Optional[str] = typer.Option(
        "", "--proxy", "-p", help="HTTP/HTTPS proxy for login requests"
    ),
    no_browser: Optional[bool] = typer.Option(
        False, "--no-browser", help="Print login URL instead of opening browser"
    ),
    timeout: Optional[int] = typer.Option(
        600, "--timeout", "-t", help="Login callback timeout in seconds", min=60
    ),
) -> None:
    """Login with Huawei DevEco account via browser OAuth."""
    proxy = _empty_as_none(proxy)
    if no_browser:
        console.print("[bold blue]Use the URL below to login:[/bold blue]")
    else:
        console.print("[bold blue]Opening browser for DevEco login...[/bold blue]")
    result = asyncio.run(
        login(proxy=proxy, no_browser=no_browser or False, timeout=timeout or 600)
    )
    if result.success and result.user_info:
        console.print(
            f"[bold green]Login successful![/bold green] Welcome, {result.user_info.user_name}"
        )
    elif result.cancelled:
        console.print("[yellow]Login cancelled by user.[/yellow]")
        raise typer.Exit(1)
    elif result.unsupported_region:
        console.print("[red]Only China site accounts are currently supported.[/red]")
        raise typer.Exit(1)
    else:
        console.print(f"[red]Login failed:[/red] {result.error}")
        raise typer.Exit(1)


@app.command()
def serve(
    host: Optional[str] = typer.Option(
        DEFAULT_HOST, "--host", "-h", envvar="HM_HOST", help="Host to bind the server"
    ),
    port: Optional[int] = typer.Option(
        DEFAULT_PORT,
        "--port",
        "-p",
        envvar="HM_PORT",
        help="Port to bind the server",
        min=1,
        max=65535,
    ),
    proxy: Optional[str] = typer.Option(
        "", "--proxy", envvar="HM_PROXY", help="HTTP/HTTPS proxy for upstream requests"
    ),
    key: Optional[str] = typer.Option(
        None,
        "--key",
        "-k",
        envvar="HM_API_KEY",
        help="API key for client authentication; omit to disable auth",
    ),
) -> None:
    """Start the OpenAI-compatible API server."""
    if not is_logged_in():
        console.print(
            "[yellow]Not logged in. Open the web panel and authorize DevEco first.[/yellow]"
        )
    else:
        import asyncio

        session = asyncio.run(load_session())
        if session:
            console.print(
                f"[blue]Logged in as {session.get('user_name') or session.get('user_id')}.[/blue]"
            )

    proxy = _empty_as_none(proxy)
    key = _empty_as_none(key)
    bind_host = host or DEFAULT_HOST
    bind_port = port or DEFAULT_PORT
    panel_host = "127.0.0.1" if bind_host in {"0.0.0.0", "::"} else bind_host

    console.print(
        f"[bold green]Starting server at http://{bind_host}:{bind_port}[/bold green]"
    )
    console.print(
        f"[bold green]Web panel at http://{panel_host}:{bind_port}[/bold green]"
    )
    if key:
        console.print("[dim]API key authentication enabled.[/dim]")
    else:
        console.print("[dim]API key authentication disabled.[/dim]")
    if proxy:
        console.print(f"[dim]Upstream proxy: {proxy}[/dim]")

    run_server(host=bind_host, port=bind_port, api_key=key, proxy=proxy)


@app.command("bridge")
def bridge(
    target: str = typer.Option(
        ...,
        "--target",
        "-t",
        help="Cloud hm-api panel origin, for example https://your-app.zeabur.app",
    ),
    port: int = typer.Option(
        DEFAULT_PORT,
        "--port",
        "-p",
        help="Local callback port DevEco redirects to",
        min=1,
        max=65535,
    ),
    proxy: Optional[str] = typer.Option(
        "", "--proxy", help="HTTP/HTTPS proxy for forwarding to the cloud service"
    ),
    key: Optional[str] = typer.Option(
        None,
        "--key",
        "-k",
        envvar="HM_API_KEY",
        help="API key for the target hm-api service",
    ),
    timeout: int = typer.Option(
        600, "--timeout", help="Bridge timeout in seconds", min=60
    ),
) -> None:
    """Forward a local DevEco OAuth callback to a cloud hm-api deployment."""
    parsed = urlparse(target)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        console.print("[red]Invalid target. Use a full http(s) URL.[/red]")
        raise typer.Exit(1)

    proxy = _empty_as_none(proxy)
    key = _empty_as_none(key)

    async def run_bridge() -> None:
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()

        async def handler(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            await _bridge_callback_handler(
                reader,
                writer,
                target=target,
                proxy=proxy,
                key=key,
                future=future,
            )

        try:
            server = await asyncio.start_server(handler, "127.0.0.1", port)
        except OSError as exc:
            raise RuntimeError(f"Cannot listen on 127.0.0.1:{port}: {exc}") from exc

        console.print(
            f"[bold green]Callback bridge listening at http://127.0.0.1:{port}/callback[/bold green]"
        )
        console.print(f"[blue]Forwarding DevEco callback to {target}[/blue]")
        console.print(
            "[dim]Keep this command running, then click authorization on the web panel.[/dim]"
        )

        async with server:
            try:
                await asyncio.wait_for(future, timeout=timeout)
            finally:
                server.close()
                await server.wait_closed()

    try:
        asyncio.run(run_bridge())
    except asyncio.TimeoutError:
        console.print("[yellow]Bridge timed out.[/yellow]")
        raise typer.Exit(1)
    except Exception as exc:
        console.print(f"[red]Bridge failed:[/red] {exc}")
        raise typer.Exit(1)
    else:
        console.print("[bold green]Bridge completed.[/bold green]")


@app.command()
def status() -> None:
    """Show current login status."""
    import asyncio

    if is_logged_in():
        session = asyncio.run(load_session())
        if session:
            console.print(
                f"[green]Logged in[/green] as {session.get('user_name') or session.get('user_id')}"
            )
        else:
            console.print("[green]Logged in[/green]")
    else:
        console.print("[red]Not logged in[/red]")


if __name__ == "__main__":
    app()
