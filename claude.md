Project: Intel System

## 1. 项目定位

这是一个事件驱动的信息情报后端。系统持续采集外部信号源，将原始数据标准化为 `Event`，经过规则过滤、评分和 AI 富化后生成 `Alert`，并通过飞书、REST API 和记忆池进一步消费。

统一抽象：

```text
Source -> Event -> Rule -> AI -> Alert -> Delivery
```

当前系统不是单一业务应用，而是一个可持续扩展的情报流水线运行时。

## 2. 当前真实架构

### 2.1 运行时层次

```text
Engine
  FastAPI + asyncio + APScheduler + Redis + optional PostgreSQL

Pipeline
  RuleRegistry + RuleContext + scheduled jobs

Sources
  Polymarket / GitHub / HackerNews / CLS / Xueqiu / Reddit / Defense

Reasoning
  OpenRouter + Jinja2 prompts + JSON parsing

Corroboration
  HN Algolia + Twitter API

Memory / Correlation
  EventMemoryPool + daily cross-event reasoning

Delivery
  Feishu Webhook + defense Feishu + REST API + dashboard
```

### 2.2 启动流程

`app/main.py` 的 `lifespan` 会做这些事：

1. 初始化 Redis
2. 初始化 `AIClient`
3. 初始化主飞书机器人
4. 初始化 defense 独立飞书机器人
5. 如果配置了 `PG_DSN`，创建 PostgreSQL 连接池并初始化 defense 表
6. 扫描 `app.rules` 自动注册规则
7. 将规则交给 `APScheduler` 注册并启动

## 3. 当前已实现的规则

以代码中的实际 schedule 为准：

| Rule | Source | Schedule | 说明 |
|------|--------|----------|------|
| `detect_polymarket_anomalies` | `polymarket` | `interval:90s` | Polymarket 两层漏斗 |
| `send_polymarket_digest` | `polymarket` | `cron:0 */6 * * *` | Polymarket 6 小时 digest |
| `discover_trending_repos` | `github` | `cron:0 17 * * *` | GitHub 每日推送 |
| `discover_hn_hot_topics` | `hackernews` | `cron:30 16 * * *` | HN 每日聚合推送 |
| `ingest_cls_news` | `cls` | `interval:120s` | 财联社入池 |
| `ingest_xueqiu_news` | `xueqiu` | `interval:300s` | 雪球入池 |
| `ingest_reddit_posts` | `reddit` | `interval:1800s` | Reddit 入池 |
| `discover_cross_event_insights` | `correlation` | `cron:30 15 * * *` | 每日关联推理 digest |
| `ingest_defense_news` | `defense` | `interval:{DEFENSE_RSS_INTERVAL}s` | 防务 RSS 管线 |

## 4. 关键模块

### 4.1 公共模型

文件：[models.py](/Users/thoma/self_codes/intel_sys/app/models.py)

- `SourceType`
- `Event`
- `Alert`
- `MemoryEvent`
- `RuleConfig`

说明：

- `SourceType.DEFENSE` 已加入统一模型
- defense 最终仍会转成通用 `Event`

### 4.2 调度与规则注册

文件：

- [registry.py](/Users/thoma/self_codes/intel_sys/app/engine/registry.py)
- [scheduler.py](/Users/thoma/self_codes/intel_sys/app/engine/scheduler.py)

设计特点：

- 规则通过装饰器注册
- 调度字符串格式只支持：
  - `interval:<number><s|m|h>`
  - `cron:<min> <hour> <day> <month> <dow>`
- 启动时全量扫描 `app.rules`

### 4.3 AI 客户端

文件：[client.py](/Users/thoma/self_codes/intel_sys/app/ai/client.py)

当前实现特点：

- Provider 固定为 OpenRouter Chat Completions
- Prompt 通过 Jinja2 渲染
- 默认尝试解析 JSON
- 失败时会返回 `"{}"` 或 `{"raw": text}`

注意：

- 这是一个比较薄的 client，没有强 schema 校验
- 修改 prompt 或模型时，要同步验证下游解析逻辑

### 4.4 记忆池

文件：[pool.py](/Users/thoma/self_codes/intel_sys/app/memory/pool.py)

当前实现：

- Redis Sorted Set 存 `MemoryEvent`
- 批量调用 `prompts/memory/event_compress.jinja2`
- dedup 仍是 `source + source_id`

限制：

- 对转载、多 URL 同文、近重复事件不够强
- defense 目前仍复用通用 memory compress prompt

## 5. Defense 子系统

### 5.1 当前定位

`app/defense/` 是当前仓库里最重要的新子系统。它已经从“设计文档”进入“代码实现”，目标是把防务新闻采集从硬编码 source 迁移到配置驱动的通用管线。

### 5.2 当前已实现模块

文件：

- [models.py](/Users/thoma/self_codes/intel_sys/app/defense/models.py)
- [source_loader.py](/Users/thoma/self_codes/intel_sys/app/defense/source_loader.py)
- [collectors/rss.py](/Users/thoma/self_codes/intel_sys/app/defense/collectors/rss.py)
- [normalizer.py](/Users/thoma/self_codes/intel_sys/app/defense/normalizer.py)
- [deduper.py](/Users/thoma/self_codes/intel_sys/app/defense/deduper.py)
- [scorer.py](/Users/thoma/self_codes/intel_sys/app/defense/scorer.py)
- [rate_limiter.py](/Users/thoma/self_codes/intel_sys/app/defense/rate_limiter.py)
- [health.py](/Users/thoma/self_codes/intel_sys/app/defense/health.py)
- [storage.py](/Users/thoma/self_codes/intel_sys/app/defense/storage.py)
- [converter.py](/Users/thoma/self_codes/intel_sys/app/defense/converter.py)
- [defense_rules.py](/Users/thoma/self_codes/intel_sys/app/rules/defense_rules.py)

### 5.3 当前管线

```text
YAML source spec
  -> RSSCollector
  -> normalize
  -> PostgreSQL append-only persistence (optional)
  -> Deduper
  -> Scorer
  -> top-k
  -> Event
  -> EventMemoryPool
  -> Alert (score >= threshold)
  -> defense Feishu digest
```

### 5.4 当前特性

- `SourceLoader` 读取 `sources/defense_*.yaml`
- collector 只实现了 `rss`
- `RSSCollector` 支持：
  - `ETag`
  - `Last-Modified`
  - negative cache
  - per-domain rate limit
- `SourceHealthManager` 支持：
  - `ok`
  - `cooling_down`
  - `pending_disable`
- PostgreSQL 可记录：
  - `normalized_events`
  - `run_history`
  - `source_health`

### 5.5 当前限制

- collector 只有 `rss`
- dedup 只有 `url_hash + content_hash`
- scorer 仍是规则分，不是 LLM relevance
- `task` 测试已经存在，但依赖完整环境才能跑通

## 6. Delivery

文件：[feishu.py](/Users/thoma/self_codes/intel_sys/app/delivery/feishu.py)

当前支持：

- 单条 alert 卡片
- GitHub digest
- Polymarket digest
- HN digest
- Correlation digest
- Defense digest

注意：

- defense 会走独立 webhook（如果配置）
- `send_batch()` 已按 source 分流聚合

## 7. Debug 与观测

### 7.1 常用接口

| Endpoint | 说明 |
|----------|------|
| `GET /debug/rules` | 当前已注册规则 |
| `GET /debug/scheduler` | 当前调度任务 |
| `POST /debug/trigger/{rule_name}` | 手动触发单条规则 |
| `POST /debug/trigger-source/{source}` | 手动触发某 source 下所有规则 |
| `GET /debug/state/{key}` | 查看 Redis key |
| `GET /debug/events/{source}` | 最近告警 |
| `POST /system/reload` | 重载规则 |

### 7.2 Defense 专用接口

| Endpoint | 说明 |
|----------|------|
| `GET /debug/defense/health` | source health 状态 |
| `GET /debug/defense/runs` | 最近 20 次 defense run |

## 8. 运行与开发

### 8.1 安装

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
pip install -e ".[dev]"
```

### 8.2 `.env`

项目依赖本地 `.env` 文件，`start.sh` 启动前会检查是否存在。

关键配置：

```bash
REDIS_URL=redis://localhost:6379/0
OPENROUTER_API_KEY=

FEISHU_WEBHOOK_URL=
FEISHU_INSIGHT_WEBHOOK_URL=
FEISHU_DEFENSE_WEBHOOK_URL=

PG_DSN=postgresql://user:pass@localhost:5432/dbname

DEFENSE_RSS_INTERVAL=1800
DEFENSE_RSS_CONCURRENCY=5
DEFENSE_DOMAIN_MIN_INTERVAL=10
DEFENSE_RSS_TIMEOUT=15
DEFENSE_TOPK=200
DEFENSE_ALERT_THRESHOLD=0.3
```

完整字段见 [config.py](/Users/thoma/self_codes/intel_sys/app/config.py)。

### 8.3 启动

```bash
./start.sh
```

或：

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 7777
```

### 8.4 测试

```bash
pytest -q
pytest -q tests/defense_tasks
```

注意：

- defense 测试需要完整依赖，例如 `feedparser`
- async 测试依赖 `pytest-asyncio`

## 9. 当前开发重点

如果你要继续在这个仓库上工作，优先看这几件事：

1. 跑通并稳定 `defense` 当前实现。
2. 为 defense 增加 `html_list` collector。
3. 给 defense 增加更强的 relevance / enrichment prompt。
4. 升级 defense 去重到近重复聚类。
5. 让 defense 更自然地进入 memory / correlation 链路。

## 10. 文档同步要求

当你修改以下内容时，要同步更新文档：

- 规则 schedule 变更：同步更新 `README.md` 和本文件
- 新 source / rule / debug endpoint：同步更新 `README.md`
- defense 子系统能力边界变化：同步更新 `docs/roadmap.md`
- 启动方式、依赖、配置字段变化：同步更新 `README.md` 和本文件

不要让 README、roadmap、`claude.md` 继续落后于代码。
