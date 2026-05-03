# GenVer

> Module documentation, not a requirements doc or changelog.

## Purpose

- **Owns**: The generator-verifier loop, structured handoff protocol, phase pipeline orchestration, artifact persistence, workspace resolution for build-style tasks, and verifier-side repair.
- **Does Not Own**: LLM provider calls (`src/providers/`), tool implementations (`src/agent/tools/`), the agent tool loop (`src/agent/loop_core.py`), or configuration schema (`src/config/schema.py:GenVerConfig`).

## Source Scope

```
src/genver/
    __init__.py          — public API re-exports
    loop.py              — GenVerLoop: retry-based gen-ver engine
    pipeline.py          — GenVerPipeline: phased pipeline driver
    phases.py            — per-phase runner functions
    review.py            — bounded gen<->ver review protocol
    verifier.py          — Verifier: autonomous verification & repair agent
    handoff.py           — HandoffPayload dataclass + SubmitForReviewTool
    models.py            — Phase enum, ReviewVerdict, PhaseArtifact, etc.
    artifact_store.py    — ArtifactStore: file-based artifact/round/runtime storage
    runner.py            — prepare_genver_tools(): tool registry builder
    workspace.py         — resolve_task_workspace(): project subdirectory derivation
    prompts.py           — prompt templates for each phase (not documented here)
```

Adjacent but outside scope: `src/agent/loop_core.py` (shared tool loop), `src/agent/agentfs.py` (workspace filesystem), `src/agent/tool_sets.py` (tool registration).

## Entry Points

| Entry Point | Where | Purpose |
|---|---|---|
| `GenVerLoop.run()` | `loop.py:124` | Single-task gen-ver with retries (legacy/direct mode) |
| `GenVerPipeline.run()` | `pipeline.py:124` | Phased pipeline: CLARIFY->SPEC->PLAN->EXECUTE->REVIEW->REPORT |
| `prepare_genver_tools()` | `runner.py:23` | Build generator tool registry before a GenVer run |
| `resolve_task_workspace()` | `workspace.py:167` | Derive project subdirectory from user request |

Callers: `src/agent/loop.py` (AgentLoop) creates `GenVerLoop` or `GenVerPipeline` depending on config. `runner.py` is called from `AgentLoop._run_genver_loop`.

## Architecture

Two execution modes share internal components:

```
                     +-----------------+
                     | GenVerPipeline  |  (phased mode)
                     |  CLARIFY->SPEC  |
                     |  ->PLAN->EXEC   |
                     |  ->REVIEW->RPT  |
                     +--------+--------+
                              |
                              | EXECUTE phase delegates to:
                              v
                     +-----------------+
                     |   GenVerLoop    |  (retry-based mode)
                     |  gen->ver->retry|
                     +--------+--------+
                              |
              +---------------+---------------+
              |                               |
     +--------v--------+          +----------v---------+
     |   Generator      |          |     Verifier        |
     | (run_tool_loop)  |          | run_verification()  |
     | tools + handoff  |          | run_repair()        |
     +---------+--------+          +----------+----------+
               |                              |
               v                              v
     SubmitForReviewTool           parse_verifier_output()
     -> HandoffPayload             -> verdict JSON
```

**GenVerLoop** (`loop.py`): Runs generator with tools, extracts `HandoffPayload` from `submit_for_review` tool call, passes it to `Verifier.run_verification()`. On failure: attempt 1 feeds errors back to generator; attempt 2+ triggers `Verifier.run_repair()`. Optionally asks user for optimization guidance before the final round.

**GenVerPipeline** (`pipeline.py`): Drives a phase sequence. Complexity auto-selection (`classify_complexity`) determines which phases run. The EXECUTE phase creates a `GenVerLoop` in `pipeline_mode=True` (generator-only, no internal verification). The REVIEW phase creates a standalone `Verifier` and runs the `PhaseReviewProtocol`.

**PhaseReviewProtocol** (`review.py`): Bounded 3-step review: ver_review -> gen_review -> ver_final_review. Used by SPEC, PLAN, and REVIEW phases.

## Data Flow

### GenVerLoop (direct mode)

1. Caller builds messages with user request.
2. `GenVerLoop.run()` injects preamble, registers `submit_for_review` and `ask_user` tools.
3. Generator runs via `run_tool_loop()` with full tools; produces code and calls `submit_for_review`.
4. `_extract_handoff()` scans messages for the tool call, parses `HandoffPayload`.
5. `Verifier.run_verification()` builds a fresh context with handoff evidence, runs an autonomous verifier agent.
6. Verifier output is parsed as JSON (`parse_verifier_output`); tolerates markdown fences and partial JSON.
7. If passed: `finalize_success_response()` builds user-facing summary. If failed: feedback or repair cycle.
8. Returns `(content, tools_used, messages, usage)`.

### GenVerPipeline (phased mode)

1. `classify_complexity()` selects phases (trivial/small/medium/large).
2. Each phase runner (`phases.py`) calls `run_tool_loop()` with phase-specific prompts.
3. SPEC and PLAN use `_write_and_review()` which runs gen_write then `PhaseReviewProtocol`.
4. EXECUTE delegates to `GenVerLoop` with `pipeline_mode=True`, injecting spec/plan context.
5. REVIEW creates a `Verifier`, runs verification, optional repair, then `PhaseReviewProtocol`.
6. REPORT generates a summary from phase artifacts and review history.
7. Final output read from `ArtifactStore.read_artifact("report.md")`.

## State & Persistence

**ArtifactStore** (`artifact_store.py`) manages `<workspace>/<genver_subdir>/`:

| Directory | Contents | Lifecycle |
|---|---|---|
| `artifacts/` | `spec.md`, `plan.md`, `review.md`, `report.md` | Persist across runs |
| `artifacts/rounds/` | Per-step JSON records (e.g. `spec_gen_write.json`) | Persist across runs |
| `runtime/` | `verify_report_N.json`, `verifier_repair_N.json` | Cleared each `pipeline.run()` or `loop.run()` |

**HandoffPayload** (`handoff.py`): In-memory dataclass. Not persisted directly; serialized into round records by EXECUTE phase (`execute_handoff` round).

**Verifier reports**: Written via `AgentFS.write()` as JSON under `runtime/`.

## Invariants

1. **Generator context is continuous** across retries in `GenVerLoop`; verifier always gets a fresh context (`loop.py:132-133`).
2. **HandoffPayload requires** `intent_summary`, `files_changed`, and `risk_assessment` (`handoff.py:57-98`).
3. **Verifier treats generator metadata as advisory only** -- it judges from user request, actual files, and tool results (`handoff.py:34-35`).
4. **Runtime data is cleared** at the start of each pipeline or loop run; artifacts and rounds persist.
5. **Phase pipeline aborts** if REVIEW returns `abort` status (`pipeline.py:271-273`).
6. **Provider errors abort immediately** without retry (both generator and verifier sides: `loop.py:282-290`, `loop.py:387-395`).
7. **Complexity classification** is a preflight decision; CLARIFY phase no longer rewrites the active phase list at runtime (`pipeline.py:143-153`).

## Extension Points

- **New phase**: Add to `Phase` enum (`models.py:12`), implement runner in `phases.py`, wire in `GenVerPipeline.run()`.
- **Custom verifier commands**: Configure via `GenVerConfig.verifier_commands` list.
- **Different models per role**: Set `generator_model`, `verifier_model`, `explorer_model` in config. Provider dispatch via `_make_provider_for_model()` (`loop.py:37-48`).
- **New handoff fields**: Extend `HandoffPayload` dataclass (`handoff.py:13`) and `HANDOFF_TOOL` schema.
- **Workspace resolution**: Modify `should_use_project_subdir()` / `derive_project_slug()` in `workspace.py`.

## Failure Modes

| Failure | Behavior | Location |
|---|---|---|
| Generator never calls `submit_for_review` | Retry with gate message; abort after `max_retries` | `loop.py:313-334` |
| Verifier provider error | Abort immediately, no retry | `loop.py:387-395` |
| Verifier JSON parse failure | Falls back to raw content as single error | `verifier.py:528-558` |
| All retries exhausted | Return error summary to user | `loop.py:506-512` |
| Job timeout in per-phase runner | `run_tool_loop` has `max_iterations` cap | `phases.py:46-53` |
| REVIEW abort verdict | Pipeline stops before REPORT | `pipeline.py:271-273` |

## Verification

```bash
uv run pytest tests/test_genver.py tests/test_genver_models.py tests/test_genver_phases.py tests/test_genver_pipeline.py tests/test_genver_review.py tests/test_review_fixes.py -q
```

## Related Files

- `src/agent/loop_core.py` -- shared `run_tool_loop()` used by both generator and verifier
- `src/agent/agentfs.py` -- `AgentFS` workspace filesystem abstraction
- `src/agent/tool_sets.py` -- `register_standard_tools()` for tool registry setup
- `src/config/schema.py` -- `GenVerConfig` dataclass (config owner)
- `src/agent/tools/ask_user.py` -- `AskUserTool` registered by GenVerLoop for clarification
- `src/agent/tools/explore.py` -- `ExploreTool` registered by `prepare_genver_tools()`
- `docs/modules/agent-core/` -- agent loop and tool system architecture
