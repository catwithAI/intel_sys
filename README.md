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
│  智能层 (Reasoning)                                          │
│  OpenRouter (Gemini 3 Flash Preview)                         │
│  Jinja2 Prompt 模板  ·  结构化 JSON 输出                      │
├──────────────────────────────────────────────────────────────┤
│  逻辑层 (Pipeline)                                           │
│  RuleRegistry  ·  装饰器注册  ·  调度声明                      │
├──────────────────────────────────────────────────────────────┤
│  信源层 (Source)                                              │
│  Polymarket (Gamma + CLOB API)  ·  GitHub (Search API)       │
├──────────────────────────────────────────────────────────────┤
│  引擎层 (Engine)                                              │
│  FastAPI  ·  asyncio  ·  APScheduler  ·  Redis               │
└──────────────────────────────────────────────────────────────┘
```

### 组件说明

| 组件 | 说明 |
|------|------|
| **引擎层** | FastAPI 应用，通过 async lifespan 管理生命周期，APScheduler 处理 cron/interval 调度，Redis 持久化状态 |
| **信源层** | 数据适配器，从外部 API 获取原始数据，产出标准化 `Event` 对象 |
| **逻辑层** | 通过装饰器注册的异步函数，对事件进行过滤/评分，决定是否触发 AI 分析 |
| **智能层** | OpenRouter LLM 客户端 + Jinja2 Prompt 模板，产出结构化 JSON 分析结果 |
| **分发层** | 告警存储在 Redis 中，通过 REST API 查询；飞书 Webhook 机器人实时推送（Card JSON v2 + collapsible_panel 折叠详情，v1 自动降级） |

### 数据流

**Polymarket 预测市场异常监控**（每 30 秒）：

```
APScheduler 间隔触发
  → Gamma API: 获取活跃市场列表
  → 前置过滤 + 按 volume24hr 降序排序 → 取前 N 个市场(默认25)
  → CLOB API: 并发获取 orderbook + 中间价 (Semaphore 限速 + 429 重试)
  → 与 Redis 基线对比，计算异动信号:
      成交量突增 / Orderbook 失衡 / 价格急速变动
  → 超过阈值? → LLM 异动分析 (中文翻译 + 交易建议 + 地缘影响)
  → 存储告警到 Redis (24h 去重) → 飞书推送
```

**GitHub Trending 项目发现**（每日 09:00）：

```
APScheduler Cron 触发
  → GitHub Search API: 按 topics 查询 + gtrending 补充采集
  → Star-delta 异常检测 + gtrending 结果合并
  → Redis 去重(30天窗口) → 按 star delta 排序取 Top 20
  → LLM 逐个分析项目 README
  → 存储非 skip 推荐的告警 → 飞书推送
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
│   │   ├── github.py        # GitHub Search API + gtrending + README 抓取
│   │   └── polymarket.py    # Gamma API (市场列表) + CLOB API (orderbook/价格)
│   ├── rules/
│   │   ├── github_rules.py  # discover_trending_repos (每日批量)
│   │   └── polymarket_rules.py  # detect_polymarket_anomalies (30s 间隔)
│   ├── ai/
│   │   └── client.py        # OpenRouter 客户端 + Jinja2 模板渲染
│   ├── delivery/
│   │   ├── base.py          # BaseDelivery 抽象基类
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

# ===== 可选调优 =====
OPENROUTER_MODEL=google/gemini-3-flash-preview
GITHUB_TOPICS=["ai","infrastructure","llm"]
GITHUB_POOL_SIZE_PER_TOPIC=250
GITHUB_STAR_DELTA_THRESHOLD=50
GITHUB_GTRENDING_LANGUAGES=["python","typescript","rust","go"]
GITHUB_GTRENDING_MIN_PERIOD_STARS=20
PM_VOLUME_SPIKE_RATIO=3.0
PM_BOOK_IMBALANCE_HIGH=0.7
PM_BOOK_IMBALANCE_LOW=0.3
PM_PRICE_VELOCITY_PCT=5.0
PM_TOP_MARKETS=25
PM_CLOB_CONCURRENCY=8
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
INFO app.main: Loaded 2 rules
INFO app.engine.scheduler: Scheduled rule discover_trending_repos: cron:0 9 * * *
INFO app.engine.scheduler: Scheduled rule detect_polymarket_anomalies: interval:30s
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
# GitHub 告警（默认 20 条）
curl http://localhost:8000/alerts/github

# Polymarket 告警，带分页
curl "http://localhost:8000/alerts/polymarket?limit=10&offset=0"

# 按 ID 获取单条告警
curl http://localhost:8000/alerts/github/{alert_id}
```

### 调试端点

```bash
# 查看已注册规则
curl http://localhost:8000/debug/rules

# 查看最近告警
curl http://localhost:8000/debug/events/github

# 查询 Redis key
curl http://localhost:8000/debug/state/pm:market:{id}:baseline

# 查看调度任务
curl http://localhost:8000/debug/scheduler
```

### 手动触发

```bash
# 触发单条规则
curl -X POST http://localhost:8000/debug/trigger/discover_trending_repos
curl -X POST http://localhost:8000/debug/trigger/detect_polymarket_anomalies

# 触发某信源下所有规则
curl -X POST http://localhost:8000/debug/trigger-source/github
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

## 编写自定义规则

规则是通过装饰器注册的异步函数。放在 `app/rules/` 目录下即可自动发现。

```python
# app/rules/my_rule.py

from app.engine.registry import rule_registry
from app.engine.context import RuleContext

@rule_registry.register(
    source="github",             # "github" 或 "polymarket"
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
| `interval:Ns` | `interval:30s` | 每 30 秒 |
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
| `gh:repo:{full_name}:pushed` | String | 30 天 | GitHub 去重 — 跳过已推送仓库 |
| `pm:market:{id}:baseline` | JSON | 7 天 | 成交量 EMA 基线 |
| `pm:market:{id}:last_price` | String | 1 小时 | 价格快照，用于 velocity 计算 |
| `pm:alert:{id}:sent` | String | 24 小时 | Polymarket 告警去重 |

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
- [x] 规则: Polymarket 异动检测 (成交量突增 / Orderbook 失衡 / 价格急速变动)
- [x] 规则: GitHub Trending 项目发现 (star-delta + gtrending 双策略)
- [x] AI 管线: OpenRouter + Jinja2 模板 + JSON 解析
- [x] 告警 REST API + 调试端点 + 调度控制
- [x] 飞书 Webhook 机器人推送 (Card JSON v2 + collapsible_panel 折叠面板，v1 fallback)
- [x] Polymarket AI 增强: 中文翻译、交易建议、地缘影响分析
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

私有项目，保留所有权利。
