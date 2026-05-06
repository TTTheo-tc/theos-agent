"""Data models and text processing helpers for structured memory.

Contains:
- TaskMemory, DomainRule, ResearchNote dataclasses
- Rule extraction, source reference, and remember/research hint helpers
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_CODE_REF_RE = re.compile(r"(?:src|tests?|lib|hooks|instinct|skills)/[\w./-]+")
_URL_RE = re.compile(r"https?://[^\s)]+")

_RULE_PATTERNS = [
    re.compile(
        r"(?:注意|Note|Always|Never|建议|推荐|记住|Remember)[：:\s](.{15,120}?[.。！!？?\n])", re.I
    ),
    re.compile(r"(?:注意|建议|推荐|记住)([^。！？!\n]{4,100}[。！？!])"),
    re.compile(r"当\s*(.{5,40})\s*时[，,]\s*(?:优先|应该|需要|建议)(.{10,80}?[.。！!？?\n])"),
    re.compile(
        r"(?:When|If)\s+(.{10,60}),\s*(?:always|should|prefer|make sure)(.{10,80}?[.。！!？?\n])",
        re.I,
    ),
]

_RESEARCH_HINT_RE = re.compile(
    r"(paper|论文|arxiv|research|academic|benchmark|baseline|dataset|模型|实验|复现)",
    re.I,
)
_REMEMBER_REQUEST_RE = re.compile(r"(?:\bremember\b|记住)", re.I)
_SERIALIZED_NOISE_RE = re.compile(
    r"(?:^error calling llm:|(?:litellm\.)?\w+error:|received messages=\[|[\[{]['\"]role['\"]\s*:|[\[{]'role':)",
    re.I,
)
_RULE_ACTION_RE = re.compile(
    r"(以后|优先|应该|需要|不要|不能|必须|记得|确保|先|再|逐层|always|never|prefer|should|make sure|if |when |当.+时)",
    re.I,
)
_RULE_CONTEXTUAL_RE = re.compile(
    r"(你之前|刚才|这次|本次|现在就|今天|明天|昨天|这个|那个|下面|上面|此时|当前)",
    re.I,
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class TaskMemory:
    """High-value task record derived from a completed agent turn."""

    id: str
    session_key: str
    created_at: str
    status: str
    user_message: str
    response_summary: str
    response_excerpt: str
    tools_used: list[str] = field(default_factory=list)
    routed_skills: list[str] = field(default_factory=list)
    routing_domains: list[str] = field(default_factory=list)
    selected_primary: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    duration_ms: float | None = None
    source_refs: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    is_latest_success: bool = True
    superseded_by: str | None = None
    superseded_at: str | None = None


@dataclass
class DomainRule:
    """Transferable domain rule accumulated from repeated task outcomes."""

    id: str
    rule_text: str
    domains: list[str]
    selected_primary: str | None
    source_task_ids: list[str] = field(default_factory=list)
    occurrence_count: int = 0
    first_seen_at: str = ""
    last_seen_at: str = ""
    confidence: float = 0.0


@dataclass
class ResearchNote:
    """Structured research note extracted from paper-oriented tasks."""

    id: str
    task_memory_id: str
    session_key: str
    created_at: str
    title: str
    summary: str
    domains: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    source_refs: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Text processing helpers
# ---------------------------------------------------------------------------


def first_sentence(text: str, *, max_chars: int = 240) -> str:
    clean = re.sub(r"\s+", " ", text or "").strip()
    if not clean:
        return ""
    match = re.match(r"^(.+?[。.!！?？])", clean)
    sentence = match.group(1).strip() if match else clean[:max_chars]
    return sentence[:max_chars]


def normalize_rule(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def extract_source_refs(*texts: str) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()
    for text in texts:
        if not text:
            continue
        for match in _URL_RE.findall(text):
            if match not in seen:
                seen.add(match)
                refs.append(match)
        for match in _CODE_REF_RE.findall(text):
            if match not in seen:
                seen.add(match)
                refs.append(match)
    return refs[:20]


def extract_rules(text: str) -> list[str]:
    clean = re.sub(r"```[\s\S]*?```", "", text or "")
    if is_noise_response(clean):
        return []
    rules: list[str] = []
    seen: set[str] = set()
    for pattern in _RULE_PATTERNS:
        for match in pattern.finditer(clean):
            rule = match.group(0).strip()[:150]
            if len(rule) < 8:
                continue
            if not is_transferable_rule_text(rule):
                continue
            normalized = normalize_rule(rule)
            if normalized in seen:
                continue
            seen.add(normalized)
            rules.append(rule)
            if len(rules) >= 5:
                return rules
    return rules


def is_transferable_rule_text(text: str) -> bool:
    clean = re.sub(r"\s+", " ", text or "").strip()
    if len(clean) < 8 or is_noise_response(clean):
        return False
    if _RULE_CONTEXTUAL_RE.search(clean):
        return False
    return bool(_RULE_ACTION_RE.search(clean))


def is_research_hint(text: str) -> bool:
    return bool(_RESEARCH_HINT_RE.search(text))


def is_noise_response(text: str) -> bool:
    clean = re.sub(r"\s+", " ", text or "").strip()
    if not clean:
        return True
    return bool(_SERIALIZED_NOISE_RE.search(clean))


def is_remember_request(text: str) -> bool:
    return bool(_REMEMBER_REQUEST_RE.search(text or ""))


def derive_remembered_note(user_message: str, response: str) -> str | None:
    if not is_remember_request(user_message):
        return None

    original = re.sub(r"\s+", " ", user_message or "").strip()
    if not original:
        return None

    cleaned = original
    cleaned = re.sub(r"^(?:好(?:的|吧)?|嗯+|噢+|哦+)\s*[，,。 ]*", "", cleaned)
    cleaned = re.sub(r"(?:这个|这件事)?你(?:给我)?(?:要|得)?记住了[。！!，, ]*", "", cleaned)
    cleaned = re.sub(
        r"(?:还需要你|请你|你|我希望你)?(?:得|要)?记住(?:的是)?[：:，,\s]*", "", cleaned
    )
    cleaned = cleaned.strip("，,。；;:： ")
    if len(cleaned) < 6:
        cleaned = original

    summary = first_sentence(response or "", max_chars=160)
    if summary and not is_noise_response(summary):
        normalized = normalize_rule(summary)
        if normalized not in {
            normalize_rule("记住了。"),
            normalize_rule("明白，记住了。"),
            normalize_rule("搞定。"),
            normalize_rule("已记住。"),
        }:
            if cleaned in summary or summary in cleaned:
                return summary[:220]
            return f"{cleaned} -> {summary}"[:220]

    return cleaned[:220]
