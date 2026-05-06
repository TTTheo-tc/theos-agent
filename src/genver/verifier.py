"""GenVer Verifier — autonomous verification and repair agent.

Extracted from GenVerLoop to keep the verification concern self-contained.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from loguru import logger

from src.agent.agentfs import AgentFS
from src.agent.loop_core import run_tool_loop
from src.agent.tool_sets import register_standard_tools
from src.agent.tools.registration import ToolRegistrationConfig
from src.agent.tools.registry import ToolRegistry
from src.utils.usage import merge_usage as _merge_usage

if TYPE_CHECKING:
    from src.genver.handoff import HandoffPayload
    from src.providers.base import LLMProvider


class Verifier:
    """Runs verification and optional repair for a GenVer round."""

    def __init__(
        self,
        *,
        workspace: Path,
        agentfs: AgentFS,
        verifier_provider: LLMProvider,
        verifier_model: str,
        generator_model: str,
        verifier_commands: list[str],
        verifier_max_iterations: int,
        max_tokens: int = 16384,
    ) -> None:
        self.workspace = workspace
        self.agentfs = agentfs
        self._provider = verifier_provider
        self.verifier_model = verifier_model
        self.generator_model = generator_model
        self.verifier_commands = verifier_commands
        self.verifier_max_iterations = verifier_max_iterations
        self.max_tokens = max_tokens

        self.last_review_report: dict[str, Any] | None = None
        self.last_review_report_name: str | None = None

    def _make_tools(
        self,
        mode: Literal["verifier", "subagent"],
        *,
        allowed_tools: set[str] | None = None,
    ) -> ToolRegistry:
        """Create a verifier-scoped tool registry."""
        tools = ToolRegistry()
        register_standard_tools(
            tools,
            ToolRegistrationConfig(
                workspace=self.workspace,
                mode=mode,
                allowed_tools=allowed_tools,
            ),
        )
        return tools

    @staticmethod
    def is_provider_error_content(content: str | None) -> bool:
        """Detect provider-level error strings returned as normal content."""
        if not content:
            return False
        prefixes = (
            "Error calling LLM:",
            "Error calling Codex:",
            "Error calling OpenAI:",
            "Error calling Anthropic:",
            "Error calling ",
        )
        return any(content.startswith(prefix) for prefix in prefixes)

    async def run_verification(
        self,
        *,
        handoff: HandoffPayload | None,
        user_request: str = "",
    ) -> dict[str, Any]:
        """Run the verifier as an autonomous agent.

        Returns: {"passed": bool, "errors": list[str], "commands": list[dict],
                  "suggestions": list[str], "usage": dict}
        """
        tools = self._make_tools("verifier")

        total_usage: dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

        handoff_section = ""
        if handoff and handoff.files_changed:
            handoff_section = (
                "\n\n## Handoff from Generator\n"
                + handoff.to_verifier_prompt(self.verifier_commands)
                + "\n"
            )

        _tool_names = {"bash", "read_file", "grep", "glob", "list_dir"}
        suggested = [c for c in self.verifier_commands if c not in _tool_names]
        suggested_section = ""
        if suggested:
            cmd_list = "\n".join(f"  - {cmd}" for cmd in suggested)
            suggested_section = (
                f"\n\n## Suggested Verification Commands\n"
                f"The following commands are suggested starting points:\n{cmd_list}\n"
                f"You may run additional checks beyond these."
            )

        user_request_section = ""
        if user_request:
            user_request_section = (
                "\n\n## Original User Request\n"
                f"{user_request}\n"
                "Verify that the implementation fully satisfies this request."
            )

        project_dir = self._detect_project_dir(handoff)
        project_hint = ""
        if project_dir:
            project_hint = (
                f"\n\n## Project Directory\n"
                f"The project is at: {project_dir}\n"
                f"Run all commands from this directory (cd there first)."
            )
        review_evidence_section, review_evidence = self._build_review_evidence(handoff, project_dir)

        system_prompt = (
            "You are an autonomous code verifier. Your job is to thoroughly verify "
            "that the Generator's changes are correct and complete.\n\n"
            "## Workspace\n"
            f"The workspace root is: {self.workspace}\n"
            "All generated code lives here. Do NOT waste time searching for the project.\n\n"
            "## Available Tools\n"
            "You have: bash (run any shell command), read_file, glob, grep, list_dir.\n"
            "Use them freely — you are not limited to a fixed list of commands.\n\n"
            "## EFFICIENCY — CRITICAL\n"
            "You have LIMITED iterations. Be direct and efficient:\n"
            "1. Go straight to running verification commands — do NOT explore directories first\n"
            "2. Combine multiple checks in a single bash call where possible\n"
            "3. Read only the files mentioned in the handoff, not the entire tree\n"
            "4. Output your JSON verdict as soon as you have enough evidence\n\n"
            "## Verification Strategy\n"
            "Run the suggested commands first, then (if iterations allow):\n"
            "1. **Tests**: Run test suite\n"
            "2. **Lint**: Run linter\n"
            "3. **Direct Code Review**: Inspect the actual changed files and evidence below. "
            "Do NOT pass solely based on the generator's handoff summary.\n"
            "4. **Requirement Coverage**: Check the implementation against the original user request — "
            "are any requested features missing or incomplete?\n"
            "5. **Code Quality Review**: Review code for naming, structure, and performance issues. "
            "These are advisory suggestions that do NOT affect pass/fail — "
            "as long as tests pass and there are no bugs, the verdict should be passed=true.\n\n"
            "## Output Format\n"
            "Your final response MUST be valid JSON (no markdown fences):\n"
            '  {"passed": true/false, "errors": ["specific error description", ...], '
            '"checks_performed": ["what you checked", ...], '
            '"suggestions": ["optional code quality suggestions", ...]}\n'
            "Include specific error messages, file paths, and line numbers in errors.\n"
            "The `suggestions` field is optional — use it for code quality advice that doesn't block passing.\n"
            'If everything passes, return {"passed": true, "errors": [], '
            '"checks_performed": [...], "suggestions": [...]}.'
            + user_request_section
            + project_hint
            + handoff_section
            + review_evidence_section
            + suggested_section
        )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": "Verify the Generator's changes. Use your tools to run tests, "
                "read files, and perform any checks needed. Report your findings as JSON.",
            },
        ]

        try:
            content, _, _, usage = await run_tool_loop(
                provider=self._provider,
                messages=messages,
                tools=tools,
                model=self.verifier_model,
                temperature=0.0,
                max_tokens=self.max_tokens,
                max_iterations=self.verifier_max_iterations,
            )
        except Exception as exc:
            logger.opt(exception=True).warning("[GenVer:Verifier] Provider error")
            return {
                "passed": False,
                "errors": [f"Verifier provider error: {exc}"],
                "commands": [],
                "usage": total_usage,
                "provider_error": True,
            }
        _merge_usage(total_usage, usage)

        if self.is_provider_error_content(content):
            logger.error("[GenVer:Verifier] Provider error in response: {}", content[:200])
            return {
                "passed": False,
                "errors": [content],
                "commands": [],
                "usage": total_usage,
                "provider_error": True,
            }

        result = self.parse_verifier_output(content)

        return {
            "passed": result.get("passed", False),
            "errors": result.get("errors", []),
            "commands": [{"check": c} for c in result.get("checks_performed", [])],
            "suggestions": result.get("suggestions", []),
            "checks_performed": result.get("checks_performed", []),
            "review_evidence": review_evidence,
            "project_dir": project_dir,
            "usage": total_usage,
        }

    async def run_repair(
        self,
        *,
        attempt: int,
        report_name: str,
        error_summary: str,
        user_request: str,
        handoff: HandoffPayload | None,
    ) -> dict[str, Any]:
        """Let the verifier directly patch remaining blocking issues."""
        tools = self._make_tools(
            "subagent",
            allowed_tools={
                "bash",
                "read_file",
                "write_file",
                "edit_file",
                "multi_edit",
                "list_dir",
                "glob",
                "grep",
            },
        )

        total_usage: dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        project_dir = self._detect_project_dir(handoff)
        project_hint = ""
        if project_dir:
            project_hint = (
                "\n\n## Project Directory\n"
                f"The project is at: {project_dir}\n"
                "Run commands from this directory."
            )

        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "You are the verifier in final repair mode.\n"
                    "The generator has already had two implementation chances.\n"
                    "Your job is to make the minimum direct code changes needed to fix the remaining blocking issues.\n"
                    "Do not change models, verifier settings, or unrelated code.\n"
                    "After editing, respond with a concise plain-text repair summary."
                    + project_hint
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Original user request:\n{user_request}\n\n"
                    f"Verifier attempt {attempt} failed with these blocking issues:\n{error_summary}\n\n"
                    f"Read the code, fix the issues directly, and summarize what you changed.\n"
                    f"Related review artifact: {report_name}"
                ),
            },
        ]

        try:
            content, tools_used, _, usage = await run_tool_loop(
                provider=self._provider,
                messages=messages,
                tools=tools,
                model=self.verifier_model,
                temperature=0.0,
                max_tokens=self.max_tokens,
                max_iterations=self.verifier_max_iterations,
            )
        except Exception as exc:
            logger.opt(exception=True).warning("[GenVer:VerifierRepair] Provider error")
            return {
                "content": "",
                "errors": [f"Verifier repair provider error: {exc}"],
                "tools_used": [],
                "usage": total_usage,
                "provider_error": True,
            }
        _merge_usage(total_usage, usage)
        if self.is_provider_error_content(content):
            return {
                "content": content,
                "errors": [content],
                "tools_used": tools_used,
                "usage": total_usage,
                "provider_error": True,
            }
        return {
            "content": (content or "").strip(),
            "errors": [],
            "tools_used": tools_used,
            "usage": total_usage,
            "project_dir": project_dir,
        }

    # ------------------------------------------------------------------
    # Evidence collection
    # ------------------------------------------------------------------

    def _build_review_evidence(
        self, handoff: HandoffPayload | None, project_dir: str | None
    ) -> tuple[str, list[dict[str, Any]]]:
        """Build direct review evidence from actual changed files or git diff output."""
        if not handoff or not handoff.files_changed:
            return "", []

        evidence: list[dict[str, Any]] = []
        sections: list[str] = ["\n\n## Direct Review Evidence"]
        total_chars = 0
        max_total_chars = 12000
        max_item_chars = 2500
        base_dir = Path(project_dir) if project_dir else self.workspace
        git_root = base_dir if (base_dir / ".git").exists() else None

        for raw_path in handoff.files_changed[:6]:
            path = self._resolve_handoff_path(raw_path, base_dir)
            rel_path = str(path)
            if git_root is not None:
                try:
                    rel_path = str(path.resolve().relative_to(git_root.resolve()))
                except Exception:
                    rel_path = str(path)

            excerpt = self._git_diff_excerpt(git_root, rel_path)
            source = "git_diff"
            if not excerpt:
                excerpt = self._file_excerpt(path)
                source = "file_snapshot"
            if not excerpt:
                continue

            excerpt = excerpt[:max_item_chars]
            if total_chars + len(excerpt) > max_total_chars:
                break
            total_chars += len(excerpt)
            evidence.append({"path": str(path), "source": source, "excerpt": excerpt})
            sections.append(f"\n### {rel_path} ({source})\n{excerpt}")

        if len(sections) == 1:
            return "", evidence
        sections.append(
            "\nReview the actual code above, then use read_file/bash for any deeper inspection before passing."
        )
        return "\n".join(sections), evidence

    @staticmethod
    def _git_diff_excerpt(git_root: Path | None, rel_path: str) -> str:
        """Return a truncated git diff excerpt for *rel_path* when available."""
        if git_root is None:
            return ""
        try:
            proc = subprocess.run(
                ["git", "-C", str(git_root), "diff", "--no-ext-diff", "--", rel_path],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except Exception:
            return ""
        diff = (proc.stdout or "").strip()
        return diff[:2500] if diff else ""

    @staticmethod
    def _file_excerpt(path: Path) -> str:
        """Return a truncated excerpt of the current file contents."""
        try:
            if not path.exists() or not path.is_file():
                return ""
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""
        lines = content.splitlines()
        excerpt = "\n".join(lines[:160])
        if len(lines) > 160:
            excerpt += "\n... (truncated)"
        return excerpt[:2500]

    def _resolve_handoff_path(self, raw_path: str, base_dir: Path) -> Path:
        """Resolve a handoff file path relative to the current task workspace."""
        path = Path(raw_path)
        if path.is_absolute():
            return path.resolve()

        direct = (base_dir / raw_path).resolve()
        if direct.exists():
            return direct

        workspace_name = self.workspace.name
        prefix = f"{workspace_name}/"
        normalized = raw_path[len(prefix) :] if raw_path.startswith(prefix) else raw_path
        return (base_dir / normalized).resolve()

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def write_review_report(
        self,
        attempt: int,
        verdict: dict[str, Any],
        *,
        passed: bool,
        handoff: HandoffPayload | None,
        user_request: str = "",
    ) -> str:
        """Persist a structured verifier report for both pass and fail outcomes."""
        report_name = f"runtime/verify_report_{attempt}"
        report = {
            "attempt": attempt,
            "passed": passed,
            "generator_model": self.generator_model,
            "verifier_model": self.verifier_model,
            "user_request": user_request,
            "handoff": asdict(handoff) if handoff else None,
            "errors": verdict.get("errors", []),
            "checks_performed": verdict.get("checks_performed", []),
            "commands": verdict.get("commands", []),
            "suggestions": verdict.get("suggestions", []),
            "review_evidence": verdict.get("review_evidence", []),
            "project_dir": verdict.get("project_dir"),
        }
        self.agentfs.write(report_name, report)
        self.last_review_report = report
        self.last_review_report_name = report_name
        return report_name

    def write_repair_report(
        self,
        *,
        attempt: int,
        source_report_name: str,
        repair: dict[str, Any],
    ) -> str:
        """Persist verifier-side repair work."""
        report_name = f"runtime/verifier_repair_{attempt}"
        report = {
            "attempt": attempt,
            "verifier_model": self.verifier_model,
            "source_report": source_report_name,
            "summary": repair.get("content", ""),
            "tools_used": repair.get("tools_used", []),
            "project_dir": repair.get("project_dir"),
        }
        self.agentfs.write(report_name, report)
        return report_name

    # ------------------------------------------------------------------
    # Project detection
    # ------------------------------------------------------------------

    def _detect_project_dir(self, handoff: HandoffPayload | None) -> str | None:
        """Try to detect the project directory from the handoff or workspace markers."""
        if handoff and handoff.files_changed:
            abs_files = [f for f in handoff.files_changed if f.startswith("/")]
            if abs_files:
                from pathlib import PurePosixPath

                common = PurePosixPath(abs_files[0]).parent
                for f in abs_files[1:]:
                    p = PurePosixPath(f).parent
                    while not str(p).startswith(str(common)) and str(common) != "/":
                        common = common.parent
                return str(common)

            workspace_name = self.workspace.name
            relative_files = [
                str(f)
                for f in handoff.files_changed
                if isinstance(f, str) and not f.startswith("/")
            ]
            if relative_files:
                normalized = []
                for raw_path in relative_files:
                    prefix = f"{workspace_name}/"
                    normalized.append(
                        raw_path[len(prefix) :] if raw_path.startswith(prefix) else raw_path
                    )
                if all((self.workspace / rel).exists() for rel in normalized):
                    return str(self.workspace)

            for candidate in [self.workspace, self.workspace.parent]:
                if (candidate / handoff.files_changed[0]).exists():
                    return str(candidate)

        for candidate in [self.workspace, self.workspace.parent, self.workspace.parent.parent]:
            for marker in ("pyproject.toml", "package.json", "Makefile", ".git"):
                if (candidate / marker).exists():
                    return str(candidate)

        return None

    # ------------------------------------------------------------------
    # Output parsing
    # ------------------------------------------------------------------

    @staticmethod
    def parse_verifier_output(content: str | None) -> dict[str, Any]:
        """Parse verifier JSON output, tolerating markdown fences and extra text."""
        if not content:
            return {"passed": False, "errors": ["Verifier produced no output"]}

        try:
            result = json.loads(content)
            if isinstance(result, dict):
                return result
        except (json.JSONDecodeError, TypeError):
            pass

        fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", content, re.DOTALL)
        if fence_match:
            try:
                result = json.loads(fence_match.group(1))
                if isinstance(result, dict):
                    return result
            except (json.JSONDecodeError, TypeError):
                pass

        brace_match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", content, re.DOTALL)
        if brace_match:
            try:
                result = json.loads(brace_match.group(0))
                if isinstance(result, dict):
                    return result
            except (json.JSONDecodeError, TypeError):
                pass

        return {"passed": False, "errors": [content]}

    # ------------------------------------------------------------------
    # Response formatting
    # ------------------------------------------------------------------

    def finalize_success_response(
        self,
        gen_content: str | None,
        verdict: dict[str, Any],
        attempt: int,
        report_name: str,
        handoff: HandoffPayload | None,
    ) -> str:
        """Build a stable user-facing success summary after verification passes."""
        summary = ""
        if handoff and handoff.intent_summary:
            summary = handoff.intent_summary.strip()
        elif gen_content and gen_content.strip():
            summary = gen_content.strip()
        else:
            summary = "Implemented the requested change."

        if summary == "Handoff submitted. The verifier will review your changes.":
            summary = "Implemented the requested change."

        lines = [summary, f"Verification passed on attempt {attempt}."]
        checks = verdict.get("checks_performed", [])
        if checks:
            lines.append("Checks: " + "; ".join(checks[:5]))
        suggestions = verdict.get("suggestions", [])
        if suggestions:
            lines.append("Review suggestions: " + "; ".join(suggestions[:3]))
        lines.append(f"Review artifact: {self.agentfs.root / (report_name + '.json')}")
        return "\n\n".join(lines)

    def finalize_repair_response(
        self,
        *,
        repair: dict[str, Any],
        attempt: int,
        report_name: str,
        repair_name: str,
    ) -> str:
        """Build a stable response when verifier finishes with direct edits."""
        summary = repair.get("content") or "Verifier applied direct fixes for the remaining issues."
        lines = [
            summary,
            f"Verifier applied final fixes after review attempt {attempt}.",
            "No additional verification pass was run after those direct fixes.",
            f"Review artifact: {self.agentfs.root / (report_name + '.json')}",
            f"Repair artifact: {self.agentfs.root / (repair_name + '.json')}",
        ]
        return "\n\n".join(lines)
