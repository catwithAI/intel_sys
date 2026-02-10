Project: Event-Driven Intelligence & Decision System (V1)

## 1. 项目愿景

构建一个可扩展的信息情报平台。核心抽象：

> **对外部信号源进行持续观测，当观测值满足预设规则时，引入 LLM 进行深度分析，并将分析结果推送给用户。**

统一数据模型：

```
Source → Event → Rule(filter + score) → AI(enrich) → Alert → Delivery
```

- **Source**: 数据来源的抽象（Polymarket API、GitHub Trending 等）
- **Event**: Source 产生的标准化数据单元（一次 orderbook 异动、一个新 trending repo）
- **Rule**: 对 Event 的过滤和评分逻辑，决定是否值得进入 AI 分析
- **AI Enrichment**: 对通过 Rule 的 Event 进行 LLM 深度分析，附加结构化洞察
- **Alert**: Rule + AI 的最终产物，包含原始事件、分析结论、严重程度
- **Delivery**: Alert 的分发通道（REST API + 飞书 Webhook 机器人）

## 2. 核心架构

### 2.1 分层设计

```
┌─────────────────────────────────────────────────────┐
│  Delivery (分发层)                                    │
│  飞书 Webhook 机器人 / REST API                       │
├─────────────────────────────────────────────────────┤
│  Reasoning (智能层)                                   │
│  OpenRouter (Gemini 3 Flash Preview)                 │
│  结构化 Prompt 模板 + JSON 解析                        │
├─────────────────────────────────────────────────────┤
│  Pipeline (逻辑层)                                    │
│  Rule Registry + 装饰器注册 + 调度声明                  │
├─────────────────────────────────────────────────────┤
│  Source (信源层)                                      │
│  Polymarket (Gamma + CLOB API) / GitHub (Search API) │
├─────────────────────────────────────────────────────┤
│  Engine (引擎层)                                      │
│  FastAPI + asyncio + APScheduler + Redis              │
└─────────────────────────────────────────────────────┘
```

### 2.2 基础框架与生命周期

- **Framework**: FastAPI (ASGI)，利用 `lifespan` 管理启动（加载规则、建立 Redis 连接、初始化 Delivery、启动调度器）与关闭。
- **Task Management**: asyncio 并发处理多信源监控。Polymarket 的秒级轮询与 GitHub 的日级定时任务互不阻塞。
- **Scheduler**: APScheduler 管理定时任务（GitHub 每日采集）与间隔任务（Polymarket 轮询）。

## 3. 信源层详细设计

### 3.1 Polymarket 预测市场异常监控

#### 数据源

| API | Base URL | 认证 | 限速 | 用途 |
|-----|----------|------|------|------|
| Gamma API | `https://gamma-api.polymarket.com` | 无 | 300 req/min | 市场发现、元数据、成交量 |
| CLOB API | `https://clob.polymarket.com` | 读取无需认证 | 100 req/min | 实时定价、Orderbook |

#### 监控范围

通过 Gamma API `GET /events?active=true&closed=false` 获取活跃市场列表（限前 100 个 event 下的所有 market），前置过滤无效市场（closed / 不接受下单 / 无 CLOB token），按 `volume24hr` 降序排序后取前 N 个（默认 25，可配置 `PM_TOP_MARKETS`）。对每个 market 的所有 token 并发获取 CLOB orderbook + midpoint，通过 `asyncio.Semaphore`（默认并发上限 8，可配置 `PM_CLOB_CONCURRENCY`）控制并发，429 响应自动重试（最多 2 次，读 `Retry-After` header）。

#### 异动检测信号

系统计算综合异动分数 `anomaly_score = volume_score * 0.4 + imbalance_score * 0.3 + velocity_score * 0.3`，三个信号任一触发即进入 AI 分析：

1. **成交量突增 (Volume Spike)** — `volume_24h / avg_volume_7d` 超过阈值（默认 3x）
2. **Orderbook 失衡 (Book Imbalance)** — `total_bid / (total_bid + total_ask)`（前 10 档）超过 0.7 或低于 0.3
3. **价格急速变动 (Price Velocity)** — 相邻两次检测间价格变动百分比超过阈值（默认 5%）

> 注意：Volume Spike 和 Price Velocity 依赖 Redis 历史快照，首次运行仅写入基线不产生告警。

#### 当前数据流

```
APScheduler 间隔触发(30s)
  → Gamma API: 获取活跃市场列表
  → 前置过滤(closed/acceptingOrders/clobTokenIds) + 按 volume24hr 降序排序
  → 取前 N 个市场(默认25)
  → CLOB API: 并发获取 orderbook + 中间价 (Semaphore 限速 + 429 重试)
  → 与 Redis 基线对比计算异动信号
  → 超过阈值? → LLM 异动分析
  → 存储告警到 Redis (24h 去重) → 飞书推送
```

### 3.2 GitHub Trending 项目发现

#### 数据源

- **主方案**: GitHub Search API — `GET /search/repositories?q=topic:{topic}&sort=stars&order=desc`，认证后 5000 req/h
- **辅助方案**: gtrending 库 — 抓取 GitHub Trending 页面，按语言过滤，作为补充信号

#### 发现算法

采用双策略合并：

1. **Star-delta 异常检测** — 每次运行记录 star 数快照，下次运行计算增量，超过阈值（默认 50）判定为异常增长
2. **gtrending 补充** — 从 Trending 页面获取当日热门项目（按语言），直接进入候选池
3. **合并去重** — 两个策略的结果按 `full_name` 合并，同时命中的项目标记为 `star_delta+gtrending`

#### 当前数据流

```
APScheduler Cron(每日 09:00)
  → GitHub Search API: 按 topics 查询(每 topic 拉取 N 个)
  → gtrending: 按语言获取 trending 项目
  → Star-delta 计算 + gtrending 合并
  → Redis 去重(30天窗口)
  → 按 star delta 排序取 Top 20
  → LLM 逐个分析 README
  → 存储非 skip 推荐的告警 → 飞书推送
```

## 4. 规则层设计

### 4.1 Rule Registry

基于装饰器的单例注册机制（`app/engine/registry.py`），启动时扫描 `app/rules` 包下所有模块，自动触发装饰器注册。

```python
@rule_registry.register(
    source="polymarket",
    schedule="interval:30s",
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
| `discover_trending_repos` | github | `cron:0 9 * * *` | batch | `app/rules/github_rules.py` |
| `detect_polymarket_anomalies` | polymarket | `interval:30s` | threshold | `app/rules/polymarket_rules.py` |

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
├── github/
│   └── project_evaluation.jinja2    # 项目潜力评估
└── polymarket/
    └── anomaly_analysis.jinja2      # 异动事件分析
```

## 6. 分发层设计

### 6.1 REST API

- `GET /alerts/{source}` — 告警列表（支持 `limit`/`offset` 分页）
- `GET /alerts/{source}/{alert_id}` — 单条告警详情
- `GET /debug/events/{source}` — 最近 5 条告警（调试用）

### 6.2 飞书 Webhook 机器人（已实现）

- 实现类：`FeishuWebhookDelivery`（`app/delivery/feishu.py`）
- 通过 httpx POST 飞书自定义机器人 Webhook URL，无需飞书 SDK
- 支持 HMAC-SHA256 签名校验（可选，配置 `FEISHU_WEBHOOK_SECRET` 后自动启用）
- 消息格式：Card JSON v2（`schema: "2.0"`，`body.elements`）+ `collapsible_panel` 折叠面板
  - Header 颜色根据 severity：CRITICAL=红, HIGH=橙, MEDIUM=蓝, LOW=灰
  - **GitHub 卡片**：摘要 + 仓库链接始终可见；详细信息（语言/Star/Fork/新功能/PR 等）折叠
  - **Polymarket 卡片**：中文翻译 + AI 摘要 + 市场链接始终可见；异动信号、交易建议、地缘影响分别折叠
  - v1 fallback：若 webhook 不支持 v2 格式，自动降级为 v1（展开所有折叠面板为普通 div）
- 降级机制：`FEISHU_WEBHOOK_URL` 为空时使用 `NoopDelivery`（空实现，不推送）
- 失败处理：`send()` 内部 catch 异常，只记日志不阻塞 Rule 执行

### 6.3 Polymarket AI 分析增强

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
| `trading_suggestion` | `{direction, reasoning}` — 交易建议（buy_yes/buy_no/hold/avoid） |
| `geopolitical_impact` | 地缘政治/货币政策/战争冲突影响分析 |

规则层将 `signals` 和 `anomaly_score` 注入 `event.data`，使 delivery 层可直接访问原始信号数据。

### 6.4 V2 分发扩展

- 通知消息内嵌操作入口（买入/卖出链接、克隆仓库、关注/转发）

## 7. 状态管理

Redis 用于以下场景：

| Key Pattern | 类型 | TTL | 用途 |
|------------|------|-----|------|
| `alerts:{source}` | List | 无 | 告警存储（LPUSH + LTRIM 环形缓冲） |
| `gh:repo:{full_name}:star_snapshot` | JSON | 30d | Star 数快照（用于 star-delta 计算） |
| `gh:repo:{full_name}:pushed` | String | 30d | GitHub 去重 — 跳过已推送仓库 |
| `pm:market:{id}:baseline` | JSON | 7d | 成交量 EMA 基线 |
| `pm:market:{id}:last_price` | String | 1h | 价格快照（用于 velocity 计算） |
| `pm:alert:{id}:sent` | String | 24h | Polymarket 告警去重 |

## 8. 调试与观测

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

## 9. 项目结构

```
intel_sys/
├── app/
│   ├── main.py              # FastAPI 入口、lifespan、execute_rule
│   ├── config.py            # Pydantic Settings（.env 配置）
│   ├── models.py            # Event, Alert, AIEnrichment, Severity, RuleConfig
│   ├── engine/
│   │   ├── registry.py      # RuleRegistry 单例 + @register 装饰器 + RuleMeta
│   │   ├── context.py       # RuleContext (data, ai, db, config, delivery, logger)
│   │   └── scheduler.py     # APScheduler 封装 (interval/cron 解析)
│   ├── sources/
│   │   ├── base.py          # BaseSource 抽象基类
│   │   ├── github.py        # GitHub Search API + gtrending + README 抓取
│   │   └── polymarket.py    # Gamma API (市场列表) + CLOB API (orderbook/价格)
│   ├── rules/
│   │   ├── github_rules.py  # discover_trending_repos (每日批量)
│   │   └── polymarket_rules.py  # detect_polymarket_anomalies (30s 间隔)
│   ├── ai/
│   │   └── client.py        # OpenRouter 客户端 + Jinja2 模板渲染 + JSON 解析
│   ├── delivery/
│   │   ├── base.py          # BaseDelivery 抽象基类 (send + close)
│   │   └── feishu.py        # FeishuWebhookDelivery + NoopDelivery
│   └── routes/
│       ├── alerts.py        # GET /alerts/{source}, GET /alerts/{source}/{id}
│       └── debug.py         # 调试、调度控制、手动触发、规则重载
├── prompts/
│   ├── github/
│   │   └── project_evaluation.jinja2
│   └── polymarket/
│       └── anomaly_analysis.jinja2
├── static/
│   └── index.html           # Dashboard 页面
├── pyproject.toml            # Hatch 构建配置 + 依赖声明
├── .env.example              # 环境变量模板
└── CLAUDE.md                 # 本文件
```

## 10. 技术栈

| 组件 | 选型 | 说明 |
|------|------|------|
| Web 框架 | FastAPI | ASGI，异步原生 |
| 任务调度 | APScheduler 3.x | AsyncIOScheduler，支持 Cron + Interval |
| 状态存储 | Redis | hiredis 加速，decode_responses=True |
| LLM 服务 | OpenRouter | 默认模型: Gemini 3 Flash Preview |
| Prompt 引擎 | Jinja2 | 模板化管理 |
| Polymarket 数据 | httpx | Gamma API (市场) + CLOB API (orderbook) |
| GitHub 数据 | httpx + gtrending | Search API + Trending fallback |
| 飞书推送 | httpx | 自定义机器人 Webhook，无需 SDK |
| 配置管理 | Pydantic Settings | `.env` 文件 + 环境变量 |
| 构建系统 | Hatch | `pyproject.toml` 声明式配置 |

## 11. V1 开发路线图

### V1（当前阶段）

- [x] 核心引擎: FastAPI + lifespan + APScheduler + Redis
- [x] 规则注册机制: 装饰器模式 + 自动发现加载 + 热重载
- [x] 信源适配: Polymarket (Gamma + CLOB API)
- [x] 信源适配: GitHub (Search API + gtrending + README 抓取)
- [x] 规则: Polymarket 异动检测 (成交量突增 / Orderbook 失衡 / 价格急速变动)
- [x] 规则: GitHub Trending 项目发现 (star-delta + gtrending 双策略)
- [x] AI 管线: OpenRouter + Jinja2 Prompt 模板 + JSON 解析
- [x] 告警 REST API + 调试端点 + 调度控制
- [x] 飞书 Webhook 机器人推送 (Card JSON v2 + collapsible_panel 折叠面板，v1 fallback)
- [x] Polymarket AI 增强: 中文翻译、交易建议、地缘影响分析
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
