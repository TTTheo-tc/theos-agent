"""Shared tool-loop core used by AgentLoop and SubagentManager.

Provides run_tool_loop(), the LLM-call -> tool-dispatch -> iterate engine.
Stateless: all state is passed in via parameters and callback functions.
Key dependencies: LLMProvider (chat calls), ToolRegistry (tool dispatch),
SafetyLayer (tool output sanitization).
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Awaitable, Callable

from loguru import logger

from src.agent.loop_detector import LoopDetector
from src.agent.tools.registry import ToolRegistry
from src.providers.base import LLMProvider, ToolCallRequest
from src.safety.leak_detector import scrub_credentials
from src.utils.text import strip_think, tool_hint
from src.utils.usage import merge_usage


class ProviderAuthError(Exception):
    """Raised when the LLM provider returns an authentication error."""


# Aliases for asyncio primitives (easier to test/mock)
_async_sleep = asyncio.sleep
_create_task = asyncio.create_task

# ---------------------------------------------------------------------------
# Callback type aliases
# ---------------------------------------------------------------------------
# Called to append an assistant message to the conversation.
# Signature: (messages, content, tool_call_dicts | None, reasoning_content) -> messages
AddAssistantFn = Callable[
    [list[dict], str | None, list[dict] | None, str | None],
    list[dict],
]

# Called to append a tool result to the conversation.
# Signature: (messages, tool_call_id, tool_name, result) -> messages
AddToolResultFn = Callable[
    [list[dict], str, str, str],
    list[dict],
]

# Called to report progress to the user (optional).
# Signature: (content, *, tool_hint=False) -> awaitable
ProgressFn = Callable[..., Awaitable[None]]

# Called with each text delta during streaming.
# Signature: (delta_text) -> awaitable
ContentDeltaFn = Callable[[str], Awaitable[None]]


# ---------------------------------------------------------------------------
# Default message helpers (simple dict-append, used by subagent)
# ---------------------------------------------------------------------------


def _default_add_assistant(
    messages: list[dict],
    content: str | None,
    tool_call_dicts: list[dict] | None,
    _reasoning_content: str | None,
) -> list[dict]:
    msg: dict[str, Any] = {"role": "assistant", "content": content or ""}
    if tool_call_dicts:
        msg["tool_calls"] = tool_call_dicts
    messages.append(msg)
    return messages


_tool_safety: Any = None  # lazy SafetyLayer singleton

# Hard limit on tool result size for the *current* turn.
# get_history() applies its own softer trim (500+500 chars) for subsequent turns,
# but nothing protects the current turn from a multi-MB grep or bash result.
_TOOL_RESULT_HARD_LIMIT = 50_000  # 50 KB
_TOOL_RESULT_HEAD = 20_000
_TOOL_RESULT_TAIL = 5_000


def _truncate_tool_result(result: str, tool_name: str) -> str:
    """Truncate oversized tool output with head+tail preservation."""
    if len(result) <= _TOOL_RESULT_HARD_LIMIT:
        return result
    head = result[:_TOOL_RESULT_HEAD]
    tail = result[-_TOOL_RESULT_TAIL:]
    trimmed = len(result) - _TOOL_RESULT_HEAD - _TOOL_RESULT_TAIL
    return f"{head}\n\n... [{tool_name}: {trimmed} chars trimmed] ...\n\n{tail}"


def _sanitize_tool_result(result: str, tool_name: str = "") -> str:
    """Scan tool output for injection/leaks, then enforce size limit."""
    global _tool_safety
    if _tool_safety is None:
        from src.safety.layer import SafetyLayer

        _tool_safety = SafetyLayer()
    cleaned = _tool_safety.sanitize_tool_output(result)
    return _truncate_tool_result(cleaned, tool_name)


# Prefixes that ToolRegistry uses for autonomy/approval denials.
# Only these patterns should increment the denial counter — generic ⚠
# warnings from tool implementations (image, PDF, etc.) must not.
_DENIAL_PREFIXES = (
    "⚠ This tool is restricted",  # owner_only
    "⚠ Tool '",  # check_tool_allowed
    "⚠ Path blocked",  # check_path_allowed
    "⚠ Command blocked",  # check_command_allowed
    "⚠ Rate limited",  # check_rate_limit
    "⚠ Operation blocked",  # approval_gate
)


def _is_registry_denial(result: str) -> bool:
    """Return True if *result* is a known ToolRegistry denial message."""
    return any(result.startswith(p) for p in _DENIAL_PREFIXES)


def _cancel_preflight_tasks(tasks: dict[str, asyncio.Task]) -> None:
    """Best-effort cancel of any streaming preflight tool tasks."""
    for task in tasks.values():
        task.cancel()
    tasks.clear()


def _tool_call_dicts(tool_calls: list[ToolCallRequest]) -> list[dict[str, Any]]:
    return [
        {
            "id": tc.id,
            "type": "function",
            "function": {
                "name": tc.name,
                "arguments": _json_args(tc.arguments),
            },
        }
        for tc in tool_calls
    ]


def _json_args(arguments: dict[str, Any], *, sort_keys: bool = False) -> str:
    return json.dumps(
        arguments,
        ensure_ascii=False,
        sort_keys=sort_keys,
        default=str,
    )


def _plan_unique_tool_calls(
    tool_calls: list[ToolCallRequest],
    tools: ToolRegistry,
) -> tuple[list[int], dict[int, int]]:
    """Return unique call indices plus duplicate-to-first index mapping."""
    dedup_map: dict[str, int] = {}
    unique_indices: list[int] = []
    duplicate_map: dict[int, int] = {}

    for i, tc in enumerate(tool_calls):
        tool_obj = tools.get(tc.name)
        if tool_obj and tool_obj.dedupe_within_turn:
            sig = f"{tc.name}:{_json_args(tc.arguments, sort_keys=True)}"
            if sig in dedup_map:
                duplicate_map[i] = dedup_map[sig]
                logger.info("[ToolLoop] Dedup: call {} is duplicate of {}", i, dedup_map[sig])
                continue
            dedup_map[sig] = i
        unique_indices.append(i)

    return unique_indices, duplicate_map


def _all_parallel_safe(tool_calls: list[ToolCallRequest], tools: ToolRegistry) -> bool:
    if len(tool_calls) <= 1:
        return False
    for tc in tool_calls:
        tool_obj = tools.get(tc.name)
        if tool_obj is None or not tool_obj.parallel_safe:
            return False
    return True


def _default_add_tool_result(
    messages: list[dict],
    tool_call_id: str,
    tool_name: str,
    result: str,
) -> list[dict]:
    messages.append(
        {"role": "tool", "tool_call_id": tool_call_id, "name": tool_name, "content": result}
    )
    return messages


# ---------------------------------------------------------------------------
# Core loop
# ---------------------------------------------------------------------------


# Callback type: async (messages) -> messages — compacts if over budget.
MaybeCompactFn = Callable[[list[dict]], Awaitable[list[dict]]]


async def run_tool_loop(
    *,
    provider: LLMProvider,
    messages: list[dict],
    tools: ToolRegistry,
    model: str,
    temperature: float,
    max_tokens: int,
    max_iterations: int,
    add_assistant: AddAssistantFn | None = None,
    add_tool_result: AddToolResultFn | None = None,
    on_progress: ProgressFn | None = None,
    on_content_delta: ContentDeltaFn | None = None,
    tool_context: Any = None,
    cache_keepalive_threshold_s: int = 0,
    maybe_compact: MaybeCompactFn | None = None,
) -> tuple[str | None, list[str], list[dict], dict[str, int]]:
    """Run the LLM -> tool-dispatch -> iterate loop.

    Returns ``(final_content, tools_used, messages, usage)``.

    Parameters
    ----------
    provider : LLMProvider
        The LLM provider to call.
    messages : list[dict]
        The initial message list (mutated in-place).
    tools : ToolRegistry
        Available tools.
    model, temperature, max_tokens :
        LLM call parameters.
    max_iterations : int
        Safety guard on total iterations.
    add_assistant : callable, optional
        Custom function to append an assistant message. Receives
        ``(messages, content, tool_call_dicts | None, reasoning_content)``.
        Defaults to a simple dict-append.
    add_tool_result : callable, optional
        Custom function to append a tool result. Receives
        ``(messages, tool_call_id, tool_name, result)``.
        Defaults to a simple dict-append.
    on_progress : callable, optional
        Async callback ``(content, *, tool_hint=False)`` for progress
        reporting.  Called with cleaned (think-stripped) model text and
        with tool hint strings.
    on_content_delta : callable, optional
        Async callback ``(delta_text)`` called with each text chunk
        during streaming.  When provided **and** the provider supports
        streaming, ``provider.chat_stream()`` is used instead of
        ``provider.chat()``.  The rest of the loop is unchanged.
    maybe_compact : callable, optional
        Async callback ``(messages) -> messages`` that checks token budget
        and compacts history if needed.  Called after all tool results for
        one iteration have been appended (never between a tool_calls
        message and its results).
    """
    _add_assistant = add_assistant or _default_add_assistant
    _add_tool_result = add_tool_result or _default_add_tool_result

    iteration = 0
    final_content: str | None = None
    tools_used: list[str] = []
    total_usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    logger.info("[ToolLoop] Starting | model={} max_iter={}", model, max_iterations)

    detector = LoopDetector()

    while iteration < max_iterations:
        iteration += 1
        logger.debug("[ToolLoop] Iteration {}/{} | model={}", iteration, max_iterations, model)

        _preflight_tasks: dict[str, asyncio.Task] = {}

        if on_content_delta and getattr(provider, "supports_streaming", False) is True:
            # Streaming path: forward text deltas to caller as they arrive.
            # Also fire-and-forget parallel_safe tool executions as soon as
            # each tool_use block completes (via tool_ready), overlapping
            # tool execution with the rest of the stream.
            accumulated_content = ""
            async for delta in provider.chat_stream(
                messages=messages,
                tools=tools.get_definitions(),
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
            ):
                if delta.content and not delta.is_final:
                    accumulated_content += delta.content
                    await on_content_delta(delta.content)

                # Start executing parallel_safe tools as soon as they're ready
                for tc in delta.tool_ready:
                    tool_obj = tools.get(tc.name)
                    if tool_obj and tool_obj.parallel_safe:
                        _preflight_tasks[tc.id] = _create_task(
                            tools.execute(tc.name, tc.arguments, context=tool_context)
                        )

                if delta.is_final:
                    from src.providers.base import LLMResponse

                    response = LLMResponse(
                        content=delta.content if delta.content else (accumulated_content or None),
                        tool_calls=delta.tool_calls,
                        finish_reason=delta.finish_reason or "stop",
                        usage=delta.usage,
                        reasoning_content=delta.reasoning_content,
                        error_type=delta.error_type,
                    )
                    break
            else:
                # Stream ended without final delta — handle gracefully.
                # Cancel any preflight tasks that were started during the stream.
                _cancel_preflight_tasks(_preflight_tasks)
                from src.providers.base import LLMResponse

                response = LLMResponse(content=accumulated_content or None, finish_reason="stop")
        else:
            # Non-streaming path (existing behavior)
            response = await provider.chat(
                messages=messages,
                tools=tools.get_definitions(),
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
            )

        # Auth errors should not be treated as normal responses.
        # Raise so the caller (_run_inference) can handle retry/propagation
        # instead of storing error text as assistant content in session history.
        if response.error_type == "AuthenticationError":
            _cancel_preflight_tasks(_preflight_tasks)
            raise ProviderAuthError(response.content or "Authentication failed")

        if response.usage:
            merge_usage(total_usage, response.usage)
            cache_read = response.usage.get("cache_read_input_tokens", 0)
            cache_create = response.usage.get("cache_creation_input_tokens", 0)
            if cache_read or cache_create:
                logger.info(
                    "[ToolLoop] Cache | read={} creation={} prompt={}",
                    cache_read,
                    cache_create,
                    response.usage.get("prompt_tokens", 0),
                )
            logger.debug(
                "[ToolLoop] model={} iter={} usage={}",
                model,
                iteration,
                response.usage,
            )

        if response.has_tool_calls:
            if on_progress:
                clean = strip_think(response.content)
                if clean:
                    await on_progress(clean)
                await on_progress(tool_hint(response.tool_calls), tool_hint=True)

            tool_call_dicts = _tool_call_dicts(response.tool_calls)

            messages = _add_assistant(
                messages,
                response.content,
                tool_call_dicts,
                response.reasoning_content,
            )

            # -- Dedup: collapse identical calls for opt-in tools --
            unique_indices, duplicate_map = _plan_unique_tool_calls(response.tool_calls, tools)

            calls_to_execute = [response.tool_calls[i] for i in unique_indices]

            # -- Parallel branch: run all tools concurrently when safe --
            all_parallel = _all_parallel_safe(calls_to_execute, tools)

            executed_results: dict[int, str] = {}

            if all_parallel:
                logger.info(
                    "[ToolLoop] Executing {} tools in parallel",
                    len(calls_to_execute),
                )
                if on_progress:
                    await on_progress(
                        f"\u23f3 Running {len(calls_to_execute)} tools...",
                        tool_hint=False,
                    )

                async def _exec_one(tc_item):
                    # Reuse preflight result if this tool was already started
                    # during streaming (fire-and-forget on tool_ready).
                    preflight = _preflight_tasks.get(tc_item.id)
                    if preflight is not None:
                        return await preflight
                    args_str = _json_args(tc_item.arguments)
                    logger.info(
                        "Tool call: {}({})", tc_item.name, scrub_credentials(args_str[:200])
                    )
                    return await tools.execute(
                        tc_item.name, tc_item.arguments, context=tool_context
                    )

                results = await asyncio.gather(
                    *[_exec_one(tc) for tc in calls_to_execute],
                    return_exceptions=True,
                )

                for idx, tc_item, result in zip(unique_indices, calls_to_execute, results):
                    if isinstance(result, Exception):
                        logger.warning("Tool {} failed: {}", tc_item.name, result)
                        executed_results[idx] = f"Error: {result}"
                    else:
                        executed_results[idx] = _sanitize_tool_result(result, tc_item.name)

                _cancel_preflight_tasks(_preflight_tasks)

            else:
                # -- Sequential branch (original path) --
                for idx in unique_indices:
                    tc = response.tool_calls[idx]
                    args_str = _json_args(tc.arguments)
                    logger.info("Tool call: {}({})", tc.name, scrub_credentials(args_str[:200]))
                    t0 = time.monotonic()

                    # P3: For long-running tools, send progress events
                    _progress_sent = False

                    async def _check_progress():
                        nonlocal _progress_sent
                        await _async_sleep(10)
                        if not _progress_sent and on_progress:
                            _progress_sent = True
                            elapsed_s = time.monotonic() - t0
                            # Use tool_hint=False so progress messages are NOT filtered
                            # by send_tool_hints=false channel config (P3 fix).
                            await on_progress(
                                f"\u23f3 Running `{tc.name}`... ({elapsed_s:.0f}s)",
                                tool_hint=False,
                            )

                    # Reuse preflight result if this tool was already started
                    # during streaming (fire-and-forget on tool_ready).
                    preflight = _preflight_tasks.pop(tc.id, None)

                    progress_task = _create_task(_check_progress())
                    try:
                        if preflight is not None:
                            result = await preflight
                        else:
                            result = await tools.execute(
                                tc.name, tc.arguments, context=tool_context
                            )
                    finally:
                        progress_task.cancel()

                    elapsed = time.monotonic() - t0

                    # TTL keepalive: if tool took too long, send a minimal request
                    # to keep the provider's prompt cache warm
                    if cache_keepalive_threshold_s > 0 and elapsed >= cache_keepalive_threshold_s:
                        logger.info(
                            "[ToolLoop] Tool {} took {:.0f}s, sending cache keepalive",
                            tc.name,
                            elapsed,
                        )
                        try:
                            await provider.chat(
                                messages=messages[:2],
                                tools=[],
                                model=model,
                                max_tokens=1,
                            )
                        except Exception:
                            logger.opt(exception=True).debug("Cache keepalive failed (non-fatal)")

                    # Safety: scan tool output for injection and credential leaks
                    executed_results[idx] = _sanitize_tool_result(result, tc.name)

            # Cancel any preflight tasks that weren't consumed (e.g., deduped
            # tools or tools that took a different execution path).
            _cancel_preflight_tasks(_preflight_tasks)

            # -- Append ALL tool results in original order (unique + deduped) --
            for i, tc in enumerate(response.tool_calls):
                tools_used.append(tc.name)
                if i in duplicate_map:
                    result = executed_results[duplicate_map[i]]
                else:
                    result = executed_results[i]
                messages = _add_tool_result(messages, tc.id, tc.name, result)
                detector.record(tc.name, tc.arguments)

                # Track autonomy/approval denials — only for results that
                # match the specific patterns produced by ToolRegistry
                # (registry.py:74,81,88,95,101,113).  Generic ⚠ warnings
                # from tools (e.g., image/PDF partial failures) are NOT
                # denials and must not be counted.
                if isinstance(result, str) and _is_registry_denial(result):
                    detector.record_denial(tc.name)
                else:
                    # Reset on any non-denial result so redirects only trigger
                    # on repeated blocked attempts, not stale earlier denials.
                    detector.reset_denial(tc.name)

            # Loop detection: inject a break message if the same call repeats
            tool_name = detector.check()
            if tool_name:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"You have called '{tool_name}' with identical arguments "
                            f"{detector._threshold} times. "
                            "This appears to be a loop. Stop repeating and explain "
                            "what is going wrong."
                        ),
                    }
                )
                detector.reset()

            # Denial detection: redirect when the same tool keeps getting blocked
            denied_tool = detector.check_denials()
            if denied_tool:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"Tool '{denied_tool}' has been denied multiple times. "
                            "The autonomy policy or approval gate is blocking this operation. "
                            "Explain what you need so the user can approve it, or use "
                            "a different approach."
                        ),
                    }
                )
                detector.reset_all_denials()

            # P1: Hard constraint — Feishu verification requirements are per-write-tool,
            # not just "any verification tool appeared in the same batch".
            batch_names = {tc.name for tc in response.tool_calls}
            missing_steps: list[str] = []
            if "feishu_edit" in batch_names and "feishu_read" not in batch_names:
                missing_steps.append("feishu_edit requires feishu_read")
            if "feishu_create" in batch_names:
                if "feishu_list" not in batch_names:
                    missing_steps.append("feishu_create requires feishu_list")
                if "feishu_read" not in batch_names:
                    missing_steps.append("feishu_create requires feishu_read")
            if missing_steps:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "[SYSTEM] You just called Feishu write tools. "
                            "You MUST verify the result BEFORE responding to the user. "
                            "Missing verification steps: "
                            f"{'; '.join(missing_steps)}. "
                            "Do NOT skip this step."
                        ),
                    }
                )

            # In-loop compaction: check token budget after all tool results
            # for this iteration have been appended.  Never runs between a
            # tool_calls message and its results — only at iteration boundary.
            if maybe_compact is not None:
                messages = await maybe_compact(messages)
        else:
            # A stream may have already started speculative tool execution via
            # tool_ready before ending in an error/final text response. Ensure
            # those tasks do not keep running after we abandon tool handling.
            _cancel_preflight_tasks(_preflight_tasks)
            clean = strip_think(response.content)
            messages = _add_assistant(
                messages,
                clean,
                None,
                response.reasoning_content,
            )
            final_content = clean
            break

    if final_content is None and iteration >= max_iterations:
        logger.warning("Max iterations ({}) reached", max_iterations)
        final_content = (
            f"I reached the maximum number of tool call iterations ({max_iterations}) "
            "without completing the task. You can try breaking the task into smaller steps."
        )

    return final_content, tools_used, messages, total_usage
