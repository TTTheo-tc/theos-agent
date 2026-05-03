"""Configurable security policy rules.

Each rule is a regex pattern + severity + action. Rules are evaluated against
file paths, command strings, or arbitrary text.

Reference: ironclaw/src/safety/policy.rs
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum, IntEnum


class Severity(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


class PolicyAction(Enum):
    WARN = "warn"
    BLOCK = "block"
    REVIEW = "review"  # Route to human approval
    SANITIZE = "sanitize"


@dataclass
class PolicyRule:
    """A single security policy rule."""

    id: str
    description: str
    pattern: re.Pattern[str]
    severity: Severity
    action: PolicyAction


@dataclass
class PolicyViolation:
    """A matched policy rule violation."""

    rule_id: str
    description: str
    severity: Severity
    action: PolicyAction
    matched_text: str


@dataclass
class PolicyResult:
    """Result of evaluating text against all policy rules."""

    violations: list[PolicyViolation] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return len(self.violations) == 0

    @property
    def max_severity(self) -> Severity:
        if not self.violations:
            return Severity.LOW
        return max(v.severity for v in self.violations)

    @property
    def should_block(self) -> bool:
        return any(v.action == PolicyAction.BLOCK for v in self.violations)

    @property
    def needs_review(self) -> bool:
        return any(v.action == PolicyAction.REVIEW for v in self.violations)


# ── Default rules ────────────────────────────────────────────────────────

_DEFAULT_RULES: list[PolicyRule] = [
    PolicyRule(
        id="SYS_FILE_ACCESS",
        description="Access to sensitive system files",
        pattern=re.compile(
            r"/etc/(?:shadow|passwd|sudoers|hosts)"
            r"|/\.ssh/"
            r"|/\.aws/credentials"
            r"|/\.gnupg/",
            re.IGNORECASE,
        ),
        severity=Severity.CRITICAL,
        action=PolicyAction.BLOCK,
    ),
    PolicyRule(
        id="PRIVATE_KEY_REF",
        description="Reference to cryptographic private keys",
        pattern=re.compile(
            r"(?:id_rsa|id_ed25519|id_ecdsa)(?:\s|$|\.pub)" r"|\.pem\b|\.key\b",
            re.IGNORECASE,
        ),
        severity=Severity.HIGH,
        action=PolicyAction.REVIEW,
    ),
    PolicyRule(
        id="ENV_FILE_ACCESS",
        description="Access to .env files",
        pattern=re.compile(r"\.env(?:\.|$)", re.IGNORECASE),
        severity=Severity.HIGH,
        action=PolicyAction.REVIEW,
    ),
    PolicyRule(
        id="DESTRUCTIVE_CMD",
        description="Potentially destructive shell command",
        pattern=re.compile(
            r"\brm\s+-rf\s+/" r"|\bdd\s+if=" r"|\bmkfs\b" r"|\bformat\b.*\bdisk" r"|:\(\)\s*\{.*\}",
            re.IGNORECASE,
        ),
        severity=Severity.CRITICAL,
        action=PolicyAction.BLOCK,
    ),
]


class PolicyEngine:
    """Evaluate text against a set of security policy rules."""

    def __init__(self, rules: list[PolicyRule] | None = None) -> None:
        self._rules = rules if rules is not None else list(_DEFAULT_RULES)

    def evaluate(self, text: str) -> PolicyResult:
        """Check *text* against all rules. Returns violations found."""
        if not text:
            return PolicyResult()

        violations: list[PolicyViolation] = []
        for rule in self._rules:
            match = rule.pattern.search(text)
            if match:
                violations.append(
                    PolicyViolation(
                        rule_id=rule.id,
                        description=rule.description,
                        severity=rule.severity,
                        action=rule.action,
                        matched_text=match.group()[:80],
                    )
                )
        return PolicyResult(violations=violations)

    def add_rule(self, rule: PolicyRule) -> None:
        """Append a custom rule."""
        self._rules.append(rule)
