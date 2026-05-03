# Shared Infrastructure

> Module documentation is not a requirements doc or a changelog.

## Purpose

- **Owns**: Cross-cutting utility functions (path resolution, text processing, tokenization, truncation, usage tracking), workspace template sync, and bootstrap template files.
- **Does Not Own**: Memory logic (`src/memory/`), session management (`src/session/`), agent tools (`src/agent/tools/`), or configuration schema (`src/config/`). These modules consume the utilities but are not part of shared infrastructure.

## Source Scope

```
src/utils/
  helpers.py           # ensure_dir, get_data_path, get_workspace_path, safe_filename, sync_workspace_templates
  path.py              # resolve_path with workspace-relative resolution and directory restriction
  text.py              # strip_think, tool_hint, split_message
  truncation.py        # truncate_tool_call_arguments for session history
  tokenize.py          # tokenize_query — bilingual (EN+CJK) text tokenizer for memory search
  usage.py             # merge_usage — accumulate LLM token usage counters

src/templates/
  SOUL.md              # Default personality bootstrap (synced to workspace)
  IDENTITY.md          # Identity definition template
  HEARTBEAT.md         # Heartbeat prompt template
  memory/
    MEMORY.md          # Long-term memory bootstrap template
```

## Entry Points

These are library functions, not commands. Primary callers:

| Function | Callers | File:line |
|---|---|---|
| `sync_workspace_templates()` | `theos init`, `theos agent`, `theos gateway` | `src/utils/helpers.py:32` |
| `resolve_path()` | Tool implementations (exec, write_file, edit_file) | `src/utils/path.py:6` |
| `tokenize_query()` | Memory search (structured FTS queries) | `src/utils/tokenize.py:63` |
| `truncate_tool_call_arguments()` | Session manager (history compaction) | `src/utils/truncation.py:20` |
| `strip_think()` | Agent loop (remove model thinking blocks from output) | `src/utils/text.py:9` |
| `merge_usage()` | Agent loop, GenVer pipeline (token accounting) | `src/utils/usage.py:12` |

## Architecture

This module is a flat collection of pure functions and one side-effecting function (`sync_workspace_templates`). There are no classes, no state, and no dependencies on the agent or session subsystems. The dependency direction is strictly one-way: other modules import from `src/utils/`, never the reverse.

### helpers.py

- `ensure_dir(path)` -- `mkdir -p` wrapper, returns the path (`helpers.py:7-10`).
- `get_data_path()` -- resolves `~/.theos`, ensures it exists (`helpers.py:13-15`).
- `get_workspace_path(workspace)` -- resolves and ensures workspace dir; defaults to `~/.theos/workspace` (`helpers.py:18-21`).
- `safe_filename(name)` -- replaces `<>:"/\|?*` with underscores (`helpers.py:27-29`).
- `sync_workspace_templates(workspace)` -- copies missing bootstrap files from `src/templates/` into the workspace. Only creates files that do not already exist. Also creates `skills/` and `reference/` directories (`helpers.py:32-70`).

### path.py

- `resolve_path(path, workspace, allowed_dir)` -- resolves relative paths against workspace, then enforces directory restriction via `Path.relative_to()`. Raises `PermissionError` on path traversal (`path.py:6-17`).

### text.py

- `strip_think(text)` -- removes `<think>...</think>` blocks some models produce (`text.py:9-13`).
- `tool_hint(tool_calls)` -- formats tool calls as concise strings like `web_search("query")` for progress display (`text.py:16-25`).
- `split_message(content, max_len)` -- splits long messages at line/word boundaries, respecting a max length (default 4000 chars). Used by channel adapters to stay within platform message limits (`text.py:28-47`).

### truncation.py

- `truncate_tool_call_arguments(tool_calls, max_chars)` -- creates a copy of tool call dicts with oversized function arguments truncated. Preserves valid JSON by parsing arguments, truncating individual string values at 200 chars, and re-serializing. Falls back to a `_note` placeholder for unparseable arguments (`truncation.py:20-63`).

### tokenize.py

- `tokenize_query(text)` -- bilingual tokenizer for memory FTS queries. Extracts ASCII terms (alphanumeric with `./_/-` separators) and CJK bigram/trigram tokens. Filters English stopwords (20 terms) and Chinese stopwords (15 terms). Returns deduplicated, lowercased tokens (`tokenize.py:63-98`).
- Extracted from `src/memory/structured_models` to break an import cycle between session and memory subsystems (`tokenize.py:4-5`).

### usage.py

- `merge_usage(target, source)` -- accumulates five LLM usage counters (`prompt_tokens`, `completion_tokens`, `total_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`) from source into target dict (`usage.py:12-17`).

## Data Flow

```
src/templates/ ──sync_workspace_templates()──> ~/.theos/workspace/
                                                  SOUL.md
                                                  IDENTITY.md
                                                  HEARTBEAT.md
                                                  memory/MEMORY.md
                                                  memory/HISTORY.md (empty)
                                                  skills/
                                                  reference/

User message ──tokenize_query()──> [token, token, ...] ──> FTS query

Tool call args ──truncate_tool_call_arguments()──> truncated copy ──> session history

Model output ──strip_think()──> clean text (no <think> blocks)

File path ──resolve_path(workspace, allowed_dir)──> resolved Path or PermissionError
```

## State & Persistence

| State | Location | Owner |
|---|---|---|
| Workspace bootstrap files | `~/.theos/workspace/{SOUL,IDENTITY,HEARTBEAT,memory/MEMORY}.md` | `sync_workspace_templates()` creates; user/agent edits |
| Template sources | `src/templates/` | Packaged with the application, read-only at runtime |

All other functions in this module are stateless.

## Invariants

1. `sync_workspace_templates()` never overwrites existing files -- it only creates missing ones (`helpers.py:50-51`).
2. `resolve_path()` enforces `allowed_dir` via `Path.relative_to()` -- any path outside raises `PermissionError` (`path.py:15-16`).
3. `truncate_tool_call_arguments()` always returns valid JSON -- it parses, truncates values, and re-serializes rather than slicing raw strings (`truncation.py:53-55`).
4. `tokenize_query()` is deterministic for the same input -- deduplication uses insertion order via a `seen` set (`tokenize.py:69-70`).
5. `merge_usage()` is additive-only -- it never resets counters, only accumulates (`usage.py:16-17`).
6. Template files synced are a fixed set: `AGENTS.md`, `SOUL.md`, `IDENTITY.md`, `HEARTBEAT.md`, `memory/MEMORY.md` (`helpers.py:56`). Note: `AGENTS.md` is in the set but no corresponding file exists in `src/templates/`; it would be created only if present in the package.

## Extension Points

- **New template**: Add a file to `src/templates/`, then add its name to the `bootstrap_templates` set or add a `_write()` call in `sync_workspace_templates()` (`helpers.py:56-61`).
- **New utility function**: Add to the appropriate file by category, or create a new `src/utils/*.py` file for a distinct concern.
- **New tokenizer language**: Extend `tokenize_query()` with additional regex blocks and stopword sets.
- **New usage counter**: Add the key name to `_USAGE_KEYS` in `usage.py:3-9`.

## Failure Modes

| Failure | Behavior |
|---|---|
| Template package data not found | `sync_workspace_templates()` logs warning, returns empty list (`helpers.py:39-45`) |
| Path outside allowed_dir | `resolve_path()` raises `PermissionError` with descriptive message (`path.py:16`) |
| Workspace dir not writable | `ensure_dir()` propagates `PermissionError` from `Path.mkdir()` |
| Malformed JSON in tool call arguments | `truncate_tool_call_arguments()` replaces with `{"_note": "tool arguments too large to include"}` (`truncation.py:57-58`) |

## Verification

```bash
uv run pytest tests/test_compaction.py tests/test_token_budget.py -q
```

## Related Files

- `src/config/loader.py` -- calls `get_data_path()` via `get_data_dir()`
- `src/cli/init_cmd.py` -- calls `sync_workspace_templates()` during init
- `src/cli/agent_cmd.py` -- calls `sync_workspace_templates()` at agent start
- `src/cli/gateway_cmd.py` -- calls `sync_workspace_templates()` at gateway start
- `src/memory/structured_models.py` -- original home of `tokenize_query()` before extraction
- `src/session/manager.py` -- uses `truncate_tool_call_arguments()` for history compaction
- `src/agent/loop.py` -- uses `strip_think()`, `merge_usage()`, `tool_hint()`
- `src/agent/tools/` -- tools use `resolve_path()` for file access enforcement
