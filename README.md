# TheOS

Theo's Agentic Operating System.

A personal agentic OS for code, memory, tools, and automation.

基于 Python 的个人 agentic OS，面向个人部署和持续开发。它把聊天入口、
代码工作流、工具调用、长期记忆、认证管理和多模型编排放在同一套运行时里。

## What It Does

- 多入口: CLI 对话 + Gateway 平台接入
- 多模型: Anthropic、OpenAI 兼容供应商、Codex、GitHub Copilot 等
- 多平台: Telegram、Discord、WhatsApp、Slack、Feishu、DingTalk、QQ、Matrix、Email、Mochat
- 多模式: `single`、`multi`、`genver`
- 持久化: 加密认证、workspace 记忆、per-group 隔离、session 历史

## Quick Start

### 1. Install

```bash
git clone https://github.com/TTTheo-tc/theos-agent
cd theos-agent
make install-core
```

这会安装默认 core 运行时：本地 CLI、provider、基础工具、session、memory 和安全层。

也可以分步执行:

```bash
make install-dev      # core + dev tools + pre-commit
make install-gateway  # core + gateway scheduling support
make install-full     # UI + WhatsApp bridge build + all extras + dev tools
```

按需安装 extras:

```bash
uv sync --extra web
uv sync --extra ui
uv sync --extra channels-telegram
uv sync --extra channels-feishu
```

不安装直接运行（仅开发时使用）:

```bash
uv run theos --help
```

### 2. Initialize

```bash
uv run theos init
```

初始化会创建:

- `~/.theos/config.json`
- `~/.theos/auth-profiles.enc`
- `~/.theos/workspace/`

常见认证路径:

- OpenAI Codex: 自动读取 `~/.codex/auth.json`
- Anthropic / Claude: `theos auth add --provider anthropic --key <key>`
- API Key: `theos auth add --provider <name> --key <key>`

认证优先级: `auth profile > config.json`

### 3. Run

```bash
uv run theos agent
uv run theos gateway
uv run theos status
```

### Docker Targets

```bash
docker build --target core -t theos-core .
docker build --target gateway -t theos-gateway .
docker build --target full -t theos-full .
```

`core` is Python-only and defaults to `theos agent`. `gateway` adds scheduling support
and defaults to `theos gateway`. `full` installs all Python extras, builds the dashboard
UI, and builds the WhatsApp Node bridge.

## Common Commands

```bash
theos init
theos agent
theos gateway
theos status
theos auth list
theos auth add --provider anthropic --key sk-ant-...
```

## Runtime Data

```text
~/.theos/
├── config.json
├── auth-profiles.enc
├── workspace/
├── groups/
└── sessions/
```

## Documentation

- `README.md`: 项目介绍、安装、运行、基础使用
- `BOT.md`: AI coding tools 的统一开发规则和文档路由
- `docs/index.md`: 内部模块文档索引
- `docs/modules/`: 按源码模块划分的长期架构文档
- `docs/dev/`: 设计文档和实现计划
- `CONTRIBUTING.md`: 贡献流程
- `STYLE.md`: 代码风格约定

如果你是使用者，看 README 就够了。
如果你在开发或让 agent 改代码，从 `BOT.md` 开始。
