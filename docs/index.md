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
│   ├── channels-integrations/  # 12+ platform adapters, Feishu deep integration, bridge
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
