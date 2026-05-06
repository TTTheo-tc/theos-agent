"""Turn context assembly for AgentLoop.

Owns ContextBuilder lifecycle (global + per-session cache) and
message construction for each LLM turn.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from src.agent.context import ContextBuilder

if TYPE_CHECKING:
    from src.bus.events import InboundMessage
    from src.config.schema import AgentRoleConfig, MemoryConfig
    from src.hooks.runner import HookRunner
    from src.memory.recall import MemoryRecallService

_EPHEMERAL_CONTEXT_TAG = "[Ephemeral Context — not part of user history]"
_INSTINCT_SIDECAR_RE = re.compile(r"<!-- instinct-routing:(.*?) -->")


@dataclass(slots=True)
class InstinctRouting:
    """Parsed instinct routing data for one pre-chat hook result."""

    domains: list[str] = field(default_factory=list)
    selected_primary: str | None = None
    skills: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)


class TurnContextAssembler:
    """Manages context builders and assembles per-turn LLM messages."""

    def __init__(
        self,
        workspace: Path,
        roles: dict[str, AgentRoleConfig] | None = None,
        recall_service: "MemoryRecallService | None" = None,
        learning_enabled: bool = False,
    ):
        self._workspace = workspace
        self._roles = roles or {}
        self._recall_service = recall_service
        self._learning_enabled = learning_enabled
        self._global_context = self._new_context()
        self._cache: dict[str, ContextBuilder] = {}

    # -- public properties ---------------------------------------------------

    @property
    def global_context(self) -> ContextBuilder:
        """The global fallback ContextBuilder."""
        return self._global_context

    # -- cache management ----------------------------------------------------

    def clear_cache(self) -> None:
        """Drop all per-session cached ContextBuilders."""
        self._cache.clear()

    def rebuild_global(self, roles: dict[str, AgentRoleConfig] | None = None) -> None:
        """Rebuild the global context and clear per-session cache.

        Called by ``reload_roles()``.
        """
        self._roles = roles or {}
        self._global_context = self._new_context()
        self._cache.clear()

    def _new_context(self, group_workspace: Path | None = None) -> ContextBuilder:
        """Create a ContextBuilder sharing assembler-wide roles and recall state."""
        return ContextBuilder(
            workspace=self._workspace,
            group_workspace=group_workspace,
            roles=self._roles,
            recall_service=self._recall_service,
            learning_enabled=self._learning_enabled,
        )

    # -- per-session / per-workspace context ---------------------------------

    def get_for_session(
        self,
        session_key: str,
        *,
        group_memory_enabled: bool,
        group_workspace_resolver: Callable[[str], Path] | None = None,
    ) -> ContextBuilder:
        """Return a ContextBuilder for the session (per-group when enabled)."""
        if not group_memory_enabled:
            return self._global_context
        if session_key not in self._cache:
            if group_workspace_resolver is None:
                raise ValueError(
                    "group_workspace_resolver is required when group_memory_enabled=True"
                )
            group_ws = group_workspace_resolver(session_key)
            self._cache[session_key] = self._new_context(group_workspace=group_ws)
        return self._cache[session_key]

    def get_for_workspace(
        self,
        *,
        session_key: str,
        workspace: Path,
        group_memory_enabled: bool,
        group_workspace_resolver: Callable[[str], Path] | None = None,
    ) -> ContextBuilder:
        """Return a transient ContextBuilder bound to a task workspace when needed."""
        if group_memory_enabled:
            if group_workspace_resolver is None:
                raise ValueError(
                    "group_workspace_resolver is required when group_memory_enabled=True"
                )
            default_workspace = group_workspace_resolver(session_key)
        else:
            default_workspace = self._workspace
        if workspace == default_workspace:
            return self.get_for_session(
                session_key,
                group_memory_enabled=group_memory_enabled,
                group_workspace_resolver=group_workspace_resolver,
            )
        return self._new_context(group_workspace=workspace)

    # -- instinct extraction (static) ----------------------------------------

    @staticmethod
    def extract_instinct_routing(hook_ctx: str | None) -> tuple[list[str], str | None]:
        """Extract matched ``category/domain`` labels from reflex output."""
        routing = TurnContextAssembler.extract_instinct_context(hook_ctx)
        return routing.domains, routing.selected_primary

    @staticmethod
    def extract_instinct_skills(hook_ctx: str | None) -> list[str]:
        """Extract recommended skill names from reflex output."""
        return TurnContextAssembler.extract_instinct_context(hook_ctx).skills

    @staticmethod
    def extract_instinct_tools(hook_ctx: str | None) -> list[str]:
        """Extract recommended deferred tool names from reflex output."""
        return TurnContextAssembler.extract_instinct_context(hook_ctx).tools

    @staticmethod
    def extract_instinct_context(hook_ctx: str | None) -> InstinctRouting:
        """Extract all instinct routing fields from one hook output."""
        if not hook_ctx:
            return InstinctRouting()

        sidecar = TurnContextAssembler._extract_instinct_sidecar(hook_ctx)
        if sidecar is not None:
            return InstinctRouting(
                domains=sidecar.get("domains", []),
                selected_primary=sidecar.get("selected_primary"),
                skills=sidecar.get("skills", []),
                tools=sidecar.get("tools", []),
            )

        domains = TurnContextAssembler._extract_legacy_instinct_domains(hook_ctx)
        return InstinctRouting(
            domains=domains,
            selected_primary=domains[0] if domains else None,
            skills=TurnContextAssembler._extract_legacy_instinct_skills(hook_ctx),
        )

    @staticmethod
    def _extract_instinct_sidecar(hook_ctx: str) -> dict[str, Any] | None:
        sidecar_match = _INSTINCT_SIDECAR_RE.search(hook_ctx)
        if not sidecar_match:
            return None
        try:
            data = json.loads(sidecar_match.group(1))
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _extract_legacy_instinct_domains(hook_ctx: str) -> list[str]:
        domains: list[str] = []
        seen: set[str] = set()
        for match in re.findall(r"^【([^】]+)】", hook_ctx, flags=re.MULTILINE):
            label = match.strip()
            if "/" not in label or label in seen:
                continue
            seen.add(label)
            domains.append(label)
        return domains

    @staticmethod
    def _extract_legacy_instinct_skills(hook_ctx: str) -> list[str]:
        skills: list[str] = []
        seen: set[str] = set()
        for match in re.findall(r"^\s*-\s+([\w-]+)\s*:", hook_ctx, flags=re.MULTILINE):
            name = match.strip()
            if not name or name in seen:
                continue
            seen.add(name)
            skills.append(name)
        return skills

    # -- turn message assembly -----------------------------------------------

    async def build_turn_messages(
        self,
        msg: "InboundMessage",
        *,
        key: str,
        run_genver: bool,
        task_workspace: Path,
        ctx: ContextBuilder,
        history: list[dict],
        hooks: "HookRunner",
        model: str,
        memory_config: "MemoryConfig | None",
        memory_search_enabled: bool,
        build_structured_recall: Callable[..., Any],
        maybe_compact: Callable[[list[dict]], Any],
        memory_tool_names: Callable[[], set[str]] | set[str] | None = None,
        tool_activator: Callable[[str], bool] | None = None,
    ) -> tuple[list[dict], int, list[str], str | None, list[str]]:
        """Build initial LLM messages from pre-chat hooks and context.

        Returns ``(initial_messages, initial_count, routing_domains,
        selected_primary, routed_skills)``.
        """
        hook_ctx = await hooks.run_pre_chat(msg.content, workspace=task_workspace)
        routing = self.extract_instinct_context(hook_ctx)
        routing_domains = routing.domains
        selected_primary = routing.selected_primary
        routed_skills = routing.skills

        # Auto-activate deferred tools recommended by domain routing
        if tool_activator and routing.tools:
            for tool_name in routing.tools:
                tool_activator(tool_name)
        memory_names = memory_tool_names() if callable(memory_tool_names) else memory_tool_names
        initial_messages = ctx.build_messages(
            history=history,
            current_message=msg.content,
            skill_names=routed_skills,
            media=msg.media if msg.media else None,
            channel=msg.channel,
            chat_id=msg.chat_id,
            model=model,
            memory_config=memory_config,
            has_memory_tools=bool(memory_names) if memory_names is not None else memory_search_enabled,
            memory_tool_names=memory_names,
            prompt_profile=(ContextBuilder._GENVER_GENERATOR_PROFILE if run_genver else None),
        )
        structured_recall = await build_structured_recall(
            session_key=key,
            query=msg.content,
            selected_primary=selected_primary,
            workspace_override=task_workspace if run_genver else None,
        )
        # Merge ephemeral context (instinct + recall) into the final user message
        # instead of inserting separate user turns, to keep one clear user turn.
        ephemeral_parts: list[str] = []
        if hook_ctx:
            ephemeral_parts.append(f"[🧠 Instinct]\n{hook_ctx}")
        if structured_recall:
            ephemeral_parts.append(structured_recall)
        if ephemeral_parts:
            tag = _EPHEMERAL_CONTEXT_TAG
            ephemeral_block = f"{tag}\n" + "\n\n".join(ephemeral_parts)
            # Prepend to the final (merged) user message content
            last_msg = initial_messages[-1]
            if isinstance(last_msg["content"], str):
                last_msg["content"] = f"{ephemeral_block}\n\n{last_msg['content']}"
            else:
                # multimodal list: prepend as text block
                last_msg["content"] = [{"type": "text", "text": ephemeral_block}] + last_msg[
                    "content"
                ]
        initial_count = len(initial_messages)

        # Phase D: Emergency compaction if context is too large
        initial_messages = await maybe_compact(initial_messages)
        return initial_messages, initial_count, routing_domains, selected_primary, routed_skills
