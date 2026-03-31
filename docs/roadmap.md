# 防务情报源集成 — Roadmap & 模块设计规划

## 1. 模块拆分总览

基于 `defense-integration-plan.md` 的分层架构，整个系统拆为以下独立可设计、可评审的模块：

```
M1  defense/models.py         数据模型（RawEvent, NormalizedEvent, SourceSpec 等）
M2  defense/source_loader.py  YAML 配置加载与校验
M3  collectors/rss.py         RSS 采集器（含 ETag/Last-Modified 缓存协商）
M4  collectors/registry.py    采集器注册表
M5  defense/normalizer.py     规范化器（URL 规范化、dedup_keys 生成）
M6  defense/deduper.py        三层去重器
M7  defense/scorer.py         Stage 1 硬过滤 + Stage 2 规则打分
M8  defense/rate_limiter.py   域名级并发限速
M9  defense/converter.py      NormalizedEvent → Event 转换
M10 rules/defense_*.py        4 个规则（串联 pipeline）
M11 sources/*.yaml            种子源 YAML 配置
M12 集成改造                    models.py, config.py, pyproject.toml 的修改
M13 defense/storage.py        PostgreSQL 持久化层
```

每个模块先完成设计文档评审，全部通过后再进入代码实现。

---

## 2. 模块间依赖关系

```
M1 (models) ← 所有模块的基础
  ├── M2 (source_loader) ← 依赖 SourceSpec 定义
  ├── M3 (rss collector) ← 依赖 RawEvent, SourceSpec
  ├── M5 (normalizer) ← 依赖 RawEvent, NormalizedEvent, SourceSpec
  ├── M6 (deduper) ← 依赖 NormalizedEvent
  ├── M7 (scorer) ← 依赖 NormalizedEvent, SourceSpec
  └── M9 (converter) ← 依赖 NormalizedEvent, app.models.Event

M4 (collector registry) ← 依赖 M3
M8 (rate_limiter) ← 独立，无模型依赖
M10 (rules) ← 依赖 M2-M9, M13 所有模块
M11 (yaml) ← 依赖 M1 的 SourceSpec 结构定义
M12 (集成改造) ← 依赖 M1, M13（lifespan 中初始化 PG 连接池）
M13 (storage) ← 依赖 M1（NormalizedEvent 定义）+ asyncpg
```

**设计顺序建议**：M1 → M11 → M2 → (M3, M5, M6, M7, M8, M13 并行) → M4 → M9 → M10 → M12

---

## 3. 每个模块的设计文档要求

每个模块设计文档应包含以下部分：

```
1. 职责边界     — 这个模块做什么，不做什么
2. 输入/输出    — 数据结构、函数签名
3. 核心逻辑     — 算法、流程、状态管理
4. 错误处理     — 异常场景、降级策略
5. 配置项       — 依赖哪些 config 字段
6. 与其他模块的接口 — 谁调用它、它调用谁
7. 已知限制     — Phase 1 明确不做什么
8. 验证方式     — 如何测试这个模块
```

---

## 4. 各模块设计状态跟踪

| 模块 | 状态 | 设计文档路径 | 备注 |
|------|------|-------------|------|
| M1 defense/models.py | 待设计 | `docs/modules/m1-models.md` | 所有模块的基础，最先设计 |
| M2 source_loader.py | 待设计 | `docs/modules/m2-source-loader.md` | YAML schema 需要与 M1 对齐 |
| M3 rss collector | 待设计 | `docs/modules/m3-rss-collector.md` | 含 ETag/Last-Modified、feedparser 细节 |
| M4 collector registry | 待设计 | `docs/modules/m4-collector-registry.md` | 简单映射，设计量最小 |
| M5 normalizer | 待设计 | `docs/modules/m5-normalizer.md` | URL 规范化规则、dedup_keys 生成 |
| M6 deduper | 待设计 | `docs/modules/m6-deduper.md` | Redis 交互、pipeline batch、TTL |
| M7 scorer | 待设计 | `docs/modules/m7-scorer.md` | 打分公式、.mil 上下文规则 |
| M8 rate_limiter | 待设计 | `docs/modules/m8-rate-limiter.md` | per-domain Lock 设计 |
| M9 converter | 待设计 | `docs/modules/m9-converter.md` | 字段映射、data/metadata 结构 |
| M10 rules | 待设计 | `docs/modules/m10-rules.md` | pipeline 编排、并发模型 |
| M11 yaml configs | 待设计 | `docs/modules/m11-yaml-configs.md` | 12 个种子源 + RSS URL 验证 |
| M12 集成改造 | 待设计 | `docs/modules/m12-integration.md` | SourceType、config、依赖、PG 连接池 |
| M13 defense/storage.py | 待设计 | `docs/modules/m13-storage.md` | PostgreSQL 持久化层 |

---

## 5. 需要讨论/确认的问题

### 5.1 数据模型 (M1)

**Q1: NormalizedEvent 用 dataclass 还是 Pydantic BaseModel？**

plan 中用 `@dataclass`。但现有系统的 Event/Alert/MemoryEvent 全部是 Pydantic `BaseModel`。

| 方案 | 优点 | 缺点 |
|------|------|------|
| dataclass | 轻量、无序列化开销 | 与现有模型风格不一致；无自动校验 |
| BaseModel | 与现有一致；自动校验和序列化 | 略重；RawEvent/NormalizedEvent 是中间态，不需要持久化 |
| dataclass 但 SourceSpec 用 BaseModel | SourceSpec 需要从 YAML 反序列化，适合 BaseModel；中间态用 dataclass | 混用两种风格 |

建议：**RawEvent/NormalizedEvent 用 dataclass（中间态、不持久化），SourceSpec 用 Pydantic BaseModel（需要校验 YAML 输入）**。

**Q2: SourceFamily/SourceTier 等枚举是否用 str Enum？**

现有 SourceType 是 `str, Enum`，建议保持一致。

**Q3: dedup_keys 的 value 类型**

plan 中 `dedup_keys: dict[str, str]`。建议明确：
- key: `"url_hash"` / `"content_hash"` / `"simhash"`（Phase 3）
- value: hex string（MD5 32 位 or 截断）

### 5.2 YAML 配置 (M2, M11)

**Q4: YAML 文件结构 — 顶层是 list 还是 dict？**

plan 中是直接 list：
```yaml
- id: breakingdefense
  ...
- id: defensenews
  ...
```

另一种方案是带 metadata 的 dict：
```yaml
version: "1.0"
family: news
sources:
  - id: breakingdefense
    ...
```

建议：**顶层直接 list**，简单直接，YAML 文件名已经隐含了 family 信息。

**Q5: YAML schema 校验时机**

- 方案 A：加载时 fail-fast，格式错误的源直接拒绝加载
- 方案 B：加载时 warn，跳过格式错误的源，继续加载其他

建议：**方案 B（warn + skip）**，符合 plan 中的 fail-open 原则。

**Q6: 种子源 RSS URL 需要提前验证**

12 个种子源的 RSS feed URL 需要在设计阶段手动验证可用性。部分站点的 RSS 可能：
- 不存在
- 需要特殊路径（如 defensenews 是 `/arc/outboundfeeds/rss/`）
- 返回 403

建议：**M11 设计阶段逐一验证 10 个 feed URL 的可用性和响应格式**。

### 5.3 RSS 采集器 (M3)

**Q7: ETag/Last-Modified 状态存 Redis 还是内存？**

| 方案 | 优点 | 缺点 |
|------|------|------|
| 内存 dict | 简单、快 | 重启丢失，首次 run 必全量拉取 |
| Redis | 跨重启持久化 | 多一次 Redis 交互 |

建议：**Phase 1 用内存**。30 分钟间隔 × 10 个源，重启后全量拉一次也只是 ~100 条事件（被去重兜住）。Phase 3 源数量增大后迁 Redis。

**Q8: feedparser 是否需要异步包装？**

`feedparser.parse()` 是同步 CPU-bound 操作（解析 XML）。10 个 feed × 20-30 条 entry 在毫秒级完成，Phase 1 不需要异步包装。Phase 3 扩到 100+ 源后可考虑 `asyncio.to_thread()`。

**Q9: RSS entry 缺失 published_at 时如何处理？**

部分 RSS entry 没有日期字段。方案：
- A：丢弃（可能丢失有价值内容）
- B：使用当前时间作为 fallback
- C：使用 feed 级别的 `updated` 时间

建议：**B（当前时间 fallback）**。宁可多留，后续被 Stage 2 降分。

### 5.4 去重 (M6)

**Q10: Redis pipeline 批量写入 dedup key 的事务性**

plan 中用 `pipeline()` 批量写入。如果 pipeline 中途失败：
- 部分 key 写入、部分没写入
- 下次 run 可能重复处理已写入 key 对应的事件（被 memory pool L3 兜住）

建议：**不需要事务**。L3 memory pool 去重是最后一道兜底。

**Q11: content_hash 的计算是否应排除日期/作者等变化字段？**

同一文章在不同时间抓取，body 可能包含"发布于 2 小时前"等动态文本。

建议：**Phase 1 先取 `title + body[:1000]` 简单 hash**。如果实际观测到假阳性再针对性处理。

### 5.5 过滤/打分 (M7)

**Q12: Stage 2 打分公式的权重是否应可配置？**

plan 中的权重（whitelist +0.3, blacklist -0.5, ceremony -0.4 等）是硬编码的。

建议：**Phase 1 硬编码**。Phase 6 再做 score calibration。过早可配会增加理解和调试成本。

**Q13: junk_patterns 是全局共享还是 per-source？**

plan 中 `filters.junk_patterns` 是 per-source 的 YAML 配置。但大部分 junk pattern 是通用的（careers, retirement 等）。

建议：**全局默认 junk patterns + per-source 可覆盖**。scorer 内置默认列表，YAML 中的 `junk_patterns` 做增量。

### 5.6 规则编排 (M10)

**Q14: 采集失败的源是否影响同 batch 中其他源的结果？**

plan 中要求 `fail_open=True`。具体实现：

```python
results = await asyncio.gather(*[_fetch_one(s) for s in specs], return_exceptions=True)
# 过滤掉异常结果，只保留成功的
```

建议：**用 `return_exceptions=True`，逐个检查结果类型**。失败源记日志，不中断整个 pipeline。

**Q15: 规则执行超时保护**

10 个 RSS 源 + 域名限速 10s 最坏情况：10 × 15s(timeout) = 150s。加上去重/过滤/LLM 压缩，单次 run 可能超过 3-5 分钟。

建议：**M10 设计中明确超时预算**：
- 采集阶段：≤ 90s（Semaphore=5, 域名限速 10s）
- 去重+过滤+打分：≤ 5s（纯内存）
- LLM 压缩入池：≤ 120s（取决于事件量）
- 总计：≤ 4min（30min 间隔下充分安全）

### 5.7 与现有系统集成 (M12)

**Q16: SourceType.DEFENSE 在 delivery 层的影响**

Phase 1 不生成 Alert，但 `_format_alert()` 中 `SourceType.DEFENSE` 会走 `else → _format_generic_card()`。

建议：**Phase 1 不改 delivery 层**。Phase 2 增加 `_format_defense_card()` 和 `_format_defense_digest_card()`。

**Q17: defense 事件在 correlation prompt 中的标记**

现有 `_build_event_digest()` 按 `ev.source.value.upper()` 生成标签。defense 事件会显示为 `[DEFENSE]`。

确认：这是否足够，还是需要更细的标记如 `[DEFENSE:breakingdefense]`？

建议：**Phase 1 用 `[DEFENSE]` 即可**。Phase 2 如果 correlation 质量不足再加 site_id。

### 5.8 参考项目启发的新问题

**Q18: M3 RSS Collector 返回值是否需要包含 SourceMeta？**

参考 Worldmonitor 的 Seed Framework，每次采集应产出完整的执行元数据（status/duration/recordCount）。

建议：**是**。M3 返回 `CollectorResult`（含 events + SourceMeta），SourceMeta 写入 PG `source_health` 表，驱动 `/debug/defense/health` 端点。这在 Phase 1 实现成本很低（只是 dataclass + 计时），但为后续熔断和健康监控打下基础。

**Q19: M7 Stage 2 打分是否引入 Delta 维度？**

参考 Crucix 的 Delta Engine，打分不应只看静态属性，还应看动态变化。

建议：**Phase 1 仅做基线记录**（每次 run 的 per-site 统计写入 PG `run_history`）。Phase 2 实现完整 Delta：源活跃度突增(+0.2)、多源实体交叉(+0.3)。Phase 1 不需要 Delta 打分因为只有 12 个种子源，数据量不足以形成有意义的基线。

**Q20: Phase 2 告警评估是否采用分层 + 衰减冷却？**

参考 Crucix 的三级告警（FLASH/PRIORITY/ROUTINE）+ 衰减冷却（同一信号重复触发时冷却递增：0→6h→12h→24h）。

建议：**Phase 2 采用**。这比现有系统的"24h 去重"更精细。新增 `M14 defense/alert_evaluator.py`。

**Q21: 是否记录每次 run 的执行历史？**

参考 Crucix 的 hot.json（最近 3 次运行 + 统计），以及 Worldmonitor 的 seed-meta。

建议：**Phase 1 实现**。PG 新增 `run_history` 表，每次 run 记录 {rule_name, started_at, finished_at, status, stats_json, sources_json}。成本极低，但对调试和趋势分析极有价值。`normalized_events` 表增加 `run_id` 字段。

---

## 6. 设计阶段排期建议

```
第 1 轮: M1 (models) + M11 (yaml configs) + M12 (集成改造)
  — 确定所有数据结构和配置 schema
  — 验证 10 个 RSS URL 可用性

第 2 轮: M2 (source_loader) + M3 (rss collector) + M8 (rate_limiter) + M13 (storage)
  — 配置加载 + 数据采集 + 限速 + 持久化
  — 可以写 spike 代码验证 feed 实际返回

第 3 轮: M5 (normalizer) + M6 (deduper) + M7 (scorer) + M9 (converter)
  — 数据处理管线的核心逻辑
  — 可以用第 2 轮 spike 的真实数据验证

第 4 轮: M4 (collector registry) + M10 (rules)
  — 串联所有模块、编排 pipeline
  — 端到端验收
```

每轮设计评审通过后再进入下一轮。全部 4 轮通过后开始代码实现。

---

## 7. 代码实现阶段（设计全部通过后）

### 7.1 实现顺序

```
Step 1: M12 集成改造 (models.py + config.py + pyproject.toml)
Step 2: M1 defense/models.py
Step 3: M8 rate_limiter.py (独立，无依赖)
Step 4: M3 rss collector + M4 registry
Step 5: M2 source_loader.py + M11 yaml configs
Step 6: M5 normalizer + M6 deduper + M7 scorer + M9 converter
Step 7: M10 rules (串联)
Step 8: 端到端验证
```

### 7.2 验收标准

- 10 个 seed 源 RSS 可稳定抓取
- 30 分钟一次 run，无系统性报错
- 去重后事件量下降可观测
- Stage 1 过滤掉明显垃圾
- 入池事件量 ≤ 200/run
- defense 事件出现在 correlation digest 中
- 无 Redis 明显膨胀

---

## 8. Phase 2-6 模块预览

Phase 1 完成后，后续 Phase 涉及的新模块：

| Phase | 新模块 | 设计文档 | 灵感来源 |
|-------|--------|---------|---------|
| Phase 2 | `M14 defense/alert_evaluator.py` | 分层告警评估 + 衰减冷却 | Crucix 告警管道 |
| Phase 2 | `M15 defense/delta.py` | Delta 引擎（源活跃度 + 实体交叉 + 变化检测） | Crucix Delta Engine |
| Phase 2 | `prompts/defense/batch_relevance.jinja2` | LLM 批量评估 prompt | — |
| Phase 2 | `prompts/defense/event_compress.jinja2` | defense 专用压缩 prompt | — |
| Phase 2 | `prompts/defense/deep_analysis.jinja2` | 深度分析 prompt | — |
| Phase 2 | defense alert routing | relevance 路由逻辑 | — |
| Phase 2 | `_format_defense_card()` | 飞书 defense 卡片 | — |
| Phase 3 | `collectors/html_list.py` | HTML 列表页采集器 | — |
| Phase 3 | `defense/learned_routes.py` | URL 学习 + 健康跟踪 + 自动驱逐 | Worldmonitor Learned Routes |
| Phase 3 | `defense/clusterer.py` | simhash 近重复聚类 | — |
| Phase 3 | `defense/cluster_scorer.py` | 多源印证评分 | — |
| Phase 3 | stampede protection | 并发缓存未命中合并 | Worldmonitor inflight Map |
| Phase 4 | `collectors/pdf_metadata.py` | PDF 元数据提取 | — |
| Phase 4 | `collectors/json_api.py` | JSON API 采集器 | — |
| Phase 5 | `collectors/youtube.py` | YouTube 元数据采集 | — |
| Phase 5 | social corroboration integration | 社交佐证集成 | — |
| Phase 6 | source health dashboard | 源健康监控 | Worldmonitor health endpoint |
| Phase 6 | blacklist auto-suggestion | 黑名单自学习 | — |
| Phase 6 | score calibration | 打分校准 | — |

这些模块在对应 Phase 启动前再做详细设计。

---

## 9. 持久化存储设计

### 9.1 问题

当前系统只用 Redis 做状态存储。Redis 适合短期状态（去重 key、缓存、快照），但存在以下问题：

| 数据 | 当前存储 | 问题 |
|------|---------|------|
| NormalizedEvent | 不存储（只存 LLM 压缩后的 MemoryEvent） | 原始完整事件丢失，无法回溯、重算、审计 |
| Alert 历史 | Redis List，LTRIM 100 条 | 超出即丢 |
| Correlation Insight | Redis List，LTRIM 100 条 | 同上 |
| Source 健康状态 | 不存储 | 采集成功率、失败次数、熔断历史无记录 |

NormalizedEvent 是管线中信息最完整的中间态（含标题、正文、站点元数据、打分），后续的 LLM 压缩、聚类、重算都可能需要回溯原始数据。

### 9.2 选型对比

| 数据库 | 适合场景 | 优点 | 缺点 | 结论 |
|--------|---------|------|------|------|
| **PostgreSQL** | 结构化查询、事务 | 强类型索引、JSONB、asyncpg 异步原生、生态成熟 | 全文搜索不如 ES | **推荐** |
| ClickHouse | OLAP 分析 | 聚合极快、压缩率高 | 不擅长点查和更新；Phase 1 数据量不需要 | 过重 |
| MongoDB | 半结构化 | schema 灵活、文档模型 | 事务弱；与 Pydantic 配合不如 PG | 次选 |
| SQLite | 单机嵌入 | 零运维 | 写入并发差、不适合异步 | 排除 |

### 9.3 推荐方案：PostgreSQL

**理由**：

1. NormalizedEvent/Alert/Insight 都是明确 schema，PG 强类型 + 索引最适合
2. `asyncpg` 是 Python 最快的 async PG 驱动，与 FastAPI 配合天然
3. `JSONB` 类型支持 raw_metadata/dedup_keys 的存储和嵌套查询
4. Docker 一键部署，运维成熟
5. 扩展路径清晰：TimescaleDB（时序分区）→ pg_trgm/Elasticsearch（全文搜索）→ Citus（分布式）

### 9.4 Redis 与 PostgreSQL 的分工

```
Redis (热数据、短期状态):
  - 去重 key (defense:dedup:url:*, defense:dedup:content:*, memory:dedup:*)
  - 记忆池 (memory:events Sorted Set) — LLM correlation 的热缓存
  - Polymarket 基线/价格快照
  - 告警缓冲区 (pm:alerts:hourly_buffer)
  - ETag/Last-Modified 缓存

PostgreSQL (持久化、可查询):
  - NormalizedEvent 完整存储 — 回溯、审计、重算
  - Alert 历史 — 不再限制 100 条
  - Correlation Insight 历史
  - Source 健康状态 — 采集成功率、熔断记录
```

### 9.5 写入时机

```
采集 → 规范化 → NormalizedEvent
  → 写入 PostgreSQL (异步, 不阻塞主管线)  ← 在去重之前
  → 去重 (Redis L1/L2)
  → 过滤/打分
  → 转换为 Event
  → 写入记忆池 (Redis)
```

NormalizedEvent 写入 PG 在去重**之前**，这样即使被去重丢弃的事件也有记录，方便后续分析去重效果和调优。

### 9.6 表设计草案

```sql
-- 规范化事件
CREATE TABLE normalized_events (
    id                 BIGSERIAL PRIMARY KEY,
    source_id          TEXT NOT NULL,
    site_id            TEXT NOT NULL,
    site_name          TEXT,
    family             TEXT NOT NULL,
    country            TEXT,
    language           TEXT,
    title              TEXT NOT NULL,
    body               TEXT,
    summary_hint       TEXT,
    url                TEXT,
    canonical_url      TEXT,
    published_at       TIMESTAMPTZ,
    source_weight      REAL,
    extraction_quality REAL,
    pre_score          REAL,
    dedup_keys         JSONB,
    raw_metadata       JSONB,
    created_at         TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_canonical_url UNIQUE (canonical_url)
);

CREATE INDEX idx_ne_site_id ON normalized_events (site_id);
CREATE INDEX idx_ne_family ON normalized_events (family);
CREATE INDEX idx_ne_published_at ON normalized_events (published_at DESC);
CREATE INDEX idx_ne_pre_score ON normalized_events (pre_score DESC);
CREATE INDEX idx_ne_created_at ON normalized_events (created_at DESC);

-- 告警历史
CREATE TABLE alerts (
    id            TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    rule_name     TEXT NOT NULL,
    severity      TEXT NOT NULL,
    title         TEXT,
    event_json    JSONB NOT NULL,
    enrichment    JSONB,
    corroboration JSONB,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_alerts_source ON alerts (source);
CREATE INDEX idx_alerts_created_at ON alerts (created_at DESC);
CREATE INDEX idx_alerts_severity ON alerts (severity);

-- 关联洞察历史
CREATE TABLE correlation_insights (
    id           TEXT PRIMARY KEY,
    title        TEXT,
    category     TEXT,
    confidence   REAL,
    insight_json JSONB NOT NULL,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

-- Source 健康状态
CREATE TABLE source_health (
    site_id              TEXT PRIMARY KEY,
    last_success_at      TIMESTAMPTZ,
    last_failure_at      TIMESTAMPTZ,
    consecutive_failures INT DEFAULT 0,
    total_fetches        INT DEFAULT 0,
    total_failures       INT DEFAULT 0,
    disabled             BOOLEAN DEFAULT FALSE,
    updated_at           TIMESTAMPTZ DEFAULT NOW()
);
```

### 9.7 技术栈

| 组件 | 选型 | 说明 |
|------|------|------|
| 数据库 | PostgreSQL 16+ | Docker 部署 |
| Python Driver | asyncpg | 原生异步，性能最佳 |
| ORM | Phase 1 纯 asyncpg；后续可加 SQLAlchemy 2.0 | 轻量优先 |
| 迁移 | Phase 1 手动 SQL；后续可加 alembic | |

### 9.8 config.py 新增

```python
pg_dsn: str = "postgresql://intel:intel@localhost:5432/intel_sys"
pg_pool_min: int = 2
pg_pool_max: int = 10
```

### 9.9 M13 模块职责

```
M13  defense/storage.py
  - asyncpg.Pool 连接池管理
  - insert_normalized_events(events) → 批量异步写入
  - insert_alert(alert) → 写入告警历史
  - insert_insight(insight) → 写入关联洞察
  - query_events(site_id?, family?, time_range?) → 查询
  - update_source_health(site_id, success/failure) → 更新健康状态
```

Pipeline 中的位置：

```
normalizer → [M13: write PG] → deduper → scorer → converter → memory pool
```

### 9.10 对 M12 集成改造的影响

- `app/main.py` lifespan 中增加 `asyncpg.create_pool()` 初始化
- `app.state.pg_pool` 存储连接池
- `RuleContext` 可选增加 `pg` 字段（或通过 `app.state` 访问）
- `pyproject.toml` 增加 `asyncpg>=0.29.0` 依赖

### 9.11 长期演进

```
Phase 1:   PG 单机 + Redis 热缓存（日均 5000 事件，绰绰有余）
Phase 3-4: TimescaleDB 按月分区 + 冷数据压缩（扩到日均 2-5 万）
Phase 6+:  加 Elasticsearch（全文搜索）；PG 仍做主存储
最终形态:  Kafka 事件总线 + Spark 实时处理 + PG/ClickHouse 持久化
```
