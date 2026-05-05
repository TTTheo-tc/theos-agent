"""Neutral text tokenization utility.

Extracted from src/memory/structured_models to break the session -> memory
import dependency.  Contains tokenize_query and the helpers it needs.
"""

from __future__ import annotations

import re

_ASCII_TERM_RE = re.compile(r"[a-z0-9][a-z0-9._/-]*", re.I)
_CJK_BLOCK_RE = re.compile(r"[\u3400-\u9fff]+")

_EN_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "be",
    "for",
    "from",
    "how",
    "in",
    "into",
    "is",
    "of",
    "on",
    "or",
    "the",
    "this",
    "that",
    "to",
    "use",
    "using",
    "what",
    "with",
}
_CN_STOPWORDS = {
    "一个",
    "一下",
    "一些",
    "什么",
    "怎么",
    "如何",
    "帮我",
    "帮忙",
    "继续",
    "分析",
    "总结",
    "看看",
    "这个",
    "这些",
    "那个",
    "那些",
    "请问",
}


def is_ascii_term(term: str) -> bool:
    return bool(_ASCII_TERM_RE.fullmatch(term))


def tokenize_query(text: str) -> list[str]:
    clean = re.sub(r"\s+", " ", (text or "").lower()).strip()
    if not clean:
        return []

    terms: list[str] = []
    seen: set[str] = set()

    def push(term: str) -> None:
        token = term.strip().lower()
        if not token or token in seen:
            return
        if is_ascii_term(token):
            token = token.strip("._/-")
            if len(token) < 2 or token in _EN_STOPWORDS:
                return
        elif len(token) < 2 or token in _CN_STOPWORDS:
            return
        seen.add(token)
        terms.append(token)

    for token in _ASCII_TERM_RE.findall(clean):
        push(token)

    for chunk in _CJK_BLOCK_RE.findall(clean):
        if len(chunk) <= 1:
            continue
        if len(chunk) <= 6:
            push(chunk)
        for n in (3, 2):
            if len(chunk) < n:
                continue
            for idx in range(len(chunk) - n + 1):
                push(chunk[idx : idx + n])

    return terms
