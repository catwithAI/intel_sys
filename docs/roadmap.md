# Roadmap

本文档描述当前代码状态下的实际路线图，不再保留“纯设计期”的模块拆解视角。

## 当前状态

### 已稳定存在的主系统能力

- `FastAPI + APScheduler + RuleRegistry` 作为统一运行时
- `Polymarket` 异动检测 + 6 小时 digest
- `GitHub` 每日项目发现
- `Hacker News` 每日话题推送
- `CLS / 雪球 / Reddit` 持续入池
- `EventMemoryPool` + `Correlation` 每日关联推理
- `FeishuWebhookDelivery` 支持单条卡片与多源 digest

### 已落地的 defense Phase 1

`defense` 已经从 design 进入实现，当前代码中已经存在：

- `SourceSpec / RawEvent / NormalizedEvent / CollectorResult`
- `SourceLoader`
- `RSSCollector`
- `DomainRateLimiter`
- `normalize / dedup / score / converter`
- `DefenseStorage` 与 `SourceHealthManager`
- `ingest_defense_news` 规则
- `sources/defense_news.yaml` 种子源
- `tests/defense_tasks/` 阶段性测试

### 当前明确限制

- collector 仍只有 `rss`
- score 仍是规则分，尚无 defense 专用 LLM relevance 漏斗
- dedup 仍为 `url_hash + content_hash`
- PostgreSQL 仅用于 defense
- 防务事件进入通用 `EventMemoryPool`，尚未有 defense 专用 memory prompt

## 已完成里程碑

### M0 运行时骨架

- [x] FastAPI lifespan
- [x] Redis 初始化
- [x] AI client
- [x] 调度器与规则注册
- [x] 调试路由

### M1 多信源规则系统

- [x] Polymarket
- [x] GitHub
- [x] Hacker News
- [x] CLS
- [x] Xueqiu
- [x] Reddit
- [x] Correlation

### M2 Defense RSS 管线

- [x] YAML source registry
- [x] RSS collector
- [x] per-domain rate limit
- [x] conditional fetch (`ETag` / `Last-Modified`)
- [x] negative cache
- [x] source health state machine
- [x] PostgreSQL run history / source health / normalized event persistence
- [x] defense Feishu digest

## 近期优先级

## P0 稳定当前 defense 实现

目标：把现有 defense Phase 1 从“代码已在仓库中”提升到“本地可稳定跑通”。

任务：

- 补齐并验证运行环境依赖
  - `feedparser`
  - `pytest-asyncio`
  - PostgreSQL 可选链路
- 跑通 `tests/defense_tasks`
- 修正文档与启动说明，使 `.env`、PG、defense webhook 配置清晰
- 验证 `sources/defense_news.yaml` 中 RSS 源的可用性与返回质量

验收标准：

- `pytest -q tests/defense_tasks` 可在完整 dev 环境下通过
- `POST /debug/trigger/ingest_defense_news` 在本地可跑通
- `/debug/defense/health` 和 `/debug/defense/runs` 返回有效数据

## P1 扩展 defense collector 能力

目标：把 defense 从 RSS-only 扩展到更接近实际 source landscape。

任务：

- `html_list` collector
- `json_api` collector
- collector registry 真正接入多 collector 分发
- source YAML 扩展到多个文件，例如：
  - `defense_news.yaml`
  - `defense_reports.yaml`
  - `strategic_docs.yaml`

验收标准：

- 至少支持 `rss + html_list`
- `SourceLoader` 能从多个 `defense_*.yaml` 文件加载配置
- 同一轮 run 能同时处理多 collector 源

## P2 升级 defense 信噪比控制

目标：从基础规则分升级到更可靠的筛噪流水线。

任务：

- 增加 defense 专用 LLM batch relevance
- 增加 defense 专用摘要/翻译字段
- 细化 source weight、authority tier、junk pattern 规则
- 增加更明确的 alert routing：`memory-first, alert-selective`

验收标准：

- 低价值 RSS 噪声明显下降
- defense alerts 数量受控，不被 ceremony / personnel 类内容淹没

## P3 升级 defense 去重与事件聚合

目标：解决多站转载、同事件多标题、跨源重复问题。

任务：

- 近重复检测
- event clustering
- cluster 级别的 corroboration / priority
- canonical event 选取

验收标准：

- 同一事件的多源报道不会重复推送多条 alert
- cluster 信息可用于后续关联推理

## P4 把 defense 接入更完整的推理链

目标：不只“抓到并发出去”，而是进入系统性分析。

任务：

- defense 专用 memory compress prompt
- defense 分类体系
- defense 事件在 correlation 中更高质量地参与推理
- defense / macro / github / polymarket 之间的跨源联动分析

验收标准：

- defense 事件进入记忆池后能产出稳定的关联洞察
- 分类不再被通用 `other` 吞掉

## 中长期方向

### 1. 运维与观测

- metrics / tracing
- run-level dashboard
- source health 可视化
- Feishu / PG / Redis 失败告警

### 2. 更强的执行边界

- scheduler 并发保护
- per-rule timeout / circuit breaker
- source-level backoff 策略细化

### 3. 交互与产品化

- dashboard 从静态页升级为真正的前端
- 更完整的 alert 检索能力
- 用户自定义规则 / source 配置

## 不在当前优先级内

- Facebook collector
- YouTube transcript 抽取
- defense 复杂语义聚类
- Kubernetes / 多实例调度

## 现在最值得先做的 5 件事

1. 跑通 `defense_tasks` 测试和完整依赖。
2. 校准 `sources/defense_news.yaml` 的 feed 可用性。
3. 把 `html_list` collector 做出来，摆脱 RSS-only。
4. 给 defense 增加专用 LLM relevance / summary prompt。
5. 把去重从双 hash 升级到近重复聚类。
