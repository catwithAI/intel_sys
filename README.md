# Intel System

事件驱动的信息情报系统。系统持续采集外部信号源，经过规则过滤、评分、LLM 富化、记忆池归档与飞书分发，形成可追踪的情报流水线。

```text
Source -> Event -> Rule -> AI -> Alert -> Delivery
```

## 当前能力

- `Polymarket`：90 秒轮询，两层漏斗筛选市场异动，6 小时汇总推送
- `GitHub`：每日扫描 trending / star delta 项目，区分新项目和项目更新
- `Hacker News`：每日 `16:30` 聚合推送热门话题
- `CLS / 雪球 / Reddit`：持续采集并压缩入记忆池，不直接告警
- `Correlation`：每日 `14:30` 基于记忆池做跨事件关联推理并聚合推送
- `Defense`：配置驱动的防务 RSS 采集管线，支持 YAML source registry、去重、评分、可选 PostgreSQL 落库、独立飞书机器人

## 系统架构

```text
┌──────────────────────────────────────────────────────────────┐
│ Delivery                                                    │
│ Feishu Webhook · Defense Feishu · REST API · Dashboard      │
├──────────────────────────────────────────────────────────────┤
│ Correlation                                                 │
│ EventMemoryPool · LLM Cross-event Reasoning                 │
├──────────────────────────────────────────────────────────────┤
│ Corroboration                                               │
│ HN Algolia + Twitter API                                    │
├──────────────────────────────────────────────────────────────┤
│ Reasoning                                                   │
│ OpenRouter · Jinja2 Prompts · JSON Parsing                  │
├──────────────────────────────────────────────────────────────┤
│ Pipeline                                                    │
│ RuleRegistry · APScheduler · RuleContext                    │
├──────────────────────────────────────────────────────────────┤
│ Sources                                                     │
│ Polymarket · GitHub · HN · CLS · Xueqiu · Reddit · Defense  │
├──────────────────────────────────────────────────────────────┤
│ Engine                                                      │
│ FastAPI · asyncio · Redis · optional PostgreSQL             │
└──────────────────────────────────────────────────────────────┘
```

### Defense 子系统

`app/defense/` 是当前仓库里第一套配置驱动采集管线，和旧的“每个信源一套 source + rule”模式不同。

当前已经实现：

- `sources/defense_*.yaml` 加载 source spec
- `RSSCollector`：ETag / Last-Modified / negative cache / domain rate limit
- `normalize -> dedup -> score -> Event` 转换
- 可选 `PostgreSQL` 落库：`normalized_events`、`run_history`、`source_health`
- 独立 defense 飞书 digest 卡片
- `/debug/defense/health`、`/debug/defense/runs` 调试接口

当前限制：

- 只落地了 `rss` collector
- 评分仍以规则分为主，尚未接入 defense 专用 LLM relevance 漏斗
- 去重为 `url_hash + content_hash`，尚无近重复聚类

## 代码结构

```text
intel_sys/
├── app/
│   ├── main.py
│   ├── config.py
│   ├── models.py
│   ├── ai/
│   │   └── client.py
│   ├── corroboration/
│   │   ├── query_builder.py
│   │   └── service.py
│   ├── defense/
│   │   ├── collectors/
│   │   │   ├── registry.py
│   │   │   └── rss.py
│   │   ├── converter.py
│   │   ├── deduper.py
│   │   ├── health.py
│   │   ├── models.py
│   │   ├── normalizer.py
│   │   ├── rate_limiter.py
│   │   ├── scorer.py
│   │   ├── source_loader.py
│   │   └── storage.py
│   ├── delivery/
│   │   ├── base.py
│   │   └── feishu.py
│   ├── engine/
│   │   ├── context.py
│   │   ├── registry.py
│   │   └── scheduler.py
│   ├── memory/
│   │   └── pool.py
│   ├── routes/
│   │   ├── alerts.py
│   │   └── debug.py
│   ├── rules/
│   │   ├── cls_ingest_rules.py
│   │   ├── correlation_rules.py
│   │   ├── defense_rules.py
│   │   ├── github_rules.py
│   │   ├── hackernews_rules.py
│   │   ├── polymarket_digest.py
│   │   ├── polymarket_rules.py
│   │   ├── reddit_ingest_rules.py
│   │   └── xueqiu_ingest_rules.py
│   └── sources/
│       ├── cls_news.py
│       ├── github.py
│       ├── hackernews.py
│       ├── polymarket.py
│       ├── reddit.py
│       ├── twitter.py
│       └── xueqiu.py
├── docs/
│   ├── defense-integration-plan.md
│   ├── roadmap.md
│   └── specs/
├── prompts/
├── sources/
│   └── defense_news.yaml
├── static/
├── tests/
│   └── defense_tasks/
├── claude.md
├── pyproject.toml
└── start.sh
```

## 调度一览

当前规则调度以代码为准：

| Rule | Schedule | 说明 |
|------|----------|------|
| `detect_polymarket_anomalies` | `interval:90s` | Polymarket 异动检测 |
| `send_polymarket_digest` | `cron:0 */6 * * *` | Polymarket 6 小时 digest |
| `discover_trending_repos` | `cron:0 17 * * *` | GitHub 每日推送 |
| `discover_hn_hot_topics` | `cron:30 16 * * *` | HN 每日聚合推送 |
| `discover_cross_event_insights` | `cron:30 14 * * *` | 关联推理每日聚合推送 |
| `ingest_cls_news` | `interval:120s` | 财联社入池 |
| `ingest_xueqiu_news` | `interval:300s` | 雪球入池 |
| `ingest_reddit_posts` | `interval:1800s` | Reddit 入池 |
| `ingest_defense_news` | `interval:{DEFENSE_RSS_INTERVAL}s` | 防务 RSS 采集，默认 1800 秒 |

说明：

- 调度表达式由 [scheduler.py](/Users/thoma/self_codes/intel_sys/app/engine/scheduler.py) 解析
- 时间解释取决于服务运行环境的本地时区
- defense 的调度频率由 `DEFENSE_RSS_INTERVAL` 控制，默认值定义在 [config.py](/Users/thoma/self_codes/intel_sys/app/config.py)

## 运行要求

### 基础依赖

- Python `3.11+`
- Redis
- OpenRouter API Key

### 可选依赖

- GitHub Token
- Reddit OAuth2 凭证
- 雪球 Cookie
- PostgreSQL：仅 defense 落库与健康状态依赖
- 飞书 Webhook：主机器人、洞察机器人、防务机器人三套可独立配置

## 安装

```bash
python -m venv .venv
source .venv/bin/activate

pip install -e .
pip install -e ".[dev]"
```

开发测试依赖包括：

- `pytest`
- `pytest-asyncio`
- `ruff`

## 配置

项目当前依赖本地 `.env` 文件，`start.sh` 会检查其是否存在。仓库里没有维护 `.env.example`，需要手动创建。

最小配置：

```bash
REDIS_URL=redis://localhost:6379/0
OPENROUTER_API_KEY=...
```

常用配置：

```bash
# 通用
OPENROUTER_MODEL=google/gemini-3-flash-preview
ALERT_MAX_PER_SOURCE=100

# GitHub
GITHUB_TOKEN=

# 飞书
FEISHU_WEBHOOK_URL=
FEISHU_WEBHOOK_SECRET=
FEISHU_INSIGHT_WEBHOOK_URL=
FEISHU_INSIGHT_WEBHOOK_SECRET=
FEISHU_DEFENSE_WEBHOOK_URL=
FEISHU_DEFENSE_WEBHOOK_SECRET=

# 佐证
SM_CORROBORATION_ENABLED=true
SM_TWITTER_API_KEY=

# 记忆池信源
XUEQIU_COOKIE=
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=

# Defense
DEFENSE_RSS_INTERVAL=1800
DEFENSE_RSS_CONCURRENCY=5
DEFENSE_DOMAIN_MIN_INTERVAL=10
DEFENSE_RSS_TIMEOUT=15
DEFENSE_TOPK=200
DEFENSE_DEDUP_TTL=604800
DEFENSE_COOLDOWN_HOURS=6
DEFENSE_MAX_CONSECUTIVE_FAILURES=3
DEFENSE_DISABLE_THRESHOLD=10
DEFENSE_ALERT_THRESHOLD=0.3

# PostgreSQL
PG_DSN=postgresql://user:pass@localhost:5432/dbname
PG_POOL_MIN=2
PG_POOL_MAX=10
```

全部字段见 [config.py](/Users/thoma/self_codes/intel_sys/app/config.py)。

## 启动

```bash
./start.sh
```

或手动：

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 7777
```

启动时会做这些事：

- 连接 Redis
- 初始化 `AIClient`
- 初始化主飞书机器人和 defense 飞书机器人
- 可选连接 PostgreSQL，并初始化 defense 表
- 自动扫描并注册 `app.rules`
- 启动 APScheduler

## 调试接口

### 通用

- `GET /debug/rules`
- `GET /debug/events/{source}`
- `GET /debug/state/{key}`
- `GET /debug/scheduler`
- `POST /debug/scheduler/pause/{rule_name}`
- `POST /debug/scheduler/resume/{rule_name}`
- `POST /debug/scheduler/pause-source/{source}`
- `POST /debug/scheduler/resume-source/{source}`
- `POST /debug/trigger/{rule_name}`
- `POST /debug/trigger-source/{source}`
- `POST /system/reload`

### Defense

- `GET /debug/defense/health`
- `GET /debug/defense/runs`

### 其他

- `GET /alerts/{source}`
- `GET /alerts/{source}/{id}`
- `GET /dashboard`
- `GET /`

## 测试

```bash
pytest -q
```

防务子系统当前配有阶段性测试：

```bash
pytest -q tests/defense_tasks
```

注意：

- 需要先安装 `.[dev]`
- 运行 defense 相关测试时需要项目依赖完整安装，例如 `feedparser`

## 当前实现边界

- `defense` 只实现了 RSS collector，HTML / JSON API / social collector 还未落地
- defense 当前以规则分与阈值为主，尚未接入专门的 relevance/translation/enrichment prompt
- 记忆池仍是通用压缩模板，防务分类还未单独建模
- Redis 是系统主状态存储；PostgreSQL 目前只服务 defense

## 参考文档

- [roadmap.md](/Users/thoma/self_codes/intel_sys/docs/roadmap.md)
- [defense-integration-plan.md](/Users/thoma/self_codes/intel_sys/docs/defense-integration-plan.md)
- [claude.md](/Users/thoma/self_codes/intel_sys/claude.md)
