"""Unified safety layer — single entry point for all safety checks.

Consolidates sanitizer, leak detector, and policy engine into a
coherent API that can be wired into the agent loop, tool execution,
and HTTP request paths.

Usage:
    safety = SafetyLayer()
    safety.validate_input(text)       # user input before LLM
    safety.sanitize_tool_output(text) # tool result before LLM context
    safety.scan_outbound(text)        # agent output before user delivery
    safety.check_policy(text)         # file paths, commands, etc.
"""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

from src.safety.leak_detector import LeakDetector, LeakScanResult
from src.safety.policy import PolicyEngine, PolicyResult
from src.safety.sanitizer import SanitizedOutput, Sanitizer


@dataclass
class SafetyCheckResult:
    """Combined result of all safety checks on a piece of text."""

    injection: SanitizedOutput | None = None
    leaks: LeakScanResult | None = None
    policy: PolicyResult | None = None
    output_text: str = ""
    block_message: str = ""

    @property
    def has_warnings(self) -> bool:
        return bool(
            (self.injection and self.injection.warnings)
            or (self.leaks and not self.leaks.clean)
            or (self.policy and not self.policy.clean)
        )

    @property
    def should_block(self) -> bool:
        return bool(
            self.block_message
            or (self.injection and self.injection.was_modified)
            or (self.leaks and self.leaks.should_block)
            or (self.policy and self.policy.should_block)
        )


class SafetyLayer:
    """Unified safety enforcement across the agent pipeline."""

    _INBOUND_BLOCKING_INJECTION_CATEGORIES = frozenset(
        {
            "instruction_override",
            "system_injection",
            "special_token",
            "code_block_injection",
            "role_prefix_injection",
        }
    )

    def __init__(
        self,
        *,
        block_injections: bool = False,
        entropy_sensitivity: float = 0.0,
    ) -> None:
        self._sanitizer = Sanitizer(block=block_injections)
        self._blocking_sanitizer = Sanitizer(block=True)
        self._leak_detector = LeakDetector(entropy_sensitivity=entropy_sensitivity)
        self._policy = PolicyEngine()

    def validate_input(self, text: str) -> SanitizedOutput:
        """Scan user input for prompt injection patterns.

        Applied before LLM call.
        """
        result = self._sanitizer.scan(text)
        if result.warnings:
            logger.warning("Prompt injection detected: {}", result.warnings)
        return result

    def scan_inbound(self, text: str) -> SafetyCheckResult:
        """Run blocking checks on inbound user content before LLM execution."""
        injection = self._sanitizer.scan(text)
        leaks = self._leak_detector.scan(text)
        policy = self._policy.evaluate(text)
        result = SafetyCheckResult(
            injection=injection,
            leaks=leaks,
            policy=policy,
            output_text=text,
        )

        if self._should_block_inbound_injection(injection.warnings):
            logger.warning("Inbound prompt injection blocked: {}", injection.warnings)
            result.block_message = (
                "Input blocked by safety checks: prompt-injection patterns detected. "
                "Remove instruction-overriding text and try again."
            )
            return result

        if not leaks.clean:
            logger.warning(
                "Inbound secret blocked: {}",
                [match.pattern_name for match in leaks.matches],
            )
            if leaks.redacted_text:
                result.output_text = leaks.redacted_text
            result.block_message = (
                "Input blocked by safety checks: your message appears to contain a secret or "
                "credential. Remove it and try again."
            )
            return result

        if policy.should_block:
            logger.warning(
                "Inbound policy blocked: {}",
                [violation.rule_id for violation in policy.violations],
            )
            result.block_message = "Input blocked by safety policy."
        return result

    @classmethod
    def _warning_categories(cls, warnings: list[str]) -> set[str]:
        return {warning.split(":", 1)[0].strip() for warning in warnings if warning}

    @classmethod
    def _should_block_inbound_injection(cls, warnings: list[str]) -> bool:
        if not warnings:
            return False
        categories = cls._warning_categories(warnings)
        if categories & cls._INBOUND_BLOCKING_INJECTION_CATEGORIES:
            return True
        # Personal-use default: natural dialogue like "你现在是什么模型" can
        # trip role-manipulation phrases, but should not block unless paired
        # with a more structural injection signal.
        return "role_manipulation" in categories and len(categories) > 1

    def sanitize_tool_output(self, text: str) -> str:
        """Scan tool output for injection patterns and credential leaks.

        Applied after tool execution, before the result enters LLM context.
        Returns the (possibly redacted) safe text.
        """
        return self._sanitize_untrusted_text(
            text,
            injection_context="tool output",
            leak_context="tool output",
        )

    def scan_outbound(self, text: str) -> SafetyCheckResult:
        """Full scan on agent output before delivery to user.

        Checks for credential leaks and returns redacted text if needed.
        """
        leak_result = self._leak_detector.scan(text)
        output = self._log_and_redact_leaks(
            text,
            leak_result,
            context="output",
        )

        return SafetyCheckResult(
            leaks=leak_result,
            output_text=output,
        )

    def scan_external_content(self, text: str) -> str:
        """Scan content from external sources (web fetch, MCP responses).

        Checks for both injection and leaks. Returns sanitized text.
        """
        return self._sanitize_untrusted_text(
            text,
            injection_context="external content",
            leak_context="external content",
        )

    def _sanitize_untrusted_text(
        self,
        text: str,
        *,
        injection_context: str,
        leak_context: str,
    ) -> str:
        """Apply blocking injection scan plus best-effort leak redaction."""
        injection = self._blocking_sanitizer.scan(text)
        if injection.was_modified:
            logger.warning("Injection patterns in {}: {}", injection_context, injection.warnings)
            return injection.content

        leak_result = self._leak_detector.scan(text)
        return self._log_and_redact_leaks(text, leak_result, context=leak_context)

    @staticmethod
    def _log_and_redact_leaks(
        text: str,
        leak_result: LeakScanResult,
        *,
        context: str,
    ) -> str:
        if leak_result.clean:
            return text
        logger.warning(
            "Credential leak in {}: {}",
            context,
            [m.pattern_name for m in leak_result.matches],
        )
        return leak_result.redacted_text or text

    def check_policy(self, text: str) -> PolicyResult:
        """Evaluate text against security policy rules.

        Applied to file paths, shell commands, tool parameters.
        """
        return self._policy.evaluate(text)

    def scan_http_body(self, body: str) -> LeakScanResult:
        """Scan outbound HTTP request body for credential leaks."""
        result = self._leak_detector.scan(body)
        if not result.clean:
            logger.warning(
                "Credential leak in HTTP body: {}",
                [m.pattern_name for m in result.matches],
            )
        return result
