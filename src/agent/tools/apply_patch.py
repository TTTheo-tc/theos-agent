"""Apply-patch tool — atomic multi-file patch operations.

Patch format (modelled after OpenClaw's apply_patch):

    *** Begin Patch
    *** Add File: path/to/new_file.py
    +line 1
    +line 2
    *** Delete File: path/to/old_file.py
    *** Update File: path/to/existing.py
    @@ optional context line
    -old line
    +new line
     context line (unchanged)
    *** End Patch

Update hunks use ``-`` for removed lines, ``+`` for added lines, and
`` `` (space) for context lines.  An ``@@ <text>`` marker can precede
each chunk to anchor the search.  ``*** End of File`` anchors a chunk
to the file tail.  ``*** Move to: <path>`` after an Update header
renames the file.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.agent.tools.base import Tool
from src.agent.tools.tool_security import policy_error
from src.utils.path import resolve_path as _resolve_path

# ---------------------------------------------------------------------------
# Patch markers
# ---------------------------------------------------------------------------

BEGIN_PATCH = "*** Begin Patch"
END_PATCH = "*** End Patch"
ADD_FILE = "*** Add File: "
DELETE_FILE = "*** Delete File: "
UPDATE_FILE = "*** Update File: "
MOVE_TO = "*** Move to: "
EOF_MARKER = "*** End of File"
CONTEXT_MARKER = "@@ "
EMPTY_CONTEXT_MARKER = "@@"

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class AddHunk:
    kind: str = "add"
    path: str = ""
    contents: str = ""


@dataclass
class DeleteHunk:
    kind: str = "delete"
    path: str = ""


@dataclass
class UpdateChunk:
    context: str | None = None
    old_lines: list[str] = field(default_factory=list)
    new_lines: list[str] = field(default_factory=list)
    is_eof: bool = False


@dataclass
class UpdateHunk:
    kind: str = "update"
    path: str = ""
    move_path: str | None = None
    chunks: list[UpdateChunk] = field(default_factory=list)


Hunk = AddHunk | DeleteHunk | UpdateHunk


# ---------------------------------------------------------------------------
# Patch parser
# ---------------------------------------------------------------------------


class PatchParseError(ValueError):
    """Raised when the patch text is malformed."""


def parse_patch(text: str) -> list[Hunk]:
    """Parse a ``*** Begin Patch … *** End Patch`` block into hunks."""
    text = text.strip()
    if not text:
        raise PatchParseError("Patch input is empty.")

    lines = text.splitlines()
    lines = _strip_heredoc(lines)

    if lines[0].strip() != BEGIN_PATCH:
        raise PatchParseError(f"First line must be '{BEGIN_PATCH}', got: {lines[0]!r}")
    if lines[-1].strip() != END_PATCH:
        raise PatchParseError(f"Last line must be '{END_PATCH}', got: {lines[-1]!r}")

    body = lines[1:-1]
    hunks: list[Hunk] = []
    pos = 0
    while pos < len(body):
        hunk, consumed = _parse_one_hunk(body, pos)
        hunks.append(hunk)
        pos += consumed

    if not hunks:
        raise PatchParseError("No file operations found in patch.")

    return hunks


def _strip_heredoc(lines: list[str]) -> list[str]:
    """Strip optional heredoc wrapper (<<EOF … EOF)."""
    if len(lines) < 4:
        return lines
    first, last = lines[0].strip(), lines[-1].strip()
    if first in ("<<EOF", "<<'EOF'", '<<"EOF"') and last == "EOF":
        return lines[1:-1]
    return lines


def _parse_one_hunk(lines: list[str], offset: int) -> tuple[Hunk, int]:
    """Parse a single hunk starting at *offset*. Returns (hunk, lines_consumed)."""
    line = lines[offset].strip()

    # --- Add File ---
    if line.startswith(ADD_FILE):
        path = line[len(ADD_FILE) :]
        contents_parts: list[str] = []
        consumed = 1
        for add_line in lines[offset + 1 :]:
            if add_line.startswith("+"):
                contents_parts.append(add_line[1:])
                consumed += 1
            else:
                break
        contents = "\n".join(contents_parts)
        if contents_parts:
            contents += "\n"
        return AddHunk(path=path, contents=contents), consumed

    # --- Delete File ---
    if line.startswith(DELETE_FILE):
        path = line[len(DELETE_FILE) :]
        return DeleteHunk(path=path), 1

    # --- Update File ---
    if line.startswith(UPDATE_FILE):
        path = line[len(UPDATE_FILE) :]
        consumed = 1
        remaining = lines[offset + 1 :]

        # Optional move
        move_path: str | None = None
        if remaining and remaining[0].strip().startswith(MOVE_TO):
            move_path = remaining[0].strip()[len(MOVE_TO) :]
            remaining = remaining[1:]
            consumed += 1

        chunks: list[UpdateChunk] = []
        idx = 0
        while idx < len(remaining):
            # Skip blank lines between chunks
            if remaining[idx].strip() == "":
                idx += 1
                consumed += 1
                continue
            # Stop at next hunk header
            if remaining[idx].startswith("***"):
                break
            chunk, chunk_consumed = _parse_update_chunk(
                remaining, idx, allow_missing_context=(len(chunks) == 0)
            )
            chunks.append(chunk)
            idx += chunk_consumed
            consumed += chunk_consumed

        if not chunks:
            raise PatchParseError(f"Update hunk for '{path}' has no chunks.")

        return UpdateHunk(path=path, move_path=move_path, chunks=chunks), consumed

    raise PatchParseError(
        f"Unexpected hunk header: {lines[offset]!r}. "
        f"Expected '*** Add File:', '*** Delete File:', or '*** Update File:'."
    )


def _parse_update_chunk(
    lines: list[str], offset: int, *, allow_missing_context: bool
) -> tuple[UpdateChunk, int]:
    """Parse one update chunk (context marker + diff lines)."""
    context: str | None = None
    start = 0

    first = lines[offset]
    if first == EMPTY_CONTEXT_MARKER:
        start = 1
    elif first.startswith(CONTEXT_MARKER):
        context = first[len(CONTEXT_MARKER) :]
        start = 1
    elif not allow_missing_context:
        raise PatchParseError(f"Expected '@@ ' context marker, got: {first!r}")

    chunk = UpdateChunk(context=context)
    parsed = 0
    for raw_line in lines[offset + start :]:
        if raw_line == EOF_MARKER:
            if parsed == 0:
                raise PatchParseError("Empty update chunk before '*** End of File'.")
            chunk.is_eof = True
            parsed += 1
            break

        marker = raw_line[0] if raw_line else ""

        if marker == " ":
            content = raw_line[1:]
            chunk.old_lines.append(content)
            chunk.new_lines.append(content)
            parsed += 1
        elif marker == "+":
            chunk.new_lines.append(raw_line[1:])
            parsed += 1
        elif marker == "-":
            chunk.old_lines.append(raw_line[1:])
            parsed += 1
        elif marker == "":
            # Empty line = empty context line
            chunk.old_lines.append("")
            chunk.new_lines.append("")
            parsed += 1
        else:
            # Not a diff line — stop this chunk
            if parsed == 0:
                raise PatchParseError(
                    f"Unexpected line in update chunk: {raw_line!r}. "
                    "Lines must start with ' ', '+', or '-'."
                )
            break

    if parsed == 0:
        raise PatchParseError("Update chunk contains no diff lines.")

    return chunk, start + parsed


# ---------------------------------------------------------------------------
# Hunk application (update files)
# ---------------------------------------------------------------------------

_UNICODE_DASH = re.compile(r"[\u2010-\u2015\u2212]")
_UNICODE_SQUOTE = re.compile(r"[\u2018\u2019\u201a\u201b]")
_UNICODE_DQUOTE = re.compile(r"[\u201c-\u201f]")
_UNICODE_SPACE = re.compile(r"[\u00a0\u2002-\u200a\u202f\u205f\u3000]")


def _normalize_punct(s: str) -> str:
    s = _UNICODE_DASH.sub("-", s)
    s = _UNICODE_SQUOTE.sub("'", s)
    s = _UNICODE_DQUOTE.sub('"', s)
    s = _UNICODE_SPACE.sub(" ", s)
    return s


def _seek_sequence(
    lines: list[str],
    pattern: list[str],
    start: int,
    eof: bool,
) -> int | None:
    """Find *pattern* in *lines* starting from *start*.

    Tries exact match, then trimEnd, then trim, then normalized punctuation.
    If *eof* is True, search starts from the end of the file.
    """
    if not pattern:
        return start
    if len(pattern) > len(lines):
        return None

    max_start = len(lines) - len(pattern)
    search_start = max_start if eof and len(lines) >= len(pattern) else start
    if search_start > max_start:
        return None

    # Cascade of increasingly fuzzy matchers
    normalizers: list[Any] = [
        lambda s: s,
        lambda s: s.rstrip(),
        lambda s: s.strip(),
        lambda s: _normalize_punct(s.strip()),
    ]
    for norm in normalizers:
        for i in range(search_start, max_start + 1):
            if all(norm(lines[i + j]) == norm(pattern[j]) for j in range(len(pattern))):
                return i
    return None


def _seek_context(
    lines: list[str],
    context: str,
    start: int,
) -> int | None:
    """Find a context anchor line.

    First tries exact sequence match (like OpenClaw), then falls back to
    substring/startswith matching for robustness.
    """
    # Exact match via _seek_sequence
    idx = _seek_sequence(lines, [context], start, False)
    if idx is not None:
        return idx

    # Fallback: line starts with or contains the context string
    ctx_stripped = context.strip()
    for i in range(start, len(lines)):
        line_stripped = lines[i].strip()
        if line_stripped.startswith(ctx_stripped) or ctx_stripped in line_stripped:
            return i
    return None


def apply_update_hunk(file_content: str, chunks: list[UpdateChunk]) -> str:
    """Apply update chunks to file content. Returns new content."""
    original_lines = file_content.split("\n")
    # Remove trailing empty line (split artifact for files ending with \n)
    if original_lines and original_lines[-1] == "":
        original_lines.pop()

    replacements: list[tuple[int, int, list[str]]] = []
    line_idx = 0

    for chunk in chunks:
        # Seek to context anchor
        if chunk.context is not None:
            ctx_idx = _seek_context(original_lines, chunk.context, line_idx)
            if ctx_idx is None:
                raise PatchApplyError(f"Failed to find context '{chunk.context}'")
            line_idx = ctx_idx + 1

        # Pure insertion (no old lines)
        if not chunk.old_lines:
            insert_at = (
                len(original_lines) - 1
                if original_lines and original_lines[-1] == ""
                else len(original_lines)
            )
            replacements.append((insert_at, 0, chunk.new_lines))
            continue

        # Find old lines
        pattern = chunk.old_lines
        new_slice = chunk.new_lines

        found = _seek_sequence(original_lines, pattern, line_idx, chunk.is_eof)

        # Retry without trailing empty line
        if found is None and pattern and pattern[-1] == "":
            pattern = pattern[:-1]
            if new_slice and new_slice[-1] == "":
                new_slice = new_slice[:-1]
            found = _seek_sequence(original_lines, pattern, line_idx, chunk.is_eof)

        if found is None:
            preview = "\n".join(chunk.old_lines[:5])
            raise PatchApplyError(f"Failed to find expected lines:\n{preview}")

        replacements.append((found, len(pattern), new_slice))
        line_idx = found + len(pattern)

    # Sort and apply in reverse order
    replacements.sort(key=lambda r: r[0])
    new_lines = list(original_lines)
    for start, old_len, new in reversed(replacements):
        new_lines[start : start + old_len] = new

    # Ensure trailing newline
    if not new_lines or new_lines[-1] != "":
        new_lines.append("")

    return "\n".join(new_lines)


class PatchApplyError(RuntimeError):
    """Raised when a patch cannot be applied to the target file."""


# ---------------------------------------------------------------------------
# Atomic file operations with rollback
# ---------------------------------------------------------------------------


@dataclass
class _FileBackup:
    """Tracks one file's original state for rollback."""

    path: Path
    existed: bool
    backup_path: Path | None = None  # temp copy of original content


class _AtomicPatchSession:
    """Manages atomic multi-file operations with rollback on failure."""

    def __init__(
        self,
        workspace: Path | None,
        allowed_dir: Path | None,
    ) -> None:
        self._workspace = workspace
        self._allowed_dir = allowed_dir
        self._backups: list[_FileBackup] = []
        self._created_dirs: list[Path] = []
        self._tmpdir: str | None = None

    def _resolve(self, raw_path: str) -> Path:
        """Resolve and security-check a path."""
        err = policy_error(raw_path, kind="Patch")
        if err:
            raise PermissionError(err)
        fp = _resolve_path(raw_path, self._workspace, self._allowed_dir)
        err2 = policy_error(str(fp), kind="Patch")
        if err2:
            raise PermissionError(err2)
        return fp

    def _ensure_tmpdir(self) -> str:
        if self._tmpdir is None:
            self._tmpdir = tempfile.mkdtemp(prefix="theos_patch_")
        return self._tmpdir

    def _backup_file(self, fp: Path) -> None:
        """Save a backup of *fp* before modifying it."""
        existed = fp.exists()
        backup_path: Path | None = None
        if existed:
            tmp = self._ensure_tmpdir()
            backup_path = Path(tmp) / fp.name
            # Handle name collisions in tmpdir
            counter = 0
            while backup_path.exists():
                counter += 1
                backup_path = Path(tmp) / f"{fp.stem}_{counter}{fp.suffix}"
            shutil.copy2(str(fp), str(backup_path))
        self._backups.append(_FileBackup(path=fp, existed=existed, backup_path=backup_path))

    def _ensure_parent(self, fp: Path) -> None:
        """Create parent directories, tracking newly created ones for rollback."""
        parent = fp.parent
        if parent.exists():
            return
        # Walk up to find the first existing ancestor
        to_create: list[Path] = []
        p = parent
        while not p.exists():
            to_create.append(p)
            p = p.parent
        for d in reversed(to_create):
            d.mkdir(parents=False, exist_ok=True)
            self._created_dirs.append(d)

    def rollback(self) -> None:
        """Undo all file operations performed in this session."""
        # Restore files in reverse order
        for backup in reversed(self._backups):
            try:
                if backup.existed and backup.backup_path:
                    shutil.copy2(str(backup.backup_path), str(backup.path))
                elif not backup.existed and backup.path.exists():
                    backup.path.unlink()
            except OSError:
                pass  # Best-effort rollback

        # Remove created directories in reverse order
        for d in reversed(self._created_dirs):
            try:
                if d.exists() and not any(d.iterdir()):
                    d.rmdir()
            except OSError:
                pass

        self._cleanup_tmp()

    def commit(self) -> None:
        """Discard backups (patch succeeded)."""
        self._cleanup_tmp()

    def _cleanup_tmp(self) -> None:
        if self._tmpdir:
            shutil.rmtree(self._tmpdir, ignore_errors=True)
            self._tmpdir = None

    # --- High-level operations ---

    def add_file(self, raw_path: str, contents: str) -> Path:
        fp = self._resolve(raw_path)
        if fp.exists():
            raise FileExistsError(f"Cannot add file (already exists): {raw_path}")
        self._backup_file(fp)
        self._ensure_parent(fp)
        fp.write_text(contents, encoding="utf-8")
        return fp

    def delete_file(self, raw_path: str) -> Path:
        fp = self._resolve(raw_path)
        if not fp.exists():
            raise FileNotFoundError(f"Cannot delete file (not found): {raw_path}")
        self._backup_file(fp)
        fp.unlink()
        return fp

    def update_file(self, raw_path: str, chunks: list[UpdateChunk]) -> Path:
        fp = self._resolve(raw_path)
        if not fp.exists():
            raise FileNotFoundError(f"Cannot update file (not found): {raw_path}")
        self._backup_file(fp)
        content = fp.read_text(encoding="utf-8")
        new_content = apply_update_hunk(content, chunks)
        fp.write_text(new_content, encoding="utf-8")
        return fp

    def move_file(
        self, raw_path: str, move_path: str, chunks: list[UpdateChunk]
    ) -> tuple[Path, Path]:
        src = self._resolve(raw_path)
        dst = self._resolve(move_path)
        if not src.exists():
            raise FileNotFoundError(f"Cannot update file (not found): {raw_path}")
        self._backup_file(src)
        content = src.read_text(encoding="utf-8")
        new_content = apply_update_hunk(content, chunks)
        self._ensure_parent(dst)
        self._backup_file(dst)
        dst.write_text(new_content, encoding="utf-8")
        src.unlink()
        return src, dst


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------


class ApplyPatchTool(Tool):
    """Apply a multi-file patch atomically."""

    def __init__(
        self,
        workspace: Path | None = None,
        allowed_dir: Path | None = None,
    ) -> None:
        self._workspace = workspace
        self._allowed_dir = allowed_dir

    @property
    def name(self) -> str:
        return "apply_patch"

    @property
    def description(self) -> str:
        return (
            "Apply a patch to create, delete, rename, or modify multiple files "
            "atomically. Uses the *** Begin Patch / *** End Patch format. "
            "All operations succeed or all are rolled back."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "patch": {
                    "type": "string",
                    "description": (
                        "Patch content using the *** Begin Patch / *** End Patch format. "
                        "Supports: *** Add File, *** Delete File, *** Update File, "
                        "*** Move to."
                    ),
                },
            },
            "required": ["patch"],
        }

    @property
    def risk_level(self) -> str:
        return "medium"

    async def execute(self, patch: str = "", **kwargs: Any) -> str:
        if not patch.strip():
            return "Error: patch content is required."

        # Parse
        try:
            hunks = parse_patch(patch)
        except PatchParseError as e:
            return f"Error parsing patch: {e}"

        # Apply atomically
        session = _AtomicPatchSession(self._workspace, self._allowed_dir)
        added: list[str] = []
        modified: list[str] = []
        deleted: list[str] = []

        try:
            for hunk in hunks:
                if isinstance(hunk, AddHunk):
                    fp = session.add_file(hunk.path, hunk.contents)
                    added.append(_display(fp, self._workspace))

                elif isinstance(hunk, DeleteHunk):
                    fp = session.delete_file(hunk.path)
                    deleted.append(_display(fp, self._workspace))

                elif isinstance(hunk, UpdateHunk):
                    if hunk.move_path:
                        src, dst = session.move_file(hunk.path, hunk.move_path, hunk.chunks)
                        modified.append(_display(dst, self._workspace))
                    else:
                        fp = session.update_file(hunk.path, hunk.chunks)
                        modified.append(_display(fp, self._workspace))

            session.commit()
        except (PatchApplyError, PatchParseError) as e:
            session.rollback()
            return f"Error applying patch (rolled back): {e}"
        except (FileNotFoundError, FileExistsError, PermissionError) as e:
            session.rollback()
            return f"Error applying patch (rolled back): {e}"
        except Exception as e:
            session.rollback()
            return f"Error applying patch (rolled back): {e}"

        return _format_summary(added, modified, deleted)


def _display(fp: Path, workspace: Path | None) -> str:
    """Return a display-friendly path relative to workspace."""
    if workspace:
        try:
            return str(fp.relative_to(workspace.resolve()))
        except ValueError:
            pass
    return str(fp)


def _format_summary(added: list[str], modified: list[str], deleted: list[str]) -> str:
    lines = ["Patch applied successfully."]
    for f in added:
        lines.append(f"  A {f}")
    for f in modified:
        lines.append(f"  M {f}")
    for f in deleted:
        lines.append(f"  D {f}")
    return "\n".join(lines)
