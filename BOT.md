# BOT.md — TheOS Development Guide

This file is the unified instruction source for all AI coding tools.
`CLAUDE.md`, `GEMINI.md`, and `AGENTS.md` should all point to this file.

## Scope

`BOT.md` is for development routing and working rules.

- `README.md` is the external project document
- `BOT.md` is the developer and agent operating guide
- `docs/` contains internal module architecture and long-lived technical detail

Instruction-source mapping:

- Repo-root `BOT.md` is the canonical development guide for external coding CLIs.
- Repo-root `CLAUDE.md`, `GEMINI.md`, and `AGENTS.md` should remain symlinks to `BOT.md`.
- `theos agent` reads workspace bootstrap files, so `src/templates/AGENTS.md` is the bridge that points runtime agent sessions back to repo-local instructions.
- If you change durable development rules that `theos agent` should also follow, update both `BOT.md` and `src/templates/AGENTS.md`.

## Reading Order

For any non-trivial task:

1. Read `BOT.md` for rules and routing.
2. Read `docs/index.md` for the documentation map.
3. Read only the specific docs for the modules you touch.
4. Read source files before editing.

Do not bulk-load `docs/` unless the task genuinely spans multiple subsystems.

## Commands

```bash
# Install
make install

# Format and lint
make fmt
make lint

# Tests
make test
uv run pytest tests/test_genver.py
uv run pytest tests/test_loop_core.py
uv run pytest tests/agent/test_model_cmd.py -k test_model

# Schema drift
make schema-check
uv run python scripts/check_config_schema.py dump

# Changelog sync
make changelog

# Local run
uv run theos --help
uv run theos agent
uv run theos gateway
uv run theos init

# Push and auto-restart gateway
git pushr

# Frontend
cd ui && npm run dev
cd ui && npm run lint
cd ui && npm run test

# Bridge
cd bridge && npm run build
```

## Documentation Routing

`docs/` 分两类，职责不同：

```
docs/
├── modules/                    # 模块架构（长期维护，跟着代码变）
│   ├── agent-core/             # src/agent/, src/orchestrator/
│   ├── sessions-messaging/     # src/bus/, src/session/
│   ├── memory/                 # src/memory/, src/store/
│   ├── providers/              # src/providers/
│   ├── auth/                   # src/auth/
│   ├── security-safety/        # src/security/, src/safety/
│   ├── channels-integrations/  # src/channels/, src/feishu/, bridge/
│   ├── learning/               # src/dream/, src/hooks/, instinct/scripts/
│   ├── genver/                 # src/genver/
│   ├── automation/             # src/cron/, src/poller/, src/daemon/
│   ├── cli-composition/        # src/cli/, src/config/
│   ├── ui-observability/       # src/ui/, ui/, src/reporting/
│   └── shared-infra/           # src/utils/, src/templates/
└── dev/                        # 开发项（任务周期，完成后保留为记录）
    ├── specs/                  # 设计文档（做什么、为什么、怎么做）
    └── plans/                  # 实现计划（拆成的具体步骤）
```

**modules/** — 描述"系统是怎么工作的"。一个模块或子系统一个文件。当你修改模块的持久行为或内部契约时，在同一次提交中更新对应文档。这些文档不默认加载——只在重构、调试或升级对应模块时读取。

**dev/** — 描述"我要做什么改动"。每个开发项（新功能、重构、模块新建）在 `dev/specs/` 放设计文档，在 `dev/plans/` 放实现计划。文件名用 `YYYY-MM-DD-<topic>.md`。实现完成后文件保留作为决策记录，不需要主动清理。

不要把这两类文档混在一起。模块文档不应包含一次性的设计决策过程；开发项文档不应包含模块的长期架构说明。

**模块文档范式：** 创建或更新 `docs/modules/` 下的文档时，参照 `docs/modules/TEMPLATE.md` 的结构。一篇合格的模块文档至少要让读者快速回答：这块代码归谁管、从哪读起、一次请求怎么流过它、状态放哪、不能破坏什么约束、改完要验证什么。

**开发项文档范式：** 创建 `docs/dev/specs/` 或 `docs/dev/plans/` 下的文件时，分别参照 `docs/dev/specs/TEMPLATE.md` 和 `docs/dev/plans/TEMPLATE.md`。spec 负责定义目标、方案、边界和验收条件；plan 负责把已确认的方案拆成可独立验证的小任务。

## Change Records

- `CHANGELOG.md` is the rolling engineering log. Update it for durable changes to code, config schema, commands, tool behavior, architecture docs, or migration-relevant fixes.
- Do not use `CHANGELOG.md` for scratch work, abandoned attempts, or private local notes.
- `RELEASE.md` or release notes are for version cuts only: summarize user-facing highlights, breaking changes, migration steps, and known issues.
- Before closing a task with durable repo changes, ensure `CHANGELOG.md` is updated.
- Preferred path: run `make changelog` if commits already exist; otherwise edit `CHANGELOG.md` manually.

### README.md / BOT.md 同步检查

`README.md` 和 `BOT.md` 的职责不同，更新标准也不同：

- `README.md` 是对外入口文档，面向使用者与集成者。它回答“这个项目是什么、怎么安装、怎么运行、怎么使用”。
- `BOT.md` 是对内协作文档，面向 agent 与开发者。它回答“改这个仓库时要遵守什么规则、看哪些文档、按什么流程交付”。
- 模块级实现细节、架构说明、专题设计不应堆进这两个文件，仍由 `docs/` 下对应文档维护。

每次完成源码更新后，必须显式检查是否需要同步更新 `README.md` 或 `BOT.md`。如需更新，必须在同一次提交中一并修改；如无需更新，也必须在最终回复中明确写出判断结果：

- `README.md: updated` 或 `README.md: not needed — <一句话理由>`
- `BOT.md: updated` 或 `BOT.md: not needed — <一句话理由>`

**README.md 的更新标准：只在对外使用面发生变化时更新。**

需要更新的场景：
- 新增、移除或修改了用户可见的命令、CLI 子命令或主要使用方式
- 安装流程、依赖、启动方式或运行环境要求发生变化
- 新增、移除或调整了平台适配（channel adapter）或外部集成
- 项目对外能力描述需要调整（如新增模式、新的集成、行为变化）

通常不需要因为以下情况更新 `README.md`：
- 纯内部重构或实现优化
- 不改变外部接口的 bug 修复
- 仅影响模块内部实现、测试或内部文档的变更

**BOT.md 的更新标准：只在内部开发约束或协作流程发生变化时更新。**

需要更新的场景：
- 新增或修改了 `make` target、常用开发命令或标准验证流程
- 影响 agent 查阅路径的 `docs/` 结构变化（新增、移除或重组文档区域）
- 项目不变量发生变化（如新增自动化机制、hook、持久约束、默认流程）
- 新增或修改了需要所有 agent 统一遵守的开发约束、交付规则或验证规则

通常不需要因为以下情况更新 `BOT.md`：
- 仅有用户可见行为变化，但不影响开发流程或协作约束
- 普通 bug 修复，不改变开发命令、验证方式或 agent 规则
- `docs/` 内部内容更新，但不影响文档入口结构或查阅路径

不要把这一步当成可选清理项。它是每次源码变更后的收尾检查项之一。

### docs/modules 同步检查

`docs/modules/` 是源码架构说明，不是需求文档，也不是 changelog。它的职责是描述“这块代码现在怎么工作、边界在哪、改动时要注意什么”。

每次完成源码更新后，必须显式检查是否需要更新对应模块文档。如需更新，必须在同一次提交中一并修改；如无需更新，也必须在最终回复中明确写出判断结果：

- `docs/modules: updated — <模块名或范围>`
- `docs/modules: not needed — <一句话理由>`

**需要更新模块文档的场景：**
- 模块新增了持久行为、后台流程、维护任务、事件流或新的数据文件
- 模块的 data flow、state/persistence、invariants、failure modes、verification 发生变化
- 模块边界变化，职责从一个模块移动到另一个模块
- 关键入口、核心对象协作关系、或主要 extension point 发生变化

**通常不需要更新模块文档的场景：**
- 纯重命名、注释整理、局部实现优化，不改变模块行为或边界
- 不影响模块对外/对内契约的微小 bug 修复
- 临时调试代码、测试夹具或一次性开发文档变更

判断原则：
- 先按源码所属模块判断落点，不要按“这次改动想讲什么”来选文档
- 跨模块改动时，更新所有受影响的模块文档，不要只改一个
- 如果改动同时影响高层入口和具体模块：
  `README.md` / `BOT.md` 负责入口与规则，`docs/modules/` 负责源码架构与运行方式

## Development Workflow

不同规模的改动走不同的流程。判断标准是改动的复杂度和风险，不是代码行数。

### 小改动（bug 修复、单文件调整）

直接改。改完跑测试，通过后提交。不需要写 spec 或 plan。

### 中等改动（跨文件的功能修改、模块内重构）

1. **明确目标**：一句话说清楚要做什么、验收条件是什么。
2. **先写测试**：为要改的行为写一个失败的测试。
3. **实现**：写最少的代码让测试通过。
4. **验证**：跑受影响模块的测试，确认没有回归。
5. **提交**。

### 大改动（新模块、跨子系统重构、架构变更）

1. **设计文档**：在 `docs/dev/specs/` 写 spec，说明目标、方案、边界、验收条件。和用户对齐后再动手。
2. **实现计划**：在 `docs/dev/plans/` 拆成可独立验证的小任务。每个任务应该能在几分钟内完成并单独验证。
3. **逐步实现**：按计划逐个任务执行。每个任务遵循"写测试 → 实现 → 验证 → 提交"的节奏。
4. **Review 检查点**：关键步骤完成后做一次 review——对比 spec 检查是否偏离，对比代码检查是否有遗漏。
5. **收尾**：更新模块文档（如果改了持久行为），更新 CHANGELOG。

### 调试

不要猜。按这个顺序走：

1. **读错误信息**：完整读，不要只看第一行。
2. **稳定复现**：确认能可靠触发问题。
3. **定位根因**：追踪数据流，找到出错的那一层。对比正常工作的代码路径。
4. **单点假设**：一次只改一个变量。
5. **写测试覆盖**：为 root cause 写一个失败测试，再修复。
6. **验证修复**：跑完整个受影响范围的测试。

如果连续 3 次修复失败，停下来重新审视假设——可能是架构层面的问题，不是单点 bug。

### Review 纪律

收到任何 review 结果时：

1. 先验证事实，再决定是否接受。对比原始代码、读实际文件、跑测试。
2. 接受修改时必须说明理由。"reviewer 说的"不是理由。
3. 如果 review 结论有问题，明确指出哪里不对、为什么。
4. 不要翻来覆去——如果之前做了一个决定被推回来，重新评估一次，然后坚持或修正，不要反复摇摆。

## Working Rules

1. Act, don't stall — execute when the task is clear.
2. Read source before editing — inspect before changing.
3. Surface assumptions early — clarify ambiguity before proceeding.
4. Make the smallest change that solves the task — no speculative abstractions.
5. Match existing style; do not refactor unrelated code opportunistically.
6. Define a checkable goal — every task should have a verifiable success condition.
7. Prefer evidence over narrative — use tool output, not guesses.
8. Verify after action — read back results, run tests.
9. No unverified claims — check before stating.
10. Challenge on high-risk — surface risk before destructive operations.
11. When code and docs disagree, trust the source code, then fix the docs.
12. Do not load unrelated docs into context.
13. Prefer targeted verification over blanket test runs, but verify every behavior change.

## Code and Verification Rules

- Follow `STYLE.md` for formatting and code conventions.
- Run focused tests for the touched module when possible.
- For config schema changes, regenerate the schema baseline.
- For frontend changes, run the relevant `ui/` lint or test command when practical.
- Use mocks for external systems; do not mock core project logic without a reason.

### CI-Aware Verification

任何 agent 在开发时，都必须先识别当前仓库的本地验证入口和 CI 阻断项，不要默认把某个仓库的命令机械套用到别的 CLI 或 agent 仓库。

通用规则：

- commit 前，至少运行该仓库的格式化和静态检查命令。
- push 或开 PR 前，至少运行受影响范围内的测试，以及会在 CI 中阻断合并的专项检查。
- 如果仓库已经提供 `Makefile`、`justfile`、`task`、`npm scripts` 或其他统一入口，优先使用仓库定义的入口，不要自行发明替代命令。
- 如某项检查因环境限制、耗时或与本次改动无关而未执行，必须在最终回复中明确写出：跑了什么、没跑什么、为什么没跑。
- 不要用 `--no-verify`、广泛的 `# noqa`、关闭 lint rule 等方式绕过验证，除非有明确理由并在最终回复中说明。

### TheOS Verification Mapping

在 `TheOS` 中，上述通用规则落地为以下约束。

commit 前必须通过：

```bash
make fmt    # pre-commit hooks: black + ruff --fix + shfmt + trailing-whitespace
make lint   # ruff check（不自动修复，只报错）
```

如果 `make fmt` 修改了文件，必须将修改后的文件重新 stage 后再 commit。
如果 `make lint` 报错，必须修复后再 commit，不要用 `# noqa` 绕过，除非有明确理由。

push 或 PR 前，至少运行与改动范围匹配的最小充分检查：

```bash
uv run pytest tests/<touched_module> -q
```

以下场景追加对应检查：

- 涉及配置 schema、默认值或配置模型变更时，运行 `make schema-check`
- 涉及大范围后端行为、共享基础设施或无法准确收敛影响面时，运行 `make test`
- 涉及 `ui/` 前端时，运行 `cd ui && npm run lint`，必要时再跑 `cd ui && npm run test`

TheOS 的 CI 会在 push/PR 后运行 `ruff check` + `pytest` + `schema-check`。本地先过 `make fmt` + `make lint`，并补足与改动相关的测试和专项检查，可以避免绝大多数 CI 失败。

**pre-commit hook 安装：** 本仓库依赖 `.pre-commit-config.yaml` 管理格式化。首次 clone 后，或发现 `.git/hooks/pre-commit` 不存在时，必须运行：

```bash
make install   # 包含 uv run pre-commit install
```

这会注册 git pre-commit hook，使 `git commit` 时自动运行格式化检查。如果 hook 拒绝了 commit，修复报错后重新 stage 并 commit，不要跳过 hook（`--no-verify`）。

## Project-Specific Invariants

- The dashboard SQLite schema is owned by Python in `src/store/dashboard_writer.py`; keep UI-side data access read-oriented and do not duplicate schema creation outside the Python writer.
- `theos init` and startup flows sync workspace templates from `src/templates/`, so prompt/bootstrap changes should be made there, not only in repo docs.
- If you touch hooks or instinct automation, verify both the shell entrypoints in `hooks/` and the Node scripts they invoke.

## Tool-Specific Adaptations

This section stores the minimum runtime-specific differences.

- The `Claude Code` subsection should only be modified by Claude Code maintainers.
- The `Gemini CLI` subsection should only be modified by Gemini CLI maintainers.
- The `Codex` subsection should only be modified by Codex maintainers.
- The `theos agent` subsection should only be modified by theos agent maintainers.

### Claude Code

- Prefer dedicated tools (`Read`, `Edit`, `Grep`, `Glob`, `Write`) over shell equivalents.
- Use `Agent` for parallel sub-tasks and deep codebase exploration.

### Gemini CLI

- Tool names may differ from Claude Code; use the Gemini-specific mapping if provided.
- Use `activate_skill` for skill invocation.

### Codex

- Prefer Codex's patch or edit workflow over ad-hoc shell rewrites when modifying files.
- Keep Codex guidance tool-agnostic enough to survive runtime differences.

### theos agent

- Tools are registered via `register_standard_tools()` in `src/agent/tool_sets.py`.
- Tool implementations live in `src/agent/tools/` and extend the `Tool` base class.
- Tools declare `requires_context` or `accepts_context = True` to receive a `ToolContext`.

For architecture detail, start from `docs/index.md`, then read the matching module docs when they exist; if a module doc has not been written yet, inspect the source directly.
