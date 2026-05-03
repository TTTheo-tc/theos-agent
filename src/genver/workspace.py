"""GenVer task workspace resolution — derives project subdirectories from user requests."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import urlparse


def should_use_project_subdir(user_request: str) -> bool:
    """Return True when a build-style request should get its own project folder."""
    text = (user_request or "").strip().lower()
    if not text:
        return False

    explicit_path_markers = (
        "workspace/",
        "src/",
        "tests/",
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".md",
        "目录",
        "folder",
        "path",
        "路径",
    )
    if any(marker in text for marker in explicit_path_markers):
        return False

    build_markers = (
        "搭建",
        "建立",
        "开发",
        "实现",
        "做一个",
        "build",
        "create",
        "implement",
        "develop",
        "bootstrap",
    )
    product_markers = (
        "系统",
        "平台",
        "产品",
        "工具",
        "模型",
        "终端",
        "网站",
        "应用",
        "system",
        "platform",
        "product",
        "tool",
        "model",
        "app",
        "service",
        "dashboard",
        "demo",
        "prototype",
        "repo",
        "github.com/",
    )
    return any(marker in text for marker in build_markers) and any(
        marker in text for marker in product_markers
    )


def derive_project_slug(user_request: str) -> str:
    """Derive a stable project folder slug from the user request itself."""
    from src.utils.helpers import safe_filename

    raw = (user_request or "").strip()
    if not raw:
        return "project"

    for match in re.findall(r"https?://[^\s)]+", raw):
        match = match.rstrip("，。,.;；:：!?！？\"'）)]}>")
        try:
            parsed = urlparse(match)
        except ValueError:
            continue
        parts = [part for part in parsed.path.split("/") if part]
        if parts:
            repo_part = parts[-1].removesuffix(".git")
            repo_match = re.match(r"[A-Za-z0-9._-]+", repo_part)
            candidate = (
                safe_filename((repo_match.group(0) if repo_match else repo_part))
                .strip()
                .strip(".-_")
            )
            if candidate:
                return candidate[:48]

    text = raw.lower()
    stopwords = {
        "build",
        "create",
        "make",
        "implement",
        "develop",
        "bootstrap",
        "help",
        "system",
        "platform",
        "product",
        "tool",
        "model",
        "app",
        "like",
        "similar",
        "with",
        "for",
        "the",
        "that",
        "一个",
        "帮我",
        "请",
        "一下",
        "搭建",
        "建立",
        "开发",
        "实现",
        "做",
        "类似",
        "参考",
        "这种",
        "那种",
        "系统",
        "平台",
        "产品",
        "工具",
        "模型",
        "终端",
        "网站",
        "应用",
    }
    ascii_terms = [
        term for term in re.findall(r"[a-z0-9]+", text) if len(term) >= 3 and term not in stopwords
    ]
    if ascii_terms:
        return safe_filename("-".join(ascii_terms[:4])).strip().strip(".-_")[:48]

    cjk_chunks = re.findall(r"[\u3400-\u9fff]{2,}", raw)
    if cjk_chunks:
        joined = "".join(cjk_chunks)
        cleaned = joined
        for word in sorted(
            [word for word in stopwords if re.search(r"[\u3400-\u9fff]", word)],
            key=len,
            reverse=True,
        ):
            cleaned = cleaned.replace(word, "")
        cleaned = re.sub(r"(方面|相关|类型|版本|方案)$", "", cleaned).strip("的了啊呀呢 ")
        if cleaned:
            return safe_filename(cleaned[:24]).strip().strip(".-_")

    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
    return f"project-{digest}"


def resolve_task_workspace(workspace: Path, user_request: str) -> Path:
    """Return the implementation workspace for the current GenVer request."""
    from src.utils.helpers import ensure_dir

    if not should_use_project_subdir(user_request):
        return workspace
    slug = derive_project_slug(user_request)
    return ensure_dir(workspace / slug)
