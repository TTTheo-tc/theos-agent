"""CLI ``theos agent`` command — interactive loop."""

from __future__ import annotations

import asyncio
import os
import signal

import typer

from src.cli.display import (
    _ANSI_RE,
    console,
    print_agent_banner,
    print_agent_response,
    print_token_usage,
)
from src.cli.repl import (
    flush_pending_tty_input,
    init_prompt_session,
    is_exit_command,
    read_interactive_input,
    restore_terminal,
)
from src.utils.helpers import sync_workspace_templates
from src.utils.usage import merge_usage


def _profile_allows_tool(profile: str | None, tool_name: str) -> bool:
    from src.agent.tools.tool_profiles import profile_allows_tool

    return profile_allows_tool(profile, tool_name)


def agent(
    message: str = typer.Option(None, "--message", "-m", help="Message to send to the agent"),
    session_id: str = typer.Option("cli:direct", "--session", "-s", help="Session ID"),
    markdown: bool = typer.Option(
        True, "--markdown/--no-markdown", help="Render assistant output as Markdown"
    ),
    logs: bool = typer.Option(
        False, "--logs/--no-logs", help="Show theos runtime logs during chat"
    ),
):
    """Interact with the agent directly."""
    from loguru import logger

    from src.agent.loop import AgentLoop
    from src.bus.queue import MessageBus
    from src.config.loader import load_config
    from src.security.secret_refs import resolve_data_secret_refs

    config = load_config()
    sync_workspace_templates(config.workspace_path)

    bus = MessageBus()
    from src.providers.factory import make_provider

    try:
        provider = make_provider(config)
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)

    cron = None
    if _profile_allows_tool(config.tools.profile, "cron"):
        from src.config.loader import get_data_dir
        from src.cron.service import CronService

        # Create cron service for tool usage (no callback needed for CLI unless running)
        cron_store_path = get_data_dir() / "cron" / "jobs.json"
        cron = CronService(cron_store_path)

    if logs:
        logger.enable("src")
    else:
        logger.remove()  # silence all loguru output

    # CLI interactive mode: always show tool hints so user sees agent progress
    cli_channels = resolve_data_secret_refs(config.channels.model_copy())
    cli_channels.send_tool_hints = True

    agent_loop = AgentLoop(
        bus=bus,
        provider=provider,
        config=config,
        cron_service=cron,
        channels_config_override=cli_channels,
    )

    # Subtle thinking indicator (like Claude Code)
    def _thinking_ctx():
        if logs or agent_loop.is_genver:
            from contextlib import nullcontext

            return nullcontext()
        return console.status("[dim]…[/dim]", spinner="dots")

    async def _cli_progress(content: str, *, tool_hint: bool = False) -> None:
        ch = agent_loop.channels_config
        if ch and tool_hint and not ch.send_tool_hints:
            return
        if ch and not tool_hint and not ch.send_progress:
            return
        console.print(f"  [dim]\u21b3 {content}[/dim]")

    if message:
        # Single message mode — direct call, no bus needed
        if logs:
            diag = agent_loop.get_diagnostics()
            console.print(
                f"[dim]{diag['model']} · {diag['mode']} mode · {diag['tools']} tools[/dim]"
            )

        async def run_once():
            with _thinking_ctx():
                response = await agent_loop.process_direct(
                    message, session_id, on_progress=_cli_progress
                )
            print_agent_response(response, render_markdown=markdown)
            print_token_usage(getattr(agent_loop, "_last_usage", None))
            await agent_loop.drain_and_consolidate(session_id)
            await agent_loop.close()

        asyncio.run(run_once())
    else:
        # Interactive mode — route through bus like other channels
        from src.bus.events import InboundMessage

        init_prompt_session()

        # Startup diagnostics banner
        diag = agent_loop.get_diagnostics()
        mode_str = diag["mode"]
        tool_count = diag["tools"]
        details = []
        if logs:
            if diag.get("genver"):
                gv = diag["genver"]
                details.append(
                    f"genver: gen={gv['generator']} ver={gv['verifier']} exp={gv['explorer']}"
                )
            if diag.get("roles"):
                for rname, rmodel in diag["roles"].items():
                    details.append(f"role/{rname}: {rmodel}")
            if diag["mcp_servers"]:
                details.append(f"mcp: {diag['mcp_servers']} server(s)")
            if diag["orchestrator"]:
                details.append("orchestrator: enabled")
            if diag["hooks"]:
                details.append(f"hooks: {diag['hooks']}")
        print_agent_banner(
            model=config.agents.defaults.model,
            mode=f"{mode_str} mode",
            tools=tool_count,
            workspace=config.workspace_path,
            session_id=session_id,
            logs=logs,
            tool_names=diag["tool_names"],
            details=details,
        )
        console.print()

        if ":" in session_id:
            cli_channel, cli_chat_id = session_id.split(":", 1)
        else:
            cli_channel, cli_chat_id = "cli", session_id

        def _exit_on_sigint(signum, frame):
            restore_terminal()
            console.print("\nGoodbye!")
            os._exit(0)

        signal.signal(signal.SIGINT, _exit_on_sigint)

        async def run_interactive():
            bus_task = asyncio.create_task(agent_loop.run())
            turn_done = asyncio.Event()
            turn_done.set()
            wizard_active = asyncio.Event()
            wizard_active.set()  # cleared while wizard is running
            turn_response: list[str] = []
            turn_usage: list[dict] = []
            session_usage: dict[str, int] = {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            }
            turn_streamed = False  # True if streaming deltas were printed this turn

            def _print_turn_usage() -> None:
                if not turn_usage:
                    return
                usage = turn_usage[-1]
                merge_usage(session_usage, usage)
                print_token_usage(usage, session_usage=session_usage)

            async def _consume_outbound():
                nonlocal turn_streamed
                while True:
                    try:
                        msg = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
                        if msg.metadata.get("_genver_ask"):
                            # GenVer is asking the user for guidance mid-loop
                            console.print(f"\n[yellow]{msg.content}[/yellow]")
                            try:
                                answer = await read_interactive_input(prompt="genver> ")
                            except (EOFError, KeyboardInterrupt):
                                answer = "abort"
                            await bus.publish_inbound(
                                InboundMessage(
                                    channel=cli_channel,
                                    sender_id="user",
                                    chat_id=cli_chat_id,
                                    content=answer,
                                    sender_is_owner=True,
                                )
                            )
                            continue
                        if msg.metadata.get("_progress"):
                            is_tool_hint = msg.metadata.get("_tool_hint", False)
                            is_stream = msg.metadata.get("_progress_kind") == "stream"
                            ch = agent_loop.channels_config
                            if ch and is_tool_hint and not ch.send_tool_hints:
                                pass
                            elif ch and not is_tool_hint and not is_stream and not ch.send_progress:
                                pass
                            elif is_stream:
                                # Streaming delta — print inline, suppress final duplicate
                                console.print(_ANSI_RE.sub("", msg.content), end="")
                                turn_streamed = True
                            else:
                                console.print(
                                    f"  [dim]\u21b3 {_ANSI_RE.sub('', msg.content)}[/dim]"
                                )
                        elif not turn_done.is_set():
                            if msg.content == AgentLoop._AGENT_TEAM_NEEDS_SETUP:
                                # Stop spinner first — Rich Live display conflicts with
                                # typer.prompt() writing to stdout from a thread.
                                turn_done.set()
                                wizard_active.clear()  # pause main loop's read_interactive_input
                                try:
                                    from src.cli.init_cmd import configure_roles_interactive
                                    from src.config.loader import load_config, save_config

                                    roles = await asyncio.to_thread(configure_roles_interactive)
                                    if roles:
                                        cfg = load_config()
                                        cfg.agents.roles = roles
                                        cfg.agents.mode = "team"
                                        save_config(cfg)
                                        agent_loop.genver_config = None
                                        agent_loop._root_agent_mode = "team"
                                        agent_loop.reload_roles(roles)
                                        agent_loop.rebuild_tools()
                                        role_lines = "\n".join(
                                            f"  • {r}: {c.model}" for r, c in roles.items()
                                        )
                                        console.print(f"\n✓ Switched to team mode.\n{role_lines}")
                                    else:
                                        console.print("\nTeam setup skipped.")
                                finally:
                                    wizard_active.set()
                            elif msg.content == AgentLoop._AGENT_GENVER_NEEDS_SETUP:
                                turn_done.set()
                                wizard_active.clear()
                                try:
                                    from src.cli.init_cmd import configure_genver_interactive
                                    from src.config.loader import load_config, save_config

                                    gv_config = await asyncio.to_thread(
                                        configure_genver_interactive
                                    )
                                    if gv_config:
                                        cfg = load_config()
                                        cfg.agents.genver = gv_config
                                        cfg.agents.mode = "genver"
                                        save_config(cfg)
                                        agent_loop.apply_genver_config(gv_config)
                                        console.print(
                                            f"\n✓ Switched to Generator-Verifier mode.\n"
                                            f"  Generator: {gv_config.generator_model}\n"
                                            f"  Verifier: {gv_config.verifier_model}\n"
                                            f"  Explorer: {gv_config.explorer_model}\n"
                                            f"  Verifier commands: {', '.join(gv_config.verifier_commands)}"
                                        )
                                    else:
                                        console.print("\nGenVer setup skipped.")
                                finally:
                                    wizard_active.set()
                            elif msg.content:
                                turn_response.append(msg.content)
                                if msg.metadata.get("usage"):
                                    turn_usage.append(msg.metadata["usage"])
                            turn_done.set()
                        elif msg.content:
                            console.print()
                            print_agent_response(msg.content, render_markdown=markdown)
                    except asyncio.TimeoutError:
                        continue
                    except asyncio.CancelledError:
                        break

            outbound_task = asyncio.create_task(_consume_outbound())

            try:
                while True:
                    try:
                        await wizard_active.wait()  # block here while wizard is running
                        flush_pending_tty_input()
                        user_input = await read_interactive_input()
                        command = user_input.strip()
                        if not command:
                            continue

                        if is_exit_command(command):
                            restore_terminal()
                            console.print("\nGoodbye!")
                            break

                        turn_done.clear()
                        turn_response.clear()
                        turn_usage.clear()
                        turn_streamed = False

                        await bus.publish_inbound(
                            InboundMessage(
                                channel=cli_channel,
                                sender_id="user",
                                chat_id=cli_chat_id,
                                content=user_input,
                                sender_is_owner=True,
                            )
                        )

                        with _thinking_ctx():
                            await turn_done.wait()

                        if turn_streamed:
                            # Streaming already printed the content inline;
                            # just add a newline to end the stream block.
                            console.print()
                            _print_turn_usage()
                        elif turn_response:
                            print_agent_response(turn_response[0], render_markdown=markdown)
                            _print_turn_usage()
                        else:
                            _print_turn_usage()
                    except KeyboardInterrupt:
                        restore_terminal()
                        console.print("\nGoodbye!")
                        break
                    except EOFError:
                        restore_terminal()
                        console.print("\nGoodbye!")
                        break
            finally:
                await agent_loop.drain_and_consolidate(session_id)
                agent_loop.stop()
                outbound_task.cancel()
                await asyncio.gather(bus_task, outbound_task, return_exceptions=True)
                await agent_loop.close()

        asyncio.run(run_interactive())
