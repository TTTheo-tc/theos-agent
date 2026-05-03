"""Slash command handlers extracted from AgentLoop.

``SlashCommandHandler`` encapsulates the ``/agent`` and ``/model`` commands,
keeping AgentLoop focused on message dispatch.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from src.bus.events import InboundMessage, OutboundMessage

if TYPE_CHECKING:
    from src.agent.loop import AgentLoop
    from src.config.schema import GenVerConfig


# Sentinel values so the CLI interactive loop can detect "needs setup"
AGENT_TEAM_NEEDS_SETUP = "__AGENT_TEAM_NEEDS_SETUP__"
AGENT_GENVER_NEEDS_SETUP = "__AGENT_GENVER_NEEDS_SETUP__"

# ---------------------------------------------------------------------------
# Channel capability matrix for slash commands.
# Scope flags: cli, owner, user (non-owner gateway), system (internal),
# subagent.  A command is allowed if its scope set includes the caller's
# context.
# ---------------------------------------------------------------------------

COMMAND_SCOPES: dict[str, frozenset[str]] = {
    "/new": frozenset({"cli", "owner", "user"}),
    "/reboot": frozenset({"cli", "owner"}),
    "/restart": frozenset({"cli", "owner"}),
    "/resume": frozenset({"cli", "owner", "user"}),
    "/agent": frozenset({"cli", "owner"}),
    "/model": frozenset({"cli", "owner"}),
    "/ui": frozenset({"cli", "owner"}),
    "/help": frozenset({"cli", "owner", "user", "subagent"}),
    "/stop": frozenset({"cli", "owner", "user"}),
    "/plan": frozenset({"cli", "owner"}),
    "/instinct": frozenset({"cli", "owner"}),
}


def resolve_caller_scope(msg: InboundMessage) -> str:
    """Map an inbound message to a scope tag.

    NOTE: "subagent" scope is defined in COMMAND_SCOPES but not yet
    resolvable here — subagents currently share the same channel as their
    parent.  When subagent-specific routing is added, detect it via
    ``msg.metadata.get("_subagent")`` or a dedicated channel tag.
    """
    if msg.channel == "system":
        return "system"
    if getattr(msg, "metadata", None) and msg.metadata.get("_subagent"):
        return "subagent"
    if msg.channel == "cli":
        return "cli"
    if getattr(msg, "sender_is_owner", False):
        return "owner"
    return "user"


def is_command_allowed(cmd: str, msg: InboundMessage) -> bool:
    """Check if *cmd* is allowed for the caller's scope.

    Unknown commands default to allowed (handled elsewhere as "not a command").
    """
    base = cmd.split()[0] if cmd else cmd
    scopes = COMMAND_SCOPES.get(base)
    if scopes is None:
        return True  # not in the matrix → not gated
    return resolve_caller_scope(msg) in scopes


# Short aliases for /model command — maps shorthand → full model ID
MODEL_ALIASES: dict[str, str] = {
    "opus": "anthropic/claude-opus-4-6",
    "sonnet": "anthropic/claude-sonnet-4-6",
    "haiku": "anthropic/claude-haiku-4-5",
    "gpt5": "openai/gpt-5.2",
    "gpt4o": "openai/gpt-4o",
    "o3": "openai/o3",
    "codex": "openai-codex/gpt-5.4",
    "deepseek": "deepseek/deepseek-chat",
    "r1": "deepseek/deepseek-reasoner",
    "gemini": "gemini/gemini-2.5-pro",
    "flash": "gemini/gemini-2.5-flash",
    "minimax": "minimax/MiniMax-M2.5",
    "groq": "groq/llama-3.3-70b-versatile",
}


def resolve_model_alias(name: str) -> str:
    """Resolve a short alias to a full model ID, or return as-is."""
    return MODEL_ALIASES.get(name.lower(), name)


def is_model_alias(name: str) -> bool:
    """Return True when *name* is a bare /model alias."""
    return name.lower() in MODEL_ALIASES


async def handle_agent_command(loop: "AgentLoop", msg: InboundMessage) -> OutboundMessage | None:
    """Handle ``/agent [single|team|genver]`` slash command for hot mode switching."""
    parts = msg.content.strip().split()
    subcommand = parts[1].lower() if len(parts) > 1 else ""

    if subcommand == "single":
        if loop.mode == "single" and not loop._is_genver:
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="Already in single-agent mode.",
            )
        from src.config.loader import load_config, save_config

        config = load_config()
        config.agents.mode = "single"
        save_config(config)
        loop.genver_config = None
        loop._root_agent_mode = "single"
        loop.reload_roles(config.agents.roles)
        loop.rebuild_tools()
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content="✓ Switched to single-agent mode.",
        )

    if subcommand == "team":
        from src.config.loader import load_config

        config = load_config()
        if config.agents.roles:
            # Roles already configured on disk — just reload them
            config.agents.mode = "team"
            from src.config.loader import save_config

            save_config(config)
            loop.genver_config = None
            loop._root_agent_mode = "team"
            loop.reload_roles(config.agents.roles)
            role_lines = [f"  • {r}: {c.model}" for r, c in config.agents.roles.items()]
            loop.rebuild_tools()
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="✓ Switched to team mode.\n" + "\n".join(role_lines),
            )
        # No roles configured — need interactive setup (CLI only)
        if msg.channel == "cli":
            # Signal the CLI layer to run the wizard
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=AGENT_TEAM_NEEDS_SETUP,
            )
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=(
                "No roles configured. Please run `theos init` or edit "
                "~/.theos/config.json to add roles, then try `/agent team` again."
            ),
        )

    if subcommand == "genver":
        from src.config.loader import load_config, save_config

        config = load_config()
        gv = config.agents.genver
        has_models = gv.generator_model or gv.verifier_model or gv.explorer_model

        if not has_models:
            if msg.channel == "cli":
                # Signal the CLI layer to run the interactive setup wizard
                return OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=AGENT_GENVER_NEEDS_SETUP,
                )
            return OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=(
                    "No genver models configured. Run `theos init` or edit "
                    "~/.theos/config.json to set genver models, "
                    "then try `/agent genver` again."
                ),
            )

        config.agents.mode = "genver"
        save_config(config)
        loop.genver_config = gv
        loop._root_agent_mode = "single"
        loop.reload_roles(config.agents.roles)
        loop.rebuild_tools()
        return _genver_status_message(loop, msg)

    # Show current status or usage
    if loop._is_genver:
        status = "Generator-Verifier mode"
    elif loop.mode == "team":
        role_lines = [f"  • {r}: {c.model}" for r, c in loop.roles.items()]
        status = "Team mode\n" + "\n".join(role_lines)
    elif loop.roles:
        role_lines = [f"  • {r}: {c.model}" for r, c in loop.roles.items()]
        status = "Single-agent mode\nDelegation roles loaded:\n" + "\n".join(role_lines)
    else:
        status = "Single-agent mode"
    return OutboundMessage(
        channel=msg.channel,
        chat_id=msg.chat_id,
        content=f"{status}\n\nUsage: /agent [single|team|genver]",
    )


def _genver_status_message(loop: "AgentLoop", msg: InboundMessage) -> OutboundMessage:
    """Build a status message showing current genver configuration."""
    gv = loop.genver_config
    return OutboundMessage(
        channel=msg.channel,
        chat_id=msg.chat_id,
        content=(
            "✓ Switched to Generator-Verifier mode.\n"
            f"  Generator: {gv.generator_model or loop.model}\n"
            f"  Verifier: {gv.verifier_model or loop.model}\n"
            f"  Explorer: {gv.explorer_model or loop.model}\n"
            f"  Max retries: {gv.max_retries}\n"
            f"  Verifier commands: {', '.join(gv.verifier_commands)}"
        ),
    )


def apply_genver_config(loop: "AgentLoop", genver_config: "GenVerConfig") -> None:
    """Apply a GenVerConfig and switch to genver mode."""
    loop.genver_config = genver_config
    loop._root_agent_mode = "single"
    loop.rebuild_tools()


async def handle_ui_command(loop: "AgentLoop", msg: InboundMessage) -> OutboundMessage | None:
    """Handle ``/ui`` — return dashboard URL."""
    ui_cfg = loop.config.gateway.ui
    if not ui_cfg.enabled:
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content="Dashboard is disabled in config (gateway.ui.enabled = false).",
        )

    from src.ui.tailscale import build_ui_url

    url = build_ui_url(ui_cfg.port, host=ui_cfg.host)
    return OutboundMessage(
        channel=msg.channel,
        chat_id=msg.chat_id,
        content=f"TheOS Dashboard\n{url}",
    )


async def handle_model_command(loop: "AgentLoop", msg: InboundMessage) -> OutboundMessage:
    """Handle ``/model [name]`` or ``/model <role> <name>`` for hot-switching models."""
    parts = msg.content.strip().split()
    # /model — show current
    if len(parts) == 1:
        lines = [f"Default model: {loop.model}"]
        if loop.roles:
            for r, c in loop.roles.items():
                lines.append(f"  • {r}: {c.model or loop.model}")
        aliases = ", ".join(MODEL_ALIASES.keys())
        lines.append(f"\nUsage: /model <name>  (aliases: {aliases})")
        if loop.roles:
            lines.append("       /model <role> <name>")
        return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id, content="\n".join(lines))

    from src.config.loader import load_config, save_config

    # /model <role> <name> — switch a specific role's model
    if len(parts) == 3 and loop.roles and parts[1] in loop.roles:
        role_name, new_model = parts[1], resolve_model_alias(parts[2])
        config = load_config()
        if role_name in config.agents.roles:
            config.agents.roles[role_name].model = new_model
            save_config(config)
        loop.roles[role_name].model = new_model
        if getattr(loop, "_subagents", None) is not None:
            loop.subagents.roles = loop.roles
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=f"✓ {role_name} model → {new_model}",
        )

    # Guard: reject agent mode names as model names
    _agent_modes = {"single", "team", "genver", "auto"}
    if parts[1].lower() in _agent_modes:
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=f"'{parts[1]}' is an agent mode, not a model. Use `/agent {parts[1]}` instead.",
        )

    # /model <name> — switch default model
    new_model = resolve_model_alias(parts[1])
    loop.model = new_model
    if getattr(loop, "_subagents", None) is not None:
        loop.subagents.model = new_model
    config = load_config()
    config.agents.defaults.model = new_model
    save_config(config)

    # Recreate provider for the new model (different providers for different prefixes)
    try:
        from src.providers.factory import make_provider

        loop.provider = make_provider(config)
        if getattr(loop, "_subagents", None) is not None:
            loop.subagents.provider = loop.provider
    except Exception as e:
        logger.warning("Failed to recreate provider for {}: {}", new_model, e)

    return OutboundMessage(
        channel=msg.channel,
        chat_id=msg.chat_id,
        content=f"✓ Default model → {new_model}",
    )
