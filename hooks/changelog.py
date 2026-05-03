"""Post-chat changelog updater.

Reads recent git commits (since last changelog update) and appends
conventional-commit entries to CHANGELOG.md.
"""

import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def get_last_changelog_commit(repo_root: Path) -> str | None:
    """Find the most recent commit that touched the changelog path."""
    result = subprocess.run(
        ["git", "log", "-1", "--format=%H", "--", "CHANGELOG.md"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    sha = result.stdout.strip()
    return sha or None


def get_new_commits(repo_root: Path, since_sha: str | None) -> list[str]:
    """Get commit messages since a given SHA (exclusive), newest first."""
    if since_sha:
        cmd = ["git", "log", f"{since_sha}..HEAD", "--format=%s"]
    else:
        # No prior changelog commit — take last 20 commits as a reasonable window
        cmd = ["git", "log", "-20", "--format=%s"]
    result = subprocess.run(cmd, cwd=repo_root, capture_output=True, text=True)
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.strip().splitlines() if line]


COMMIT_RE = re.compile(
    r"^(feat|fix|refactor|chore|docs|test|style|perf|instinct)" r"(?:\([^)]+\))?:\s+(.+)",
    re.IGNORECASE,
)


def parse_entries(messages: list[str]) -> list[str]:
    """Extract changelog bullets from conventional commit messages."""
    seen: set[str] = set()
    bullets: list[str] = []
    for msg in messages:
        m = COMMIT_RE.match(msg)
        if not m:
            continue
        prefix, desc = m.group(1).lower(), m.group(2).strip()
        entry = f"- **{prefix}**: {desc}"
        if entry not in seen:
            seen.add(entry)
            bullets.append(entry)
    return bullets


def update_changelog(changelog_path: Path, bullets: list[str]) -> int:
    """Append bullets under today's date section. Returns count of new entries."""
    today = datetime.now().strftime("%Y-%m-%d")
    section_header = f"## {today}"

    if changelog_path.exists():
        content = changelog_path.read_text(encoding="utf-8")
    else:
        content = "# Changelog\n"

    if section_header in content:
        # Collect existing bullets to deduplicate
        existing: set[str] = set()
        in_section = False
        for line in content.splitlines():
            if line.strip() == section_header:
                in_section = True
                continue
            if in_section:
                if line.startswith("## "):
                    break
                existing.add(line.strip())
        to_add = [b for b in bullets if b.strip() not in existing]
        if not to_add:
            return 0
        insert = "\n".join(to_add) + "\n"
        content = content.replace(section_header + "\n", section_header + "\n" + insert, 1)
    else:
        # Prepend new date section after the title line
        to_add = bullets
        lines = content.split("\n", 1)
        rest = ("\n" + lines[1]) if len(lines) > 1 else ""
        content = lines[0] + "\n\n" + section_header + "\n" + "\n".join(to_add) + "\n" + rest

    changelog_path.write_text(content, encoding="utf-8")
    return len(to_add)


def main() -> None:
    repo_root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    changelog_path = repo_root / "CHANGELOG.md"

    last_sha = get_last_changelog_commit(repo_root)
    messages = get_new_commits(repo_root, last_sha)
    if not messages:
        return

    bullets = parse_entries(messages)
    if not bullets:
        return

    count = update_changelog(changelog_path, bullets)
    if count:
        print(f"[changelog] appended {count} entries")


if __name__ == "__main__":
    main()
