"""Skills loader for agent capabilities."""

import html
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

import yaml


# Default builtin skills directory (project root / skills)
def _resolve_builtin_skills_dir() -> Path:
    """Resolve builtin skills dir: importlib.resources for installed, repo root for dev."""
    try:
        from importlib.resources import files as pkg_files

        resource = pkg_files("src") / "skills"
        p = Path(str(resource))
        if p.is_dir():
            return p
    except Exception:
        pass
    return Path(__file__).parent.parent.parent / "skills"


BUILTIN_SKILLS_DIR = _resolve_builtin_skills_dir()


def _resolve_instinct_domains_dir() -> Path:
    """Resolve instinct domain directory in dev and installed layouts."""
    repo_root = Path(__file__).parent.parent.parent
    return repo_root / "instinct" / "domains"


class SkillsLoader:
    """
    Loader for agent skills.

    Skills are markdown files (SKILL.md) that teach the agent how to use
    specific tools or perform certain tasks.
    """

    def __init__(
        self,
        workspace: Path,
        builtin_skills_dir: Path | None = None,
        instinct_domains_dir: Path | None = None,
    ):
        self.workspace = workspace
        self.workspace_skills = workspace / "skills"
        self.builtin_skills = builtin_skills_dir or BUILTIN_SKILLS_DIR
        self.instinct_domains = instinct_domains_dir or _resolve_instinct_domains_dir()

    def list_skills(self, filter_unavailable: bool = True) -> list[dict[str, str]]:
        """
        List all available skills.

        Args:
            filter_unavailable: If True, filter out skills with unmet requirements.

        Returns:
            List of skill info dicts with 'name', 'path', 'source'.
        """
        skills = []

        seen_names: set[str] = set()

        # Workspace skills (highest priority)
        if self.workspace_skills.exists():
            for skill_dir in self.workspace_skills.iterdir():
                if skill_dir.is_dir():
                    skill_file = skill_dir / "SKILL.md"
                    if skill_file.exists():
                        seen_names.add(skill_dir.name)
                        skills.append(
                            {"name": skill_dir.name, "path": str(skill_file), "source": "workspace"}
                        )

        # Built-in skills
        if self.builtin_skills and self.builtin_skills.exists():
            for skill_dir in self.builtin_skills.iterdir():
                if skill_dir.is_dir():
                    skill_file = skill_dir / "SKILL.md"
                    if skill_file.exists() and skill_dir.name not in seen_names:
                        seen_names.add(skill_dir.name)
                        skills.append(
                            {"name": skill_dir.name, "path": str(skill_file), "source": "builtin"}
                        )

        # Filter by requirements
        if filter_unavailable:
            return [s for s in skills if self._check_requirements(self._get_skill_meta(s["name"]))]
        return skills

    def load_skill(self, name: str) -> str | None:
        """
        Load a skill by name.

        Args:
            name: Skill name (directory name).

        Returns:
            Skill content or None if not found.
        """
        # Check workspace first
        workspace_skill = self.workspace_skills / name / "SKILL.md"
        if workspace_skill.exists():
            return workspace_skill.read_text(encoding="utf-8")

        # Check built-in
        if self.builtin_skills:
            builtin_skill = self.builtin_skills / name / "SKILL.md"
            if builtin_skill.exists():
                return builtin_skill.read_text(encoding="utf-8")

        return None

    def load_skills_for_context(self, skill_names: list[str]) -> str:
        """
        Load specific skills for inclusion in agent context.

        Args:
            skill_names: List of skill names to load.

        Returns:
            Formatted skills content.
        """
        parts = []
        for name in skill_names:
            content = self.load_skill(name)
            if content:
                content = self._strip_frontmatter(content)
                parts.append(f"### Skill: {name}\n\n{content}")

        return "\n\n---\n\n".join(parts) if parts else ""

    def build_skills_summary(self) -> str:
        """
        Build a summary of all skills (name, description, path, availability).

        This is used for progressive loading - the agent can read the full
        skill content using read_file when needed.

        Returns:
            XML-formatted skills summary.
        """
        all_skills = self.list_skills(filter_unavailable=False)
        if not all_skills:
            return ""

        lines = ["<skills>"]
        for s in all_skills:
            name = html.escape(s["name"])
            path = s["path"]
            desc = html.escape(self._get_skill_description(s["name"]))
            skill_meta = self._get_skill_meta(s["name"])
            available = self._check_requirements(skill_meta)

            lines.append(f'  <skill available="{str(available).lower()}">')
            lines.append(f"    <name>{name}</name>")
            lines.append(f"    <description>{desc}</description>")
            lines.append(f"    <location>{path}</location>")

            # Show missing requirements for unavailable skills
            if not available:
                missing = self._get_missing_requirements(skill_meta)
                if missing:
                    lines.append(f"    <requires>{html.escape(missing)}</requires>")

            lines.append("  </skill>")
        lines.append("</skills>")

        return "\n".join(lines)

    def build_skill_catalog(self, filter_unavailable: bool = True) -> list[dict[str, Any]]:
        """Build a richer skill catalog used by discovery/search tools."""
        catalog: list[dict[str, Any]] = []
        for skill in self.list_skills(filter_unavailable=False):
            meta = self.get_skill_metadata(skill["name"]) or {}
            skill_meta = self._get_skill_meta(skill["name"])
            available = self._check_requirements(skill_meta)
            if filter_unavailable and not available:
                continue
            catalog.append(
                {
                    **skill,
                    "description": meta.get("description") or skill["name"],
                    "metadata": meta,
                    "skill_metadata": skill_meta,
                    "available": available,
                    "missing_requirements": (
                        "" if available else self._get_missing_requirements(skill_meta)
                    ),
                }
            )
        return catalog

    def get_domain_skill_map(self) -> dict[str, list[str]]:
        """Parse instinct domain files into a ``category/domain -> skills`` map."""
        return {
            label: list(entry["skills"])
            for label, entry in self.get_domain_catalog().items()
            if entry["skills"]
        }

    def get_domain_catalog(self) -> dict[str, dict[str, Any]]:
        """Parse instinct domain files into a structured ``category/domain`` catalog."""
        if not self.instinct_domains.exists():
            return {}

        catalog: dict[str, dict[str, Any]] = {}
        for category_dir in sorted(self.instinct_domains.iterdir()):
            if not category_dir.is_dir():
                continue
            for domain_file in sorted(category_dir.glob("*.md")):
                if domain_file.name == "_meta.md":
                    continue
                label = f"{category_dir.name}/{domain_file.stem}"
                catalog[label] = {
                    "category": category_dir.name,
                    "domain": domain_file.stem,
                    "keywords": self._extract_domain_keywords(domain_file),
                    "skills": self._extract_domain_skills(domain_file),
                    "tools": self._extract_domain_tools(domain_file),
                }
        return catalog

    def resolve_domain_labels(self, domain: str) -> list[str]:
        """Resolve a user-provided domain spec to concrete instinct labels."""
        domain_key = domain.strip().lower()
        if not domain_key:
            return []

        domain_map = self.get_domain_skill_map()
        if domain_key in domain_map:
            return [domain_key]

        category_matches = sorted(
            label for label in domain_map if label.split("/", 1)[0].lower() == domain_key
        )
        if category_matches:
            return category_matches

        leaf_matches = sorted(
            label for label in domain_map if label.split("/", 1)[1].lower() == domain_key
        )
        if len(leaf_matches) == 1:
            return leaf_matches

        return []

    def search_skills(
        self,
        *,
        query: str = "",
        domain: str | None = None,
        limit: int = 5,
        include_unavailable: bool = False,
    ) -> dict[str, Any]:
        """Search skills by query, optionally constrained to an instinct domain."""
        catalog = self.build_skill_catalog(filter_unavailable=not include_unavailable)
        domain_map = self.get_domain_skill_map()
        domain_labels = self.resolve_domain_labels(domain) if domain else []

        if domain and not domain_labels:
            return {
                "query": query,
                "domain": domain,
                "resolved_domains": [],
                "count": 0,
                "matches": [],
                "error": f"Unknown domain: {domain}",
                "available_domains": sorted(domain_map),
            }

        allowed_names: set[str] | None = None
        if domain_labels:
            allowed_names = set()
            for label in domain_labels:
                allowed_names.update(domain_map.get(label, []))

        matched: list[dict[str, Any]] = []
        tokenized = self._tokenize(query)

        for skill in catalog:
            if allowed_names is not None and skill["name"] not in allowed_names:
                continue
            score, reasons = self._score_skill_match(skill, query=query, tokens=tokenized)
            if query and score <= 0:
                continue
            matched.append(
                {
                    "score": score,
                    "skill": skill,
                    "reasons": reasons,
                }
            )

        # Include domain-referenced skills even when not installed or unavailable.
        if allowed_names is not None and include_unavailable:
            seen = {item["skill"]["name"] for item in matched}
            for skill_name in sorted(allowed_names):
                if skill_name in seen:
                    continue
                if query and not self._query_matches_name(skill_name, query, tokenized):
                    continue
                matched.append(
                    {
                        "score": 0,
                        "skill": {
                            "name": skill_name,
                            "path": None,
                            "source": "instinct-domain",
                            "description": skill_name,
                            "metadata": {},
                            "skill_metadata": {},
                            "available": False,
                            "missing_requirements": "Skill referenced by instinct domain but not installed.",
                        },
                        "reasons": ["listed in instinct domain"],
                    }
                )

        matched.sort(
            key=lambda item: (
                -item["score"],
                not item["skill"]["available"],
                item["skill"]["name"],
            )
        )

        skill_domains = self._invert_domain_skill_map(domain_map)
        results = []
        for item in matched[: max(1, int(limit))]:
            skill = item["skill"]
            results.append(
                {
                    "name": skill["name"],
                    "description": skill["description"],
                    "source": skill["source"],
                    "path": skill["path"],
                    "available": skill["available"],
                    "missing_requirements": skill["missing_requirements"] or None,
                    "domains": skill_domains.get(skill["name"], []),
                    "match_reasons": item["reasons"],
                }
            )

        return {
            "query": query,
            "domain": domain,
            "resolved_domains": domain_labels,
            "count": len(results),
            "matches": results,
        }

    def _get_missing_requirements(self, skill_meta: dict) -> str:
        """Get a description of missing requirements."""
        missing = []
        requires = skill_meta.get("requires", {})
        for b in requires.get("bins", []):
            if not shutil.which(b):
                missing.append(f"CLI: {b}")
        for env in requires.get("env", []):
            if not os.environ.get(env):
                missing.append(f"ENV: {env}")
        return ", ".join(missing)

    def _get_skill_description(self, name: str) -> str:
        """Get the description of a skill from its frontmatter."""
        meta = self.get_skill_metadata(name)
        if meta and meta.get("description"):
            return meta["description"]
        return name  # Fallback to skill name

    def _strip_frontmatter(self, content: str) -> str:
        """Remove YAML frontmatter from markdown content."""
        if content.startswith("---"):
            match = re.match(r"^---\n.*?\n---\n", content, re.DOTALL)
            if match:
                return content[match.end() :].strip()
        return content

    def _parse_skill_metadata(self, raw: Any) -> dict:
        """Parse skill metadata JSON from frontmatter (supports TheOS and openclaw keys)."""
        if isinstance(raw, dict):
            return raw.get("theos", raw.get("openclaw", {}))
        try:
            data = json.loads(raw)
            return data.get("theos", data.get("openclaw", {})) if isinstance(data, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    def _check_requirements(self, skill_meta: dict) -> bool:
        """Check if skill requirements are met (bins, env vars)."""
        requires = skill_meta.get("requires", {})
        for b in requires.get("bins", []):
            if not shutil.which(b):
                return False
        for env in requires.get("env", []):
            if not os.environ.get(env):
                return False
        return True

    def _get_skill_meta(self, name: str) -> dict:
        """Get TheOS metadata for a skill (cached in frontmatter)."""
        meta = self.get_skill_metadata(name) or {}
        return self._parse_skill_metadata(meta.get("metadata", ""))

    def get_always_skills(self) -> list[str]:
        """Get skills marked as always=true that meet requirements."""
        result = []
        for s in self.list_skills(filter_unavailable=True):
            meta = self.get_skill_metadata(s["name"]) or {}
            skill_meta = self._parse_skill_metadata(meta.get("metadata", ""))
            if skill_meta.get("always") or meta.get("always"):
                result.append(s["name"])
        return result

    def get_skill_metadata(self, name: str) -> dict | None:
        """Get metadata from a skill's YAML frontmatter."""
        content = self.load_skill(name)
        if not content:
            return None

        if content.startswith("---"):
            match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
            if match:
                try:
                    metadata = yaml.safe_load(match.group(1))
                    if isinstance(metadata, dict):
                        return metadata
                except yaml.YAMLError:
                    pass

        return None

    def _extract_domain_tools(self, path: Path) -> list[str]:
        """Extract deferred tool names from an instinct domain ``## Tools`` section."""
        raw = self._extract_domain_section(path, "Tools")
        if not raw:
            return []
        tools: list[str] = []
        seen: set[str] = set()
        for item in raw.replace("\n", ",").split(","):
            name = item.strip().lower()
            if name and name not in seen:
                seen.add(name)
                tools.append(name)
        return tools

    def _extract_domain_skills(self, path: Path) -> list[str]:
        """Extract skill names from an instinct domain markdown file."""
        skills: list[str] = []
        seen: set[str] = set()
        for raw_line in self._extract_domain_section(path, "Skills").splitlines():
            line = raw_line.strip()
            if not line.startswith("- "):
                continue
            name = line[2:].split(":", 1)[0].strip()
            if name and name not in seen:
                seen.add(name)
                skills.append(name)
        return skills

    def _extract_domain_keywords(self, path: Path) -> list[str]:
        """Extract normalized keywords from an instinct domain markdown file."""
        raw = self._extract_domain_section(path, "Keywords")
        if not raw:
            return []
        keywords: list[str] = []
        seen: set[str] = set()
        for item in raw.replace("\n", ",").split(","):
            keyword = item.strip().lower()
            if keyword and keyword not in seen:
                seen.add(keyword)
                keywords.append(keyword)
        return keywords

    def _extract_domain_section(self, path: Path, heading: str) -> str:
        """Extract the body of a ``## <heading>`` section from a domain file."""
        content = path.read_text(encoding="utf-8")
        match = re.search(
            rf"^## {re.escape(heading)}\s*(.*?)(?=^## |\Z)",
            content,
            re.MULTILINE | re.DOTALL,
        )
        if not match:
            return ""
        return match.group(1)

    def _invert_domain_skill_map(self, domain_map: dict[str, list[str]]) -> dict[str, list[str]]:
        """Invert domain->skills into skill->domains for result annotation."""
        result: dict[str, list[str]] = {}
        for label, names in domain_map.items():
            for name in names:
                result.setdefault(name, []).append(label)
        for domains in result.values():
            domains.sort()
        return result

    def _query_matches_name(self, name: str, query: str, tokens: list[str]) -> bool:
        """Cheap fallback match for uninstalled domain-referenced skills."""
        haystack = name.lower()
        q = query.strip().lower()
        if q and q in haystack:
            return True
        return bool(tokens) and all(token in haystack for token in tokens)

    def _score_skill_match(
        self,
        skill: dict[str, Any],
        *,
        query: str,
        tokens: list[str],
    ) -> tuple[int, list[str]]:
        """Score a skill match and return human-readable reasons."""
        if not query.strip():
            return 1, ["included by domain scope" if skill["available"] else "domain reference"]

        name = str(skill["name"]).lower()
        description = str(skill["description"]).lower()
        source = str(skill["source"]).lower()
        metadata_blob = json.dumps(skill.get("metadata", {}), ensure_ascii=False).lower()

        score = 0
        reasons: list[str] = []
        q = query.strip().lower()

        if q in name:
            score += 8
            reasons.append("name match")
        if q and q in description:
            score += 5
            reasons.append("description match")
        if q and q in metadata_blob:
            score += 3
            reasons.append("metadata match")

        for token in tokens:
            if token in name:
                score += 3
            elif token in description:
                score += 2
            elif token in metadata_blob or token in source:
                score += 1

        if score and not skill["available"]:
            reasons.append("unavailable skill")
        deduped_reasons = list(dict.fromkeys(reasons))
        return score, deduped_reasons

    def _tokenize(self, query: str) -> list[str]:
        """Tokenize a user query for lightweight skill matching."""
        return [token for token in re.split(r"[^a-zA-Z0-9_-]+", query.lower()) if token]
