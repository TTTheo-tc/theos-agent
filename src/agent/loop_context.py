"""Turn context assembly for AgentLoop.

Owns ContextBuilder lifecycle (global + per-session cache) and
message construction for each LLM turn.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from src.agent.context import ContextBuilder

if TYPE_CHECKING:
    from src.bus.events import InboundMessage
    from src.config.schema import AgentRoleConfig, MemoryConfig
    from src.hooks.runner import HookRunner
    from src.memory.recall import MemoryRecallService

_EPHEMERAL_CONTEXT_TAG = "[Ephemeral Context — not part of user history]"


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
        self._global_context = ContextBuilder(
            workspace,
            roles=self._roles,
            recall_service=recall_service,
            learning_enabled=learning_enabled,
        )
        self._cache: dict[str, ContextBuilder] = {}

    # -- public properties ---------------------------------------------------

    @property
    def global_context(self) -> ContextBuilder:
        """The global fallback ContextBuilder (replaces old ``loop.context``)."""
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
        self._global_context = ContextBuilder(
            self._workspace,
            roles=self._roles,
            recall_service=self._recall_service,
            learning_enabled=self._learning_enabled,
        )
        self._cache.clear()

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
            self._cache[session_key] = ContextBuilder(
                workspace=self._workspace,  # global (skills)
                group_workspace=group_ws,  # per-group (memory, bootstrap)
                roles=self._roles,
                recall_service=self._recall_service,
                learning_enabled=self._learning_enabled,
            )
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
        return ContextBuilder(
            workspace=self._workspace,
            group_workspace=workspace,
            roles=self._roles,
            recall_service=self._recall_service,
            learning_enabled=self._learning_enabled,
        )

    # -- instinct extraction (static) ----------------------------------------

    @staticmethod
    def extract_instinct_routing(hook_ctx: str | None) -> tuple[list[str], str | None]:
        """Extract matched ``category/domain`` labels from reflex output."""
        if not hook_ctx:
            return [], None

        # I7: Try structured sidecar first
        import json

        sidecar_match = re.search(r"<!-- instinct-routing:(.*?) -->", hook_ctx)
        if sidecar_match:
            try:
                data = json.loads(sidecar_match.group(1))
                domains = data.get("domains", [])
                primary = data.get("selected_primary")
                return domains, primary
            except (json.JSONDecodeError, KeyError):
                pass  # fallback to regex

        # Fallback: existing regex parsing
        domains: list[str] = []
        seen: set[str] = set()
        for match in re.findall(r"^【([^】]+)】", hook_ctx, flags=re.MULTILINE):
            label = match.strip()
            if "/" not in label or label in seen:
                continue
            seen.add(label)
            domains.append(label)
        return domains, (domains[0] if domains else None)

    @staticmethod
    def extract_instinct_skills(hook_ctx: str | None) -> list[str]:
        """Extract recommended skill names from reflex output."""
        if not hook_ctx:
            return []

        # I7: Try structured sidecar first
        import json

        sidecar_match = re.search(r"<!-- instinct-routing:(.*?) -->", hook_ctx)
        if sidecar_match:
            try:
                data = json.loads(sidecar_match.group(1))
                return data.get("skills", [])
            except (json.JSONDecodeError, KeyError):
                pass  # fallback to regex

        # Fallback: existing regex parsing
        skills: list[str] = []
        seen: set[str] = set()
        for match in re.findall(r"^\s*-\s+([\w-]+)\s*:", hook_ctx, flags=re.MULTILINE):
            name = match.strip()
            if not name or name in seen:
                continue
            seen.add(name)
            skills.append(name)
        return skills

    @staticmethod
    def extract_instinct_tools(hook_ctx: str | None) -> list[str]:
        """Extract recommended deferred tool names from reflex output."""
        if not hook_ctx:
            return []

        import json

        sidecar_match = re.search(r"<!-- instinct-routing:(.*?) -->", hook_ctx)
        if sidecar_match:
            try:
                data = json.loads(sidecar_match.group(1))
                return data.get("tools", [])
            except (json.JSONDecodeError, KeyError):
                pass
        return []

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
        tool_activator: Callable[[str], bool] | None = None,
    ) -> tuple[list[dict], int, list[str], str | None, list[str]]:
        """Build initial LLM messages from pre-chat hooks and context.

        Returns ``(initial_messages, initial_count, routing_domains,
        selected_primary, routed_skills)``.
        """
        hook_ctx = await hooks.run_pre_chat(msg.content, workspace=task_workspace)
        routing_domains, selected_primary = self.extract_instinct_routing(hook_ctx)
        routed_skills = self.extract_instinct_skills(hook_ctx)
        routed_tools = self.extract_instinct_tools(hook_ctx)

        # Auto-activate deferred tools recommended by domain routing
        if tool_activator and routed_tools:
            for tool_name in routed_tools:
                tool_activator(tool_name)
        initial_messages = ctx.build_messages(
            history=history,
            current_message=msg.content,
            skill_names=routed_skills,
            media=msg.media if msg.media else None,
            channel=msg.channel,
            chat_id=msg.chat_id,
            model=model,
            memory_config=memory_config,
            has_memory_tools=memory_search_enabled,
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
