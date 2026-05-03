# Memory Upgrade v1: Recall Policy + Telemetry + Pre-Flush + Observability

**Status:** Pending review
**Based on:** `~/Downloads/2026-04-13-memory-upgrade-v1.md` + TheOS & openclaw full source review
**Goal:** 让记忆系统从"被动可用"升级为"主动闭环"：模型被约束在回答历史问题前先搜索，搜索行为产生信号回写 KG 规则元数据，接近 compaction 时后台 flush durable facts，全链路可观测。

---

## Cross-Repo Source Review

### TheOS (已验证)

| 位置 | 当前状态 |
|------|----------|
| `src/agent/context.py:125-132` | prompt 是 `"use these tools instead of guessing"`，无强制约束 |
| `src/agent/tools/memory_search.py:148-255` | `execute()` 直接 return，无 telemetry |
| `src/agent/tools/structured_memory.py` | 4 个 tool 均无 telemetry |
| `src/agent/loop_memory.py:700` | `compact_messages()` 直接 LLM 总结，无 pre-flush |
| `src/config/schema.py:131-135` | `MemoryFlushConfig(enabled=True, soft_threshold_tokens=4000)` 已定义但未接线 |
| `src/agent/loop_memory.py:259` | `_extract_cursor: dict[str, int]` — 纯内存态，进程重启归零 |
| `src/memory/extract.py:91-158` | `extract_durable_facts()` 可复用，`merge_extracted_facts()` 有 case-insensitive 去重 |
| `src/memory/structured.py:432-490` | `_upsert_rule()` 维护 `occurrence_count`, `confidence`, `last_seen_at`，KG rule 有稳定 `rule-{fingerprint}` id |
| `src/memory/rule_cleanup.py:17` | 6h cron 已存在，但操作的是 legacy JSON 文件，不是 KG 节点 |
| `src/agent/tools/context.py:7-19` | `ToolContext` 无 `routing_domains` |
| `src/store/database.py` + `src/memory/knowledge_graph.py` | 两个 SQLite 数据库分离：`theos.db`（FTS、短期记忆）和 `kg.db`（KG 节点/边/embedding） |

### openclaw (已验证)

| 位置 | 实际实现 |
|------|----------|
| `extensions/memory-core/src/prompt-section.ts:16-20` | 硬性要求 "Before answering anything about prior work... run memory_search"。条件式：根据可用工具组合生成不同文案 |
| `extensions/memory-core/src/tools.ts:59-74` | `queueShortTermRecallTracking()` — `void recordShortTermRecalls(...).catch(() => {})` 显式 fire-and-forget |
| `extensions/memory-core/src/short-term-promotion.ts:815-922` | 每个被召回片段追踪：`recallCount`, `dailyCount`, `totalScore`, `maxScore`, `queryHashes` (SHA1[:12] 去重上限 32), `recallDays` (去重上限 16), `conceptTags`, `claimHash` |
| `extensions/memory-core/src/flush-plan.ts:95` + `src/auto-reply/reply/memory-flush.ts:60-85` | soft threshold 触发 flush，写入 `memory/YYYY-MM-DD.md`（daily notes），不是 MEMORY.md。三条硬编码安全规则：只写 daily file、append-only、bootstrap 文件只读 |
| `extensions/memory-core/src/short-term-promotion.ts:52-58` | promotion 评分 6 维加权：frequency(0.24) + relevance(0.30) + diversity(0.15) + recency(0.15) + consolidation(0.10) + conceptual(0.06)。门槛：score >= 0.75, recall_count >= 3, unique_queries >= 2 |
| `src/memory-host-sdk/events.ts:5-54` | 统一事件流 `memory/.dreams/events.jsonl`，三种事件类型：`memory.recall.recorded`, `memory.promotion.applied`, `memory.dream.completed` |

### 与原设计文档的偏差

原设计文档的 4 个缺口诊断和"借机制不借平台"策略全部正确。以下 3 处需要修正：

1. **recall 信号维度不足**：原设计只记 `query_hash` + `score`。openclaw 的 promotion 核心输入是 per-target 的 unique query 数和 unique recall day 数（`short-term-promotion.ts:52-58` diversity + consolidation 权重共 0.25）。v1 不接 promotion，但 schema 必须预留这些维度，否则 v2 接不上。

2. **pre-flush 目标文件的设计选择**：openclaw 写 `memory/YYYY-MM-DD.md`（daily notes），TheOS 复用 `extract_durable_facts()` 写 MEMORY.md。这是合理的适配——TheOS 没有 daily notes 体系，MEMORY.md 是唯一的 narrative 真相源。但必须明确：这和 openclaw 不同，且 MEMORY.md 的去重依赖 `merge_extracted_facts` 的 case-insensitive 匹配，不如 openclaw 的 append-only daily file 天然幂等。

3. **rule_cleanup.py 的复用路径**：原设计说"复用 rule_cleanup maintenance 窗口"。实际上 `rule_cleanup.py` 操作 legacy JSON 文件（`workspace/memory/structured/rules/*.json`），recall ingestion 需要走 `KnowledgeGraph.update_node()`（`kg.db`）。应该是共用 cron 调度但逻辑完全独立。

---

## 6 个改动

### 1. Harden prompt recall policy

**File:** `src/agent/context.py:125-132`

替换当前泛泛提示：

```
# Memory Tools

You have memory_search, memory_get, structured_memory_search,
research_note_get, task_memory_get, and domain_rule_get tools.

**Mandatory recall policy:**
- When the user asks about prior work, past decisions, stated preferences,
  commitments, or todos — and the injected Memory section above does not
  already cover the topic — you MUST call memory_search or
  structured_memory_search BEFORE answering.
- Do NOT guess or fabricate historical facts. If memory tools return
  nothing, say you don't have that information.
- The Memory section above is pre-loaded context; for specific historical
  questions beyond its scope, always search first.
```

**与 openclaw 的差异**：openclaw 无条件要求搜索（`prompt-section.ts:16`）。TheOS 已有 `MemoryRecallService` 预注入（`recall.py:62-104`），所以条件式更合理——"已注入的 Memory section 没覆盖时才强制搜索"。

### 2. Recall telemetry on search tools

**Files:**
- `src/agent/tools/memory_search.py` — `MemorySearchTool.execute()` return 前
- `src/agent/tools/structured_memory.py` — `StructuredMemorySearchTool.execute()` 和 `DomainRuleGetTool.execute()` return 前

```python
import asyncio
from src.memory.recall_journal import append_recall_entries

# At end of execute(), before return:
asyncio.create_task(append_recall_entries(
    workspace=workspace,
    session_key=_context.session_key if _context else None,
    tool=self.name,
    query=query,
    results=results_for_telemetry,
))
```

best-effort（`create_task` + 内部 catch），失败只 log，不阻塞工具返回。与 openclaw 的 `void ...catch(() => {})` 模式一致（`tools.ts:72-74`）。

`results_for_telemetry` 每条包含：
- `target_kind`: `markdown_section` | `kg_rule` | `kg_task` | `kg_research`
- `target_id`: KG node id 或 `null`（markdown 结果无 id）
- `path`: `source:section`
- `score`: 排序分数，exact-ID 读取为 `null`
- `domains`: 从 KG metadata 提取，拿不到则 `[]`

`DomainRuleGetTool` 特殊处理：`query=rule_id`, `target_kind="kg_rule"`, `target_id=rule_id`, `score=null`。

### 3. recall_journal.jsonl

**New file:** `src/memory/recall_journal.py`（~140 行）

Write path: `{workspace}/memory/instinct/recall_journal.jsonl`

每次 search tool 调用产生 1-N 行（每个搜索结果一行）：

```json
{
  "timestamp": "ISO",
  "session_key": "channel:chat_id",
  "tool": "memory_search",
  "query": "原始查询",
  "query_hash": "SHA1[:12]",
  "day": "YYYY-MM-DD",
  "target_kind": "kg_rule",
  "target_id": "rule-abc123",
  "path": "MEMORY:Architecture Decisions",
  "score": 0.85,
  "domains": ["coding/general"]
}
```

**与原设计的差异——为 v2 promotion 预留的聚合层**：schema 本身不变，但新增一个 maintenance-owned 的派生快照 `recall_targets.json`：

```json
{
  "rule-abc123": {
    "recall_count": 5,
    "distinct_query_hashes": ["a1b2c3d4e5f6", "..."],
    "distinct_days": ["2026-04-14", "2026-04-15"],
    "last_recalled_at": "ISO",
    "max_score": 0.92
  }
}
```

这与 openclaw 的 `short-term-recall.json` 的 per-entry 聚合模式对齐（`short-term-promotion.ts:815-900`），但只追踪有 `target_id` 的 KG 结果。去重上限：`distinct_query_hashes` 最多 32 个，`distinct_days` 最多 16 个（与 openclaw 一致）。

**实现收口**：
- `recall_journal.jsonl` 是唯一热路径写入，append-only
- `recall_targets.json` 是由 maintenance 从 journal 折叠出来的派生快照，不在 search tool 热路径更新
- 另存一个小型 checkpoint（例如 `recall_targets.checkpoint.json`，保存 journal byte offset / line count），避免每次全量重扫

这样做的原因是：
- 避免在每次 recall 工具调用时做 update-in-place 写放大
- 避免为派生缓存引入额外的并发锁复杂度
- 明确 `journal` 是 source of truth，`targets` 可删可重建

约束：
- `recall_journal.jsonl` append-only
- `recall_targets.json` / checkpoint 是 maintenance-owned derived cache
- 三个文件都放在 `memory/instinct/` 下，与 `events/`, `live_rules.jsonl` 同层
- 如果 `recall_targets.json` 缺失或损坏，maintenance 必须能从 `recall_journal.jsonl` 全量重建

### 4. KG rule recall metadata batch ingestion

**New functions:** `src/memory/recall_maintenance.py` 的 `fold_recall_journal()` + `ingest_recall_to_kg()`
**Scheduling:** 共用现有 6h maintenance 窗口，但实现放在独立 Python 模块；`rule_cleanup.py` 只复用调度习惯，不承载 recall 逻辑本体

maintenance 每次运行做两步：

1. `fold_recall_journal()`：从 checkpoint 位置开始消费 `recall_journal.jsonl` 新增尾部，更新 `recall_targets.json`
2. `ingest_recall_to_kg()`：读取 `recall_targets.json` 快照，把 `kg_rule` 聚合信号回写到 KG rule metadata

对每个 `target_id` 前缀为 `rule-` 的 entry：

```python
async def ingest_recall_to_kg(workspace: Path) -> int:
    """Batch-update KG rule nodes with recall metadata."""
    # 1. Read recall_targets.json
    # 2. Filter: target_id starts with "rule-"
    # 3. For each: open StructuredMemoryStore, call kg.update_node() to set:
    #    - recall_count
    #    - last_recalled_at
    #    - distinct_recall_queries (count)
    #    - distinct_recall_days (count)
    # 4. Close store
    # 5. Return count of updated rules
```

**关键实现细节**：
- 走 `KnowledgeGraph.update_node()`（`knowledge_graph.py`），写 `kg.db`，不碰 legacy JSON
- 与 `rule_cleanup.py:cleanup_structured_rules()` 共用 cron 窗口（6h），但逻辑完全独立
- checkpoint 只属于 `fold_recall_journal()`，用于推进 journal -> targets 的折叠游标
- `ingest_recall_to_kg()` 只读当前 `recall_targets.json` 快照，不直接扫 journal

**这不是 promotion 路径**。v1 不让 recall 影响 instinct `PROBATION -> ACTIVE`。原因（源码验证）：
- instinct PROBATION 规则没有 KG object id
- `kg_pending.jsonl` 导入 KG 时创建的是 `lesson` 节点（`loop_memory.py:440`），不是 `rule` 节点
- `evolve.js` 的 promotion 逻辑不读 KG metadata

v1 先只做 `recall -> KG rule metadata`，等打通 instinct rule identity 再考虑反哺。

### 5. Pre-compaction background flush

**Files:** `src/agent/loop_memory.py` + `src/agent/loop.py`

接线已有但未使用的 `MemoryFlushConfig`（`schema.py:131-135`），但**只在 pre-turn compaction 路径启用**，不在 in-loop compaction 路径启用。

原因是当前 `_extract_cursor` 记录的是 `session.messages` 的绝对游标（`loop_finalize.py:333-364`），而 in-loop compaction 处理的是当前 turn 的临时 `messages` 列表，两者坐标系不同，不能直接混算。

实现上分两步：

1. `src/agent/loop.py` 里调用 `self._context.build_turn_messages(...)` 的这条 pre-turn 路径，把 `history` capture 到 `maybe_compact(...)` 闭包里
2. `AgentLoop._run_agent_loop()` 的 in-loop compaction 回调继续传 `persisted_history=None`，此时 pre-flush 自动禁用

伪代码：

```python
async def _schedule_pre_compaction_flush(
    self,
    *,
    session_key,
    persisted_history,
    compact_prefix_count,
    provider,
    model,
    workspace,
):
    """Best-effort: extract durable facts from about-to-be-compacted messages."""
    cfg_flush = self._memory_config.flush
    if not cfg_flush.enabled:
        return

    # _extract_cursor is absolute over session.messages / persisted history.
    cursor = max(self._extract_cursor.get(session_key, 0), 0)
    compact_end = min(len(persisted_history), compact_prefix_count)
    if compact_end <= cursor:
        return

    gap_msgs = persisted_history[cursor:compact_end]

    # Guard: skip if gap is empty or too large (>50 messages = likely process restart)
    if len(gap_msgs) < 2:
        return
    if len(gap_msgs) > 50:
        logger.warning("Pre-flush gap too large ({}), skipping to avoid stale extraction", len(gap_msgs))
        return

    try:
        facts = await asyncio.wait_for(
            extract_durable_facts(gap_msgs, provider, model),
            timeout=30.0,
        )
        if facts:
            merged = merge_extracted_facts(MemoryStore(workspace), facts)
            if merged > 0:
                # Write flush event
                _write_flush_event(workspace, session_key, merged)
                logger.info("Pre-compaction flush: {} facts merged", merged)
    except Exception:
        logger.opt(exception=True).debug("Pre-compaction flush failed (best-effort)")
```

**关键设计决策（与 openclaw 的差异）**：
- openclaw 写 `memory/YYYY-MM-DD.md`（daily notes，append-only，天然幂等）
- TheOS 写 MEMORY.md（通过 `merge_extracted_facts` 的 case-insensitive 去重）
- 这是合理适配：TheOS 没有 daily notes 体系。去重不如 append-only 天然幂等，但 `merge_extracted_facts` 已有的去重逻辑（`extract.py:199`）在实践中够用

**重启安全**：`_extract_cursor` 是纯内存态（`loop_memory.py:259`），进程重启后归零，gap 可能异常大。加了 `> 50` 的上限保护：gap 过大说明 cursor 已过期，跳过比错误抽取更安全。

**不阻塞 compaction**：`create_task`，compaction 不等 flush。

### 6. Unified event stream

不新增存储层。所有事件落 `memory/instinct/`：

| 事件 | 位置 | 写入者 | 格式 |
|------|------|--------|------|
| Reflect events | `events/{ts}-{session}.json` | reflect.js | JSON per file |
| Evolve promotions | `rules/index.json` | evolve.js | JSON |
| **Recall journal** | `recall_journal.jsonl` | recall_journal.py | JSONL append |
| **Recall targets** | `recall_targets.json` | recall maintenance | JSON snapshot |
| **Flush events** | `events/{ts}-flush.json` | loop_memory.py | JSON per file |

与 openclaw 的 `memory/.dreams/events.jsonl` 对比：openclaw 用单一 JSONL 文件 + 事件类型字段。TheOS 用 `memory/instinct/` 目录下的多文件，与已有 instinct 事件格式一致。两种方式都支持单目录扫描。

---

## Files Changed / Created

| Action | Path | 改动量 |
|--------|------|--------|
| Modify | `src/agent/context.py:125-132` | ~10 行 |
| Modify | `src/agent/tools/memory_search.py` | ~15 行 |
| Modify | `src/agent/tools/structured_memory.py` | ~20 行 |
| **Create** | `src/memory/recall_journal.py` | ~140 行 |
| **Create** | `src/memory/recall_maintenance.py` | ~80 行 |
| Modify | `src/memory/rule_cleanup.py` | ~10 行（只复用 maintenance 调度入口） |
| Modify | `src/agent/loop.py` | ~10 行（仅 pre-turn 路径传 persisted_history） |
| Modify | `src/agent/loop_memory.py` | ~55 行 |
| **Create** | `tests/test_recall_journal.py` | ~100 行 |
| **Create** | `tests/test_pre_compaction_flush.py` | ~60 行 |

总计约 ~475 行新/改代码。

---

## Acceptance Criteria

1. **Prompt policy 生效**: 问"上次我们决定了什么"且 Memory section 未覆盖时，模型先调 `memory_search` 再回答
2. **Recall journal 有数据**: 每次 memory search 后 `recall_journal.jsonl` 新增行，schema 合规
3. **Recall targets 聚合**: `recall_targets.json` 按 target_id 聚合 recall_count, distinct_query_hashes, distinct_days
4. **KG rule metadata 生效**: 运行一次 maintenance 后，被检索过的 KG rule 节点 metadata 出现 `recall_count` / `last_recalled_at`
5. **Pre-flush 生效**: 接近 soft_threshold 后产生 flush event；如果抽到 durable facts，MEMORY.md 有更新
6. **Pre-flush 重启安全**: gap > 50 时跳过，不做错误抽取
7. **Targets 可重建**: 删除 `recall_targets.json` 后，maintenance 能从 `recall_journal.jsonl` 全量重建
8. **全部 best-effort**: 任何新增逻辑的失败都不阻塞 agent loop、tool 返回、compaction

---

## Risks

| 风险 | 缓解 |
|------|------|
| Prompt 过度触发搜索 | "已注入 Memory section 如果已覆盖就不需要" |
| recall_journal.jsonl 增长 | v1 不做轮转，>10MB 再加按月轮转 |
| recall_targets.json 与 journal 漂移 | journal 为真相源；targets 是 maintenance 派生快照，可删可重建 |
| Pre-flush LLM 额外成本 | best-effort + 30s timeout + skip-if-cursor-covers |
| Pre-flush 重启后 gap 过大 | >50 message 上限，跳过而非错误抽取 |
| pre-flush 坐标系搞混 | 只在 pre-turn compaction 启用，并显式传 `persisted_history` |
| merge_extracted_facts 去重不精确 | case-insensitive 文本匹配在实践中够用；比 openclaw 的 append-only daily file 弱，但 TheOS 没有 daily notes 体系 |

---

## Out of Scope

- openclaw capability/supplement registry（`memory-state.ts`）
- builtin/QMD 双后端（`manager.ts` + `qmd-manager.ts`）
- Light/REM/Deep 三阶段 dreaming（`dreaming-phases.ts`）
- ToolContext 扩展 routing_domains
- recall → instinct PROBATION promotion
- KG schema 变更
- daily notes 体系
