# <Plan Title>

> 实现计划回答的是：在设计已经确认后，准备按什么顺序落地、每一步怎么验证。
> 它不是设计文档，也不是完成后的总结。

## Request Context

记录这次实现计划对应的需求来源，便于后续追溯。

- **User Request**: 用户原始需求或压缩后的准确表述。
- **Execution Goal**: 这次计划准备解决哪部分问题。
- **Constraints**: 时间、风险、环境、兼容性等实现约束。

## Source Spec

指向对应的 spec 文件，例如：

- `docs/dev/specs/YYYY-MM-DD-<topic>.md`

## Plan History

记录计划拆分和执行顺序上的关键决策。

- **Initial Strategy**: 最初准备怎么拆。
- **Current Strategy**: 现在采用的任务顺序和分工方式。
- **Why**: 为什么这样拆、为什么这样排序。
- **Changes Since Draft**: 如果计划在实现过程中改过，记录关键变化；如果没有，写 `None`。

## Goal

一句话说明这个计划要把哪份已确认设计落地。

## Task Breakdown

把实现拆成一组可独立验证的小任务。每个任务都应明确：

1. 要改什么
2. 涉及哪些文件 / 子系统
3. 完成后如何验证

建议格式：

### Task 1: <name>

- **Scope**: 这一步负责什么
- **Files**: 主要会动哪些文件
- **Verification**: 这一步完成后跑什么检查

### Task 2: <name>

- **Scope**: ...
- **Files**: ...
- **Verification**: ...

## Dependencies and Order

说明哪些任务必须串行，哪些可以并行，为什么。

## Checkpoints

列出需要停下来做 review 或重新对齐 spec 的关键节点。

## Risks

列出实现阶段最容易失控或返工的点。

## Final Verification

定义整项工作收尾时必须完成的验证集合。

---

## Writing Rules

- plan 只服务实现，不重复解释设计动机；设计动机回到 spec
- `Request Context` 和 `Plan History` 用来保留溯源信息，但只记录影响实现的关键信息，不写完整执行日志
- 任务拆分要能独立验证，避免“一个大 task 包一切”
- 不把尚未确认的设计决定写进 plan
- 如果实现顺序或任务边界发生明显变化，及时更新 plan
- 引用源码或 spec 时统一用仓库相对路径
