"""OpenAI-compatible provider — direct OpenAI SDK."""

from __future__ import annotations

import secrets
import string
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import openai
from loguru import logger
from openai import AsyncOpenAI

from src.providers.base import (
    LLMProvider,
    LLMResponse,
    StreamDelta,
    ToolCallRequest,
)
from src.providers.errors import short_error_message
from src.providers.tool_args import parse_tool_arguments_object
from src.providers.tool_call_parser import FALLBACK_PROVIDER_ALLOWLIST, parse_tool_calls_from_text

if TYPE_CHECKING:
    from src.providers.registry import ProviderSpec

# Standard OpenAI chat-completion message keys plus reasoning_content for
# thinking-enabled models (Kimi k2.5, DeepSeek-R1, etc.).
_ALLOWED_MSG_KEYS = frozenset(
    {"role", "content", "tool_calls", "tool_call_id", "name", "reasoning_content"}
)
_ALNUM = string.ascii_letters + string.digits
_USAGE_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens")
_CACHE_USAGE_KEYS = ("cache_creation_input_tokens", "cache_read_input_tokens")


def _short_tool_id() -> str:
    """Generate a 9-char alphanumeric ID compatible with all providers (incl. Mistral)."""
    return "".join(secrets.choice(_ALNUM) for _ in range(9))


def _parse_tool_arguments(raw: Any) -> dict[str, Any]:
    return parse_tool_arguments_object(raw)


def _usage_dict(usage: Any, *, include_cache: bool = False) -> dict[str, int]:
    if not usage:
        return {}

    result = {key: getattr(usage, key, 0) or 0 for key in _USAGE_KEYS}
    if include_cache:
        for key in _CACHE_USAGE_KEYS:
            val = getattr(usage, key, 0) or 0
            if val:
                result[key] = val
    return result


def _recover_text_tool_calls(
    content: str | None,
    provider_name: str | None,
    *,
    streamed: bool = False,
) -> tuple[str | None, list[ToolCallRequest]]:
    if not content or provider_name not in FALLBACK_PROVIDER_ALLOWLIST:
        return content, []

    parsed = parse_tool_calls_from_text(content)
    if not parsed:
        return content, []

    logger.debug(
        "Recovered {} tool call(s) from {}text for provider {!r}",
        len(parsed),
        "streamed " if streamed else "",
        provider_name,
    )
    return None, parsed


def _chat_error_response(exc: Exception, model: str) -> LLMResponse:
    err_msg = short_error_message(exc)
    if isinstance(exc, openai.AuthenticationError):
        logger.error("OpenAI-compat authentication failed (model={}): {}", model, err_msg)
        return LLMResponse(
            content=f"Error (authentication failed): {err_msg}",
            finish_reason="error",
            error_type="AuthenticationError",
        )
    if isinstance(exc, openai.RateLimitError):
        logger.warning("OpenAI-compat rate limited (model={}): {}", model, err_msg)
        return LLMResponse(
            content=f"Error (rate limited): {err_msg}",
            finish_reason="error",
            error_type="RateLimitError",
        )

    logger.warning("OpenAI-compat call failed (model={}): {}", model, err_msg)
    return LLMResponse(
        content=f"Error calling LLM: {err_msg}",
        finish_reason="error",
        error_type=type(exc).__name__,
    )


def _stream_error_delta(exc: Exception, model: str) -> StreamDelta:
    err_msg = short_error_message(exc)
    if isinstance(exc, openai.AuthenticationError):
        logger.error("OpenAI-compat stream auth failed (model={}): {}", model, err_msg)
        return StreamDelta(
            is_final=True,
            finish_reason="error",
            error_type="AuthenticationError",
        )
    if isinstance(exc, openai.RateLimitError):
        logger.warning("OpenAI-compat stream rate limited (model={}): {}", model, err_msg)
        return StreamDelta(
            is_final=True,
            finish_reason="error",
            error_type="RateLimitError",
        )

    logger.warning("OpenAI-compat stream failed (model={}): {}", model, err_msg)
    return StreamDelta(
        content=f"Error: {err_msg}",
        is_final=True,
        finish_reason="error",
        error_type=type(exc).__name__,
    )


class OpenAICompatProvider(LLMProvider):
    """Direct OpenAI-compatible provider for custom endpoints.

    Uses the ``openai`` SDK directly, making it suitable for
    any OpenAI-compatible API: vLLM, Ollama, LMStudio, custom gateways, etc.

    Receives an optional :class:`ProviderSpec` from the caller for model
    overrides, prefix stripping, and provider-name-based text tool-call
    fallback.
    """

    def __init__(
        self,
        api_key: str = "no-key",
        api_base: str = "http://localhost:8000/v1",
        default_model: str = "default",
        model_prefix_to_strip: str | None = None,
        extra_headers: dict[str, str] | None = None,
        spec: ProviderSpec | None = None,
    ):
        super().__init__(api_key, api_base)
        self._default_model = default_model
        self._model_prefix_to_strip = model_prefix_to_strip
        self._extra_headers = extra_headers or {}
        self._spec = spec
        self._provider_name: str | None = spec.name if spec else None

        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=api_base,
            default_headers=self._extra_headers or None,
        )

    # ------------------------------------------------------------------
    # Model resolution
    # ------------------------------------------------------------------

    def _resolve_model(self, model: str) -> str:
        """Strip provider prefix from the model name if configured."""
        # Explicit strip takes priority
        if self._model_prefix_to_strip:
            prefix = self._model_prefix_to_strip + "/"
            if model.startswith(prefix):
                return model[len(prefix) :]

        # Spec-based strip
        if self._spec and self._spec.strip_model_prefix:
            return model.split("/")[-1]

        return model

    # ------------------------------------------------------------------
    # Message sanitization
    # ------------------------------------------------------------------

    @staticmethod
    def _sanitize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Strip non-standard keys and ensure assistant messages have a content key.

        Only keeps: role, content, name, tool_calls, tool_call_id, reasoning_content.
        """
        sanitized: list[dict[str, Any]] = []
        for msg in messages:
            clean = {k: v for k, v in msg.items() if k in _ALLOWED_MSG_KEYS}
            # Strict providers require "content" even when assistant only has tool_calls
            if clean.get("role") == "assistant" and "content" not in clean:
                clean["content"] = None
            sanitized.append(clean)
        return sanitized

    # ------------------------------------------------------------------
    # Model overrides
    # ------------------------------------------------------------------

    def _apply_model_overrides(self, model: str, kwargs: dict[str, Any]) -> None:
        """Apply model-specific parameter overrides from the spec."""
        if not self._spec:
            return
        model_lower = model.lower()
        for pattern, overrides in self._spec.model_overrides:
            if pattern in model_lower:
                kwargs.update(overrides)
                return

    # ------------------------------------------------------------------
    # Request builder
    # ------------------------------------------------------------------

    def _build_kwargs(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str | None,
        max_tokens: int,
        temperature: float,
        *,
        stream: bool = False,
    ) -> tuple[str, dict[str, Any]]:
        """Build kwargs for ``self._client.chat.completions.create()``.

        Returns ``(resolved_model, kwargs_dict)``.
        """
        original_model = model or self._default_model
        resolved_model = self._resolve_model(original_model)

        kwargs: dict[str, Any] = {
            "model": resolved_model,
            "messages": self._sanitize_messages(self._sanitize_empty_content(messages)),
            "max_tokens": max(1, max_tokens),
            "temperature": temperature,
        }

        if stream:
            kwargs["stream"] = True
            kwargs["stream_options"] = {"include_usage": True}

        # Apply model-specific overrides (e.g. kimi-k2.5 temperature)
        self._apply_model_overrides(resolved_model, kwargs)

        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        return resolved_model, kwargs

    # ------------------------------------------------------------------
    # Response parsing (non-streaming)
    # ------------------------------------------------------------------

    def _parse_response(self, response: Any) -> LLMResponse:
        """Parse an OpenAI SDK ChatCompletion into our LLMResponse."""
        choice = response.choices[0]
        msg = choice.message

        tool_calls: list[ToolCallRequest] = []
        for tc in msg.tool_calls or []:
            tool_calls.append(
                ToolCallRequest(
                    id=tc.id or _short_tool_id(),
                    name=tc.function.name,
                    arguments=_parse_tool_arguments(tc.function.arguments),
                )
            )

        content = msg.content

        # Text-based tool-call fallback for allowlisted providers
        if not tool_calls:
            content, tool_calls = _recover_text_tool_calls(content, self._provider_name)

        reasoning_content = getattr(msg, "reasoning_content", None) or None

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "stop",
            usage=_usage_dict(response.usage),
            reasoning_content=reasoning_content,
        )

    # ------------------------------------------------------------------
    # Public: chat (non-streaming)
    # ------------------------------------------------------------------

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        resolved_model, kwargs = self._build_kwargs(
            messages,
            tools,
            model,
            max_tokens,
            temperature,
        )
        try:
            response = await self._client.chat.completions.create(**kwargs)
            return self._parse_response(response)
        except Exception as e:
            return _chat_error_response(e, resolved_model)

    # ------------------------------------------------------------------
    # Public: chat_stream (streaming)
    # ------------------------------------------------------------------

    @property
    def supports_streaming(self) -> bool:
        return True

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> AsyncIterator[StreamDelta]:
        """Stream a chat completion, yielding StreamDelta chunks.

        Text fragments are yielded immediately.  Tool-call deltas are
        accumulated across chunks.  A final ``StreamDelta(is_final=True)``
        carries the complete tool calls, usage, and finish_reason.
        """
        resolved_model, kwargs = self._build_kwargs(
            messages,
            tools,
            model,
            max_tokens,
            temperature,
            stream=True,
        )

        try:
            response_stream = await self._client.chat.completions.create(**kwargs)

            # Accumulators
            accumulated_content: list[str] = []
            tc_accum: dict[int, dict[str, str]] = {}  # index -> {name, arguments}
            final_usage: dict[str, int] = {}
            final_finish_reason: str | None = None

            async for chunk in response_stream:
                if not chunk.choices:
                    # Usage-only final chunk (no choices)
                    usage = _usage_dict(getattr(chunk, "usage", None), include_cache=True)
                    if usage:
                        final_usage = usage
                    continue

                choice = chunk.choices[0]
                delta = choice.delta

                # --- Text content ---
                chunk_text = getattr(delta, "content", None)
                if chunk_text:
                    accumulated_content.append(chunk_text)
                    yield StreamDelta(content=chunk_text)

                # --- Reasoning content ---
                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning:
                    yield StreamDelta(reasoning_content=reasoning)

                # --- Tool call deltas ---
                tc_deltas = getattr(delta, "tool_calls", None)
                if tc_deltas:
                    for tc_delta in tc_deltas:
                        idx = tc_delta.index
                        if idx not in tc_accum:
                            tc_accum[idx] = {"name": "", "arguments": "", "id": ""}
                        fn = getattr(tc_delta, "function", None)
                        if fn:
                            if getattr(fn, "name", None):
                                tc_accum[idx]["name"] = fn.name
                            if getattr(fn, "arguments", None):
                                tc_accum[idx]["arguments"] += fn.arguments
                        tc_id = getattr(tc_delta, "id", None)
                        if tc_id:
                            tc_accum[idx]["id"] = tc_id

                # --- Final chunk detection ---
                if choice.finish_reason is not None:
                    final_finish_reason = choice.finish_reason

                # --- Usage (sometimes on final chunk) ---
                if hasattr(chunk, "usage") and chunk.usage:
                    final_usage = _usage_dict(chunk.usage, include_cache=True)

            # Build complete tool calls from accumulated deltas
            tool_calls: list[ToolCallRequest] = []
            for idx in sorted(tc_accum):
                entry = tc_accum[idx]
                name = entry["name"]
                raw_args = entry["arguments"]
                if name:
                    tool_calls.append(
                        ToolCallRequest(
                            id=entry["id"] or _short_tool_id(),
                            name=name,
                            arguments=_parse_tool_arguments(raw_args) if raw_args else {},
                        )
                    )

            # Text-based tool call fallback for allowlisted providers
            full_content = "".join(accumulated_content) if accumulated_content else None
            if not tool_calls:
                full_content, tool_calls = _recover_text_tool_calls(
                    full_content,
                    self._provider_name,
                    streamed=True,
                )

            # When tool_calls present, omit content (loop_core.py uses accumulated_content)
            yield StreamDelta(
                content=full_content if not tool_calls else None,
                tool_calls=tool_calls,
                is_final=True,
                finish_reason=final_finish_reason or "stop",
                usage=final_usage,
            )

        except Exception as e:
            yield _stream_error_delta(e, resolved_model)

    # ------------------------------------------------------------------
    # Default model
    # ------------------------------------------------------------------

    def get_default_model(self) -> str:
        return self._default_model
