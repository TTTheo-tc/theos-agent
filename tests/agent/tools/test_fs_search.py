from __future__ import annotations

from pathlib import Path

import pytest

from src.agent.tools import fs_search
from src.agent.tools.fs_search import GlobTool, GrepTool


async def test_glob_accepts_root_alias_and_filters_allowed_dir(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "src" / "main.py"
    target.parent.mkdir()
    target.write_text("print('ok')\n", encoding="utf-8")
    (workspace / "notes.txt").write_text("skip\n", encoding="utf-8")

    tool = GlobTool(workspace=workspace, allowed_dir=workspace)
    result = await tool.execute(pattern="**/*.py", root=".")

    assert str(target) in result
    assert "notes.txt" not in result


async def test_grep_python_fallback_applies_aliases_window_and_truncation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fs_search, "_find_rg", lambda: None)
    first = tmp_path / "a.py"
    second = tmp_path / "b.py"
    first.write_text("alpha one\nskip\nALPHA two\n", encoding="utf-8")
    second.write_text("alpha three\n", encoding="utf-8")

    tool = GrepTool(workspace=tmp_path)
    result = await tool.execute(
        pattern="alpha",
        include="*.py",
        ignore_case=True,
        head_limit=1,
        offset=1,
    )

    assert result.splitlines()[0] == f"{first}:3: ALPHA two"
    assert "... (truncated at 1 results)" in result


async def test_grep_python_fallback_skips_vcs_dirs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fs_search, "_find_rg", lambda: None)
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text("secret-needle\n", encoding="utf-8")

    tool = GrepTool(workspace=tmp_path)
    result = await tool.execute(pattern="secret-needle")

    assert result == "No matches found for 'secret-needle'"


async def test_grep_python_fallback_files_with_matches_and_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fs_search, "_find_rg", lambda: None)
    target = tmp_path / "app.py"
    other = tmp_path / "readme.md"
    target.write_text("needle\nneedle\n", encoding="utf-8")
    other.write_text("needle\n", encoding="utf-8")

    tool = GrepTool(workspace=tmp_path)

    files = await tool.execute(pattern="needle", glob="*.py", output_mode="files_with_matches")
    assert files == str(target)

    counts = await tool.execute(pattern="needle", glob="*.py", output_mode="count")
    assert counts == f"{target}:2"


def test_grep_builds_rg_command_from_compatible_aliases(tmp_path: Path) -> None:
    tool = GrepTool()
    options = fs_search._GrepOptions.from_kwargs(
        {
            "pattern": "needle",
            "glob": "*.py",
            "type": "py",
            "ignore_case": True,
            "context": 2,
            "max_results": 10,
        }
    )

    cmd = tool._build_rg_command(rg_path="/usr/bin/rg", base=tmp_path, options=options)

    assert cmd[0] == "/usr/bin/rg"
    assert "--with-filename" in cmd
    assert "--ignore-case" in cmd
    assert cmd[cmd.index("--context") + 1] == "2"
    assert cmd[cmd.index("--type") + 1] == "py"
    assert cmd[cmd.index("--glob") + 1].startswith("!")
    assert "*.py" in cmd
    assert cmd[-3:] == ["-e", "needle", str(tmp_path)]


async def test_grep_rg_backend_filters_allowed_dir_before_window(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "allowed-sibling"
    allowed.mkdir()
    outside.mkdir()
    fake_rg = tmp_path / "fake-rg"
    fake_rg.write_text(
        "\n".join(
            [
                "#!/bin/sh",
                "cat <<'EOF'",
                f"{allowed}/a.py:1:needle one",
                f"{outside}/x.py:1:needle outside",
                f"{allowed}/b.py:2:needle two",
                f"{allowed}/c.py:3:needle three",
                "EOF",
            ]
        ),
        encoding="utf-8",
    )
    fake_rg.chmod(0o755)

    tool = GrepTool(allowed_dir=allowed)
    options = fs_search._GrepOptions.from_kwargs(
        {"pattern": "needle", "head_limit": 1, "offset": 1}
    )
    result = await tool._run_rg(rg_path=str(fake_rg), base=allowed, options=options)

    assert result.splitlines()[0] == f"{allowed}/b.py:2:needle two"
    assert str(outside) not in result
    assert "... (truncated at 1 results)" in result


async def test_grep_rg_backend_filters_context_separators_after_allowed_dir(
    tmp_path: Path,
) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "allowed-sibling"
    allowed.mkdir()
    outside.mkdir()
    fake_rg = tmp_path / "fake-rg"
    fake_rg.write_text(
        "\n".join(
            [
                "#!/bin/sh",
                "cat <<'EOF'",
                f"{outside}/x.py-1-before outside",
                f"{outside}/x.py:2:needle outside",
                "--",
                f"{allowed}/a.py-1-before allowed",
                f"{allowed}/a.py:2:needle allowed",
                "--",
                "EOF",
            ]
        ),
        encoding="utf-8",
    )
    fake_rg.chmod(0o755)

    tool = GrepTool(allowed_dir=allowed)
    options = fs_search._GrepOptions.from_kwargs({"pattern": "needle", "context": 1})
    result = await tool._run_rg(rg_path=str(fake_rg), base=allowed, options=options)

    assert result.splitlines() == [
        f"{allowed}/a.py-1-before allowed",
        f"{allowed}/a.py:2:needle allowed",
    ]


async def test_grep_rg_backend_reports_no_matches_after_allowed_dir_filter(
    tmp_path: Path,
) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "allowed-sibling"
    allowed.mkdir()
    outside.mkdir()
    fake_rg = tmp_path / "fake-rg"
    fake_rg.write_text(
        "\n".join(
            [
                "#!/bin/sh",
                "cat <<'EOF'",
                f"{outside}/x.py:1:needle outside",
                "EOF",
            ]
        ),
        encoding="utf-8",
    )
    fake_rg.chmod(0o755)

    tool = GrepTool(allowed_dir=allowed)
    options = fs_search._GrepOptions.from_kwargs({"pattern": "needle"})
    result = await tool._run_rg(rg_path=str(fake_rg), base=allowed, options=options)

    assert result == "No matches found for 'needle'"
