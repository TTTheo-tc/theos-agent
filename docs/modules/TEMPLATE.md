# <Module Name>

> 模块文档不是需求文档，也不是 changelog。
> 它回答的是：这块代码现在怎么工作、边界在哪、改动时要注意什么。

## Purpose

- **Owns**: 这个模块负责什么。
- **Does Not Own**: 哪些相邻能力虽然相关，但不归它管。

## Source Scope

对应哪些源码目录/文件。哪些相邻目录虽然相关，但不归它管。

## Entry Points

开发者从哪几个文件读起。主要入口函数/类/CLI 接线点。

## Architecture

模块内部怎么分层。关键对象之间怎么协作。只画稳定关系，不写流水账。

## Data Flow

一次典型调用/请求/任务怎么流过这个模块。输入、处理中间态、输出分别是什么。

## State & Persistence

状态存在哪里。哪些是内存态，哪些是文件/SQLite/JSONL/缓存。生命周期和恢复方式。

## Invariants

这块代码绝不能破坏的约束。例如 ownership、append-only、read-only、path guard、schema owner。

## Extension Points

新增能力应该改哪里。哪些接口是官方扩展点。哪些地方不要直接碰。

## Failure Modes

常见失败路径。超时、重试、降级、fallback 在哪层发生。

## Verification

改这块通常该跑哪些测试。哪些命令是最小充分验证。

## Related Files

相关源码文件。相邻模块文档。只列最关键的，不要大而全。

---

## Writing Rules

- 只写源码里能证实的内容
- 多写边界和约束，少写实现细枝末节
- 优先写"为什么这里这样分层"，不是"这个函数做了什么"
- 不贴大段代码；引用源码时统一用仓库相对路径加行号，例如 `src/agent/loop.py:49`
- 不把 issue/spec/plan 的过程性内容塞进模块文档
- 如果代码和文档冲突，先修代码理解，再改文档

## When to Split

满足任一条件就拆成子文档：

- 单篇超过 150-200 行
- 模块内有 2 个以上相对独立的子系统
- 读者在维护时只会关心其中一个专题
- 不同专题的更新频率明显不同

拆分后仍必须保留一个稳定入口页 `overview.md`，由它说明模块总览并链接到各子文档。

子文档命名建议：`runtime.md`、`data-model.md`、`persistence.md`、`execution-flow.md`、`extension.md`。
避免含糊名字：`notes.md`、`misc.md`、`details.md`。
