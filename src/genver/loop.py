"""Generator-Verifier loop engine.

Implements the gen-ver pattern:
  1. Generator plans and executes with full tools
  2. Verifier reviews changes and returns pass/fail
  3. First verifier failure feeds back to Generator for one direct retry
  4. Second verifier failure triggers verifier-side repair
  5. User can optionally request one final optimization round
  6. Third round is the final generator-verifier pass
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

from src.agent.agentfs import AgentFS
from src.agent.loop_core import run_tool_loop
from src.agent.tools.ask_user import AskUserTool
from src.genver.handoff import (
    HandoffPayload,
    SubmitForReviewTool,
    parse_handoff,
)
from src.genver.verifier import Verifier
from src.providers.base import LLMProvider
from src.utils.usage import merge_usage as _merge_usage

# Callback type: async fn that sends a question to the user and returns their answer
AskUserFn = Callable[[str], Awaitable[str | None]]


def _make_provider_for_model(model: str, fallback: LLMProvider) -> LLMProvider:
    """Create a provider appropriate for the given model.

    If the model requires a different provider type than the fallback
    (e.g. openai-codex/ needs OpenAICodexProvider), create one.
    Otherwise return the fallback.
    """
    if model.startswith(("openai-codex/", "openai_codex/")):
        from src.providers.openai_codex_provider import OpenAICodexProvider

        return OpenAICodexProvider(default_model=model)
    return fallback


if TYPE_CHECKING:
    from src.agent.loop_core import AddAssistantFn, AddToolResultFn, ProgressFn
    from src.agent.tools.registry import ToolRegistry
    from src.config.schema import GenVerConfig


class GenVerLoop:
    """Orchestrates the Generator-Verifier iteration cycle."""

    def __init__(
        self,
        config: GenVerConfig,
        provider: LLMProvider,
        workspace: Path,
        generator_tools: ToolRegistry,
        default_model: str,
        temperature: float = 0.1,
        max_tokens: int = 16384,
        add_assistant: AddAssistantFn | None = None,
        add_tool_result: AddToolResultFn | None = None,
        on_progress: ProgressFn | None = None,
        tool_context: Any = None,
        ask_user: AskUserFn | None = None,
        pipeline_mode: bool = False,
    ) -> None:
        self.config = config
        self.provider = provider
        self.workspace = workspace
        self.generator_tools = generator_tools
        self.default_model = default_model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.add_assistant = add_assistant
        self.add_tool_result = add_tool_result
        self.on_progress = on_progress
        self.tool_context = tool_context
        self.ask_user = ask_user
        self.pipeline_mode = pipeline_mode

        self.agentfs = AgentFS(workspace, subdir=config.workspace_subdir)
        self.last_handoff: HandoffPayload | None = None

        # Create per-role providers when models need different provider types
        self._generator_provider = _make_provider_for_model(self.generator_model, provider)
        self._verifier_provider = _make_provider_for_model(self.verifier_model, provider)

        self._verifier = Verifier(
            workspace=workspace,
            agentfs=self.agentfs,
            verifier_provider=self._verifier_provider,
            verifier_model=self.verifier_model,
            generator_model=self.generator_model,
            verifier_commands=config.verifier_commands,
            verifier_max_iterations=config.verifier_max_iterations,
            max_tokens=max_tokens,
        )

    @property
    def generator_model(self) -> str:
        return self.config.generator_model or self.default_model

    @property
    def verifier_model(self) -> str:
        return self.config.verifier_model or self.default_model

    @property
    def last_review_report(self) -> dict[str, Any] | None:
        return self._verifier.last_review_report

    @property
    def last_review_report_name(self) -> str | None:
        return self._verifier.last_review_report_name

    async def run(
        self,
        messages: list[dict],
    ) -> tuple[str | None, list[str], list[dict], dict[str, int]]:
        """Execute the gen-ver loop. Returns (content, tools_used, messages, usage).

        The Generator's messages persist across retries (continuous context).
        The Verifier gets a fresh context each time.
        """
        # Only clear runtime data; preserve artifacts from earlier pipeline phases
        runtime_dir = self.agentfs.root / "runtime"
        if runtime_dir.exists():
            shutil.rmtree(runtime_dir)
        runtime_dir.mkdir(parents=True, exist_ok=True)

        # Extract original user request for verifier context (before preamble injection)
        self._user_request = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                content = m.get("content", "")
                if isinstance(content, str) and not content.startswith("[GenVer"):
                    self._user_request = content
                    break

        # Inject generator role context as the first system-level user message.
        # This ensures the generator knows its job even when the main system prompt
        # doesn't mention gen-ver mode.
        if self.pipeline_mode:
            planning_rule = "- Follow the approved spec/plan context provided by the pipeline.\n"
        else:
            planning_rule = "- Before coding, output a short `[Plan]`.\n"

        # Build preamble lines based on mode
        preamble_lines = [
            "[GenVer Mode — Generator Role]\n",
            "You are the generator in a generator-verifier loop.\n",
            "Rules:\n",
            "- If the request is ambiguous or contradictory, use `ask_user`.\n",
            f"{planning_rule}",
            "- Implement the requested change directly in the workspace.\n",
            "- When done, call `submit_for_review` with a structured handoff.\n",
        ]

        if self.pipeline_mode:
            preamble_lines.extend(
                [
                    "- You may run tests and lint as development aids (dev-loop), "
                    "but you do NOT make the final quality decision.\n",
                    "- Record commands you run and their results in your "
                    "submit_for_review handoff under `dev_log`.\n",
                    "- Your job ends when the handoff is ready, " "not when verification passes.\n",
                ]
            )
        else:
            preamble_lines.append(
                "- If you receive a Verifier Report, fix the issues "
                "directly without repeating it.\n"
            )

        preamble_lines.extend(
            [
                "- Avoid changing config or verifier settings "
                "unless the user explicitly asks.\n",
                f"- Write generated tests to {self.workspace}/tests/.\n",
            ]
        )

        genver_preamble = "".join(preamble_lines)
        # Insert after the system message (index 0) so it's always visible
        if messages and messages[0].get("role") == "system":
            messages = [
                messages[0],
                {"role": "user", "content": genver_preamble},
                *messages[1:],
            ]
        else:
            messages = [
                {"role": "user", "content": genver_preamble},
                *messages,
            ]

        # Register submit_for_review tool for structured handoff
        if not self.generator_tools.has("submit_for_review"):
            self.generator_tools.register(SubmitForReviewTool())

        # Register ask_user tool for requirement clarification (Phase 1)
        if not self.generator_tools.has("ask_user"):
            self.generator_tools.register(AskUserTool(ask_fn=self.ask_user))

        all_tools_used: list[str] = []
        total_usage: dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        generator_usage: dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        verifier_usage: dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

        def _attach_breakdown() -> None:
            """Attach per-role usage breakdown to total_usage before returning."""
            total_usage["_breakdown"] = {
                "generator": {"model": self.generator_model, **generator_usage},
                "verifier": {"model": self.verifier_model, **verifier_usage},
            }

        # --- Pipeline mode: generator-only execution ---
        if self.pipeline_mode:
            gen_content, gen_tools, messages, gen_usage = await run_tool_loop(
                provider=self._generator_provider,
                model=self.generator_model,
                messages=messages,
                tools=self.generator_tools,
                max_iterations=self.config.generator_max_iterations,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                add_assistant=self.add_assistant,
                add_tool_result=self.add_tool_result,
                on_progress=self.on_progress,
                tool_context=self.tool_context,
            )
            _merge_usage(total_usage, gen_usage)
            all_tools_used.extend(gen_tools)
            self.last_handoff = self._extract_handoff(messages)
            return gen_content, all_tools_used, messages, total_usage

        for attempt in range(1, self.config.max_retries + 1):
            logger.info(
                "GenVer attempt {}/{} | generator={} verifier={}",
                attempt,
                self.config.max_retries,
                self.generator_model,
                self.verifier_model,
            )

            # --- Generator phase ---
            logger.info("[GenVer:Generator] Starting with model={}", self.generator_model)
            try:
                gen_content, gen_tools, messages, gen_usage = await run_tool_loop(
                    provider=self._generator_provider,
                    messages=messages,
                    tools=self.generator_tools,
                    model=self.generator_model,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    max_iterations=self.config.generator_max_iterations,
                    add_assistant=self.add_assistant,
                    add_tool_result=self.add_tool_result,
                    on_progress=self.on_progress,
                    tool_context=self.tool_context,
                )
            except Exception as exc:
                logger.opt(exception=True).warning("[GenVer:Generator] Provider error")
                _attach_breakdown()
                return (
                    f"Generator provider error (aborting): {exc}",
                    all_tools_used,
                    messages,
                    total_usage,
                )
            all_tools_used.extend(gen_tools)
            _merge_usage(generator_usage, gen_usage)
            _merge_usage(total_usage, gen_usage)

            # Extract structured handoff if generator called submit_for_review
            self.last_handoff = self._extract_handoff(messages)
            if self.last_handoff:
                logger.info(
                    "[GenVer:Generator] Handoff received | risk={} files={}",
                    self.last_handoff.risk_assessment,
                    self.last_handoff.files_changed,
                )
            else:
                logger.warning(
                    "[GenVer:Generator] No submit_for_review handoff found on attempt {}", attempt
                )

            if not self.config.verifier_commands:
                # No verifier configured — single-pass generator
                _attach_breakdown()
                return gen_content, all_tools_used, messages, total_usage

            if self.last_handoff is None:
                if attempt >= self.config.max_retries:
                    _attach_breakdown()
                    return (
                        "GenVer stopped because the generator did not submit a review handoff. "
                        "The implementation was not verified.",
                        all_tools_used,
                        messages,
                        total_usage,
                    )
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"[GenVer Gate — attempt {attempt}/{self.config.max_retries}]\n"
                            "You must call `submit_for_review` after implementing the change.\n"
                            "Include at least: intent_summary, files_changed, and risk_assessment.\n"
                            "Do not ask for verification before submitting the handoff."
                        ),
                    }
                )
                continue

            logger.info(
                "[GenVer:Generator] Done | tools={} usage={}",
                gen_tools,
                gen_usage,
            )

            # --- Verifier phase ---
            if self.on_progress:
                await self.on_progress(
                    f"[GenVer] Running verification (attempt {attempt}/{self.config.max_retries})..."
                )

            logger.info("[GenVer:Verifier] Starting with model={}", self.verifier_model)
            verdict = await self._verifier.run_verification(
                handoff=self.last_handoff, user_request=self._user_request
            )
            _merge_usage(verifier_usage, verdict.get("usage", {}))
            _merge_usage(total_usage, verdict.get("usage", {}))
            logger.info(
                "[GenVer:Verifier] Done | passed={} errors={} usage={}",
                verdict["passed"],
                len(verdict["errors"]),
                verdict.get("usage", {}),
            )

            if verdict["passed"]:
                logger.info("GenVer verification passed on attempt {}", attempt)
                report_name = self._verifier.write_review_report(
                    attempt,
                    verdict,
                    passed=True,
                    handoff=self.last_handoff,
                    user_request=self._user_request,
                )
                # Surface code quality suggestions to user (advisory only)
                suggestions = verdict.get("suggestions", [])
                if suggestions and self.on_progress:
                    hint_text = "\n".join(f"  - {s}" for s in suggestions)
                    await self.on_progress(
                        f"[GenVer] Verification passed. Code review suggestions:\n{hint_text}"
                    )
                _attach_breakdown()
                return (
                    self._verifier.finalize_success_response(
                        gen_content, verdict, attempt, report_name, self.last_handoff
                    ),
                    all_tools_used,
                    messages,
                    total_usage,
                )

            # Provider-level error (bad model, auth failure, etc.) — abort immediately
            if verdict.get("provider_error"):
                logger.error("[GenVer] Verifier provider error, aborting (no retry)")
                error_summary = "\n".join(verdict["errors"][:5])
                final_msg = (
                    f"Verifier failed due to provider error (not retrying):\n{error_summary}"
                )
                _attach_breakdown()
                return final_msg, all_tools_used, messages, total_usage

            # --- Failed review report ---
            report_name = self._verifier.write_review_report(
                attempt,
                verdict,
                passed=False,
                handoff=self.last_handoff,
                user_request=self._user_request,
            )

            error_summary = "\n".join(verdict["errors"][:10])  # Cap at 10 errors
            logger.warning("GenVer attempt {} failed, {} errors", attempt, len(verdict["errors"]))

            if attempt == 1:
                feedback_msg = (
                    f"[Verifier Report — attempt {attempt}/{self.config.max_retries}]\n"
                    f"Tests FAILED. Errors:\n{error_summary}\n\n"
                    f"Full report: {report_name}\n"
                    "Fix the above issues now. Use your tools to edit the code directly. "
                    "Do NOT repeat this message or ask for clarification."
                )
                messages.append({"role": "user", "content": feedback_msg})
                continue

            repair = await self._verifier.run_repair(
                attempt=attempt,
                report_name=report_name,
                error_summary=error_summary,
                user_request=self._user_request,
                handoff=self.last_handoff,
            )
            all_tools_used.extend(repair.get("tools_used", []))
            _merge_usage(verifier_usage, repair.get("usage", {}))
            _merge_usage(total_usage, repair.get("usage", {}))

            if repair.get("provider_error"):
                _attach_breakdown()
                return (
                    "Verifier repair failed:\n" + "\n".join(repair.get("errors", [])[:5]),
                    all_tools_used,
                    messages,
                    total_usage,
                )

            repair_name = self._verifier.write_repair_report(
                attempt=attempt,
                source_report_name=report_name,
                repair=repair,
            )

            if attempt >= self.config.max_retries or not self.ask_user:
                _attach_breakdown()
                return (
                    self._verifier.finalize_repair_response(
                        repair=repair,
                        attempt=attempt,
                        report_name=report_name,
                        repair_name=repair_name,
                    ),
                    all_tools_used,
                    messages,
                    total_usage,
                )

            if self.on_progress:
                await self.on_progress(
                    f"[GenVer] Verifier applied direct fixes after attempt {attempt}. "
                    "Waiting for user guidance before the final optimization round..."
                )
            user_guidance = await self.ask_user(
                f"GenVer attempt {attempt} still had issues, so the verifier applied direct fixes.\n"
                f"Blocking issues were:\n{error_summary}\n\n"
                f"Verifier repair summary:\n{repair.get('content', '(no summary)')}\n\n"
                "If you want one final optimization round, describe what should still be improved. "
                "Otherwise reply 'done'."
            )
            if user_guidance is None or user_guidance.strip().lower() in (
                "",
                "done",
                "ok",
                "好的",
                "完成",
                "不用",
                "no",
                "n",
            ):
                _attach_breakdown()
                return (
                    self._verifier.finalize_repair_response(
                        repair=repair,
                        attempt=attempt,
                        report_name=report_name,
                        repair_name=repair_name,
                    ),
                    all_tools_used,
                    messages,
                    total_usage,
                )

            feedback_msg = (
                f"[Verifier Repair — attempt {attempt}/{self.config.max_retries}]\n"
                f"Verifier applied direct fixes for the previous blocking issues.\n"
                f"Repair summary:\n{repair.get('content', '(no summary)')}\n\n"
                f"Repair artifact: {repair_name}\n\n"
                f"[User Optimization Request]\n{user_guidance}\n\n"
                "Make the requested improvements now, then call submit_for_review again."
            )
            messages.append({"role": "user", "content": feedback_msg})

        # All retries exhausted
        logger.error("GenVer exhausted all {} retries", self.config.max_retries)
        final_msg = (
            f"Verification failed after {self.config.max_retries} attempts. "
            f"Last errors:\n" + "\n".join(verdict["errors"][:5])
        )
        _attach_breakdown()
        return final_msg, all_tools_used, messages, total_usage

    def _extract_handoff(self, messages: list[dict]) -> HandoffPayload | None:
        """Scan messages for a submit_for_review tool call and extract the handoff."""
        for msg in reversed(messages):
            for tc in msg.get("tool_calls", []):
                fn = tc.get("function", {})
                if fn.get("name") == "submit_for_review":
                    try:
                        args = json.loads(fn.get("arguments", "{}"))
                        return parse_handoff(args)
                    except (json.JSONDecodeError, KeyError, TypeError) as exc:
                        logger.warning("Failed to parse handoff: {}", exc)
                        return None
        return None
