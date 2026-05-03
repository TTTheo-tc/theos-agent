"""Tests for vendor_study parser and tool."""

from __future__ import annotations

import asyncio
import textwrap
from datetime import date
from pathlib import Path
from unittest.mock import PropertyMock, patch

import pytest

from src.agent.tools.vendor_study import (
    StudyGuide,
    StudyTopic,
    VendorConfig,
    VendorStudyTool,
    parse_study_guide,
)

_VALID_YAML = """
version: 1
vendors:
  openclaw:
    root: vendor/openclaw
    topics:
      channel_registry:
        goal: Compare channel registration and plugin boundaries.
        vendor_paths:
          - src/channels/registry.ts
        source_paths:
          - src/channels/registry.py
  daily_stock_analysis:
    root: vendor/daily_stock_analysis
    topics:
      analysis_pipeline:
        goal: Learn orchestration patterns.
        vendor_paths:
          - src/core/pipeline.py
        source_paths:
          - src/agent/tools/stock.py
"""


def parse(yaml_text: str) -> StudyGuide:
    return parse_study_guide(yaml_text)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_parse_valid_yaml():
    guide = parse(_VALID_YAML)

    assert isinstance(guide, StudyGuide)
    assert guide.version == 1
    assert set(guide.vendors) == {"openclaw", "daily_stock_analysis"}

    openclaw = guide.vendors["openclaw"]
    assert isinstance(openclaw, VendorConfig)
    assert openclaw.root == "vendor/openclaw"
    assert "channel_registry" in openclaw.topics

    topic = openclaw.topics["channel_registry"]
    assert isinstance(topic, StudyTopic)
    assert topic.goal == "Compare channel registration and plugin boundaries."
    assert topic.vendor_paths == ["src/channels/registry.ts"]
    assert topic.source_paths == ["src/channels/registry.py"]


def test_parse_multiple_paths():
    yaml_text = """
version: 1
vendors:
  mypkg:
    root: vendor/mypkg
    topics:
      multi:
        goal: Test multiple paths.
        vendor_paths:
          - a.ts
          - b.ts
        source_paths:
          - x.py
          - y.py
"""
    guide = parse(yaml_text)
    topic = guide.vendors["mypkg"].topics["multi"]
    assert topic.vendor_paths == ["a.ts", "b.ts"]
    assert topic.source_paths == ["x.py", "y.py"]


# ---------------------------------------------------------------------------
# Version validation
# ---------------------------------------------------------------------------


def test_parse_missing_version_fails():
    yaml_text = """
vendors:
  pkg:
    root: vendor/pkg
    topics:
      t:
        goal: g
        vendor_paths: [a.ts]
        source_paths: [b.py]
"""
    with pytest.raises(ValueError, match="version"):
        parse(yaml_text)


def test_parse_wrong_version_fails():
    yaml_text = """
version: 2
vendors:
  pkg:
    root: vendor/pkg
    topics:
      t:
        goal: g
        vendor_paths: [a.ts]
        source_paths: [b.py]
"""
    with pytest.raises(ValueError, match="version"):
        parse(yaml_text)


def test_parse_string_version_fails():
    yaml_text = """
version: "1"
vendors:
  pkg:
    root: vendor/pkg
    topics:
      t:
        goal: g
        vendor_paths: [a.ts]
        source_paths: [b.py]
"""
    with pytest.raises(ValueError, match="version"):
        parse(yaml_text)


# ---------------------------------------------------------------------------
# Vendors key
# ---------------------------------------------------------------------------


def test_parse_missing_vendors_fails():
    yaml_text = "version: 1\n"
    with pytest.raises(ValueError, match="vendors"):
        parse(yaml_text)


def test_parse_vendors_not_mapping_fails():
    yaml_text = "version: 1\nvendors: [a, b]\n"
    with pytest.raises(ValueError, match="vendors"):
        parse(yaml_text)


def test_parse_vendor_key_must_be_string():
    yaml_text = """
version: 1
vendors:
  123:
    root: vendor/pkg
    topics:
      t:
        goal: g
        vendor_paths: [a.ts]
        source_paths: [b.py]
"""
    with pytest.raises(ValueError, match="vendor: key must be a string"):
        parse(yaml_text)


def test_parse_vendor_key_must_be_safe_slug():
    yaml_text = """
version: 1
vendors:
  bad/vendor:
    root: vendor/pkg
    topics:
      t:
        goal: g
        vendor_paths: [a.ts]
        source_paths: [b.py]
"""
    with pytest.raises(ValueError, match=r"vendor: key must match"):
        parse(yaml_text)


# ---------------------------------------------------------------------------
# Vendor root
# ---------------------------------------------------------------------------


def test_parse_missing_root_fails():
    yaml_text = """
version: 1
vendors:
  pkg:
    topics:
      t:
        goal: g
        vendor_paths: [a.ts]
        source_paths: [b.py]
"""
    with pytest.raises(ValueError, match="root"):
        parse(yaml_text)


def test_parse_root_traversal_fails():
    yaml_text = """
version: 1
vendors:
  pkg:
    root: ../../etc
    topics:
      t:
        goal: g
        vendor_paths: [a.ts]
        source_paths: [b.py]
"""
    with pytest.raises(ValueError, match="traversal"):
        parse(yaml_text)


def test_parse_absolute_root_fails():
    yaml_text = """
version: 1
vendors:
  pkg:
    root: /tmp/evil
    topics:
      t:
        goal: g
        vendor_paths: [a.ts]
        source_paths: [b.py]
"""
    with pytest.raises(ValueError, match="absolute"):
        parse(yaml_text)


# ---------------------------------------------------------------------------
# Topic goal
# ---------------------------------------------------------------------------


def test_parse_missing_goal_fails():
    yaml_text = """
version: 1
vendors:
  pkg:
    root: vendor/pkg
    topics:
      t:
        vendor_paths: [a.ts]
        source_paths: [b.py]
"""
    with pytest.raises(ValueError, match="goal"):
        parse(yaml_text)


# ---------------------------------------------------------------------------
# Topic key
# ---------------------------------------------------------------------------


def test_parse_topic_key_must_be_safe_slug():
    yaml_text = """
version: 1
vendors:
  pkg:
    root: vendor/pkg
    topics:
      bad/topic:
        goal: g
        vendor_paths: [a.ts]
        source_paths: [b.py]
"""
    with pytest.raises(ValueError, match=r"topic: key must match"):
        parse(yaml_text)


# ---------------------------------------------------------------------------
# vendor_paths validation
# ---------------------------------------------------------------------------


def test_parse_empty_vendor_paths_fails():
    yaml_text = """
version: 1
vendors:
  pkg:
    root: vendor/pkg
    topics:
      t:
        goal: g
        vendor_paths: []
        source_paths: [b.py]
"""
    with pytest.raises(ValueError, match="vendor_paths"):
        parse(yaml_text)


def test_parse_missing_vendor_paths_fails():
    yaml_text = """
version: 1
vendors:
  pkg:
    root: vendor/pkg
    topics:
      t:
        goal: g
        source_paths: [b.py]
"""
    with pytest.raises(ValueError, match="vendor_paths"):
        parse(yaml_text)


def test_parse_absolute_vendor_path_fails():
    yaml_text = """
version: 1
vendors:
  pkg:
    root: vendor/pkg
    topics:
      t:
        goal: g
        vendor_paths: [/etc/hosts]
        source_paths: [b.py]
"""
    with pytest.raises(ValueError, match="absolute"):
        parse(yaml_text)


# ---------------------------------------------------------------------------
# source_paths validation
# ---------------------------------------------------------------------------


def test_parse_empty_source_paths_fails():
    yaml_text = """
version: 1
vendors:
  pkg:
    root: vendor/pkg
    topics:
      t:
        goal: g
        vendor_paths: [a.ts]
        source_paths: []
"""
    with pytest.raises(ValueError, match="source_paths"):
        parse(yaml_text)


def test_parse_missing_source_paths_fails():
    yaml_text = """
version: 1
vendors:
  pkg:
    root: vendor/pkg
    topics:
      t:
        goal: g
        vendor_paths: [a.ts]
"""
    with pytest.raises(ValueError, match="source_paths"):
        parse(yaml_text)


def test_parse_absolute_source_path_fails():
    yaml_text = """
version: 1
vendors:
  pkg:
    root: vendor/pkg
    topics:
      t:
        goal: g
        vendor_paths: [a.ts]
        source_paths: [/etc/shadow]
"""
    with pytest.raises(ValueError, match="absolute"):
        parse(yaml_text)


# ---------------------------------------------------------------------------
# Path traversal (relative ..)
# ---------------------------------------------------------------------------


def test_parse_path_traversal_vendor_paths_fails():
    yaml_text = """
version: 1
vendors:
  pkg:
    root: vendor/pkg
    topics:
      t:
        goal: g
        vendor_paths:
          - ../../etc/passwd
        source_paths: [b.py]
"""
    with pytest.raises(ValueError, match="traversal"):
        parse(yaml_text)


def test_parse_path_traversal_source_paths_fails():
    yaml_text = """
version: 1
vendors:
  pkg:
    root: vendor/pkg
    topics:
      t:
        goal: g
        vendor_paths: [a.ts]
        source_paths:
          - ../../etc/shadow
"""
    with pytest.raises(ValueError, match="traversal"):
        parse(yaml_text)


# ===========================================================================
# VendorStudyTool — behaviour tests
# ===========================================================================

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_STUDY_YAML = textwrap.dedent(
    """\
    version: 1
    vendors:
      acme:
        root: vendor/acme
        topics:
          widgets:
            goal: Learn widget patterns.
            vendor_paths:
              - src/widget.ts
            source_paths:
              - src/agent/tools/stock.py
      beta:
        root: vendor/beta
        topics:
          core:
            goal: Understand beta core.
            vendor_paths:
              - lib/core.py
            source_paths:
              - src/agent/loop.py
    """
)


def _make_tool(tmp_path: Path, yaml_text: str = _STUDY_YAML) -> VendorStudyTool:
    """Create a VendorStudyTool whose study_guide_path points to tmp_path/STUDY.md."""
    study_md = tmp_path / "STUDY.md"
    study_md.write_text(yaml_text, encoding="utf-8")

    # Write vendor file: vendor/acme/src/widget.ts
    vendor_widget = tmp_path / "vendor" / "acme" / "src" / "widget.ts"
    vendor_widget.parent.mkdir(parents=True, exist_ok=True)
    vendor_widget.write_text("export const widget = () => {};", encoding="utf-8")

    # Write source file: src/agent/tools/stock.py
    sb_stock = tmp_path / "src" / "agent" / "tools" / "stock.py"
    sb_stock.parent.mkdir(parents=True, exist_ok=True)
    sb_stock.write_text("# stub stock tool", encoding="utf-8")

    # Write vendor file: vendor/beta/lib/core.py
    vendor_core = tmp_path / "vendor" / "beta" / "lib" / "core.py"
    vendor_core.parent.mkdir(parents=True, exist_ok=True)
    vendor_core.write_text("# beta core", encoding="utf-8")

    # Write source file: src/agent/loop.py
    sb_loop = tmp_path / "src" / "agent" / "loop.py"
    sb_loop.parent.mkdir(parents=True, exist_ok=True)
    sb_loop.write_text("# stub loop", encoding="utf-8")

    return VendorStudyTool()


def _run(tool: VendorStudyTool, tmp_path: Path, **kwargs) -> str:
    """Execute tool with study_guide_path patched to tmp_path."""
    with patch.object(
        VendorStudyTool,
        "study_guide_path",
        new_callable=PropertyMock,
        return_value=tmp_path / "STUDY.md",
    ):
        return asyncio.run(tool.execute(**kwargs))


# ---------------------------------------------------------------------------
# List mode (no args)
# ---------------------------------------------------------------------------


def test_list_mode_returns_vendor_names(tmp_path: Path):
    tool = _make_tool(tmp_path)
    result = _run(tool, tmp_path)
    assert "acme" in result
    assert "beta" in result


def test_list_mode_returns_topic_names(tmp_path: Path):
    tool = _make_tool(tmp_path)
    result = _run(tool, tmp_path)
    assert "widgets" in result
    assert "core" in result


def test_list_mode_returns_goals(tmp_path: Path):
    tool = _make_tool(tmp_path)
    result = _run(tool, tmp_path)
    assert "Learn widget patterns." in result


# ---------------------------------------------------------------------------
# Vendor-only listing mode
# ---------------------------------------------------------------------------


def test_vendor_only_returns_topics_for_that_vendor(tmp_path: Path):
    tool = _make_tool(tmp_path)
    result = _run(tool, tmp_path, vendor="acme")
    assert "widgets" in result
    assert "Learn widget patterns." in result
    # Should NOT list topics from other vendors
    assert "core" not in result


# ---------------------------------------------------------------------------
# Unknown vendor / topic
# ---------------------------------------------------------------------------


def test_unknown_vendor_returns_error(tmp_path: Path):
    tool = _make_tool(tmp_path)
    result = _run(tool, tmp_path, vendor="nonexistent")
    assert "Error" in result
    assert "nonexistent" in result


def test_unknown_topic_returns_error(tmp_path: Path):
    tool = _make_tool(tmp_path)
    result = _run(tool, tmp_path, vendor="acme", topic="no_such_topic")
    assert "Error" in result
    assert "no_such_topic" in result


# ---------------------------------------------------------------------------
# Missing declared file
# ---------------------------------------------------------------------------


def test_missing_vendor_file_returns_error_with_path(tmp_path: Path):
    yaml_text = textwrap.dedent(
        """\
        version: 1
        vendors:
          acme:
            root: vendor/acme
            topics:
              widgets:
                goal: Learn widget patterns.
                vendor_paths:
                  - src/missing_file.ts
                source_paths:
                  - src/agent/tools/stock.py
        """
    )
    tool = _make_tool(tmp_path, yaml_text)
    result = _run(tool, tmp_path, vendor="acme", topic="widgets")
    assert "Error" in result
    assert "missing_file.ts" in result


def test_missing_source_file_returns_error_with_path(tmp_path: Path):
    yaml_text = textwrap.dedent(
        """\
        version: 1
        vendors:
          acme:
            root: vendor/acme
            topics:
              widgets:
                goal: Learn widget patterns.
                vendor_paths:
                  - src/widget.ts
                source_paths:
                  - src/agent/tools/no_such_source.py
        """
    )
    tool = _make_tool(tmp_path, yaml_text)
    result = _run(tool, tmp_path, vendor="acme", topic="widgets")
    assert "Error" in result
    assert "no_such_source.py" in result


# ---------------------------------------------------------------------------
# Size limit — checked before reading content (P1 fix)
# ---------------------------------------------------------------------------


def test_size_limit_exceeded_returns_error(tmp_path: Path):
    """Combined content > 120 KB should fail before reading all files."""
    yaml_text = textwrap.dedent(
        """\
        version: 1
        vendors:
          big:
            root: vendor/big
            topics:
              fat:
                goal: Size test.
                vendor_paths:
                  - huge.py
                source_paths:
                  - src/tiny.py
        """
    )
    # Write a 130 KB vendor file
    vendor_huge = tmp_path / "vendor" / "big" / "huge.py"
    vendor_huge.parent.mkdir(parents=True, exist_ok=True)
    vendor_huge.write_text("x" * (130 * 1024), encoding="utf-8")

    sb_tiny = tmp_path / "src" / "tiny.py"
    sb_tiny.parent.mkdir(parents=True, exist_ok=True)
    sb_tiny.write_text("# tiny", encoding="utf-8")

    study_md = tmp_path / "STUDY.md"
    study_md.write_text(yaml_text, encoding="utf-8")

    tool = VendorStudyTool()
    with patch.object(
        VendorStudyTool,
        "study_guide_path",
        new_callable=PropertyMock,
        return_value=tmp_path / "STUDY.md",
    ):
        result = asyncio.run(tool.execute(vendor="big", topic="fat"))
    assert "Error" in result
    assert "120" in result or "bytes" in result


# ---------------------------------------------------------------------------
# File count limit
# ---------------------------------------------------------------------------


def test_file_count_limit_returns_error(tmp_path: Path):
    """More than 10 total files should fail."""
    vendor_paths = [f"v{i}.py" for i in range(6)]
    source_paths = [f"s{i}.py" for i in range(6)]
    vp_yaml = "\n".join(f"              - {p}" for p in vendor_paths)
    sp_yaml = "\n".join(f"              - {p}" for p in source_paths)
    yaml_text = (
        "version: 1\n"
        "vendors:\n"
        "  many:\n"
        "    root: vendor/many\n"
        "    topics:\n"
        "      lots:\n"
        "        goal: Count test.\n"
        f"        vendor_paths:\n{vp_yaml}\n"
        f"        source_paths:\n{sp_yaml}\n"
    )
    study_md = tmp_path / "STUDY.md"
    study_md.write_text(yaml_text, encoding="utf-8")

    tool = VendorStudyTool()
    with patch.object(
        VendorStudyTool,
        "study_guide_path",
        new_callable=PropertyMock,
        return_value=tmp_path / "STUDY.md",
    ):
        result = asyncio.run(tool.execute(vendor="many", topic="lots"))
    assert "Error" in result
    assert "10" in result


# ---------------------------------------------------------------------------
# Suggested report path
# ---------------------------------------------------------------------------


def test_suggested_report_path_is_under_expected_dir(tmp_path: Path):
    tool = _make_tool(tmp_path)
    result = _run(tool, tmp_path, vendor="acme", topic="widgets")
    expected = tmp_path / "docs" / "superpowers" / "reports" / "vendor-study"
    expected = expected / f"{date.today().isoformat()}-acme-widgets.md"
    assert str(expected) in result


# ---------------------------------------------------------------------------
# Study packet content
# ---------------------------------------------------------------------------


def test_study_packet_includes_source_refs(tmp_path: Path):
    tool = _make_tool(tmp_path)
    result = _run(tool, tmp_path, vendor="acme", topic="widgets")
    assert "## Sources" in result
    assert "vendor/acme/src/widget.ts" in result
    assert "src/agent/tools/stock.py" in result


def test_study_packet_includes_instructions_block(tmp_path: Path):
    tool = _make_tool(tmp_path)
    result = _run(tool, tmp_path, vendor="acme", topic="widgets")
    assert "## Instructions" in result
    assert "Explain what the vendor does" in result
    assert "final report" in result


def test_study_packet_includes_vendor_code_section(tmp_path: Path):
    tool = _make_tool(tmp_path)
    result = _run(tool, tmp_path, vendor="acme", topic="widgets")
    assert "## Vendor Code" in result
    assert "export const widget" in result


def test_study_packet_includes_source_code_section(tmp_path: Path):
    tool = _make_tool(tmp_path)
    result = _run(tool, tmp_path, vendor="acme", topic="widgets")
    assert "## TheOS Code" in result
    assert "stub stock tool" in result


# ---------------------------------------------------------------------------
# P0: Absolute path escape — runtime guard in _build_packet
# ---------------------------------------------------------------------------


def test_runtime_absolute_vendor_path_escape(tmp_path: Path):
    """Even if parser is bypassed, _build_packet must reject paths that
    resolve outside repo root."""
    # Create a file outside the repo root
    outside = tmp_path / "outside" / "secret.txt"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("secret data", encoding="utf-8")

    # Craft a STUDY.md that uses a symlink to escape
    repo = tmp_path / "repo"
    repo.mkdir()
    vendor_dir = repo / "vendor" / "evil"
    vendor_dir.mkdir(parents=True)
    # Create a symlink inside vendor dir pointing outside
    (vendor_dir / "escape.txt").symlink_to(outside)

    yaml_text = textwrap.dedent(
        """\
        version: 1
        vendors:
          evil:
            root: vendor/evil
            topics:
              steal:
                goal: Test escape.
                vendor_paths:
                  - escape.txt
                source_paths:
                  - ok.py
        """
    )
    (repo / "STUDY.md").write_text(yaml_text, encoding="utf-8")
    ok_file = repo / "ok.py"
    ok_file.write_text("# ok", encoding="utf-8")

    tool = VendorStudyTool()
    with patch.object(
        VendorStudyTool,
        "study_guide_path",
        new_callable=PropertyMock,
        return_value=repo / "STUDY.md",
    ):
        result = asyncio.run(tool.execute(vendor="evil", topic="steal"))
    assert "Error" in result
    assert "escapes repo root" in result


def test_runtime_absolute_source_path_escape(tmp_path: Path):
    """TheOS paths that resolve outside repo root are rejected at runtime."""
    outside = tmp_path / "outside" / "secret.txt"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("secret data", encoding="utf-8")

    repo = tmp_path / "repo"
    repo.mkdir()
    vendor_dir = repo / "vendor" / "evil"
    vendor_dir.mkdir(parents=True)
    (vendor_dir / "ok.ts").write_text("// ok", encoding="utf-8")

    # Symlink source path outside
    (repo / "escape.py").symlink_to(outside)

    yaml_text = textwrap.dedent(
        """\
        version: 1
        vendors:
          evil:
            root: vendor/evil
            topics:
              steal:
                goal: Test escape.
                vendor_paths:
                  - ok.ts
                source_paths:
                  - escape.py
        """
    )
    (repo / "STUDY.md").write_text(yaml_text, encoding="utf-8")

    tool = VendorStudyTool()
    with patch.object(
        VendorStudyTool,
        "study_guide_path",
        new_callable=PropertyMock,
        return_value=repo / "STUDY.md",
    ):
        result = asyncio.run(tool.execute(vendor="evil", topic="steal"))
    assert "Error" in result
    assert "escapes repo root" in result


def test_runtime_invalid_vendor_key_is_rejected_before_report_path(tmp_path: Path):
    """Even if parser is bypassed, _build_packet must reject unsafe report slugs."""
    guide = StudyGuide(
        version=1,
        vendors={
            "bad/vendor": VendorConfig(
                root="vendor/acme",
                topics={
                    "widgets": StudyTopic(
                        goal="g",
                        vendor_paths=["src/widget.ts"],
                        source_paths=["src/agent/tools/stock.py"],
                    )
                },
            )
        },
    )
    tool = _make_tool(tmp_path)
    with (
        patch.object(VendorStudyTool, "_load_guide", return_value=guide),
        patch.object(
            VendorStudyTool,
            "study_guide_path",
            new_callable=PropertyMock,
            return_value=tmp_path / "STUDY.md",
        ),
    ):
        result = asyncio.run(tool.execute(vendor="bad/vendor", topic="widgets"))
    assert "Error" in result
    assert "key must match" in result


# ===========================================================================
# Registration tests
# ===========================================================================


def test_registration_with_study_md(tmp_path: Path):
    """vendor_study tool is registered when STUDY.md exists."""
    from src.agent.tool_sets import register_standard_tools
    from src.agent.tools.registration import ToolRegistrationConfig
    from src.agent.tools.registry import ToolRegistry

    study_md = tmp_path / "STUDY.md"
    study_md.write_text("version: 1\nvendors: {}\n", encoding="utf-8")

    registry = ToolRegistry()
    with patch(
        "src.agent.tools.vendor_study.VendorStudyTool.study_guide_path",
        new_callable=lambda: property(lambda self: study_md),
    ):
        register_standard_tools(registry, ToolRegistrationConfig(workspace=tmp_path, mode="single"))

    assert "vendor_study" in registry.tool_names


def test_registration_without_study_md(tmp_path: Path):
    """vendor_study tool is NOT registered when STUDY.md is absent."""
    from src.agent.tool_sets import register_standard_tools
    from src.agent.tools.registration import ToolRegistrationConfig
    from src.agent.tools.registry import ToolRegistry

    missing = tmp_path / "STUDY.md"
    # Deliberately do not create the file

    registry = ToolRegistry()
    with patch(
        "src.agent.tools.vendor_study.VendorStudyTool.study_guide_path",
        new_callable=lambda: property(lambda self: missing),
    ):
        register_standard_tools(registry, ToolRegistrationConfig(workspace=tmp_path, mode="single"))

    assert "vendor_study" not in registry.tool_names
