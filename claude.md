Project: Event-Driven Intelligence & Decision System (V1)

## 1. 项目愿景

构建一个可扩展的信息情报平台。核心抽象：

> **对外部信号源进行持续观测，当观测值满足预设规则时，引入 LLM 进行深度分析，并将分析结果推送给用户。**

统一数据模型：

```
Source → Event → Rule(filter + score) → AI(enrich) → Alert → Delivery
```

- **Source**: 数据来源的抽象（Polymarket API、GitHub Trending、Hacker News 等）
- **Event**: Source 产生的标准化数据单元（一次 orderbook 异动、一个新 trending repo、一篇热门帖子）
- **Rule**: 对 Event 的过滤和评分逻辑，决定是否值得进入 AI 分析
- **AI Enrichment**: 对通过 Rule 的 Event 进行 LLM 深度分析，附加结构化洞察
- **Alert**: Rule + AI 的最终产物，包含原始事件、分析结论、严重程度
- **Corroboration**: 对 Alert 搜索 HN/Twitter 佐证，计算置信度增减
- **Delivery**: Alert 的分发通道（REST API + 飞书 Webhook 机器人）

## 2. 核心架构

### 2.1 分层设计

```
┌─────────────────────────────────────────────────────┐
│  Delivery (分发层)                                    │
│  飞书 Webhook 机器人 / REST API                       │
├─────────────────────────────────────────────────────┤
│  Corroboration (佐证层)                               │
│  HN Algolia + Twitter API 交叉验证                    │
├─────────────────────────────────────────────────────┤
│  Reasoning (智能层)                                   │
│  OpenRouter (Gemini 3 Flash Preview)                 │
│  结构化 Prompt 模板 + JSON 解析                        │
├─────────────────────────────────────────────────────┤
│  Pipeline (逻辑层)                                    │
│  Rule Registry + 装饰器注册 + 调度声明                  │
├─────────────────────────────────────────────────────┤
│  Source (信源层)                                      │
│  Polymarket / GitHub / Hacker News / Twitter(佐证)   │
├─────────────────────────────────────────────────────┤
│  Engine (引擎层)                                      │
│  FastAPI + asyncio + APScheduler + Redis              │
└─────────────────────────────────────────────────────┘
```

### 2.2 基础框架与生命周期

- **Framework**: FastAPI (ASGI)，利用 `lifespan` 管理启动（加载规则、建立 Redis 连接、初始化 Delivery、启动调度器）与关闭。
- **Task Management**: asyncio 并发处理多信源监控。Polymarket 的秒级轮询、GitHub 的日级定时任务、HN 的小时级轮询互不阻塞。
- **Scheduler**: APScheduler 管理定时任务（GitHub 每日采集）与间隔任务（Polymarket 轮询、HN 轮询）。

## 3. 信源层详细设计

### 3.1 Polymarket 预测市场异常监控

#### 数据源

| API | Base URL | 认证 | 限速 | 用途 |
|-----|----------|------|------|------|
| Gamma API | `https://gamma-api.polymarket.com` | 无 | 300 req/min | 市场发现、元数据、成交量、价格变动 |
| CLOB API | `https://clob.polymarket.com` | 读取无需认证 | 100 req/min | 实时定价、Orderbook |

#### 两层漏斗架构

**设计动机**：原 Top 25 方案覆盖率仅 ~1.8%。Gamma API 已返回丰富字段（`volume24hr`、`volume1wk`、`oneDayPriceChange`、`oneHourPriceChange`、`bestBid`、`bestAsk`、`outcomePrices` 等），可零 CLOB 调用筛选全部 ~6,800 个有效市场。

```
Tier 1: 广域扫描 (1 次 Gamma API 调用, ~6800 市场, 零 CLOB)
  ↓ 轻量信号 + breaking_score 筛选
  ↓ 约 20-30 个候选 (上限 pm_wide_max_tier2=50)
Tier 2: 深度分析 (CLOB orderbook + Redis 基线 + AI)
  ↓ ~60-120 次 CLOB 调用 (在 100 req/min 限速内)
  ↓ 告警 + HN 佐证 + 飞书推送
```

**Tier 1 广域扫描信号**（纯 Gamma 字段，零 CLOB）：

1. **成交量突增 (Wide: Volume Spike)** — `volume_24h / (volume_1wk / 7)` ≥ 2.0
2. **价格速度 1d (Wide: Price Velocity 1d)** — `|oneDayPriceChange|` ≥ 0.05 (5%)
3. **价格速度 1h (Wide: Price Velocity 1h)** — `|oneHourPriceChange|` ≥ 0.03 (3%)
4. **价差异常 (Wide: Spread Anomaly)** — `spread` ≥ 0.10
5. **Volume Floor**: `volume_24h` ≥ $1,000（前置过滤，非信号）

**Breaking Score** = `vol_component * 0.4 + price_component * 0.3 + spread_component * 0.3`，阈值 0.3。

**Tier 2 深度分析信号**（CLOB + Redis 历史）：

1. **成交量突增 (Deep: Volume Spike)** — `volume_24h / EMA基线` ≥ 3x（Redis EMA）
2. **Orderbook 失衡 (Deep: Orderbook Imbalance)** — `total_bid / (total_bid + total_ask)`（前 10 档）≥ 0.7 或 ≤ 0.3
3. **价格急速变动 (Deep: Price Velocity)** — 相邻两次检测间价格变动 ≥ 5%（Redis 快照）

> 注意：Tier 1 信号首次运行即可触发（Gamma 字段始终可用）。Tier 2 的 Volume Spike 和 Price Velocity 依赖 Redis 历史，首次运行仅写入基线。

#### 数据流（`fetch_wide()` → `_tier1_screen()` → `fetch_selected()` → `_tier2_analyze()`）

```
APScheduler 间隔触发(90s)
  → Gamma API (limit=500): 获取全量 ~6800 有效市场
  → Tier 1 筛选: breaking_score ≥ 0.3 → ~20-30 候选
  → CLOB API: 仅对候选并发获取 orderbook + midpoint (Semaphore=8, 429 重试)
  → Tier 2: Redis 基线对比 + 信号计算
  → Tier 1 + Tier 2 信号合并
  → LLM 分析 (中文翻译 + 具体交易建议 + 地缘影响)
  → HN 佐证 (skip_twitter=True 避免 429)
  → 存储告警到 Redis (24h 去重) → 飞书推送
```

#### 保留方法

- `fetch()`: 原 Top N 方法，保留用于调试
- `fetch_wide()`: Tier 1 广域扫描（零 CLOB）
- `fetch_selected(markets)`: Tier 2 定向 CLOB 查询

### 3.2 GitHub Trending 项目发现

#### 数据源

- **主方案**: GitHub Search API — `GET /search/repositories?q=topic:{topic}&sort=stars&order=desc`，认证后 5000 req/h
- **辅助方案**: gtrending 库 — 抓取 GitHub Trending 页面，按语言过滤，作为补充信号

#### 发现算法

采用双策略合并：

1. **Star-delta 异常检测** — 每次运行记录 star 数快照，下次运行计算增量，超过阈值（默认 50）判定为异常增长
2. **gtrending 补充** — 从 Trending 页面获取当日热门项目（按语言），直接进入候选池
3. **合并去重** — 两个策略的结果按 `full_name` 合并，同时命中的项目标记为 `star_delta+gtrending`

#### 双流处理

- **新项目流**: 首次发现的项目，获取 README → AI 评估（recommend: worth_promoting / worth_watching / skip）
- **返回项目流**: 已推送过的项目再次出现，获取近期合并 PR → AI 分析更新动态

#### 数据流

```
APScheduler Cron(每日 09:00)
  → GitHub Search API: 按 topics 查询(每 topic 拉取 N 个)
  → gtrending: 按语言获取 trending 项目
  → Star-delta 计算 + gtrending 合并
  → Redis 去重(30天窗口)
  → 新项目流: 按 star delta 排序取 Top 20 → LLM 分析 README → 佐证 → 飞书推送
  → 返回项目流: 取 Top 10 → 获取合并 PR → LLM 分析更新 → 飞书推送
```

### 3.3 Hacker News 热门话题发现

#### 数据源

- **HN Algolia API** — `https://hn.algolia.com/api/v1`

#### 发现算法

合并两个策略：

1. **Front Page** — 首页热门故事，`points ≥ hn_front_page_min_points`（默认 100）
2. **Rising** — 最近 N 小时内上升的故事，`points ≥ hn_rising_min_points`（默认 30）

按 `objectID` 去重，front_page 优先。取 Top N（默认 15）后获取评论，调用 AI 分析。

#### 数据流

```
APScheduler 间隔触发(7200s)
  → HN Algolia: front_page + rising 合并
  → Redis 去重(7天窗口) → 按点数排序取 Top 15
  → 获取热门评论(每篇前 5 条) → LLM 话题分析
  → 存储告警 → 飞书推送
```

### 3.4 Twitter（佐证用）

- **API**: twitterapi.io — `GET /twitter/tweet/advanced_search`
- **角色**: 仅用于 Corroboration 层，不是独立信源
- **注意**: Polymarket 规则中 `skip_twitter=True`，避免高频调用触发 429

## 4. 规则层设计

### 4.1 Rule Registry

基于装饰器的单例注册机制（`app/engine/registry.py`），启动时扫描 `app/rules` 包下所有模块，自动触发装饰器注册。

```python
@rule_registry.register(
    source="polymarket",
    schedule="interval:90s",
    trigger="threshold"
)
async def detect_polymarket_anomalies(ctx: RuleContext) -> bool:
    ...
```

### 4.2 RuleContext

```python
@dataclass
class RuleContext:
    data: dict              # 标准化的事件数据
    ai: AIClient            # LLM 调用接口
    db: Redis               # 状态持久化（去重、基线数据、快照）
    config: RuleConfig      # 当前规则的配置（name, source）
    delivery: BaseDelivery  # 告警分发（飞书/Noop）
    logger: Logger          # 结构化日志
```

### 4.3 已实现的规则

| 规则 | 信源 | 调度 | 触发方式 | 文件 |
|------|------|------|---------|------|
| `detect_polymarket_anomalies` | polymarket | `interval:90s` | threshold | `app/rules/polymarket_rules.py` |
| `discover_trending_repos` | github | `cron:0 9 * * *` | batch | `app/rules/github_rules.py` |
| `discover_hn_hot_topics` | hackernews | `interval:7200s` | batch | `app/rules/hackernews_rules.py` |

### 4.4 规则加载

启动时全量扫描注册，不做热加载。提供 `POST /system/reload` 接口触发重新扫描（清空旧注册 → 删除缓存模块 → 重新 import）。

## 5. 智能层设计

- **Provider**: OpenRouter（`https://openrouter.ai/api/v1/chat/completions`）
- **Model**: 可配置，默认 `google/gemini-3-flash-preview`
- **调用方式**: `AIClient` 封装 — Jinja2 模板渲染 → HTTP POST → JSON 解析（自动处理 markdown code block）
- **Temperature**: 0.3

### Prompt 模板

```
prompts/
├── polymarket/
│   └── anomaly_analysis.jinja2      # 异动分析 + 交易建议
├── github/
│   ├── project_evaluation.jinja2    # 新项目评估
│   └── project_update.jinja2        # 项目更新分析
└── hackernews/
    └── topic_analysis.jinja2        # 话题分析
```

## 6. 佐证层设计

### 6.1 CorroborationService（`app/corroboration/service.py`）

对已生成的 Alert 搜索 HN + Twitter 佐证，计算置信度增减。

- **HN 贡献**: points ≥ 100 (+0.15)，≥ 30 (+0.10)，≥ 5 (+0.05)
- **Twitter 贡献**: likes ≥ 100 或 followers ≥ 50k (+0.10)，likes ≥ 20 (+0.05)
- **跨平台奖励**: HN + Twitter 同时命中 (+0.05)
- **无证据惩罚**: -0.05
- **上限**: 0.30
- **超时保护**: `sm_corroboration_timeout`（默认 10s）
- **skip_twitter**: Polymarket 规则传入 `skip_twitter=True` 避免 429

### 6.2 QueryBuilder（`app/corroboration/query_builder.py`）

根据告警类型构建搜索查询：
- **Polymarket**: event_slug、问题关键词、"polymarket" + 问题截断
- **GitHub**: full_name、name + description 关键词
- **HackerNews**: 返回空（不自我佐证）

## 7. 分发层设计

### 7.1 REST API

- `GET /alerts/{source}` — 告警列表（支持 `limit`/`offset` 分页）
- `GET /alerts/{source}/{alert_id}` — 单条告警详情
- `GET /debug/events/{source}` — 最近 5 条告警（调试用）

### 7.2 飞书 Webhook 机器人

- 实现类：`FeishuWebhookDelivery`（`app/delivery/feishu.py`）
- 通过 httpx POST 飞书自定义机器人 Webhook URL，无需飞书 SDK
- 支持 HMAC-SHA256 签名校验（可选，配置 `FEISHU_WEBHOOK_SECRET` 后自动启用）
- 消息格式：Card JSON v2（`schema: "2.0"`，`body.elements`）+ `collapsible_panel` 折叠面板
  - Header 颜色根据 severity：CRITICAL=红, HIGH=橙, MEDIUM=蓝, LOW=灰
  - **GitHub 卡片**：摘要 + 仓库链接始终可见；详细信息（语言/Star/Fork/新功能/PR 等）折叠
  - **Polymarket 卡片**：
    - 始终可见：中文翻译 + AI 摘要 + 市场链接 + **交易建议**（含具体买入项、价格、回报率）
    - 折叠：异动信号（区分广域扫描/深度分析）、地缘影响
    - `breaking_score ≥ 2.0` 时显示 BREAKING 标识
  - **HackerNews 卡片**：摘要 + 原文/HN 链接始终可见；详情、洞察、影响评估折叠
  - **佐证面板**：如有佐证，附加折叠面板显示 HN 故事 + 推文
  - v1 fallback：若 webhook 不支持 v2 格式，自动降级为 v1（展开所有折叠面板为普通 div）
- 降级机制：`FEISHU_WEBHOOK_URL` 为空时使用 `NoopDelivery`（空实现，不推送）
- 失败处理：`send()` 内部 catch 异常，只记日志不阻塞 Rule 执行

### 7.3 Polymarket AI 分析增强

Prompt 模板（`prompts/polymarket/anomaly_analysis.jinja2`）输出以下结构化字段：

| 字段 | 说明 |
|------|------|
| `summary` | 一句话中文摘要 |
| `question_zh` | 市场问题中文翻译 |
| `likely_cause` | 异动原因分类 |
| `confidence` | 0.0-1.0 置信度 |
| `analysis` | 详细分析段落 |
| `severity` | 严重程度 |
| `action_items` | 建议操作列表 |
| `trading_suggestion` | `{direction, outcome, price, reasoning}` — 交易建议，含具体买入选项和价格 |
| `geopolitical_impact` | 地缘政治/货币政策/战争冲突影响分析 |

Prompt 上下文包含 `outcomes_with_prices`（每个选项及其当前价格），AI 输出具体买入项和价格。规则层将 `signals`、`anomaly_score`、`breaking_score`、`outcome_prices` 注入 `event.data`，使 delivery 层可直接访问。

## 8. 状态管理

Redis 用于以下场景：

| Key Pattern | 类型 | TTL | 用途 |
|------------|------|-----|------|
| `alerts:{source}` | List | 无 | 告警存储（LPUSH + LTRIM 环形缓冲） |
| `gh:repo:{full_name}:star_snapshot` | JSON | 30d | Star 数快照（用于 star-delta 计算） |
| `gh:repo:{full_name}:pushed` | JSON | 30d | 上次推送时间戳 + Star 数 |
| `pm:market:{id}:baseline` | JSON | 7d | 成交量 EMA 基线 |
| `pm:market:{id}:last_price` | String | 1h | 价格快照（用于 velocity 计算） |
| `pm:alert:{id}:sent` | String | 24h | Polymarket 告警去重 |
| `hn:story:{id}:pushed` | String | 7d | HackerNews 去重 |

## 9. 调试与观测

### Debug Endpoints

| 端点 | 方法 | 说明 |
|------|------|------|
| `/debug/rules` | GET | 已注册规则列表及调度信息 |
| `/debug/events/{source}` | GET | 最近 5 条告警 |
| `/debug/state/{key}` | GET | 查询任意 Redis key |
| `/debug/scheduler` | GET | 调度任务列表及下次执行时间 |
| `/debug/scheduler/pause/{rule_name}` | POST | 暂停单条规则的调度 |
| `/debug/scheduler/resume/{rule_name}` | POST | 恢复单条规则的调度 |
| `/debug/scheduler/pause-source/{source}` | POST | 暂停某信源下所有规则 |
| `/debug/scheduler/resume-source/{source}` | POST | 恢复某信源下所有规则 |
| `/debug/trigger/{rule_name}` | POST | 手动触发单条规则 |
| `/debug/trigger-source/{source}` | POST | 手动触发某信源下所有规则 |
| `/system/reload` | POST | 重载所有规则（无需重启） |

### 其他

- `/dashboard` — 静态 HTML Dashboard 页面（`static/index.html`）
- `/` — 系统状态（名称、版本、已加载规则数）

## 10. 项目结构

```
intel_sys/
├── app/
│   ├── main.py              # FastAPI 入口、lifespan、execute_rule
│   ├── config.py            # Pydantic Settings（.env 配置）
│   ├── models.py            # Event, Alert, AIEnrichment, Severity, RuleConfig, SourceType
│   ├── engine/
│   │   ├── registry.py      # RuleRegistry 单例 + @register 装饰器 + RuleMeta
│   │   ├── context.py       # RuleContext (data, ai, db, config, delivery, logger)
│   │   └── scheduler.py     # APScheduler 封装 (interval/cron 解析)
│   ├── sources/
│   │   ├── base.py          # BaseSource 抽象基类
│   │   ├── polymarket.py    # Gamma API + CLOB API (fetch_wide / fetch_selected / fetch)
│   │   ├── github.py        # GitHub Search API + gtrending + README 抓取
│   │   ├── hackernews.py    # HN Algolia API (front_page / rising / search)
│   │   └── twitter.py       # twitterapi.io (佐证用，非独立信源)
│   ├── rules/
│   │   ├── polymarket_rules.py   # 两层漏斗 (Tier1 广域 + Tier2 深度)
│   │   ├── github_rules.py       # 新项目发现 + 返回项目更新
│   │   └── hackernews_rules.py   # HN 热门话题发现
│   ├── ai/
│   │   └── client.py        # OpenRouter 客户端 + Jinja2 模板渲染 + JSON 解析
│   ├── delivery/
│   │   ├── base.py          # BaseDelivery 抽象基类 (send + close)
│   │   └── feishu.py        # FeishuWebhookDelivery + NoopDelivery
│   ├── corroboration/
│   │   ├── service.py       # CorroborationService (HN + Twitter 交叉验证)
│   │   └── query_builder.py # 告警 → 搜索查询转换
│   └── routes/
│       ├── alerts.py        # GET /alerts/{source}, GET /alerts/{source}/{id}
│       └── debug.py         # 调试、调度控制、手动触发、规则重载
├── prompts/
│   ├── polymarket/
│   │   └── anomaly_analysis.jinja2      # 异动分析 + 交易建议
│   ├── github/
│   │   ├── project_evaluation.jinja2    # 新项目评估
│   │   └── project_update.jinja2        # 项目更新分析
│   └── hackernews/
│       └── topic_analysis.jinja2        # 话题分析
├── static/
│   └── index.html           # Dashboard 页面
├── pyproject.toml            # Hatch 构建配置 + 依赖声明
├── .env.example              # 环境变量模板
└── CLAUDE.md                 # 本文件
```

## 11. 技术栈

| 组件 | 选型 | 说明 |
|------|------|------|
| Web 框架 | FastAPI | ASGI，异步原生 |
| 任务调度 | APScheduler 3.x | AsyncIOScheduler，支持 Cron + Interval |
| 状态存储 | Redis | hiredis 加速，decode_responses=True |
| LLM 服务 | OpenRouter | 默认模型: Gemini 3 Flash Preview |
| Prompt 引擎 | Jinja2 | 模板化管理 |
| Polymarket 数据 | httpx | Gamma API (市场) + CLOB API (orderbook) |
| GitHub 数据 | httpx + gtrending | Search API + Trending fallback |
| Hacker News | httpx | Algolia Search API |
| Twitter 佐证 | httpx | twitterapi.io（按量付费） |
| 飞书推送 | httpx | 自定义机器人 Webhook，无需 SDK |
| 配置管理 | Pydantic Settings | `.env` 文件 + 环境变量 |
| 构建系统 | Hatch | `pyproject.toml` 声明式配置 |

## 12. V1 开发路线图

### V1（当前阶段）

- [x] 核心引擎: FastAPI + lifespan + APScheduler + Redis
- [x] 规则注册机制: 装饰器模式 + 自动发现加载 + 热重载
- [x] 信源适配: Polymarket (Gamma + CLOB API)
- [x] 信源适配: GitHub (Search API + gtrending + README 抓取)
- [x] 信源适配: Hacker News (Algolia API — front_page + rising)
- [x] 信源适配: Twitter (twitterapi.io — 佐证用)
- [x] 规则: Polymarket 两层漏斗 — 全量 ~6800 市场广域扫描 + CLOB 深度分析
- [x] 规则: GitHub Trending 项目发现 (star-delta + gtrending) + 返回项目更新检测
- [x] 规则: Hacker News 热门话题发现
- [x] AI 管线: OpenRouter + Jinja2 Prompt 模板 + JSON 解析
- [x] Polymarket AI 增强: 中文翻译、具体交易建议(买入项+价格+回报率)、地缘影响分析
- [x] Social Media 佐证: HN + Twitter 交叉验证，置信度增减
- [x] 告警 REST API + 调试端点 + 调度控制
- [x] 飞书 Webhook 机器人推送 (Card JSON v2 + collapsible_panel 折叠面板，v1 fallback)
- [x] Dashboard 静态页面
- [ ] 集成测试

### V2

- [ ] Polymarket WebSocket 实时流（替代轮询）
- [ ] 可疑钱包行为分析（新钱包 + 大额押注、高胜率 + 冷门市场）
- [ ] 交叉信源验证（Polymarket 异动 + Twitter 舆情关联）
- [ ] 通知消息内嵌操作入口（买入/卖出链接、克隆仓库、关注/转发）
- [ ] Docker Compose 部署

### V3

- [ ] 更多信源接入: Kalshi、X/Twitter、链上数据
- [ ] 前端 Dashboard（替代静态页面）
- [ ] Kubernetes 部署
- [ ] 用户通过 API 自定义告警规则
