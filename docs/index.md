# Documentation

## Structure

```
docs/
├── modules/                    # 模块架构（长期维护）
│   ├── agent-core/             # Agent loop, tools, delegation, orchestrator
│   ├── sessions-messaging/     # Message bus, session persistence, group dispatch
│   ├── memory/                 # Multi-tier memory, FTS, knowledge graph, store
│   ├── providers/              # LLM provider abstraction, streaming, recovery
│   ├── auth/                   # OAuth plugin framework, token lifecycle
│   ├── security-safety/        # Encryption, keychain, injection detection, leak scan
│   ├── channels-integrations/  # 10 platform adapters, Feishu deep integration, bridge
│   ├── learning/               # Dream exploration, instinct (reflect/evolve/reflex), hooks
│   ├── genver/                 # Generator-Verifier loop
│   ├── automation/             # Cron scheduler, poller, daemon (systemd/launchd)
│   ├── cli-composition/        # Typer CLI, config schema and loading
│   ├── ui-observability/       # Dashboard backend (Starlette), React frontend, reporting
│   └── shared-infra/           # Utilities, templates, path/text/token helpers
└── dev/                        # 开发项（任务周期）
    ├── specs/                  # 设计文档
    └── plans/                  # 实现计划
```

## Source Code Mapping

| Module | Source Directories |
|--------|--------------------|
| agent-core | `src/agent/`, `src/orchestrator/` |
| sessions-messaging | `src/bus/`, `src/session/` |
| memory | `src/memory/`, `src/store/` |
| providers | `src/providers/` |
| auth | `src/auth/` |
| security-safety | `src/security/`, `src/safety/` |
| channels-integrations | `src/channels/`, `src/feishu/`, `bridge/` |
| learning | `src/dream/`, `src/hooks/`, `instinct/scripts/` |
| genver | `src/genver/` |
| automation | `src/cron/`, `src/poller/`, `src/daemon/` |
| cli-composition | `src/cli/`, `src/config/` |
| ui-observability | `src/ui/`, `ui/`, `src/reporting/` |
| shared-infra | `src/utils/`, `src/templates/` |

详见 `BOT.md` 的 Documentation Routing 和 Development Workflow 章节。

## Runtime Shape

The default install is intentionally `core`: local CLI agent, providers,
session persistence, markdown memory, basic tools, and safety/security. Larger
systems are still in source, but they are opened through config gates and
optional dependency extras:

- default runtime: `agents.mode=single`, `tools.profile=minimal`
- default disabled: team/subagent switching, GenVer, learning hooks, browser,
  KG memory, gateway heartbeat, dashboard UI, stock, pollers
- packaging: the core wheel excludes `ui/`, `bridge/`, and `instinct/`; the
  sdist and full Docker target retain those sources for full installs

Use `theos config features` to inspect the current feature flags and
`theos config full-access` only on a trusted personal development machine.
