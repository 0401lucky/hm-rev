"""CLI entry point."""

from __future__ import annotations

import asyncio
from typing import Optional

import typer
from rich.console import Console

from .config import DEFAULT_HOST, DEFAULT_PORT
from .login import is_logged_in, load_session, login
from .server import run_server

app = typer.Typer(help="hm-api - DevEco Code OpenAI-compatible API CLI")
console = Console()


def _empty_as_none(value: str | None) -> str | None:
    return value if value else None


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
    result = asyncio.run(login(proxy=proxy, no_browser=no_browser or False, timeout=timeout or 600))
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
        DEFAULT_HOST, "--host", "-h", help="Host to bind the server"
    ),
    port: Optional[int] = typer.Option(
        DEFAULT_PORT, "--port", "-p", help="Port to bind the server", min=1, max=65535
    ),
    proxy: Optional[str] = typer.Option(
        "", "--proxy", help="HTTP/HTTPS proxy for upstream requests"
    ),
    key: Optional[str] = typer.Option(
        None, "--key", "-k", help="API key for client authentication; omit to disable auth"
    ),
) -> None:
    """Start the OpenAI-compatible API server."""
    if not is_logged_in():
        console.print(
            "[yellow]Not logged in. Run [bold]hm-api login[/bold] first.[/yellow]"
        )
        raise typer.Exit(1)

    import asyncio

    session = asyncio.run(load_session())
    if session:
        console.print(
            f"[blue]Logged in as {session.get('user_name') or session.get('user_id')}.[/blue]"
        )

    proxy = _empty_as_none(proxy)
    key = _empty_as_none(key)

    console.print(f"[bold green]Starting server at http://{host}:{port}[/bold green]")
    if key:
        console.print("[dim]API key authentication enabled.[/dim]")
    else:
        console.print("[dim]API key authentication disabled.[/dim]")
    if proxy:
        console.print(f"[dim]Upstream proxy: {proxy}[/dim]")

    run_server(host=host or DEFAULT_HOST, port=port or DEFAULT_PORT, api_key=key, proxy=proxy)


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
