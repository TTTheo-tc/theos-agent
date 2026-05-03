# <Spec Title>

> 设计文档回答的是：要做什么、为什么做、准备怎么做、边界在哪里、什么算完成。
> 它不是实现记录，也不是任务流水账。

## Request Context

记录这次设计直接来自什么需求输入，便于后续回溯。

- **User Request**: 用户原始需求或压缩后的准确表述。
- **Background**: 必要的上下文、约束、问题来源。
- **Trigger**: 是新功能、review 发现、bug、重构需求，还是外部变更触发。

## Design History

记录设计形成过程中的关键决策，不展开成流水账。

- **Options Considered**: 曾评估过哪些方案。
- **Chosen Direction**: 最终决定采用什么方案。
- **Why**: 做这个决定的关键理由。
- **Open Questions**: 仍待确认的问题；如果没有，写 `None`。

## Goal

一句话说明这次改动要达成什么结果。

## Problem

当前实现有什么具体问题、缺口或限制。只写当前事实，不写泛泛而谈的动机。

## Scope

- **In Scope**: 这次要覆盖的内容。
- **Out of Scope**: 明确这次不做什么，避免范围漂移。

## Current State

基于当前源码说明现状。列出关键入口、现有行为、已知约束。

## Proposed Design

说明准备采用的方案：

- 核心思路
- 关键数据流 / 调用链
- 主要结构调整
- 需要新增或修改的接口 / 文件 / 子系统

只写稳定设计，不写实现过程中的临时尝试。

## Alternatives Considered

列出主要备选方案，以及为什么不选它。

## Risks and Tradeoffs

说明这个方案带来的成本、风险、限制和后续影响。

## Acceptance Criteria

定义可检查的完成标准。完成后，读者应该能明确判断这项设计是否已经落地。

## Verification Plan

说明实现后至少要做哪些验证：

- 单元测试 / 集成测试
- 手工验证
- 需要观察的日志、状态或输出

## Related Files

列出最相关的源码和上下游文件。只列关键文件，不要大而全。

---

## Writing Rules

- 只写当前源码和当前目标能支持的内容
- `Request Context` 和 `Design History` 用来保留溯源信息，但只记录关键输入和关键决策，不写完整对话抄录
- 重点写设计边界和验收标准，不写实现流水账
- 如果方案还没定，不要把猜想写成结论
- 引用源码时统一用仓库相对路径加行号，例如 `src/agent/loop.py:49`
- 设计一旦确认，后续实现应以这份 spec 为准；若设计改变，应先更新 spec
