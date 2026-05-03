"""Explore tool — drives a cheap model with read-only tools for codebase exploration.

Used in Generator-Verifier mode: the Generator calls this tool to delegate
exploration tasks to a cheaper/faster model. Results are written as structured
JSON to the shared AgentFS workspace, and a lightweight pointer is returned.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from src.agent.tools.base import Tool


class ExploreTool(Tool):
    """Tool that spawns a read-only exploration sub-loop and returns structured results."""

    def __init__(
        self,
        provider: Any,  # LLMProvider
        workspace: Path,
        agentfs: Any,  # AgentFS
        explorer_model: str,
        max_iterations: int = 15,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        restrict_to_workspace: bool = False,
    ):
        self._provider = provider
        self._workspace = workspace
        self._agentfs = agentfs
        self._explorer_model = explorer_model
        self._max_iterations = max_iterations
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._restrict_to_workspace = restrict_to_workspace

    @property
    def name(self) -> str:
        return "explore"

    @property
    def description(self) -> str:
        return (
            "Explore the codebase using a fast model with read-only tools. "
            "Give a specific exploration task (e.g. 'find all API endpoint handlers', "
            "'understand the authentication flow'). Returns a reference to structured "
            "findings stored in the shared workspace."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The exploration task to perform",
                },
            },
            "required": ["task"],
        }

    async def execute(self, task: str, **kwargs: Any) -> str:
        """Run the exploration sub-loop and return a workspace pointer."""
        from src.agent.loop_core import run_tool_loop
        from src.agent.tools.filesystem import (
            GlobTool,
            GrepTool,
            ListDirTool,
            ReadFileTool,
        )
        from src.agent.tools.registry import ToolRegistry

        # Build read-only tool set
        tools = ToolRegistry()
        allowed_dir = self._workspace if self._restrict_to_workspace else None
        tools.register(ReadFileTool(workspace=self._workspace, allowed_dir=allowed_dir))
        tools.register(ListDirTool(workspace=self._workspace, allowed_dir=allowed_dir))
        tools.register(GlobTool(workspace=self._workspace, allowed_dir=allowed_dir))
        tools.register(GrepTool(workspace=self._workspace, allowed_dir=allowed_dir))

        system_prompt = (
            "You are a codebase explorer. Your job is to investigate the codebase "
            "and answer the given exploration task.\n\n"
            "## Rules\n"
            "1. Use the available read-only tools to explore files and code\n"
            "2. Be thorough but efficient — don't read files unnecessarily\n"
            "3. Your final response MUST be valid JSON with this structure:\n"
            '   {"summary": "...", "findings": [...], "files": [...]}\n'
            "   - summary: brief answer to the task\n"
            "   - findings: list of key observations (strings)\n"
            "   - files: list of relevant file paths\n"
            "4. Output ONLY the JSON object, no markdown fences or extra text\n\n"
            f"Workspace: {self._workspace}"
        )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": task},
        ]

        final_content, _, _, _ = await run_tool_loop(
            provider=self._provider,
            messages=messages,
            tools=tools,
            model=self._explorer_model,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            max_iterations=self._max_iterations,
        )

        # Parse the result and write to AgentFS
        result_name = f"explore_{uuid.uuid4().hex[:8]}"

        try:
            parsed = json.loads(final_content or "{}")
        except (json.JSONDecodeError, TypeError):
            parsed = {
                "summary": final_content or "No results",
                "findings": [],
                "files": [],
            }

        path = self._agentfs.write(result_name, parsed)
        summary = parsed.get("summary", "Exploration complete")

        return (
            f"Exploration complete. Summary: {summary}\n"
            f"Full results: {result_name} (workspace: {path})"
        )
