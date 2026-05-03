"""Runtime adapter: AgentRoleConfig -> RuntimeRoleConfig."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from src.config.schema import AgentRoleConfig


@dataclass
class RuntimeRoleConfig:
    name: str
    description: str
    system_prompt: str
    model: str
    max_iterations: int
    allowed_tools: set[str] | None
    allow_nested_spawn: bool = False
    timeout_seconds: int | None = None
    isolation: str | None = None

    @classmethod
    def from_agent_role(
        cls,
        name: str,
        role_cfg: AgentRoleConfig,
        default_model: str,
    ) -> RuntimeRoleConfig:
        tools_list = role_cfg.tools
        allowed_tools: set[str] | None = set(tools_list) if tools_list else None
        allow_nested = "agent" in tools_list if tools_list else False

        if allow_nested and allowed_tools and "subagent_wait" not in allowed_tools:
            logger.warning(
                "Role {!r} includes 'agent' but not 'subagent_wait' — "
                "nested agents won't be able to retrieve child results.",
                name,
            )

        return cls(
            name=name,
            description=role_cfg.description,
            system_prompt=role_cfg.prompt,
            model=role_cfg.model or default_model,
            max_iterations=role_cfg.max_iterations,
            allowed_tools=allowed_tools,
            allow_nested_spawn=allow_nested,
            timeout_seconds=role_cfg.timeout_seconds,
            isolation=role_cfg.isolation,
        )
