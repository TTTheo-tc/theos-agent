"""Anthropic provider — native SDK integration for Claude models.

Uses the ``anthropic`` Python SDK directly for first-class access to Anthropic-
specific features: prompt caching, extended thinking, and native streaming.
"""

from __future__ import annotations

import re
import secrets
import string
from collections.abc import AsyncIterator
from typing import Any

from loguru import logger

from src.providers.base import (
    LLMProvider,
    LLMResponse,
    StreamDelta,
    ToolCallRequest,
)
from src.providers.errors import short_error_message
from src.providers.tool_args import parse_tool_arguments_object

_ALNUM = string.ascii_letters + string.digits

_ANTHROPIC_OAUTH_PREFIX = "sk-ant-oat"

_STOP_REASON_MAP = {"end_turn": "stop", "tool_use": "tool_calls", "max_tokens": "length"}


def _gen_tool_id() -> str:
    return "toolu_" + "".join(secrets.choice(_ALNUM) for _ in range(22))


def _chat_error_response(exc: Exception, model_name: str) -> LLMResponse:
    import anthropic as _anthropic

    err_msg = short_error_message(exc)
    if isinstance(exc, _anthropic.AuthenticationError):
        logger.error("Anthropic auth failed (model={}): {}", model_name, err_msg)
        return LLMResponse(
            content=(
                f"Error calling LLM (authentication failed): {err_msg}\n\n"
                "API key may be expired or invalid. Check your provider credentials."
            ),
            finish_reason="error",
            error_type="AuthenticationError",
        )

    if isinstance(exc, _anthropic.RateLimitError):
        logger.warning("Anthropic rate limited (model={}): {}", model_name, err_msg)
        return LLMResponse(
            content=f"Error calling LLM (rate limited): {err_msg}",
            finish_reason="error",
            error_type="RateLimitError",
        )

    logger.warning("Anthropic call failed (model={}): {}", model_name, err_msg)
    return LLMResponse(
        content=f"Error calling LLM: {err_msg}",
        finish_reason="error",
        error_type=type(exc).__name__,
    )


def _mark_last_user_message_cached(
    messages: list[dict[str, Any]],
    marker: dict[str, str],
) -> list[dict[str, Any]]:
    """Return messages with cache_control on the last user content block."""
    new_msgs = list(messages)
    for i in range(len(new_msgs) - 1, -1, -1):
        if new_msgs[i].get("role") != "user":
            continue

        c = new_msgs[i]["content"]
        if isinstance(c, str):
            new_msgs[i] = {
                **new_msgs[i],
                "content": [{"type": "text", "text": c, "cache_control": marker}],
            }
        elif isinstance(c, list) and c:
            nc = list(c)
            nc[-1] = {**nc[-1], "cache_control": marker}
            new_msgs[i] = {**new_msgs[i], "content": nc}
        break
    return new_msgs


def _mark_last_tool_cached(
    tools: list[dict[str, Any]] | None,
    marker: dict[str, str],
) -> list[dict[str, Any]] | None:
    if not tools:
        return tools
    new_tools = list(tools)
    new_tools[-1] = {**new_tools[-1], "cache_control": marker}
    return new_tools


class AnthropicProvider(LLMProvider):
    """LLM provider using the native Anthropic SDK for Claude models.

    Handles message format conversion (OpenAI chat → Anthropic Messages API),
    prompt caching, tool calls, and streaming.
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        default_model: str = "claude-sonnet-4-20250514",
        extra_headers: dict[str, str] | None = None,
        provider_name: str | None = None,
        spec: Any | None = None,
    ) -> None:
        super().__init__(api_key, api_base)
        self._default_model = default_model
        self._extra_headers = extra_headers or {}
        self._provider_name = provider_name
        self._spec = spec

        from anthropic import AsyncAnthropic

        if api_key and api_key.startswith(_ANTHROPIC_OAUTH_PREFIX):
            raise ValueError(
                "Anthropic OAuth tokens are disabled in TheOS. "
                "Use an Anthropic API key (sk-ant-...) instead."
            )

        client_kw: dict[str, Any] = {}
        if api_key:
            client_kw["api_key"] = api_key
        if api_base:
            client_kw["base_url"] = api_base
        self._client = AsyncAnthropic(**client_kw)

    # ------------------------------------------------------------------
    # Model name helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _strip_prefix(model: str) -> str:
        """Remove ``anthropic/`` prefix — the SDK expects bare model names."""
        if model.startswith("anthropic/"):
            return model[len("anthropic/") :]
        return model

    # ------------------------------------------------------------------
    # Message conversion: OpenAI chat format → Anthropic Messages API
    # ------------------------------------------------------------------

    def _convert_messages(
        self, messages: list[dict[str, Any]]
    ) -> tuple[str | list[dict[str, Any]], list[dict[str, Any]]]:
        """Return ``(system, anthropic_messages)``.

        Extracts system messages into a separate top-level parameter,
        converts tool_calls/tool results, and merges consecutive same-role
        messages (required by the Anthropic API).
        """
        system: str | list[dict[str, Any]] = ""
        raw: list[dict[str, Any]] = []

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content")

            # System messages → top-level system parameter
            if role == "system":
                if isinstance(system, list):
                    # Multiple system messages: accumulate as blocks
                    if isinstance(content, str):
                        system.append({"type": "text", "text": content})
                    elif isinstance(content, list):
                        system.extend(content)
                elif system:
                    # Already have a string system, convert to list
                    blocks: list[dict[str, Any]] = [{"type": "text", "text": system}]
                    if isinstance(content, str):
                        blocks.append({"type": "text", "text": content})
                    elif isinstance(content, list):
                        blocks.extend(content)
                    system = blocks
                else:
                    system = content if isinstance(content, (str, list)) else str(content or "")
                continue

            # Tool result → user message with tool_result block
            if role == "tool":
                block = self._tool_result_block(msg)
                if raw and raw[-1]["role"] == "user":
                    prev_c = raw[-1]["content"]
                    if isinstance(prev_c, list):
                        prev_c.append(block)
                    else:
                        raw[-1]["content"] = [
                            {"type": "text", "text": prev_c or ""},
                            block,
                        ]
                else:
                    raw.append({"role": "user", "content": [block]})
                continue

            # Assistant message → convert tool_calls to tool_use blocks
            if role == "assistant":
                raw.append({"role": "assistant", "content": self._assistant_blocks(msg)})
                continue

            # User message → pass through with image conversion
            if role == "user":
                raw.append(
                    {
                        "role": "user",
                        "content": self._convert_user_content(content),
                    }
                )
                continue

        return system, self._merge_consecutive(raw)

    @staticmethod
    def _tool_result_block(msg: dict[str, Any]) -> dict[str, Any]:
        content = msg.get("content")
        block: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": msg.get("tool_call_id", ""),
        }
        if isinstance(content, (str, list)):
            block["content"] = content
        else:
            block["content"] = str(content) if content else ""
        return block

    @staticmethod
    def _assistant_blocks(msg: dict[str, Any]) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        content = msg.get("content")

        if isinstance(content, str) and content:
            blocks.append({"type": "text", "text": content})
        elif isinstance(content, list):
            blocks.extend(
                item if isinstance(item, dict) else {"type": "text", "text": str(item)}
                for item in content
            )

        for tc in msg.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            func = tc.get("function", {})
            blocks.append(
                {
                    "type": "tool_use",
                    "id": tc.get("id") or _gen_tool_id(),
                    "name": func.get("name", ""),
                    "input": parse_tool_arguments_object(func.get("arguments", "{}")),
                }
            )

        return blocks or [{"type": "text", "text": ""}]

    def _convert_user_content(self, content: Any) -> Any:
        """Convert user message content, translating image_url blocks."""
        if isinstance(content, str) or content is None:
            return content or "(empty)"
        if not isinstance(content, list):
            return str(content)

        result: list[dict[str, Any]] = []
        for item in content:
            if not isinstance(item, dict):
                result.append({"type": "text", "text": str(item)})
                continue
            if item.get("type") == "image_url":
                converted = self._convert_image_block(item)
                if converted:
                    result.append(converted)
                continue
            result.append(item)
        return result or "(empty)"

    @staticmethod
    def _convert_image_block(block: dict[str, Any]) -> dict[str, Any] | None:
        """Convert OpenAI image_url block to Anthropic image block."""
        url = (block.get("image_url") or {}).get("url", "")
        if not url:
            return None
        m = re.match(r"data:(image/\w+);base64,(.+)", url, re.DOTALL)
        if m:
            return {
                "type": "image",
                "source": {"type": "base64", "media_type": m.group(1), "data": m.group(2)},
            }
        return {
            "type": "image",
            "source": {"type": "url", "url": url},
        }

    @staticmethod
    def _merge_consecutive(msgs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Anthropic requires alternating user/assistant roles."""
        merged: list[dict[str, Any]] = []
        for msg in msgs:
            if merged and merged[-1]["role"] == msg["role"]:
                prev_c = merged[-1]["content"]
                cur_c = msg["content"]
                if isinstance(prev_c, str):
                    prev_c = [{"type": "text", "text": prev_c}]
                if isinstance(cur_c, str):
                    cur_c = [{"type": "text", "text": cur_c}]
                if isinstance(cur_c, list):
                    prev_c.extend(cur_c)
                merged[-1]["content"] = prev_c
            else:
                merged.append(msg)
        return merged

    # ------------------------------------------------------------------
    # Tool conversion
    # ------------------------------------------------------------------

    @staticmethod
    def _convert_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
        """Convert OpenAI tool format to Anthropic tool format.

        OpenAI: ``{"type": "function", "function": {"name", "description", "parameters"}}``
        Anthropic: ``{"name", "description", "input_schema"}``
        """
        if not tools:
            return None
        result = []
        for tool in tools:
            func = tool.get("function", tool)
            entry: dict[str, Any] = {
                "name": func.get("name", ""),
                "input_schema": func.get("parameters", {"type": "object", "properties": {}}),
            }
            desc = func.get("description")
            if desc:
                entry["description"] = desc
            if "cache_control" in tool:
                entry["cache_control"] = tool["cache_control"]
            result.append(entry)
        return result

    # ------------------------------------------------------------------
    # Prompt caching
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_cache_control(
        system: str | list[dict[str, Any]],
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> tuple[str | list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]] | None]:
        """Inject ``cache_control`` breakpoints for Anthropic prompt caching.

        Marks system content, last user message, and last tool definition
        with ``cache_control: {"type": "ephemeral"}``.

        When the system string contains ``PROMPT_CACHE_BOUNDARY``, the
        static portion (before the boundary) gets the cache marker so it
        can be reused across turns, while the dynamic portion (after) does
        not.
        """
        from src.agent.context import ContextBuilder

        marker = {"type": "ephemeral"}
        boundary = ContextBuilder.PROMPT_CACHE_BOUNDARY

        # System
        if isinstance(system, str) and system:
            if boundary in system:
                static, dynamic = system.split(boundary, 1)
                system = [
                    {"type": "text", "text": static.strip(), "cache_control": marker},
                    {"type": "text", "text": dynamic.strip()},
                ]
            else:
                system = [{"type": "text", "text": system, "cache_control": marker}]
        elif isinstance(system, list) and system:
            system = list(system)
            system[-1] = {**system[-1], "cache_control": marker}

        new_msgs = _mark_last_user_message_cached(messages, marker)
        new_tools = _mark_last_tool_cached(tools, marker)
        return system, new_msgs, new_tools

    async def _retry_after_auth_failure(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str | None,
        max_tokens: int,
        temperature: float,
    ) -> tuple[LLMResponse | None, BaseException | None]:
        """Retry an auth-failed request once after a short delay."""
        import asyncio

        logger.warning("Anthropic auth failed; retrying once after 1s delay")
        await asyncio.sleep(1)

        retry_model_name, retry_kwargs = self._build_kwargs(
            messages, tools, model, max_tokens, temperature
        )
        try:
            response = await self._client.messages.create(**retry_kwargs)
            logger.info("Anthropic auth recovered after retry (model={})", retry_model_name)
            return self._parse_response(response), None
        except Exception as exc:
            return None, exc

    # ------------------------------------------------------------------
    # Usage normalization
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_usage(usage: Any) -> dict[str, int]:
        """Normalize Anthropic usage to standard field names.

        Anthropic: ``input_tokens``, ``output_tokens``
        Standard:  ``prompt_tokens``, ``completion_tokens``, ``total_tokens``
        """
        if not usage:
            return {}

        prompt = getattr(usage, "input_tokens", 0) or 0
        completion = getattr(usage, "output_tokens", 0) or 0
        result: dict[str, int] = {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        }

        cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        if cache_creation:
            result["cache_creation_input_tokens"] = cache_creation
        if cache_read:
            result["cache_read_input_tokens"] = cache_read

        return result

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_response(self, response: Any) -> LLMResponse:
        """Parse Anthropic Message into LLMResponse."""
        content_parts: list[str] = []
        tool_calls: list[ToolCallRequest] = []
        reasoning_parts: list[str] = []

        for block in response.content:
            if block.type == "text":
                content_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCallRequest(
                        id=block.id,
                        name=block.name,
                        arguments=block.input if isinstance(block.input, dict) else {},
                    )
                )
            elif block.type == "thinking":
                reasoning_parts.append(block.thinking)

        finish_reason = _STOP_REASON_MAP.get(
            response.stop_reason or "", response.stop_reason or "stop"
        )
        return LLMResponse(
            content="".join(content_parts) or None,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=self._normalize_usage(response.usage),
            reasoning_content="".join(reasoning_parts) or None,
        )

    # ------------------------------------------------------------------
    # Shared request builder
    # ------------------------------------------------------------------

    def _build_kwargs(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str | None,
        max_tokens: int,
        temperature: float,
    ) -> tuple[str, dict[str, Any]]:
        """Build kwargs for ``messages.create`` / ``messages.stream``.

        Returns ``(model_name, kwargs)``.
        """
        model_name = self._strip_prefix(model or self._default_model)
        system, anthropic_msgs = self._convert_messages(self._sanitize_empty_content(messages))
        anthropic_tools = self._convert_tools(tools)

        if self._spec and self._spec.supports_prompt_caching:
            system, anthropic_msgs, anthropic_tools = self._apply_cache_control(
                system, anthropic_msgs, anthropic_tools
            )

        kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": anthropic_msgs,
            "max_tokens": max(1, max_tokens),
            "temperature": temperature,
        }
        if system:
            kwargs["system"] = system
        if anthropic_tools:
            kwargs["tools"] = anthropic_tools
            kwargs["tool_choice"] = {"type": "auto"}
        if self._extra_headers:
            kwargs["extra_headers"] = self._extra_headers

        return model_name, kwargs

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def supports_streaming(self) -> bool:
        return True

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        model_name, kwargs = self._build_kwargs(messages, tools, model, max_tokens, temperature)
        try:
            response = await self._client.messages.create(**kwargs)
            return self._parse_response(response)
        except Exception as e:
            import anthropic as _anthropic

            if isinstance(e, _anthropic.AuthenticationError):
                retried, retry_exc = await self._retry_after_auth_failure(
                    messages=messages,
                    tools=tools,
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                if retried is not None:
                    return retried
                if retry_exc is not None:
                    e = retry_exc

            return _chat_error_response(e, model_name)

    async def _do_stream(self, kwargs: dict[str, Any]) -> AsyncIterator[StreamDelta]:
        """Internal stream implementation (extracted for auth-retry reuse)."""
        current_tool: dict[str, Any] | None = None
        tool_calls: list[ToolCallRequest] = []
        final_usage: dict[str, int] = {}
        stop_reason: str | None = None

        async with self._client.messages.stream(**kwargs) as stream:
            async for event in stream:
                event_type = event.type

                if event_type == "content_block_start":
                    cb = event.content_block
                    if cb.type == "tool_use":
                        current_tool = {
                            "id": cb.id,
                            "name": cb.name,
                            "input_json": "",
                        }

                elif event_type == "content_block_delta":
                    delta = event.delta
                    if delta.type == "text_delta":
                        yield StreamDelta(content=delta.text)
                    elif delta.type == "thinking_delta":
                        yield StreamDelta(reasoning_content=delta.thinking)
                    elif delta.type == "input_json_delta" and current_tool is not None:
                        current_tool["input_json"] += delta.partial_json

                elif event_type == "content_block_stop":
                    if current_tool is not None:
                        raw_args = current_tool["input_json"]
                        tc = ToolCallRequest(
                            id=current_tool["id"],
                            name=current_tool["name"],
                            arguments=parse_tool_arguments_object(raw_args) if raw_args else {},
                        )
                        tool_calls.append(tc)
                        current_tool = None
                        # Yield immediately so the caller can start executing
                        # this tool while the rest of the stream continues.
                        yield StreamDelta(tool_ready=[tc])

                elif event_type == "message_delta":
                    stop_reason = getattr(event.delta, "stop_reason", None)
                    if event.usage:
                        # Merge (not overwrite) — message_delta has output_tokens
                        final_usage.update(self._normalize_usage(event.usage))

                elif event_type == "message_start":
                    if hasattr(event, "message") and event.message.usage:
                        # message_start has input_tokens (prompt-side usage)
                        final_usage = self._normalize_usage(event.message.usage)

        # Map stop_reason
        finish_reason = _STOP_REASON_MAP.get(stop_reason or "", stop_reason or "stop")

        yield StreamDelta(
            is_final=True,
            tool_calls=tool_calls,
            usage=final_usage,
            finish_reason=finish_reason,
        )

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> AsyncIterator[StreamDelta]:
        """Stream a chat completion, yielding ``StreamDelta`` chunks."""
        model_name, kwargs = self._build_kwargs(messages, tools, model, max_tokens, temperature)
        has_yielded = False
        try:
            async for delta in self._do_stream(kwargs):
                if not delta.is_final and (delta.content or delta.tool_ready):
                    has_yielded = True
                yield delta
            return

        except Exception as e:
            import anthropic as _anthropic

            # Auth recovery: only retry if no actionable content has been
            # yielded yet.  Both text content AND tool_ready events count
            # because the caller may have already started executing tools.
            if not has_yielded and isinstance(e, _anthropic.AuthenticationError):
                import asyncio

                logger.warning(
                    "Anthropic stream auth failed; retrying once after 1s delay (model={})",
                    model_name,
                )
                await asyncio.sleep(1)

                _, retry_kwargs = self._build_kwargs(
                    messages, tools, model, max_tokens, temperature
                )
                try:
                    async for delta in self._do_stream(retry_kwargs):
                        yield delta
                    logger.info("Anthropic stream auth recovered after retry")
                    return
                except Exception as retry_exc:
                    e = retry_exc  # retry also failed, use new error

            err_msg = short_error_message(e)
            logger.warning("Anthropic stream failed (model={}): {}", model_name, err_msg)
            yield StreamDelta(
                content=f"Error: {err_msg}",
                is_final=True,
                finish_reason="error",
                error_type=type(e).__name__,
            )

    def get_default_model(self) -> str:
        return self._default_model
