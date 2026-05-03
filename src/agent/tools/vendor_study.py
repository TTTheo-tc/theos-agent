"""Parser and data models for STUDY.md vendor study guide."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from src.agent.tools.base import Tool


@dataclass
class StudyTopic:
    goal: str
    vendor_paths: list[str]
    source_paths: list[str]


@dataclass
class VendorConfig:
    root: str
    topics: dict[str, StudyTopic]


@dataclass
class StudyGuide:
    version: int
    vendors: dict[str, VendorConfig]


_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _validate_key(key: object, field: str) -> str:
    """Return a validated YAML mapping key used in selectors and report paths."""
    if not isinstance(key, str):
        raise ValueError(f"{field}: key must be a string")
    if not _KEY_RE.fullmatch(key):
        raise ValueError(f"{field}: key must match [A-Za-z0-9_-]+, got {key!r}")
    return key


def _reject_unsafe_path(paths: list[str], field: str) -> None:
    """Raise ValueError if any path escapes the repo root.

    Rejects:
    - ``..`` components (parent traversal)
    - absolute paths (``/etc/hosts``, ``C:\\...``)
    """
    for p in paths:
        pp = Path(p)
        if pp.is_absolute():
            raise ValueError(f"{field}: absolute paths not allowed: {p!r}")
        if ".." in pp.parts:
            raise ValueError(f"{field}: path traversal not allowed: {p!r}")


def _parse_topic(name: str, data: object) -> StudyTopic:
    if not isinstance(data, dict):
        raise ValueError(f"topic {name!r}: must be a mapping")

    goal = data.get("goal")
    if not goal:
        raise ValueError(f"topic {name!r}: missing required 'goal'")
    if not isinstance(goal, str):
        raise ValueError(f"topic {name!r}: 'goal' must be a string")

    vendor_paths = data.get("vendor_paths")
    if not isinstance(vendor_paths, list) or len(vendor_paths) == 0:
        raise ValueError(f"topic {name!r}: 'vendor_paths' must be a non-empty list")
    if not all(isinstance(p, str) for p in vendor_paths):
        raise ValueError(f"topic {name!r}: all 'vendor_paths' entries must be strings")

    source_paths = data.get("source_paths")
    if not isinstance(source_paths, list) or len(source_paths) == 0:
        raise ValueError(f"topic {name!r}: 'source_paths' must be a non-empty list")
    if not all(isinstance(p, str) for p in source_paths):
        raise ValueError(f"topic {name!r}: all 'source_paths' entries must be strings")

    _reject_unsafe_path(vendor_paths, f"topic {name!r} vendor_paths")
    _reject_unsafe_path(source_paths, f"topic {name!r} source_paths")

    return StudyTopic(
        goal=goal,
        vendor_paths=list(vendor_paths),
        source_paths=list(source_paths),
    )


def _parse_vendor(name: str, data: object) -> VendorConfig:
    if not isinstance(data, dict):
        raise ValueError(f"vendor {name!r}: must be a mapping")

    root = data.get("root")
    if not root:
        raise ValueError(f"vendor {name!r}: missing required 'root'")
    if not isinstance(root, str):
        raise ValueError(f"vendor {name!r}: 'root' must be a string")
    _reject_unsafe_path([root], f"vendor {name!r} root")

    topics_raw = data.get("topics")
    if not isinstance(topics_raw, dict):
        raise ValueError(f"vendor {name!r}: 'topics' must be a mapping")

    topics: dict[str, StudyTopic] = {}
    for topic_name_raw, topic_data in topics_raw.items():
        topic_name = _validate_key(topic_name_raw, f"vendor {name!r} topic")
        topics[topic_name] = _parse_topic(topic_name, topic_data)

    return VendorConfig(root=root, topics=topics)


def parse_study_guide(yaml_text: str) -> StudyGuide:
    """Parse STUDY.md YAML text into a StudyGuide.

    Args:
        yaml_text: Raw YAML content of STUDY.md.

    Returns:
        Parsed StudyGuide dataclass.

    Raises:
        ValueError: On any validation failure.
    """
    try:
        doc = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML: {exc}") from exc

    if not isinstance(doc, dict):
        raise ValueError("Study guide must be a YAML mapping")

    if "version" not in doc:
        raise ValueError("Missing required 'version' field")

    version = doc["version"]
    if not isinstance(version, int) or version != 1:
        raise ValueError(f"'version' must be integer 1, got {version!r}")

    if "vendors" not in doc:
        raise ValueError("Missing required 'vendors' field")

    vendors_raw = doc["vendors"]
    if not isinstance(vendors_raw, dict):
        raise ValueError("'vendors' must be a mapping")

    vendors: dict[str, VendorConfig] = {}
    for vendor_name_raw, vendor_data in vendors_raw.items():
        vendor_name = _validate_key(vendor_name_raw, "vendor")
        vendors[vendor_name] = _parse_vendor(vendor_name, vendor_data)

    return StudyGuide(version=version, vendors=vendors)


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

_MAX_FILES = 10
_MAX_BYTES = 120 * 1024  # 120 KB


def _lang_from_path(p: str) -> str:
    """Return a markdown fenced-code language hint from file extension."""
    ext = Path(p).suffix.lstrip(".")
    _map = {
        "ts": "typescript",
        "tsx": "typescript",
        "js": "javascript",
        "jsx": "javascript",
        "py": "python",
        "md": "markdown",
        "yaml": "yaml",
        "yml": "yaml",
        "json": "json",
        "sh": "bash",
    }
    return _map.get(ext, ext)


class VendorStudyTool(Tool):
    """Read-only vendor study tool — lists vendors/topics or returns a study packet."""

    name = "vendor_study"
    description = (
        "Explore vendor study topics from STUDY.md. "
        "No args → list all vendors and topics. "
        "vendor only → list topics for that vendor. "
        "vendor + topic → return a full study packet with source files and instructions."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "vendor": {
                "type": "string",
                "description": "Vendor name (key in STUDY.md vendors map).",
            },
            "topic": {
                "type": "string",
                "description": "Topic name within the vendor (key in topics map).",
            },
        },
        "required": [],
    }

    @property
    def risk_level(self) -> str:
        return "low"

    @property
    def study_guide_path(self) -> Path:
        return Path(__file__).parent.parent.parent.parent / "STUDY.md"

    @property
    def report_dir(self) -> Path:
        return self.study_guide_path.parent / "docs" / "superpowers" / "reports" / "vendor-study"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_guide(self) -> StudyGuide | str:
        """Return parsed StudyGuide or an error string."""
        p = self.study_guide_path
        if not p.is_file():
            return f"Error: STUDY.md not found at {p}"
        try:
            return parse_study_guide(p.read_text(encoding="utf-8"))
        except ValueError as exc:
            return f"Error: STUDY.md parse failed: {exc}"

    # ------------------------------------------------------------------
    # execute
    # ------------------------------------------------------------------

    async def execute(
        self,
        vendor: str | None = None,
        topic: str | None = None,
        **kwargs: Any,
    ) -> str:
        guide_or_err = self._load_guide()
        if isinstance(guide_or_err, str):
            return guide_or_err
        guide: StudyGuide = guide_or_err

        # --- list all vendors ---
        if vendor is None:
            lines = ["# Vendor Study Guide\n"]
            for vname, vcfg in guide.vendors.items():
                lines.append(f"## {vname}")
                for tname, tcfg in vcfg.topics.items():
                    lines.append(f"  - **{tname}**: {tcfg.goal}")
            return "\n".join(lines)

        # --- validate vendor ---
        if vendor not in guide.vendors:
            known = ", ".join(guide.vendors.keys())
            return f"Error: unknown vendor {vendor!r}. Known vendors: {known}"

        vcfg = guide.vendors[vendor]

        # --- list topics for vendor ---
        if topic is None:
            lines = [f"# Topics for vendor: {vendor}\n"]
            for tname, tcfg in vcfg.topics.items():
                lines.append(f"  - **{tname}**: {tcfg.goal}")
            return "\n".join(lines)

        # --- validate topic ---
        if topic not in vcfg.topics:
            known = ", ".join(vcfg.topics.keys())
            return f"Error: unknown topic {topic!r} for vendor {vendor!r}. Known topics: {known}"

        tcfg = vcfg.topics[topic]
        return self._build_packet(vendor, topic, vcfg.root, tcfg)

    def _build_packet(
        self,
        vendor: str,
        topic: str,
        vendor_root: str,
        tcfg: StudyTopic,
    ) -> str:
        repo_root = self.study_guide_path.parent
        try:
            vendor_slug = _validate_key(vendor, "vendor")
            topic_slug = _validate_key(topic, "topic")
        except ValueError as exc:
            return f"Error: {exc}"

        # Collect all paths and check count limit
        all_paths = tcfg.vendor_paths + tcfg.source_paths
        if len(all_paths) > _MAX_FILES:
            return (
                f"Error: file count {len(all_paths)} exceeds limit of {_MAX_FILES}. "
                f"Reduce vendor_paths + source_paths."
            )

        # Read vendor files
        vendor_dir = repo_root / vendor_root
        vendor_contents: list[tuple[str, str]] = []
        total_bytes = 0
        for rel in tcfg.vendor_paths:
            full = (vendor_dir / rel).resolve()
            if not full.is_relative_to(repo_root.resolve()):
                return f"Error: vendor path escapes repo root: {rel!r}"
            if not full.is_file():
                return f"Error: vendor file not found: {full}"
            size = full.stat().st_size
            total_bytes += size
            if total_bytes > _MAX_BYTES:
                return (
                    f"Error: combined content exceeds limit of {_MAX_BYTES} bytes "
                    f"(120 KB) at file {rel!r}. Reduce files or narrow the topic."
                )
            vendor_contents.append((rel, full.read_text(encoding="utf-8")))

        # Read source files
        source_contents: list[tuple[str, str]] = []
        for rel in tcfg.source_paths:
            full = (repo_root / rel).resolve()
            if not full.is_relative_to(repo_root.resolve()):
                return f"Error: source path escapes repo root: {rel!r}"
            if not full.is_file():
                return f"Error: source file not found: {full}"
            size = full.stat().st_size
            total_bytes += size
            if total_bytes > _MAX_BYTES:
                return (
                    f"Error: combined content exceeds limit of {_MAX_BYTES} bytes "
                    f"(120 KB) at file {rel!r}. Reduce files or narrow the topic."
                )
            source_contents.append((rel, full.read_text(encoding="utf-8")))

        today = date.today().isoformat()
        report_path = str(self.report_dir / f"{today}-{vendor_slug}-{topic_slug}.md")

        lines: list[str] = []
        lines.append("# Vendor Study Packet\n")
        lines.append(f"- Vendor: {vendor}")
        lines.append(f"- Topic: {topic}")
        lines.append(f"- Goal: {tcfg.goal}")
        lines.append(f"- Suggested report path: {report_path}")
        lines.append("")
        lines.append("## Sources\n")
        lines.append("### Vendor")
        for rel in tcfg.vendor_paths:
            lines.append(f"- {vendor_root}/{rel}")
        lines.append("")
        lines.append("### TheOS")
        for rel in tcfg.source_paths:
            lines.append(f"- {rel}")
        lines.append("")

        lines.append("## Vendor Code\n")
        for rel, content in vendor_contents:
            lang = _lang_from_path(rel)
            lines.append(f"### {vendor_root}/{rel}")
            lines.append(f"```{lang}")
            lines.append(content)
            lines.append("```")
            lines.append("")

        lines.append("## TheOS Code\n")
        for rel, content in source_contents:
            lang = _lang_from_path(rel)
            lines.append(f"### {rel}")
            lines.append(f"```{lang}")
            lines.append(content)
            lines.append("```")
            lines.append("")

        lines.append("## Instructions\n")
        lines.append("Using only the sources above:")
        lines.append("1. Explain what the vendor does.")
        lines.append("2. Explain what TheOS does.")
        lines.append("3. Identify key differences.")
        lines.append("4. Propose suggestions worth testing.")
        lines.append("5. Explain risks and why not to copy directly.")
        lines.append("6. Write the final report to the exact suggested absolute report path.")
        lines.append("7. The final report must not include full raw source dumps.")

        return "\n".join(lines)
