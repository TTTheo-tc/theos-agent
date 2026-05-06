"""Load agent definitions from workspace frontmatter files.

Each ``*.md`` file in the agents directory is expected to contain YAML
frontmatter (between ``---`` markers) followed by a markdown body that
becomes the agent's system prompt.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from loguru import logger

from src.config.schema import AgentRoleConfig


def load_agent_definitions(agents_dir: Path) -> dict[str, AgentRoleConfig]:
    """Scan *agents_dir* for ``*.md`` files and return parsed role configs.

    Files without valid YAML frontmatter or missing a ``description``
    field are silently skipped.  Returns an empty dict when the
    directory does not exist or is empty.
    """
    if not agents_dir.is_dir():
        return {}

    definitions: dict[str, AgentRoleConfig] = {}

    for md_path in sorted(agents_dir.glob("*.md")):
        try:
            cfg = _parse_definition(md_path)
        except Exception:
            logger.debug("Skipping malformed agent definition: {}", md_path.name)
            continue
        if cfg is None:
            continue
        definitions[md_path.stem] = cfg

    return definitions


def _parse_definition(md_path: Path) -> AgentRoleConfig | None:
    """Parse a single markdown file into an *AgentRoleConfig*.

    Returns ``None`` when the file lacks frontmatter or a description.
    """
    text = md_path.read_text(encoding="utf-8")

    frontmatter, body = _split_frontmatter(text)
    if frontmatter is None:
        return None

    data: dict = yaml.safe_load(frontmatter)  # type: ignore[assignment]
    if not isinstance(data, dict) or not data.get("description"):
        return None

    # The markdown body (after frontmatter) becomes the prompt.
    if body:
        data["prompt"] = body.strip()

    return AgentRoleConfig(**data)


def _split_frontmatter(text: str) -> tuple[str | None, str]:
    """Split YAML frontmatter from body.

    Returns ``(frontmatter_str, body_str)``.  If the file does not
    start with ``---`` the frontmatter is ``None``.
    """
    lines = text.splitlines(keepends=True)
    if not lines or not _is_frontmatter_marker(lines[0]):
        return None, text

    for idx, line in enumerate(lines[1:], start=1):
        if _is_frontmatter_marker(line):
            frontmatter = "".join(lines[1:idx]).strip()
            body = "".join(lines[idx + 1 :])
            return frontmatter, body

    return None, text


def _is_frontmatter_marker(line: str) -> bool:
    return line.strip() == "---"
