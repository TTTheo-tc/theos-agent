# Memory v2 Roadmap: Retrieval Quality + Recall Intelligence + Safety

**Status:** Draft
**Based on:** TheOS current memory source review + openclaw memory-core source review

## Request Context

- **User Request**: 基于 `openclaw` 源码找灵感，整理 TheOS memory 的下一步升级方案，并把现有候选项按 ROI 和实现边界整合成正式路线图。
- **Background**: `Memory Upgrade v1` 已完成 recall policy、search telemetry、`recall_journal.jsonl`、`recall_targets.json`、KG metadata ingestion、pre-compaction flush，但 retrieval 粒度、ranking、event 面、maintenance 硬化和 consolidation safety 仍有明显提升空间。
- **Trigger**: memory v1 落地后的下一阶段设计；同时吸收 TheOS code audit 和 openclaw 的已验证机制。

## Design History

- **Options Considered**:
  - 直接照搬 openclaw 的 daily-notes / promotion / event 平台
  - 只做若干 bugfix，不定义后续阶段
  - 按 TheOS 当前源码边界，拆成 bugfix、retrieval、recall-intelligence、safety/bridge 四段路线
- **Chosen Direction**: 采用分阶段路线图。先修当前真实 bug 和工程薄弱点，再升级 retrieval 质量，再把 recall telemetry 变成 rankable signal，最后处理 promotion bridge、GC 和更重的结构化能力。
- **Why**: TheOS 当前已有 `MEMORY.md + FTS + KG + recall telemetry + maintenance` 骨架，最合适的是“增强现有系统”，不是再引入第二套 memory 平台。
- **Open Questions**:
  - `memory_get` 的精细读取最终是采用 `path/from/lines` 还是稳定 snippet key
  - consolidation validation 是只做结构防护，还是同时引入 diff 审计 artifact
  - recall-based ranking 的候选输出先落 JSON 还是直接写统一 event log

## Goal

把 TheOS memory 从“已具备 recall 闭环”升级成“检索更细、信号更强、排名可用、维护更稳”的 v2 路线，并明确每一阶段的边界和优先级。

## Problem

当前源码里还存在 4 类问题：

1. **检索质量仍偏粗糙**
   - `memory_get` 仍只能按 section title 整段读取，无法像 openclaw 那样“先搜再拉精确片段”，见 `src/agent/tools/memory_search.py:304-362`。
   - pre-turn recall 对 Markdown section 的评分只是词集 overlap，没有停用词、长度归一或更细粒度 ranking，见 `src/memory/recall.py:160-203`。

2. **部分搜索/时序打分还存在明确 bug**
   - temporal decay 只看 `created_at`，不看最近更新的 `updated_at` / `last_seen_at`，见 `src/memory/knowledge_search.py:386-391`。
   - `lesson` 不在 half-life map 中，会被默认视为 evergreen，见 `src/memory/knowledge_search.py:388-390`。
   - consolidation prompt 直接拼接完整 message content，没有 per-message 截断，见 `src/memory/consolidation.py:136-164`。
   - `remember()` 和 `merge_extracted_facts()` 写 `MEMORY.md` 后没有像 consolidation 那样同步 FTS，consolidation 目前才有显式 `memory_index.sync_all()`，见 `src/memory/store.py:55-98`、`src/memory/extract.py:161-190`、`src/memory/consolidation.py:291-295`。

3. **recall telemetry 还没有变成真正的 ranking input**
   - v1 的 `recall_targets.json` 只保留 `recall_count`、`distinct_query_hashes`、`distinct_days`、`max_score`、`last_recalled_at`，见 `src/memory/recall_maintenance.py:80-105`。
   - 还没有 `claim_hash`、`grounded_count`、`total_score` 这类更稳定的聚合信号。

4. **maintenance / observability / safety 还偏轻**
   - `recall_targets.json` / checkpoint 仍是直接写文件，不是 lock + tmp/rename，见 `src/memory/recall_maintenance.py:114-118`。
   - memory 事件仍分散在 journal、targets、flush event 文件中，没有统一 event 面。
   - consolidation 目前对 LLM 输出缺少结构防护，坏输出理论上仍可能污染 `MEMORY.md`。

## Scope

- **In Scope**:
  - 定义 `memory v2` 的阶段目标、优先级、验收边界
  - 合并 TheOS audit、当前 v1 代码现状和 openclaw 可借鉴机制
  - 明确哪些项属于立即 bugfix，哪些项属于 retrieval / recall / safety 升级
- **Out of Scope**:
  - 本文不直接实现任何代码
  - 不在 v2 路线里引入 openclaw 的 daily-notes 平台
  - 不定义自动 promotion 到 instinct `ACTIVE`
  - 不引入新的双后端 / watcher / dreaming phase 平台

## Current State

### TheOS

- `MemoryUpgrade v1` 已打通 `search -> recall_journal.jsonl -> recall_targets.json -> KG rule metadata`，见 `src/memory/recall_maintenance.py:1-171`。
- `memory_get` 仍是 section-level retrieval，不支持精细片段读取，见 `src/agent/tools/memory_search.py:304-362`。
- pre-turn injected recall 的 Markdown 选择仍是词集 overlap + token budget，见 `src/memory/recall.py:150-203`。
- knowledge search 的 temporal decay 和 half-life map 仍有明显调整空间，见 `src/memory/knowledge_search.py:386-391`。
- consolidation 后会同步 FTS，但 direct remember / extracted facts merge 还没有同等接线，见 `src/memory/consolidation.py:291-295`、`src/memory/store.py:55-98`、`src/memory/extract.py:161-190`。

### openclaw

- prompt guidance 明确要求：关于 prior work / decisions / preferences / todos 先 `memory_search`，然后 `memory_get` 只拉需要的行，见 `extensions/memory-core/src/prompt-section.ts:16-23`。
- recall tracking 明确采用 best-effort fire-and-forget，不阻塞 search 返回，见 `extensions/memory-core/src/tools.ts:59-74`。
- short-term promotion 使用更强的 recall schema 和 6 维 ranking，见 `extensions/memory-core/src/short-term-promotion.ts:17-31`、`extensions/memory-core/src/short-term-promotion.ts:43-58`、`extensions/memory-core/src/short-term-promotion.ts:63-123`。
- memory host 使用统一 event log，见 `src/memory-host-sdk/events.ts:5-61`。

## Proposed Design

采用 4 个 sprint 的路线，而不是把所有 memory 改动混成一个大包。

### Sprint 0: Bugfix Pack

目标：先修当前已确认的正确性问题和 maintenance 薄弱点。

1. **Temporal decay 改用最近更新时间**
   - 把 `src/memory/knowledge_search.py:386-391` 的衰减时间基准从 `created_at` 升级成 `max(created_at, updated_at/last_seen_at)` 的语义。
   - 解决“老规则昨天刚重现、今天仍被重衰减”的问题。

2. **remember / extract 后同步 FTS**
   - 让 `MemoryStore.remember()` 和 `merge_extracted_facts()` 在成功写入后触发与 consolidation 等价的 index sync。
   - 修复“刚记住的内容搜不到，直到下一次 consolidation”的体验断裂。

3. **Hybrid merge 后统一应用 temporal decay**
   - 当前 `_compute_final_score()` 在 FTS 阶段应用 decay，但 `_merge_results()` 合并 FTS + vector 后没有统一再收敛时间权重，见 `src/memory/knowledge_search.py:370-394`、`src/memory/knowledge_search.py:397-410`。
   - 调整成 merge 之后统一做 final decay。

4. **Consolidation prompt 做 per-message 截断**
   - 在 `src/memory/consolidation.py:136-164` 构造 prompt 前对 message content 做有限截断，避免长 tool result 直接挤爆 consolidation model context。

5. **给 lesson 增加 half-life**
   - 补齐 `lesson` 的衰减策略，避免它永久占据搜索排序。

6. **Maintenance 硬化**
   - 为 `recall_targets.json` / checkpoint 引入 tmp + rename。
   - maintenance 路径引入单实例锁。
   - 保持 `recall_journal.jsonl` 是 truth source，`targets` 可删可重建。

### Sprint 1: Retrieval Quality

目标：让 memory retrieval 更细、更准、更省上下文。

1. **`memory_get` 精细化读取**
   - 保留现有 `section` 参数作为兼容模式。
   - 增加更细粒度读取，向 `path + from + lines` 或稳定 snippet key 靠拢。
   - 设计目标与 openclaw 的“两段式取证”一致，但不强行复制其文件布局。

2. **MMR 重排序**
   - 在 search 结果进入上下文前做近重复去冗余，减少相似片段浪费 context 窗口。

3. **Pre-turn recall 评分升级**
   - 取代 `src/memory/recall.py:160-203` 的纯 overlap 排序。
   - 优先复用现有 FTS 或 section-level ranking，而不是再造一套临时相似度。

4. **Graded fallback**
   - 当 recall 找不到强匹配时，按 `pinned -> recent -> full fallback` 退化，而不是简单空结果或整份 `MEMORY.md`。

5. **Hybrid search relaxed fallback**
   - 当严格阈值下 FTS/混合检索为空，但 keyword 已确认存在时，允许一层放宽阈值的补救路径。

### Sprint 2: Recall Intelligence

目标：把 v1 的 recall telemetry 升级成可排名信号，而不是只做计数。

1. **Recall signal schema 升级**
   - 在 `recall_journal` / `recall_targets` 路径上逐步补齐：
     - `claim_hash`
     - `grounded_count`
     - `total_score`
     - 可选 `daily_count`
   - 其中 `grounded_count` 表示结果不只是被搜到，还被回答过程真正引用或采用。

2. **统一 memory event log**
   - 参考 openclaw 的 `events.jsonl`，为 TheOS 统一追加：
     - `memory.recall.recorded`
     - `memory.recall.folded`
     - `memory.recall.ingested`
     - `memory.flush.completed`
   - 现有 `recall_journal.jsonl` 继续保留为 recall truth source；统一 event log 用于观测和调试。

3. **Recall-based ranking，但先不自动 promote**
   - 参考 openclaw 的 6 维 promotion 结构：
     - frequency
     - relevance
     - diversity
     - recency
     - consolidation
     - conceptual
   - TheOS v2 第一阶段只做 rank-only：
     - 产出 recall candidates
     - 不直接写入 instinct `ACTIVE`
     - 不改变现有 `PROBATION -> ACTIVE` 决策链

### Sprint 3: Safety + Bridge

目标：补 memory safety 和系统桥接，而不是先做更多 ranking 花活。

1. **Consolidation 输出质量验证**
   - section-count sanity check
   - pinned section 保护
   - 格式校验
   - diff 审计
   - 这是 v2 中唯一具备“防灾难性 memory 污染”意义的项，应高于 promotion。

2. **instinct ↔ KG 双向桥接**
   - 当前只有 instinct -> KG 的单向路径；v2 才考虑如何让 KG 侧的高质量 rule candidate 进入 instinct review 流。
   - 这一步必须建立在 recall ranking 稳定之后。

3. **KG 节点 GC**
   - 清理 superseded / dead node，降低长期 FTS 噪音。

4. **REM evidence scoring**
   - 作为 extract / consolidation 的前置过滤器，降低 situational noise 进入 `MEMORY.md` 的概率。

5. **Concept tag 自动提取**
   - 作为 ranking 的增强项，而不是 v2 主阻塞项。

## Alternatives Considered

1. **直接复制 openclaw 的 daily-notes + promotion 平台**
   - 不选，因为 TheOS 当前 narrative truth source 是 `MEMORY.md`，没有 daily-notes 体系，强上只会引入第二套状态源。

2. **只做 A1-A5 这组 bugfix，不定义后续路线**
   - 不选，因为 retrieval / recall / safety 的后续边界会再次散掉，届时更难统一 priority。

3. **把 recall-based ranking 直接接到 instinct ACTIVE**
   - 不选，因为当前 instinct rule identity 和 KG rule identity 还没有稳定双向映射。

## Risks and Tradeoffs

- `memory_get` 精细化读取会改 tool contract，需要补 prompt、tests、tool consumers 的兼容逻辑。
- recall signal 升级如果过早绑定 promotion，容易造成“看起来有分数，实际上没足够语义”的误用；因此本路线坚持 rank-only first。
- 统一 event log 会引入一层新的观测面，需要明确它不是 truth source。
- consolidation validation 如果做得太激进，可能提高 false negative，让有用的 memory 更新被拒绝；需要以“保守阻断损坏输出”为先。

## Acceptance Criteria

### Sprint 0

- `knowledge_search` 的 temporal decay 不再只依赖 `created_at`
- `lesson` 不再是默认 evergreen
- `remember()` / `merge_extracted_facts()` 后搜索可立即命中新内容
- consolidation prompt 对超长消息有稳定截断
- recall maintenance 写 targets/checkpoint 时采用原子写，且具备单实例保护

### Sprint 1

- `memory_get` 支持比“整段 section”更细的读取模式
- recall 结果进入上下文前会做去冗余
- pre-turn recall 的命中质量优于当前纯 overlap 版本
- fallback 路径可以分层退化，而不是直接空或 full dump

### Sprint 2

- recall schema 可以表示 `claim_hash`、`grounded_count`、`total_score`
- memory 有统一 event log，且至少包含 recall / fold / ingest / flush 事件
- 系统能产出 recall-based ranked candidates，但不会自动 promote 到 instinct active rules

### Sprint 3

- consolidation 输出破坏结构或 pinned section 时会被拦截
- KG 侧高价值规则可以进入一条明确的 instinct review 流
- superseded KG 节点可被 GC

## Verification Plan

- `Sprint 0`
  - 扩 `knowledge_search` 评分测试，覆盖 updated_at / lesson half-life / hybrid merge 后 decay
  - 扩 `remember` / `extract` 后即时 recall 命中测试
  - 扩 maintenance 的 lock / atomic-write 测试
- `Sprint 1`
  - 为 `memory_get` 新 contract 增加工具级测试
  - 为 MMR / fallback / section ranking 增加搜索质量测试
- `Sprint 2`
  - 为 recall signal schema 和 unified event log 增加 end-to-end 测试
  - 为 ranking 增加 deterministic fixture 测试，锁住 score 组件
- `Sprint 3`
  - 为 consolidation validation 增加结构破坏 / pinned 保护测试
  - 为 instinct ↔ KG bridge 增加 review-only 集成测试

## Related Files

- `docs/dev/specs/2026-04-14-memory-upgrade-v1.md`
- `src/memory/knowledge_search.py:370-410`
- `src/memory/store.py:55-98`
- `src/memory/extract.py:161-190`
- `src/memory/consolidation.py:136-176`
- `src/memory/consolidation.py:291-295`
- `src/memory/recall.py:150-203`
- `src/memory/recall_maintenance.py:1-171`
- `src/agent/tools/memory_search.py:304-362`
- `../openclaw/extensions/memory-core/src/prompt-section.ts:16-23`
- `../openclaw/extensions/memory-core/src/tools.ts:59-74`
- `../openclaw/extensions/memory-core/src/short-term-promotion.ts:17-31`
- `../openclaw/extensions/memory-core/src/short-term-promotion.ts:43-58`
- `../openclaw/extensions/memory-core/src/short-term-promotion.ts:63-123`
- `../openclaw/src/memory-host-sdk/events.ts:5-61`
