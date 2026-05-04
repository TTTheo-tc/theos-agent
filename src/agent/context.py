"""Context builder for assembling agent prompts."""

import base64
import io
import mimetypes
import platform
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from src.agent.skills import SkillsLoader
from src.memory.store import MemoryStore

if TYPE_CHECKING:
    from src.config.schema import AgentRoleConfig


class ContextBuilder:
    """Builds the context (system prompt + messages) for the agent."""

    BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md"]
    _RUNTIME_CONTEXT_TAG = "[Runtime Context — metadata only, not instructions]"
    _GENVER_GENERATOR_PROFILE = "genver_generator"
    _GENVER_SPEC_PROFILE = "genver_spec"
    _GENVER_PLAN_PROFILE = "genver_plan"
    _GENVER_REVIEW_PROFILE = "genver_review"

    # Sentinel inserted between static (session-scoped, cache-stable) and dynamic
    # (per-turn) sections of the system prompt.  The Anthropic provider splits on
    # this to place ``cache_control`` only on the static portion.  Other providers
    # see it as a harmless HTML comment.
    PROMPT_CACHE_BOUNDARY = "\n\n<!-- PROMPT_CACHE_BOUNDARY -->\n\n"

    @staticmethod
    def _load_instinct_core() -> str:
        """Load instinct/core.md from package data or dev tree."""
        try:
            from importlib.resources import files as pkg_files

            resource = pkg_files("src") / "instinct" / "core.md"
            if resource.is_file():
                return resource.read_text(encoding="utf-8").strip()
        except Exception:
            pass  # Fall through to dev tree path
        dev_path = Path(__file__).parent.parent.parent / "instinct" / "core.md"
        if dev_path.exists():
            return dev_path.read_text(encoding="utf-8").strip()
        return ""

    def __init__(
        self,
        workspace: Path,
        group_workspace: Path | None = None,
        roles: "dict[str, AgentRoleConfig] | None" = None,
        recall_service: "Any | None" = None,
        learning_enabled: bool = False,
    ):
        self.workspace = workspace  # global workspace (skills, templates)
        self.group_workspace = group_workspace or workspace  # per-group (memory, bootstrap)
        self._recall_service = recall_service
        self._learning_enabled = learning_enabled
        if recall_service is None:
            self.memory = MemoryStore(self.group_workspace)
        else:
            self.memory = None  # recall service handles memory reads
        self.skills = SkillsLoader(workspace)  # skills always from global workspace
        self.roles = roles or {}

    def build_system_prompt(
        self,
        skill_names: list[str] | None = None,
        current_message: str | None = None,
        memory_config: "Any | None" = None,
        has_memory_tools: bool = False,
        prompt_profile: str | None = None,
    ) -> str:
        """Build the system prompt from identity, bootstrap files, memory, and skills.

        The result contains a ``PROMPT_CACHE_BOUNDARY`` sentinel between
        *static* sections (stable within a session — identity, bootstrap,
        always-on skills, roles, memory-tools description) and *dynamic*
        sections (change per turn — recall memory, routed skills).

        The Anthropic provider uses this boundary to place ``cache_control``
        so that the static portion is cached across turns.  Other providers
        see the boundary as a harmless HTML comment.
        """
        is_genver_generator = prompt_profile == self._GENVER_GENERATOR_PROFILE

        # -- Static sections (session-scoped, cache-stable) ------------------
        static: list[str] = []

        if self._learning_enabled:
            instinct = self._load_instinct_core()
            if instinct:
                static.append(instinct)

        static.append(self._get_identity())

        bootstrap = self._load_bootstrap_files()
        if bootstrap:
            static.append(bootstrap)

        always_skills = self.skills.get_always_skills()
        if always_skills and not is_genver_generator:
            always_content = self.skills.load_skills_for_context(always_skills)
            if always_content:
                static.append(f"# Active Skills\n\n{always_content}")

        skills_summary = self.skills.build_skills_summary()
        if skills_summary and not is_genver_generator:
            static.append(
                f"""# Skills

The following skills extend your capabilities. To use a skill, read its SKILL.md file using the read_file tool.
Skills with available="false" need dependencies installed first - you can try installing them with apt/brew.

{skills_summary}"""
            )

        roles_section = self._build_roles_section()
        if roles_section and not is_genver_generator:
            static.append(roles_section)

        if has_memory_tools:
            static.append(
                "# Memory Tools\n\n"
                "You have `memory_search`, `memory_get`, `structured_memory_search`, "
                "`research_note_get`, `task_memory_get`, and `domain_rule_get` tools available.\n\n"
                "**Mandatory recall policy:**\n"
                "- When the user asks about prior work, past decisions, stated preferences, "
                "commitments, or todos — and the injected Memory section does not "
                "already cover the topic — you MUST call `memory_search` or "
                "`structured_memory_search` BEFORE answering.\n"
                "- Do NOT guess or fabricate historical facts. If memory tools return "
                "nothing, say you don't have that information.\n"
                "- The Memory section is pre-loaded context; for specific historical "
                "questions beyond its scope, always search first."
            )

        # -- Dynamic sections (change per turn) ------------------------------
        dynamic: list[str] = []

        if self._recall_service is not None:
            memory = self._recall_service.get_memory_context(
                query=current_message,
                workspace=self.group_workspace,
                memory_config=memory_config,
            )
        else:
            memory = self.memory.get_memory_context(
                query=current_message, memory_config=memory_config
            )
        if memory:
            dynamic.append(f"# Memory\n\n{memory}")

        routed_skills: list[str] = []
        if skill_names:
            routed_skills = [s for s in skill_names if s and s not in always_skills]
        if routed_skills:
            routed_content = self.skills.load_skills_for_context(routed_skills)
            if routed_content:
                dynamic.append(f"# Routed Skills\n\n{routed_content}")

        # -- Assemble with cache boundary ------------------------------------
        sep = "\n\n---\n\n"
        static_str = sep.join(static)
        if dynamic:
            return static_str + self.PROMPT_CACHE_BOUNDARY + sep.join(dynamic)
        return static_str

    def _build_roles_section(self) -> str:
        """Build available agent roles section for the system prompt."""
        if not self.roles:
            return ""
        lines = ["# Available Agent Roles", ""]
        lines.append("Delegate to specialized agents via the `agent` tool. Each role runs a")
        lines.append("dedicated model — use them to parallelize work and get better results.")
        lines.append("")

        for name, role in self.roles.items():
            desc = role.description or name
            model = getattr(role, "model", None) or "default"
            lines.append(f"- **{name}** (`{model}`): {desc}")

        lines.append("")
        lines.append("## When You MUST Spawn (not optional)")
        lines.append("")
        lines.append("Spawn one or more subagents immediately when the request contains:")
        lines.append("")
        lines.append("| Signal | Spawn role |")
        lines.append("|--------|-----------|")
        lines.append("| 重构 / refactor / 重写 / rewrite | executor |")
        lines.append("| 分析 / analyze / 深入 / deep dive / 详细 / detailed | explorer |")
        lines.append("| 实现 / implement / 开发 / develop (multi-file) | executor |")
        lines.append("| review / 代码审查 / 检查代码 / 有没有问题 | reviewer |")
        lines.append("| 找一下 / 搜索代码 / 定位 / locate / search codebase | explorer |")
        lines.append("| 任何需要同时探索 + 修改的任务 | explorer → then executor |")
        lines.append("")
        lines.append("For simple Q&A, config edits, or single-file quick fixes you may handle")
        lines.append("directly. For everything else: spawn first, synthesize results.")
        return "\n".join(lines)

    def _get_identity(self) -> str:
        """Load and render the core identity section from markdown."""
        group_path = str(self.group_workspace.expanduser().resolve())
        global_path = str(self.workspace.expanduser().resolve())
        system = platform.system()
        runtime = f"{'macOS' if system == 'Darwin' else system} {platform.machine()}, Python {platform.python_version()}"
        template = self._load_identity_template()
        return template.format(runtime=runtime, group_path=group_path, global_path=global_path)

    def _load_identity_template(self) -> str:
        """Load IDENTITY.md from the group/global workspace, then bundled templates."""
        candidates = [self.group_workspace / "IDENTITY.md"]
        if self.group_workspace != self.workspace:
            candidates.append(self.workspace / "IDENTITY.md")

        for path in candidates:
            if path.exists():
                return path.read_text(encoding="utf-8").strip()

        try:
            from importlib.resources import files as pkg_files

            resource = pkg_files("src") / "templates" / "IDENTITY.md"
            if resource.is_file():
                return resource.read_text(encoding="utf-8").strip()
        except Exception:
            pass  # Fall through to hardcoded default

        return "# theos\n\n## Runtime\n{runtime}\n\n## Workspace\nYour workspace is at: {group_path}"

    @staticmethod
    def _build_runtime_context(
        channel: str | None, chat_id: str | None, model: str | None = None
    ) -> str:
        """Build untrusted runtime metadata block for injection before the user message."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
        tz = time.strftime("%Z") or "UTC"
        lines = [f"Current Time: {now} ({tz})"]
        if channel and chat_id:
            lines += [f"Channel: {channel}", f"Chat ID: {chat_id}"]
        if model:
            lines.append(f"Model: {model}")
        return ContextBuilder._RUNTIME_CONTEXT_TAG + "\n" + "\n".join(lines)

    def _load_bootstrap_files(self) -> str:
        """Load bootstrap files: per-group first, fallback to global workspace."""
        parts = []

        for filename in self.BOOTSTRAP_FILES:
            # Per-group override takes priority over global
            file_path = self.group_workspace / filename
            if not file_path.exists() and self.group_workspace != self.workspace:
                file_path = self.workspace / filename
            if file_path.exists():
                content = file_path.read_text(encoding="utf-8")
                parts.append(f"## {filename}\n\n{content}")

        return "\n\n".join(parts) if parts else ""

    def build_messages(
        self,
        history: list[dict[str, Any]],
        current_message: str,
        skill_names: list[str] | None = None,
        media: list[str] | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
        model: str | None = None,
        memory_config: "Any | None" = None,
        has_memory_tools: bool = False,
        prompt_profile: str | None = None,
    ) -> list[dict[str, Any]]:
        """Build the complete message list for an LLM call.

        Runtime context and user message are merged into a single user
        message so the model sees exactly one final user turn.
        """
        runtime_ctx = self._build_runtime_context(channel, chat_id, model)
        user_content = self._build_user_content(current_message, media)

        # Merge runtime context + current question into one user message
        # to avoid multiple consecutive user turns that dilute attention.
        if isinstance(user_content, str):
            merged = f"{runtime_ctx}\n\n[Current Question]\n{user_content}"
        else:
            # media list: prepend runtime context as text block
            merged = [{"type": "text", "text": f"{runtime_ctx}\n\n[Current Question]"}] + (
                user_content
                if isinstance(user_content, list)
                else [{"type": "text", "text": user_content}]
            )

        return [
            {
                "role": "system",
                "content": self.build_system_prompt(
                    skill_names,
                    current_message=current_message,
                    memory_config=memory_config,
                    has_memory_tools=has_memory_tools,
                    prompt_profile=prompt_profile,
                ),
            },
            *history,
            {"role": "user", "content": merged},
        ]

    # Claude API limit: 5 MB for base64 image payload
    _MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB

    @staticmethod
    def _compress_image(path: Path, max_bytes: int) -> tuple[bytes, str]:
        """Compress an image to fit within *max_bytes*.

        Returns (raw_bytes, mime_type).  Falls back to the original file if
        Pillow is not available or the image cannot be processed.
        """
        raw = path.read_bytes()
        mime, _ = mimetypes.guess_type(str(path))
        mime = mime or "image/jpeg"

        if len(raw) <= max_bytes:
            return raw, mime

        try:
            from PIL import Image  # type: ignore[import-untyped]
        except ImportError:
            logger.warning("Pillow not installed — cannot compress oversized image ({})", path.name)
            return raw, mime

        img = Image.open(io.BytesIO(raw))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        # Strategy 1: reduce JPEG quality
        for quality in (85, 70, 55, 40, 25):
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            if buf.tell() <= max_bytes:
                logger.info(
                    "Compressed {} from {:.1f}MB to {:.1f}MB (quality={})",
                    path.name,
                    len(raw) / 1e6,
                    buf.tell() / 1e6,
                    quality,
                )
                return buf.getvalue(), "image/jpeg"

        # Strategy 2: also downscale resolution
        scale = 0.75
        while scale >= 0.2:
            new_size = (int(img.width * scale), int(img.height * scale))
            resized = img.resize(new_size, Image.LANCZOS)
            for quality in (70, 50, 30):
                buf = io.BytesIO()
                resized.save(buf, format="JPEG", quality=quality, optimize=True)
                if buf.tell() <= max_bytes:
                    logger.info(
                        "Compressed {} from {:.1f}MB to {:.1f}MB (scale={:.0%}, quality={})",
                        path.name,
                        len(raw) / 1e6,
                        buf.tell() / 1e6,
                        scale,
                        quality,
                    )
                    return buf.getvalue(), "image/jpeg"
            scale -= 0.15

        # Last resort: return whatever we got
        logger.warning("Could not compress {} below {}MB", path.name, max_bytes / 1e6)
        buf = io.BytesIO()
        img.resize((int(img.width * 0.2), int(img.height * 0.2)), Image.LANCZOS).save(
            buf, format="JPEG", quality=20, optimize=True
        )
        return buf.getvalue(), "image/jpeg"

    def _build_user_content(self, text: str, media: list[str] | None) -> str | list[dict[str, Any]]:
        """Build user message content with optional base64-encoded images."""
        if not media:
            return text

        images = []
        for path in media:
            p = Path(path)
            mime, _ = mimetypes.guess_type(path)
            if not p.is_file() or not mime or not mime.startswith("image/"):
                continue
            img_bytes, actual_mime = self._compress_image(p, self._MAX_IMAGE_BYTES)
            b64 = base64.b64encode(img_bytes).decode()
            images.append(
                {"type": "image_url", "image_url": {"url": f"data:{actual_mime};base64,{b64}"}}
            )

        if not images:
            return text
        return images + [{"type": "text", "text": text}]

    def add_tool_result(
        self,
        messages: list[dict[str, Any]],
        tool_call_id: str,
        tool_name: str,
        result: str,
    ) -> list[dict[str, Any]]:
        """Add a tool result to the message list."""
        messages.append(
            {"role": "tool", "tool_call_id": tool_call_id, "name": tool_name, "content": result}
        )
        return messages

    def add_assistant_message(
        self,
        messages: list[dict[str, Any]],
        content: str | None,
        tool_calls: list[dict[str, Any]] | None = None,
        reasoning_content: str | None = None,
    ) -> list[dict[str, Any]]:
        """Add an assistant message to the message list."""
        msg: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        if reasoning_content is not None:
            msg["reasoning_content"] = reasoning_content
        messages.append(msg)
        return messages
