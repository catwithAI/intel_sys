# Intel System

事件驱动的信息情报系统 — 持续监控外部信号源，通过规则过滤与评分，引入 LLM 深度分析，并将告警推送给用户。

```
Source → Event → Rule(过滤 + 评分) → AI(富化分析) → Alert → Delivery
```

## 系统架构

```
┌──────────────────────────────────────────────────────────────┐
│  分发层 (Delivery)                                           │
│  飞书 Webhook (Card v2 + 折叠面板)  ·  REST API               │
├──────────────────────────────────────────────────────────────┤
│  佐证层 (Corroboration)                                      │
│  HN Algolia + Twitter API 交叉验证  ·  置信度增减              │
├──────────────────────────────────────────────────────────────┤
│  智能层 (Reasoning)                                          │
│  OpenRouter (Gemini 3 Flash Preview)                         │
│  Jinja2 Prompt 模板  ·  结构化 JSON 输出                      │
├──────────────────────────────────────────────────────────────┤
│  逻辑层 (Pipeline)                                           │
│  RuleRegistry  ·  装饰器注册  ·  调度声明                      │
├──────────────────────────────────────────────────────────────┤
│  信源层 (Source)                                              │
│  Polymarket  ·  GitHub  ·  Hacker News  ·  Twitter(佐证)     │
├──────────────────────────────────────────────────────────────┤
│  引擎层 (Engine)                                              │
│  FastAPI  ·  asyncio  ·  APScheduler  ·  Redis               │
└──────────────────────────────────────────────────────────────┘
```

### 组件说明

| 组件 | 说明 |
|------|------|
| **引擎层** | FastAPI 应用，通过 async lifespan 管理生命周期，APScheduler 处理 cron/interval 调度，Redis 持久化状态 |
| **信源层** | 数据适配器，从外部 API 获取原始数据，产出标准化 `Event` 对象。当前 4 个信源：Polymarket、GitHub、Hacker News、Twitter（仅佐证用） |
| **逻辑层** | 通过装饰器注册的异步函数，对事件进行过滤/评分，决定是否触发 AI 分析 |
| **智能层** | OpenRouter LLM 客户端 + Jinja2 Prompt 模板，产出结构化 JSON 分析结果 |
| **佐证层** | 对告警搜索 HN/Twitter 佐证，计算置信度增减（-0.05 ~ +0.30），跨平台命中额外加分 |
| **分发层** | 告警存储在 Redis 中，通过 REST API 查询；飞书 Webhook 机器人实时推送（Card JSON v2 + collapsible_panel 折叠详情，v1 自动降级） |

### 数据流

**Polymarket 预测市场异常监控**（每 90 秒，两层漏斗）：

```
APScheduler 间隔触发(90s)
  → Tier 1 — 广域扫描:
      Gamma API: limit=500, 获取全量 ~6800 个有效市场 (零 CLOB 调用)
      轻量信号: 成交量突增 / 价格速度 1d·1h / 价差异常
      复合 breaking_score 筛选 → ~20-30 个候选
  → Tier 2 — 深度分析:
      CLOB API: 仅对候选发起 orderbook + midpoint (~90 次调用)
      Redis 基线: 成交量 EMA / Orderbook 失衡 / 价格速度(Redis 快照)
      LLM 分析: 中文翻译 + 具体交易建议(买入项+价格+回报率) + 地缘影响
      HN 佐证 (跳过 Twitter 避免 429)
  → 存储告警到 Redis (24h 去重) → 飞书推送
```

**GitHub Trending 项目发现**（每日 09:00）：

```
APScheduler Cron 触发
  → GitHub Search API: 按 topics 查询 + gtrending 补充采集
  → Star-delta 异常检测 + gtrending 结果合并
  → Redis 去重(30天窗口) → 按 star delta 排序取 Top 20
  → 新项目流: LLM 分析 README → 推荐/观望/跳过
  → 返回项目流: 获取近期合并 PR → LLM 分析更新动态
  → HN + Twitter 佐证 → 飞书推送
```

**Hacker News 热门话题发现**（每 2 小时）：

```
APScheduler 间隔触发(7200s)
  → HN Algolia API: front_page + rising 合并
  → Redis 去重(7天窗口) → 按点数排序取 Top 15
  → 获取热门评论 → LLM 话题分析
  → 存储告警 → 飞书推送
```

### 项目结构

```
intel_sys/
├── app/
│   ├── main.py              # FastAPI 入口、lifespan、execute_rule
│   ├── config.py            # Pydantic Settings（.env 配置）
│   ├── models.py            # Event, Alert, AIEnrichment, Severity, RuleConfig
│   ├── engine/
│   │   ├── registry.py      # RuleRegistry 单例 + @register 装饰器
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
│   │   └── client.py        # OpenRouter 客户端 + Jinja2 模板渲染
│   ├── delivery/
│   │   ├── base.py          # BaseDelivery 抽象基类
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
├── pyproject.toml
├── .env.example
└── CLAUDE.md
```

---

## 快速开始

### 前置要求

- **Python 3.11+**
- **Redis** 服务（本地或远程均可）
- **OpenRouter API Key** — 用于 LLM 分析（[openrouter.ai](https://openrouter.ai)）
- **GitHub Token**（可选但推荐）— 可将 Search API 速率限制从 10/min 提升到 30/min

### 1. 克隆与安装

```bash
git clone <repo-url> intel_sys
cd intel_sys

python -m venv .venv
source .venv/bin/activate

# 安装所有依赖
pip install -e .

# 开发环境（额外包含 pytest, ruff）
pip install -e ".[dev]"
```

### 2. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`，填入凭证和参数：

```bash
# ===== 必填 =====
REDIS_URL=redis://localhost:6379/0
OPENROUTER_API_KEY=sk-or-v1-xxxxxxxxxxxx

# ===== 推荐 =====
GITHUB_TOKEN=ghp_xxxxxxxxxxxx

# ===== 飞书推送（可选，不填则不推送）=====
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx
FEISHU_WEBHOOK_SECRET=                # 签名密钥，未开启签名校验可留空

# ===== Social Media 佐证（可选）=====
SM_CORROBORATION_ENABLED=true
SM_TWITTER_API_KEY=                   # twitterapi.io Key，为空则仅用 HN 佐证

# ===== 可选调优 =====
OPENROUTER_MODEL=google/gemini-3-flash-preview
PM_WIDE_BREAKING_THRESHOLD=0.3        # Tier 1 筛选阈值（越低覆盖越广）
PM_WIDE_MAX_TIER2=50                  # Tier 2 最大候选数
PM_VOLUME_SPIKE_RATIO=3.0             # Tier 2 成交量突增阈值
ALERT_MAX_PER_SOURCE=100
```

完整配置说明参见 `.env.example`。

### 3. 启动 Redis

```bash
# macOS (Homebrew)
brew services start redis

# Linux
sudo systemctl start redis

# Docker
docker run -d --name redis -p 6379:6379 redis:alpine

# 验证
redis-cli ping   # => PONG
```

### 4. 启动服务

```bash
# 开发模式（代码变更自动重载）
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 生产模式
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

正常启动日志：

```
INFO app.main: Starting Intel System...
INFO app.main: Redis connected: redis://localhost:6379/0
INFO app.main: Feishu delivery enabled          # 或 "disabled (no webhook URL)"
INFO app.main: Loaded 3 rules
INFO app.engine.scheduler: Scheduled rule detect_polymarket_anomalies: interval:90s
INFO app.engine.scheduler: Scheduled rule discover_trending_repos: cron:0 9 * * *
INFO app.engine.scheduler: Scheduled rule discover_hn_hot_topics: interval:7200s
```

### 5. 配置飞书机器人（可选）

1. 打开目标飞书群 → 右上角 **「...」→ 设置 → 群机器人 → 添加机器人**
2. 选择 **「自定义机器人」**，设置名称后点击添加
3. 复制 Webhook 地址，填入 `.env` 的 `FEISHU_WEBHOOK_URL`
4. （可选）在安全设置中开启「签名校验」，将密钥填入 `FEISHU_WEBHOOK_SECRET`
5. 重启服务，日志显示 `Feishu delivery enabled` 即配置成功

---

## API 接口

### 告警查询

```bash
# 各信源告警
curl http://localhost:8000/alerts/polymarket
curl http://localhost:8000/alerts/github
curl http://localhost:8000/alerts/hackernews

# 带分页
curl "http://localhost:8000/alerts/polymarket?limit=10&offset=0"

# 按 ID 获取单条告警
curl http://localhost:8000/alerts/github/{alert_id}
```

### 调试端点

```bash
# 查看已注册规则
curl http://localhost:8000/debug/rules

# 查看最近告警
curl http://localhost:8000/debug/events/polymarket

# 查询 Redis key
curl http://localhost:8000/debug/state/pm:market:{id}:baseline

# 查看调度任务
curl http://localhost:8000/debug/scheduler
```

### 手动触发

```bash
# 触发单条规则
curl -X POST http://localhost:8000/debug/trigger/detect_polymarket_anomalies
curl -X POST http://localhost:8000/debug/trigger/discover_trending_repos
curl -X POST http://localhost:8000/debug/trigger/discover_hn_hot_topics

# 触发某信源下所有规则
curl -X POST http://localhost:8000/debug/trigger-source/polymarket
```

### 调度控制

```bash
# 暂停/恢复单条规则
curl -X POST http://localhost:8000/debug/scheduler/pause/detect_polymarket_anomalies
curl -X POST http://localhost:8000/debug/scheduler/resume/detect_polymarket_anomalies

# 暂停/恢复某信源下所有规则
curl -X POST http://localhost:8000/debug/scheduler/pause-source/polymarket
curl -X POST http://localhost:8000/debug/scheduler/resume-source/polymarket
```

### 系统管理

```bash
# 重载规则（无需重启服务）
curl -X POST http://localhost:8000/system/reload
```

---

## Polymarket 两层漏斗架构

### 设计动机

原方案仅监控 Top 25 市场（按 24h volume 排序），覆盖率 ~1.8%（25 / 1,389 有效市场）。Gamma API 已返回丰富字段（`volume24hr`、`volume1wk`、`oneDayPriceChange`、`oneHourPriceChange`、`spread` 等），可以零 CLOB 调用筛选全部 ~6,800 个有效市场，仅对真正异动的少量市场发起 CLOB 深度分析。

### 架构

```
Tier 1: 广域扫描 (1 次 Gamma API 调用, ~6800 市场, 零 CLOB)
  ↓ 轻量信号筛选:
  │   Volume Spike (24h/日均) ≥ 2x
  │   Price Velocity 1d ≥ 5%
  │   Price Velocity 1h ≥ 3%
  │   Spread Anomaly ≥ 0.10
  ↓ 复合 breaking_score ≥ 0.3
  ↓ 约 20-30 个候选 (上限 50)
Tier 2: 深度分析 (CLOB orderbook + Redis 基线 + AI)
  ↓ ~60-120 次 CLOB 调用 (在 100 req/min 限速内)
  ↓ 告警 + HN 佐证 + 飞书推送
```

### Breaking Score 计算

```
breaking_score = vol_component × 0.4 + price_component × 0.3 + spread_component × 0.3
```

- `vol_component` = min(volume_ratio / 10, 1.0)，当 volume_ratio ≥ 2.0 时触发
- `price_component` = max(1d 分量, 1h 分量)
- `spread_component` = min(spread / 0.30, 1.0)，当 spread ≥ 0.10 时触发

### Tier 2 信号（依赖 Redis 历史）

| 信号 | 算法 | 阈值 | 冷启动 |
|------|------|------|--------|
| 成交量突增 | `volume_24h / EMA基线` | ≥ 3x | 首次仅写入基线 |
| Orderbook 失衡 | `bid_size / (bid+ask)` 前 10 档 | ≥ 0.7 或 ≤ 0.3 | 首次即可触发 |
| 价格速度 | `|当前 - 上次| / 上次 × 100` | ≥ 5% | 首次仅写入快照 |

### 交易建议

AI 输出包含具体买入项和价格：
- `direction`: buy_yes / buy_no / hold / avoid
- `outcome`: 具体选项名称（如 "Yes" 或 "No"）
- `price`: 该选项当前价格
- 飞书卡片显示：**买入 Yes @ $0.3200（若胜出回报 3.1x）**

---

## 编写自定义规则

规则是通过装饰器注册的异步函数。放在 `app/rules/` 目录下即可自动发现。

```python
# app/rules/my_rule.py

from app.engine.registry import rule_registry
from app.engine.context import RuleContext

@rule_registry.register(
    source="github",             # "github" / "polymarket" / "hackernews"
    schedule="cron:0 */6 * * *", # 每 6 小时执行一次
    trigger="batch",             # "batch" (批量处理) 或 "threshold" (阈值触发)
)
async def my_custom_rule(ctx: RuleContext) -> bool:
    """
    返回 True 表示生成了告警，False 表示未触发。

    ctx.data     - dict, 事件数据
    ctx.ai       - AIClient, 调用 LLM 分析
    ctx.db       - Redis 客户端
    ctx.config   - RuleConfig (name, source)
    ctx.delivery - BaseDelivery, 发送飞书通知
    ctx.logger   - 结构化日志
    """

    # 1. 从信源获取数据
    # 2. 过滤 / 评分
    # 3. ctx.ai.analyze(...) 进行 LLM 分析
    # 4. 构造 Alert，存入 Redis
    # 5. await ctx.delivery.send(alert) 推送飞书
    # 6. 返回 True/False

    return False
```

**调度格式：**

| 格式 | 示例 | 说明 |
|------|------|------|
| `interval:Ns` | `interval:90s` | 每 90 秒 |
| `interval:Nm` | `interval:5m` | 每 5 分钟 |
| `interval:Nh` | `interval:1h` | 每小时 |
| `cron:...` | `cron:0 9 * * *` | 每天 09:00 |
| `cron:...` | `cron:*/15 * * * *` | 每 15 分钟 |

---

## Redis Key 一览

| Key 格式 | 类型 | TTL | 用途 |
|----------|------|-----|------|
| `alerts:{source}` | List | 无 | 告警存储（LPUSH + LTRIM 环形缓冲） |
| `gh:repo:{full_name}:star_snapshot` | JSON | 30 天 | Star 数快照，用于 delta 计算 |
| `gh:repo:{full_name}:pushed` | JSON | 30 天 | 上次推送时间戳 + Star 数 |
| `pm:market:{id}:baseline` | JSON | 7 天 | 成交量 EMA 基线 |
| `pm:market:{id}:last_price` | String | 1 小时 | 价格快照，用于 velocity 计算 |
| `pm:alert:{id}:sent` | String | 24 小时 | Polymarket 告警去重 |
| `hn:story:{id}:pushed` | String | 7 天 | HackerNews 去重 |

---

## 技术栈

| 组件 | 选型 | 说明 |
|------|------|------|
| Web 框架 | FastAPI | ASGI，异步原生 |
| 任务调度 | APScheduler 3.x | AsyncIOScheduler，Cron + Interval |
| 状态存储 | Redis | hiredis 加速，decode_responses |
| LLM 服务 | OpenRouter | 默认 Gemini 3 Flash Preview |
| Prompt 引擎 | Jinja2 | 模板化管理 |
| Polymarket | httpx | Gamma API + CLOB API |
| GitHub | httpx + gtrending | Search API + Trending fallback |
| Hacker News | httpx | Algolia Search API |
| Twitter 佐证 | httpx | twitterapi.io (按量付费) |
| 飞书推送 | httpx | 自定义机器人 Webhook (Card JSON v2)，无需 SDK |
| 配置管理 | Pydantic Settings | `.env` + 环境变量 |
| 构建系统 | Hatch | pyproject.toml 声明式配置 |

---

## Roadmap

### V1（当前阶段）

- [x] 核心引擎: FastAPI + lifespan + APScheduler + Redis
- [x] 规则注册: 装饰器模式 + 自动发现加载 + 热重载
- [x] 信源: Polymarket (Gamma + CLOB API)
- [x] 信源: GitHub (Search API + gtrending + README 抓取)
- [x] 信源: Hacker News (Algolia API — front_page + rising)
- [x] 信源: Twitter (twitterapi.io — 佐证用)
- [x] 规则: Polymarket 两层漏斗 — 全量 ~6800 市场广域扫描 + CLOB 深度分析
- [x] 规则: GitHub Trending 项目发现 (star-delta + gtrending) + 返回项目更新检测
- [x] 规则: Hacker News 热门话题发现
- [x] AI 管线: OpenRouter + Jinja2 模板 + JSON 解析
- [x] Polymarket AI 增强: 中文翻译、具体交易建议(买入项+价格+回报率)、地缘影响
- [x] Social Media 佐证: HN + Twitter 交叉验证，置信度增减
- [x] 告警 REST API + 调试端点 + 调度控制
- [x] 飞书 Webhook 机器人推送 (Card JSON v2 + collapsible_panel，v1 fallback)
- [x] Dashboard 页面
- [ ] 集成测试

### V2

- [ ] Polymarket WebSocket 实时流（替代轮询）
- [ ] 可疑钱包行为分析（新钱包 + 大额押注、高胜率 + 冷门市场）
- [ ] 交叉信源验证（Polymarket 异动 + Twitter 舆情关联）
- [ ] 通知消息内嵌操作入口
- [ ] Docker Compose 部署

### V3

- [ ] 更多信源: Kalshi、X/Twitter、链上数据
- [ ] 前端 Dashboard（替代静态页面）
- [ ] Kubernetes 部署
- [ ] 用户自定义告警规则

---

## License
[MIT License](LICENSE) © 2026 CatWithAI

如果你在项目中使用或基于本项目进行二次开发，
非常欢迎在 README 或文档中注明来源并附上本仓库链接。
这将有助于项目的持续维护和社区发展。

---

## ⚠️ 免责声明

本项目仅供学习和研究使用，不构成任何投资建议。股市有风险，投资需谨慎。作者不对使用本项目产生的任何损失负责。

---