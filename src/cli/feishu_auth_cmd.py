"""CLI Feishu OAuth command."""

from __future__ import annotations

import typer

from src.cli.display import console


def feishu_auth(
    port: int = typer.Option(9527, help="Local callback server port"),
    reconfigure: bool = typer.Option(
        False, "--reconfigure", "-r", help="Update app_id and app_secret before auth"
    ),
    remote: bool = typer.Option(
        False, "--remote", help="Remote mode: send auth URL via Feishu, wait for code"
    ),
    send_to: str = typer.Option(
        None, "--send-to", help="Feishu open_id to send auth URL to (for --remote)"
    ),
    code: str = typer.Option(
        None, "--code", "-c", help="Exchange an authorization code directly (skip browser)"
    ),
) -> None:
    """Authorize Feishu user token (OAuth flow).

    Use --reconfigure / -r to update app credentials.
    Use --remote to send auth URL via Feishu bot (for phone authorization).
    Use --code to exchange a code directly without opening a browser.
    """
    from src.config.loader import load_config, save_config
    from src.feishu.token import check_token_valid, init_token

    config = load_config()
    fs = config.channels.feishu

    # --reconfigure: prompt for new credentials
    if reconfigure or not fs.app_id or not fs.app_secret:
        console.print("Configure Feishu app credentials:\n")
        if fs.app_id:
            console.print(f"  Current App ID: [dim]{fs.app_id[:10]}...[/dim]")
        new_id = typer.prompt("  App ID", default=fs.app_id or "", prompt_suffix=" ").strip()
        new_secret = typer.prompt(
            "  App Secret", default=fs.app_secret or "", prompt_suffix=" "
        ).strip()
        if not new_id or not new_secret:
            console.print("[red]App ID and App Secret are required.[/red]")
            raise typer.Exit(1)
        fs.app_id = new_id
        fs.app_secret = new_secret
        fs.enabled = True
        save_config(config)
        console.print("[green]Credentials saved.[/green]\n")

    token_dir = fs.token_dir or "~/.theos/feishu_tokens"

    # --code: direct code exchange (no browser needed)
    if code:
        from src.feishu.remote_auth import exchange_auth_code

        result = exchange_auth_code(
            code=code,
            app_id=fs.app_id,
            app_secret=fs.app_secret,
            token_dir=token_dir,
        )
        if result["ok"]:
            console.print(
                f"[green]✓ Authorized successfully.[/green] "
                f"refresh_token TTL={result['refresh_token_ttl'] / 86400:.1f}d"
            )
        else:
            console.print(f"[red]✗ Authorization failed: {result['error']}[/red]")
            raise typer.Exit(1)
        return

    # --remote: send auth URL via Feishu bot message
    if remote:
        from src.feishu.oauth_callback import register_oauth_state
        from src.feishu.remote_auth import (
            generate_auth_url,
            get_gateway_redirect_uri,
            is_callback_server_alive,
        )

        gateway_uri = get_gateway_redirect_uri()
        auto_exchange = bool(gateway_uri and is_callback_server_alive(gateway_uri))

        # Prefer gateway callback URL (auto-exchange) over localhost
        if auto_exchange and gateway_uri:
            redirect_uri = gateway_uri
            console.print(f"[green]Using gateway callback (auto-exchange): {redirect_uri}[/green]")
        else:
            redirect_uri = f"http://localhost:{port}/callback"
            if gateway_uri:
                console.print(
                    "[yellow]Gateway callback is not reachable from this machine. "
                    "Falling back to manual code exchange.[/yellow]"
                )
        auth_url, state = generate_auth_url(app_id=fs.app_id, redirect_uri=redirect_uri)
        if auto_exchange:
            try:
                register_oauth_state(state, token_dir=token_dir, redirect_uri=redirect_uri)
            except Exception as exc:
                console.print(
                    f"[yellow]Could not register callback state ({exc}). "
                    "Falling back to manual code exchange.[/yellow]"
                )
                auto_exchange = False
                redirect_uri = f"http://localhost:{port}/callback"
                auth_url, state = generate_auth_url(app_id=fs.app_id, redirect_uri=redirect_uri)

        console.print("[cyan]Auth URL generated.[/cyan]\n")
        console.print(f"  {auth_url}\n")

        # Try to send via Feishu bot
        target = send_to or (config.channels.owner_ids[0] if config.channels.owner_ids else None)
        if target:
            try:
                import json as _json

                from lark_oapi.api.im.v1 import (
                    CreateMessageRequest,
                    CreateMessageRequestBody,
                )

                from src.feishu.api import make_client

                client = make_client(fs.app_id, fs.app_secret)
                receive_id_type = "chat_id" if target.startswith("oc_") else "open_id"
                card = {
                    "config": {"wide_screen_mode": True},
                    "header": {
                        "title": {"tag": "plain_text", "content": "🔑 飞书授权"},
                        "template": "orange",
                    },
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": (
                                (
                                    "Token 已过期，需要重新授权。\n\n"
                                    "**步骤：**\n"
                                    "1. 点击下方按钮授权\n"
                                    "2. 授权完成后 token 会自动保存\n"
                                    "3. 无需复制 code\n"
                                )
                                if auto_exchange
                                else (
                                    "Token 已过期，需要重新授权。\n\n"
                                    "**步骤：**\n"
                                    "1. 点击下方按钮授权\n"
                                    "2. 授权后页面会跳转（可能显示无法访问）\n"
                                    "3. 复制地址栏中 `code=` 后面的内容\n"
                                    "4. 发给我即可完成授权"
                                )
                            ),
                        },
                        {
                            "tag": "action",
                            "actions": [
                                {
                                    "tag": "button",
                                    "text": {"tag": "plain_text", "content": "🔗 点击授权"},
                                    "type": "primary",
                                    "multi_url": {
                                        "url": auth_url,
                                        "pc_url": auth_url,
                                        "android_url": auth_url,
                                        "ios_url": auth_url,
                                    },
                                }
                            ],
                        },
                    ],
                }
                request = (
                    CreateMessageRequest.builder()
                    .receive_id_type(receive_id_type)
                    .request_body(
                        CreateMessageRequestBody.builder()
                        .receive_id(target)
                        .msg_type("interactive")
                        .content(_json.dumps(card, ensure_ascii=False))
                        .build()
                    )
                    .build()
                )
                response = client.im.v1.message.create(request)
                if response.success():
                    console.print(f"[green]✓ Auth URL sent to {target}[/green]")
                else:
                    console.print(
                        f"[yellow]Failed to send: code={response.code}, msg={response.msg}[/yellow]"
                    )
                    console.print("Copy the URL above and open it manually.")
            except Exception as e:
                console.print(f"[yellow]Could not send via Feishu: {e}[/yellow]")
                console.print("Copy the URL above and open it manually.")
        else:
            console.print(
                "[yellow]No --send-to or owner_ids configured. Copy the URL above.[/yellow]"
            )

        if auto_exchange:
            console.print(
                "[green]Open the link on any device that can reach your Tailscale/private URL. "
                "After the success page appears, the token is already saved.[/green]"
            )
        else:
            # Wait for code input
            console.print()
            auth_code = typer.prompt("Enter the authorization code", prompt_suffix=" ").strip()
            if not auth_code:
                console.print("[red]No code provided.[/red]")
                raise typer.Exit(1)

            from src.feishu.remote_auth import exchange_auth_code

            result = exchange_auth_code(
                code=auth_code,
                app_id=fs.app_id,
                app_secret=fs.app_secret,
                redirect_uri=redirect_uri,
                token_dir=token_dir,
            )
            if result["ok"]:
                console.print(
                    f"\n[green]✓ Authorized successfully.[/green] "
                    f"refresh_token TTL={result['refresh_token_ttl'] / 86400:.1f}d"
                )
            else:
                console.print(f"\n[red]✗ Authorization failed: {result['error']}[/red]")
                raise typer.Exit(1)
        return

    # Default: local browser flow
    if check_token_valid(token_dir):
        console.print("[green]Current user token is valid.[/green]")
        if not typer.confirm("Re-authorize anyway?", default=False):
            raise typer.Exit()

    console.print(f"Starting OAuth flow on port {port}...")
    console.print(
        f"[yellow]Ensure redirect URI is set to "
        f"http://localhost:{port}/callback in Feishu developer console.[/yellow]\n"
    )
    from src.feishu.remote_auth import FEISHU_OAUTH_SCOPES

    try:
        token = init_token(
            app_id=fs.app_id,
            app_secret=fs.app_secret,
            redirect_uri=f"http://localhost:{port}/callback",
            scope=FEISHU_OAUTH_SCOPES,
            enable_autofill=True,
        )
        console.print(f"\n[green]Authorized successfully. Token: {token[:8]}...[/green]")
    except Exception as e:
        console.print(f"\n[red]Authorization failed: {e}[/red]")
        raise typer.Exit(1)
