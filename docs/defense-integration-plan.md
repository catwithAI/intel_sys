# 防务情报源集成 — 可落地技术方案

## 1. 背景与目标

### 1.1 现状

当前 `intel_sys` 已接入 Polymarket、GitHub、Hacker News、财联社、雪球、Reddit、Twitter 等信源，整体链路是：

```text
Source → Event → Rule → AI → Alert / Memory Pool → Correlation → Delivery
```

现有系统已经具备这些可复用能力：

- `APScheduler` 定时调度
- `Event` 标准模型
- Redis 去重与记忆池
- LLM 批量压缩
- 跨事件关联推理
- 飞书 / REST API 分发

因此，防务情报集成不应重写系统，而应作为一条新管线接入现有骨架。

### 1.2 需求概况

输入来源约 600+，包含：

| 类别 | 数量 | 特征 |
|------|------|------|
| 防务资讯 | 103 网站 | 军方官网、防务媒体、军工企业新闻 |
| 防务报告 | 19 机构 / 130 链接 | 智库报告、研究论文、政策分析 |
| 战略法令 | 12 网站 | 法规、条令、官方文件 |
| 军事影像 | 80 YouTube 频道 | 视频标题、描述、发布时间 |
| 军事情报舆情 | 200 社交账号 | Twitter/X 为主，Facebook 次要 |
| 收录队列 | 52 网站 | 补充源，优先级较低 |

### 1.3 现实约束

方案必须同时处理以下现实问题：

- 规模：600+ 源，日原始事件量可能 2,000 到 5,000
- 异构：RSS、HTML、JSON、PDF、视频元数据、社交帖完全不同
- 防爬：`.mil`、部分智库和媒体存在 WAF、Cloudflare、速率控制
- 信噪比：大量“仪式、任命、招募、照片集、活动预告”是低价值噪声
- 多语种：英语为主，另含俄语、法语、德语、日语、韩语、土耳其语等
- 成本：LLM 只能处理过滤后的子集，不能全量兜底
- 可运维性：新增源应尽量只改配置，不改代码

### 1.4 总目标

构建一套：

- `YAML 声明式 source registry`
- `少量 collector 模板`
- `标准化 + 去重 + 规则过滤 + 分级 LLM`
- `memory-first, alert-selective`

的防务情报接入框架。

### 1.5 非目标

以下内容不在 Phase 1 范围内：

- 对抗式绕过 Cloudflare / WAF
- 无授权抓取需要登录、验证码、动态签名的私有接口
- 视频全文转写
- Facebook 大规模采集
- 所有 600+ 源一次性上线

---

## 2. 总体设计

### 2.1 分层架构

```text
┌──────────────────────────────────────────────────────────────────┐
│ Layer 0: Source Registry                                        │
│ YAML 配置 · source metadata · filters · dedup policy            │
├──────────────────────────────────────────────────────────────────┤
│ Layer 1: Collectors                                             │
│ RSS · HTML List · JSON API · PDF Metadata · YouTube · Twitter   │
├──────────────────────────────────────────────────────────────────┤
│ Layer 2: Normalizer + Deduper                                   │
│ RawEvent → NormalizedEvent                                      │
│ URL 规范化 · 语言标准化 · 内容指纹 · 近重复预留                  │
├──────────────────────────────────────────────────────────────────┤
│ Layer 3: Filtering + Enrichment                                 │
│ Stage 1 硬过滤 · Stage 2 规则打分 · Stage 3 LLM 批量评估         │
├──────────────────────────────────────────────────────────────────┤
│ Layer 4: Memory + Correlation                                   │
│ EventMemoryPool · Cross-event reasoning                         │
├──────────────────────────────────────────────────────────────────┤
│ Layer 5: Delivery                                               │
│ 飞书 · REST API · Digest                                        │
└──────────────────────────────────────────────────────────────────┘
```

### 2.2 核心原则

- `配置驱动`：新增站点优先通过 YAML 配置完成
- `collector 少而稳`：控制采集器种类，不为每站写一套逻辑
- `memory-first`：大多数事件先进记忆池，不直接告警
- `分层过滤`：规则先砍噪声，LLM 只处理有价值候选
- `防封优先`：宁可少抓，也不做高风险、高频率、对抗式抓取
- `渐进式接入`：先 RSS 和稳定源，再扩 HTML/PDF/社交

### 2.3 最终推荐数据流

```text
YAML sources
  → load SourceSpec
  → collector.collect(spec)
  → RawEvent
  → normalize(spec, raw)
  → NormalizedEvent
  → L1/L2 dedup
  → Stage 1 hard filter
  → Stage 2 rule score
  → Top-K truncate
  → Stage 3 batch relevance (Phase 2+)
  → Event
  → EventMemoryPool.add_events_batch()
  → correlation rules
  → selective alerting
```

---

## 3. Phase 1 范围重定义

Phase 1 不追求“所有能力齐全”，只追求“真正能跑且不容易失控”。

### 3.1 Phase 1 交付目标

Phase 1 实现：

- RSS collector
- YAML source registry
- source loader
- normalizer
- URL/content 双层去重
- Stage 1 硬过滤
- Stage 2 规则打分
- 10 个种子源
- 入记忆池，不直接发 alert

### 3.2 Phase 1 明确不做

- 不做 per-source `schedule` 动态调度
- 不做 HTML 列表页抓取
- 不做 PDF 正文提取
- 不做 simhash 近重复聚类
- 不做 LLM relevance batch
- 不做独立 defense alert 卡片

### 3.3 为什么这样收缩

原因有三点：

- RSS-only 已经足够验证 source registry、normalizer、deduper、memory integration
- 真正高风险的是“噪声失控”和“IP 被 ban”，不是 collector 数量不够
- 把 Phase 1 收窄后，后续每一 phase 的收益和风险更可测

---

## 4. 数据模型

### 4.1 RawEvent

`RawEvent` 是 collector 的直接输出，只保留采集到的原始结构。

```python
@dataclass
class RawEvent:
    source_id: str
    collector: str
    url: str | None
    title: str
    body: str | None
    published_at: datetime | None
    language: str | None
    raw_metadata: dict = field(default_factory=dict)
```

字段说明：

- `source_id`：原始事件唯一标识。Phase 1 推荐 `"{site_id}:{entry_guid_or_url_hash}"`。
- `collector`：采集器类型，如 `rss`。
- `url`：原始链接；社交类未来可为空。
- `title`：原始标题。
- `body`：正文、摘要或 summary；RSS 场景允许为空。
- `published_at`：原始发布时间。
- `language`：来源侧给出的语言，可能为空。
- `raw_metadata`：entry id、feed title、author、tags 等 collector 特有字段。

### 4.2 NormalizedEvent

`NormalizedEvent` 是进入去重和打分前的标准中间结构。

```python
@dataclass
class NormalizedEvent:
    source_id: str
    site_id: str
    site_name: str
    family: str
    country: str
    language: str
    title: str
    body: str
    summary_hint: str
    url: str | None
    canonical_url: str | None
    published_at: datetime | None
    source_weight: float
    extraction_quality: float
    dedup_keys: dict[str, str]
    raw_metadata: dict
    pre_score: float = 0.0
```

字段说明：

- `site_id`：具体站点 ID，如 `defensenews`
- `family`：内容族，如 `news / report / law / video / social`
- `source_weight`：站点权威度基线
- `extraction_quality`：抽取质量评分
- `summary_hint`：后续给 LLM 的短上下文
- `dedup_keys`：URL hash、content hash 等去重键

### 4.3 Event

最终仍映射到现有 `app.models.Event`：

```python
Event(
    source=SourceType.DEFENSE,
    source_id=ne.source_id,
    timestamp=ne.published_at or datetime.now(timezone.utc),
    data={
        "title": ne.title,
        "content": ne.body[:2000],
        "url": ne.url or "",
        "family": ne.family,
        "country": ne.country,
        "language": ne.language,
        "summary_hint": ne.summary_hint,
        "pre_score": ne.pre_score,
    },
    metadata={
        "site_id": ne.site_id,
        "site_name": ne.site_name,
        "canonical_url": ne.canonical_url,
        "source_weight": ne.source_weight,
        "extraction_quality": ne.extraction_quality,
        "dedup_keys": ne.dedup_keys,
    },
)
```

### 4.4 SourceType 设计

只新增一个：

```python
DEFENSE = "defense"
```

不为每个站点单独建枚举值，用 `metadata.site_id` 区分具体来源。

这是当前仓库改动最小、长期仍可扩展的方案。

---

## 5. Source Registry 设计

### 5.1 配置原则

- 一个 YAML 文件管理一类来源
- source spec 同时描述“怎么抓”和“怎么处理”
- Phase 1 只消费一部分字段，其他字段可预留但必须标注

### 5.2 YAML 结构

```yaml
- id: breakingdefense
  enabled: true
  family: news
  tier: p1
  authority_tier: 2
  collector: rss
  country: US
  language: en
  credibility: 0.80
  url: "https://breakingdefense.com/feed/"

  access:
    mode: direct
    risk_level: low
    allow_fetch: true
    notes: "stable rss"

  # Phase 1 生效
  filters:
    title_blacklist:
      - sponsored
      - podcast
      - careers
    title_whitelist:
      - missile
      - hypersonic
      - deployment
      - F-35
    junk_patterns:
      - photo gallery
      - change of command

  dedup:
    canonicalize_url: true
    content_hash: true
    simhash: false

  fetch:
    timeout_sec: 15
    max_entries: 30
    respect_etag: true
    respect_last_modified: true
    retry_count: 2
    negative_ttl_sec: 120

  extra:
    name: "Breaking Defense"
    notes: "seed source"

  # Phase 2+ 预留，不在 Phase 1 生效
  alert_policy: memory_first
  schedule: "interval:30m"
```

### 5.3 Phase 1 生效字段

Phase 1 只消费这些字段：

- `id`
- `enabled`
- `family`
- `collector`
- `country`
- `language`
- `credibility`
- `authority_tier`
- `url`
- `access.*`
- `filters.*`
- `dedup.*`
- `fetch.*`
- `extra.name`

### 5.4 Phase 1 不生效字段

以下字段只做预留，不进入实际逻辑：

- `schedule`
- `alert_policy`
- `tier` 仅作为观测标签，不参与调度

文档和代码都要明确这一点，避免“配了但没生效”的错觉。

### 5.5 Source Governance 字段

建议把来源治理字段显式写进配置，而不是散落在代码逻辑里。

#### `authority_tier`

表示来源权威等级：

- `1`：官方机构、国际组织、顶级通讯社
- `2`：主流高质量媒体、专业机构
- `3`：垂直媒体、研究机构、领域博客
- `4`：聚合站、次级来源、低稳定性来源

用途：

- 粗粒度优先级
- canonical source 选取
- Stage 2 辅助打分

#### `access.mode`

- `direct`：允许直接抓取
- `relay_only`：未来仅允许走 relay
- `disabled`：明确停用

Phase 1 只实现 `direct` 与 `disabled`，`relay_only` 只预留。

#### `access.risk_level`

- `low`
- `medium`
- `high`

用途：

- 调整限速与重试策略
- 决定是否纳入 seed
- 决定后续 phase 是否值得继续接入

#### `access.allow_fetch`

快速开关。便于临时停用单个来源而不删配置。

---

## 6. Source Governance 与 Allowlist

### 6.1 为什么要做 Source Governance

对防务情报系统来说，问题不只是“能不能抓”，而是：

- 这个源值不值得长期维护
- 这个源是不是稳定可抓
- 这个源会不会制造过多噪声
- 这个源会不会把出口 IP 打黑

因此来源治理是主流程的一部分，而不是运维补丁。

### 6.2 Allowlist

建议把来源分成三种状态：

- `candidate`：来自原始清单，但还未验证
- `approved`：已验证通过，允许正式抓取
- `blocked`：不抓或暂缓

当前仓库推荐的最简单实现是：

- 只有出现在 `sources/*.yaml` 且 `enabled=true`、`access.allow_fetch=true` 的来源，才视为 allowlist

### 6.3 Relay-only 预留

借鉴成熟 RSS 系统的经验，某些域名会稳定封锁某类出口 IP。  
因此配置中应预留：

- `access.mode = relay_only`

但 Phase 1 不实现 relay。

正确顺序应是：

1. 先直连抓取并记录失败模式
2. 识别高价值且稳定直连失败的少数域名
3. 再考虑为这部分域名引入 relay

### 6.4 默认不进入 allowlist 的来源

默认不建议进入 Phase 1/2 allowlist：

- 需要登录或验证码
- 需要动态签名 token
- 稳定出现 Cloudflare challenge
- Facebook 受限页
- 明显转载聚合站

---

## 7. Collector 设计

### 6.1 Collector 列表

长期目标 collector：

- `rss`
- `html_list`
- `json_api`
- `pdf_metadata`
- `youtube`
- `twitter`

Phase 1 只落地：

- `rss`

### 6.2 RSS Collector 设计

职责：

- 请求 feed URL
- 解析 RSS/Atom
- 提取 title、summary/body、link、published_at、entry_id
- 尊重 `ETag` / `Last-Modified`
- 返回 `list[RawEvent]`

### 6.3 RSS-only 的现实处理

RSS entry 可能只有标题，没有正文。Phase 1 不能简单把这类 entry 全丢掉。

因此 `extraction_quality` 的定义必须分层：

- `1.0`：有标题 + 有足够 summary/body
- `0.7`：只有标题 + 可用 feed summary
- `0.4`：只有标题，无正文、无摘要
- `0.0`：解析失败或字段异常

Phase 1 的 Stage 1 过滤规则应改为：

- `extraction_quality < 0.4` 才丢弃

这样 RSS-only 事件仍能进入记忆池验证链路，但低质量内容会被降权。

### 6.4 Future: HTML List Collector

Phase 3 才落地。必须支持：

- CSS selector
- 分页
- 详情页抓取
- 主体正文抽取
- 失败 fallback

不在 Phase 1 提前设计成过度抽象。

---

## 7. 现实抓取策略

### 7.1 防封原则

系统默认是“低频、守规矩、缓存优先”的抓取策略。

必须遵守：

- 优先 RSS、公开 API、静态 feed
- 启用 `ETag` / `Last-Modified`
- 严控并发
- 同域名限速
- 出现 403/429 时退避
- 不使用 headless 浏览器对抗 JS 挑战作为 Phase 1 默认路径
- 不因为个别高价值站点抓不到就立即引入代理池或浏览器集群

### 7.2 IP 封锁与 WAF 风险分级

| 风险级别 | 特征 | Phase 处理策略 |
|----------|------|----------------|
| 低 | 稳定 RSS / 公共静态 feed | 正常接入 |
| 中 | HTML 页面、偶发 403 | 低频抓取，失败即跳过 |
| 高 | Cloudflare / 验证码 / 登录 / token 签名 | 不接或后置 |

### 7.3 失败策略

当 collector 遇到这些情况时：

- `429`：指数退避，不重试超过 2 次
- `403`：记日志并短期熔断该源
- `5xx`：有限重试
- 解析失败：丢弃当前源数据，不影响整个 batch

同时建议区分：

- `hard_fail`：403、challenge、认证失败
- `soft_fail`：超时、5xx、偶发解析失败

`hard_fail` 应更快进入 cooldown。

### 7.4 熔断建议

为每个 source 增加轻量熔断状态：

- 连续失败 3 次：暂停 6 小时
- 连续失败 10 次：标记 `disabled_candidate`

Phase 1 就建议写入 Redis，而不是只记日志。

推荐 key：

- `defense:source:health:{site_id}`
- `defense:source:cooldown:{site_id}`

健康状态建议包含：

- `failures`
- `last_error`
- `last_success_ts`
- `cooldown_until`

### 7.5 Conditional Fetch

RSS collector 应优先支持：

- `ETag`
- `Last-Modified`
- `304 Not Modified`

好处：

- 减少重复下载
- 降低被判定为异常抓取的概率
- 缩短单次 run 时间

建议缓存：

- `defense:http:etag:{site_id}`
- `defense:http:last_modified:{site_id}`

### 7.6 Negative Cache

对稳定失败的源，短时间内不要反复尝试。

建议：

- fetch 返回 `5xx` / timeout 后，写 60 到 300 秒 negative cache
- 同一 run 或短时间重复触发时直接跳过

这能避免失败风暴。

---

## 8. 性能与并发设计

### 8.1 性能目标

Phase 1 目标：

- 10 个 RSS 源单次 run 在 30 到 90 秒内完成
- 正常 run 原始事件量控制在 50 到 200
- 入记忆池前候选事件不超过 200

### 8.2 并发模型

采用两层限制：

- 全局并发：`Semaphore(N)`，Phase 1 推荐 `N=5`
- 域名级限速：同域最小间隔

建议再加第三层：

- source 级请求预算：单源单次 run 不超过 `max_entries`

### 8.3 DomainRateLimiter 正确实现

不能只用一个无锁 `dict`。并发下会出现同域 burst。

正确设计应是：

- 每域名一个 `asyncio.Lock`
- 使用 `time.monotonic()`
- 在锁内读写 `last_access`

伪代码：

```python
class DomainRateLimiter:
    def __init__(self):
        self._locks: dict[str, asyncio.Lock] = {}
        self._last_access: dict[str, float] = {}

    async def wait_if_needed(self, domain: str, min_interval: float) -> None:
        lock = self._locks.setdefault(domain, asyncio.Lock())
        async with lock:
            now = time.monotonic()
            last = self._last_access.get(domain, 0.0)
            elapsed = now - last
            if elapsed < min_interval:
                await asyncio.sleep(min_interval - elapsed)
            self._last_access[domain] = time.monotonic()
```

### 8.4 多实例约束

Phase 1 默认单实例运行。  
如果未来要多实例或多 worker，域名限速必须迁移到 Redis/分布式令牌桶。

### 8.5 资源预算

Phase 1 推荐配置：

- `defense_rss_concurrency = 5`
- `defense_domain_min_interval = 10s`
- `defense_news_max_per_run = 200`
- `RSS fetch timeout = 15s`

高风险源建议更严格：

- `risk_level=medium` → `min_interval >= 20s`
- `risk_level=high` → 默认不进入 seed，或只做人工验证

---

## 9. 去重设计

### 9.1 Phase 1 去重层级

| 层级 | Key | TTL | 作用 |
|------|-----|-----|------|
| L1 URL dedup | `defense:dedup:url:{hash}` | 30d | canonical URL 精确去重 |
| L2 content dedup | `defense:dedup:content:{hash}` | 30d | 同内容不同 URL 去重 |
| L3 pool dedup | `memory:dedup:defense:{source_id}` | 7d | 防止重复压缩 |

### 9.2 URL 规范化

去掉跟踪参数：

- `utm_source`
- `utm_medium`
- `utm_campaign`
- `utm_content`
- `utm_term`
- `fbclid`
- `gclid`
- `ref`
- `source`
- `mc_cid`
- `mc_eid`

### 9.3 Content hash

推荐计算：

```text
normalize(title) + "\n" + normalize(body[:1000])
```

其中 `normalize` 包括：

- lower-case
- 压缩空白
- 去除明显 HTML 残留

### 9.4 Phase 1 的现实边界

Phase 1 的 content hash 只能处理“几乎相同”的重复，不解决：

- 标题改写
- 跨语言转载
- 相同事件不同文案

这些交给 Phase 3 的 `simhash / event clustering`。

### 9.5 近重复聚类

Phase 3 增加：

- `simhash(title + lead)`
- 汉明距离阈值
- `event_cluster_id`
- canonical source 选取
- cluster size 计入后续 score

---

## 10. 过滤与打分设计

### 10.1 总体漏斗

```text
Raw events
  → Stage 1 硬过滤
  → Stage 2 规则打分
  → Top-K 截断
  → Stage 3 LLM relevance (Phase 2+)
  → Memory / Alert routing
```

### 10.2 Stage 1: 硬过滤

Phase 1 必做，零 LLM 成本。

#### 过滤项

- 发布时间超过 72 小时
- 空标题
- `extraction_quality < 0.4`
- 明显垃圾页

#### Junk pattern 示例

```text
careers?
job\s*opening
hiring
retirement\s*ceremony
photo\s*gallery
change\s*of\s*command
obituar
in\s*memoriam
```

#### 高价值例外词

```text
missile
hypersonic
deployment
exercise
carrier\s*strike
nuclear
ICBM
submarine
stealth
drone
strike
sanction
blockade
escalat
```

命中 junk pattern 但也命中高价值词时，不应直接丢弃。

### 10.3 Stage 2: 规则打分

Phase 1 必做，不调用 LLM。

建议公式：

```text
pre_score =
  source_weight
  + whitelist_bonus
  - blacklist_penalty
  - junk_context_penalty
  + high_value_context_bonus
  + family_bonus
  - low_quality_penalty
```

#### 打分原则

- `source_weight`：来源基线
- `authority_tier`：离散权威等级辅助修正
- `whitelist_bonus`：标题命中高价值关键词
- `blacklist_penalty`：标题命中低价值模式
- `family_bonus`：报告、法规可加分
- `low_quality_penalty`：仅标题、无摘要时减分

### 10.3.1 黑白名单配置原则

黑白名单不应只有一套全局硬编码，建议分三层：

- 全局通用规则
- family 级规则
- source 级规则

例如：

- 全局黑名单：`careers`, `job opening`
- news family 黑名单：`photo gallery`, `change of command`
- 某些官方源特定黑名单：`community outreach`

白名单同理：

- 全局白名单：`missile`, `nuclear`, `deployment`
- report family 白名单：`report`, `assessment`, `paper`
- source 级白名单：某来源特有项目缩写

### 10.3.2 升权降权配置

建议后续配置上支持：

- `title_whitelist`
- `title_blacklist`
- `body_whitelist`
- `body_blacklist`
- `family_bonus`
- `official_context_penalty`
- `high_value_context_bonus`

这样未来规则调优可以主要靠配置，而不是频繁改代码。

### 10.4 官方源处理原则

不建议“一刀切地给 `.mil` 降权”。

更合理的策略是：

- 官方源默认可信度不低
- 官方源中的低情报价值题材重罚
- 官方源中的高价值题材重奖

例子：

| 场景 | 处理 |
|------|------|
| `.mil + ceremony/parade/retirement` | 明显降权 |
| `.mil + missile/procurement/deployment` | 明显升权 |
| `.mil + 普通新闻` | 保留基线 |

### 10.5 Top-K 截断

Phase 1 不应“全量入池”。

建议：

- 去重、过滤、打分后
- 按 `pre_score desc, published_at desc`
- 截断至 `max_per_run = 200`

这既控 Redis 膨胀，也控 LLM 压缩成本。

### 10.6 Stage 3: LLM Relevance

Phase 2 落地。  
批量输出建议：

```json
{
  "id": "...",
  "relevance": 8,
  "category": "weapon_system",
  "entities": ["DARPA", "hypersonic missile"],
  "is_routine": false,
  "event_type": "test_launch",
  "newsworthiness": 7,
  "cross_source_keywords": ["hypersonic", "flight test"],
  "skip_reason": ""
}
```

路由策略建议：

- `relevance >= 5` → 入记忆池
- `relevance >= 7` → 深度分析候选

---

## 11. Memory Pool 与 Prompt 设计

### 11.1 直接复用现有记忆池的边界

可以复用现有 `EventMemoryPool`，但不能盲目认为现有通用压缩 prompt 足够好。

原因：

- 现有 prompt 的 category 更偏财经/宏观
- defense 事件需要更细的分类和实体抽取
- correlation 的质量高度依赖 summary/category/entities

### 11.2 Phase 1 策略

Phase 1 可以先复用现有 `memory/event_compress.jinja2`，但要明确：

- 这只能验证“链路打通”
- 不保证 defense 分类质量最优

### 11.3 Phase 2 必做

Phase 2 新增 defense 专用压缩 prompt，例如：

- `prompts/defense/event_compress.jinja2`

分类建议：

- `weapon_system`
- `military_op`
- `procurement`
- `policy_strategy`
- `tech_rd`
- `intel_recon`
- `nuclear`
- `space`
- `cyber`
- `alliance`
- `industry`
- `other`

---

## 12. 规则设计

### 12.1 四个规则

| 规则 | 调度 | Phase | 说明 |
|------|------|-------|------|
| `ingest_defense_news` | `interval:30m` | Phase 1 | RSS 新闻采集 |
| `ingest_defense_reports` | `cron:0 6 * * *` | Phase 4 | 智库报告 |
| `ingest_strategic_docs` | `cron:0 8 * * *` | Phase 4 | 法规/条令 |
| `ingest_defense_social` | `interval:1h` | Phase 5 | Twitter/YouTube |

### 12.2 Phase 1 规则行为

`ingest_defense_news` 的行为必须明确为：

- 读取 `family=news` 且 `collector=rss` 的源
- 拉取 feed
- 标准化
- L1/L2 去重
- Stage 1 / Stage 2
- Top-K 截断
- 转 `Event`
- 入记忆池
- `return False`

Phase 1 不直接发 alert。

### 12.3 占位规则

其余三个规则在对应 phase 之前应只做最小占位，不应误导为“已具备能力”。

推荐：

- 注册规则
- 日志打印 `not implemented yet`
- `return False`

---

## 13. 配置建议

`app/config.py` 推荐新增：

```python
defense_sources_dir: str = "sources"
defense_rss_timeout: float = 15.0
defense_rss_concurrency: int = 5
defense_domain_min_interval: int = 10
defense_stage1_max_age_hours: int = 72
defense_dedup_url_ttl_days: int = 30
defense_dedup_content_ttl_days: int = 30
defense_news_max_per_run: int = 200
defense_fetch_retry_count: int = 2
defense_source_fail_open: bool = True
defense_negative_cache_ttl_sec: int = 120
defense_source_cooldown_failures: int = 3
defense_source_cooldown_sec: int = 21600
```

说明：

- `fail_open=True`：单个源失败不影响整体 run
- `retry_count=2`：防止单次抖动导致整源漏抓
- `negative_cache_ttl`：避免失败风暴
- `source_cooldown_*`：让源健康控制可配置

---

## 14. 12 个 Phase 1 种子源

建议保留种子源策略，但优先选择”有稳定 RSS、低风险、内容质量高”的站点。

| ID | 名称 | 国家 | Credibility | 说明 |
|----|------|------|-------------|------|
| breakingdefense | Breaking Defense | US | 0.80 | 稳定、防务垂直 |
| defensenews | Defense News | US | 0.80 | 权威行业媒体 |
| defenseone | Defense One | US | 0.75 | 政策相关 |
| janes | Janes | GB | 0.90 | 权威度高 |
| reuters_defense | Reuters Defense | US | 0.85 | 通讯社标准 |
| darpa | DARPA | US | 0.85 | 美国国防高级研究计划局，前沿军事技术研发 |
| c4isrnet | C4ISRNET | US | 0.75 | C4ISR 与网络战专业媒体 |
| thewarzone | The War Zone | US | 0.70 | 装备分析 |
| ukdefjournal | UK Defence Journal | GB | 0.65 | 英国视角 |
| navalnews | Naval News | FR | 0.70 | 海军垂直 |
| airandspaceforces | Air & Space Forces Mag | US | 0.70 | 空天垂直 |
| army_mil | U.S. Army | US | 0.60 | 官方源样本 |

说明：

- `darpa` — DARPA 官网新闻，前沿技术信号源，credibility 高；RSS URL 待验证（`https://www.darpa.mil/news` 页面需确认 feed 地址）
- `c4isrnet` — 专注 C4ISR（指挥、控制、通信、计算机、情报、监视、侦察）和网络战领域
- `army_mil` 留作官方低信噪源过滤验证样本
- 如果某源 RSS 不稳定，应替换，不应为凑数保留
- 如果某源需要 `relay_only` 才能稳定访问，不应放入 Phase 1 seed

---

## 15. 详细 Roadmap

### Phase 1: RSS 入池验证

目标：

- 打通从 YAML 到 Memory Pool 的完整链路
- 控住性能和抓取风险
- 验证 dedup / filter / score 是否有效

实现项：

- `SourceType.DEFENSE`
- `app/defense/models.py`
- `source_loader.py`
- `rss collector`
- `normalizer.py`
- `deduper.py`
- `scorer.py`
- `converter.py`
- `defense_news_rules.py`
- `sources/defense_news.yaml`
- source health tracking
- conditional fetch metadata
- negative cache

现实注意事项：

- 所有 seed 源都必须先手工验证可达性
- 不稳定源优先替换，不为保 coverage 强接
- 不引入 relay，不引入 headless browser
- 先把 source governance 打牢，再扩 collector

验收标准：

- 10 个 seed 源可稳定抓取
- 30 分钟一次 run 不报系统性错误
- 去重后事件量显著下降
- 每次 run 入池事件量可控
- 不出现明显 Redis 膨胀
- 连续失败的源会自动进入 cooldown
- 同一失败源不会在短时间内被反复请求

### Phase 2: relevance 与 alert routing

目标：

- 在规则打分基础上加 LLM relevance
- 从 memory-first 升级到 selective alerting
- 增加 defense 专用 prompt

实现项：

- `prompts/defense/batch_relevance.jinja2`
- `prompts/defense/event_compress.jinja2`
- `prompts/defense/deep_analysis.jinja2`
- relevance batch parser
- `relevance >= 7` 的深度分析与告警路由
- source health metrics 面板或调试端点
- defense 专用压缩 prompt

验收标准：

- 每日 LLM 成本稳定
- 告警数量可控
- 告警质量明显高于纯规则打分
- source health 与 relevance 结果可观测

### Phase 3: HTML 与近重复聚类

目标：

- 将 coverage 从 RSS 源扩到更多新闻站
- 解决“同一事件不同写法”的重复

实现项：

- `html_list collector`
- selector 配置
- 详情页抓取
- simhash 近重复聚类
- event cluster scoring

现实注意事项：

- HTML 站点更容易被 ban
- 需要更严格的域名限速
- 需要失败熔断
- 需要判断哪些域名必须 `relay_only`
- 需要决定哪些高价值站点值得补抓详情页

验收标准：

- 可扩到 50 到 100 新闻源
- 无明显同域 burst
- 近重复归并有效

### Phase 4: Reports / PDF / Strategic Docs

目标：

- 接入智库报告、战略法令
- 引入长文本元数据提取

实现项：

- `pdf_metadata collector`
- 报告 landing page 抽取
- PDF link 发现
- 执行摘要抽取
- report family 的单独打分策略
- PDF 元数据缓存
- 报告类 source governance 策略

现实注意事项：

- 不全量 OCR
- 不下载过大 PDF
- 长文本只提 metadata 和前几页摘要

验收标准：

- 高价值报告稳定入池
- 关联推理能利用报告类事件

### Phase 5: Social as corroboration

目标：

- 接入 Twitter / YouTube 作为弱信号与佐证层
- 不把社交媒体直接当主信源

实现项：

- `twitter collector`
- `youtube metadata collector`
- engagement threshold
- corroboration integration
- source allowlist 缩紧，不做全量社交 flood

现实注意事项：

- Facebook 仍不建议优先接入
- YouTube 只抓元数据
- 社交源默认高噪声、高波动

验收标准：

- 社交源能增强相关性判断
- 不明显污染主情报流

### Phase 6: 运维与自学习优化

目标：

- 优化 source health
- 自动统计低价值标题模式
- 更细的 source weight 调优

实现项：

- source failure dashboard
- blacklist 统计建议
- score calibration
- source health metrics
- relay-only 列表治理
- approved / blocked 源生命周期管理

---

## 16. 专业名词说明

### RSS

一种网站内容订阅格式。站点通过 XML 暴露最新文章列表，通常包含标题、链接、摘要、发布时间。优点是结构稳定、抓取成本低。

### Atom

与 RSS 类似的内容订阅格式，字段结构略不同，但用途相同。

### Collector

采集器。负责把外部来源的数据抓回来，输出统一的 `RawEvent`。

### Source Registry

来源注册表。用 YAML 维护所有来源的配置，包括抓取方式、来源信息、过滤规则和去重策略。

### Source Governance

来源治理。指对来源做准入、分级、健康监控、熔断、停用和维护决策，而不只是“把它抓回来”。

### Allowlist

允许接入列表。只有明确批准的来源才进入正式抓取流程。

### Normalizer

规范化器。把不同 collector 的输出整理成统一字段，方便后续去重、过滤和存储。

### Dedup / 去重

识别重复内容并丢弃，避免同一事件重复入池或重复告警。

### Canonical URL

规范化后的 URL，去掉追踪参数后得到的标准链接。

### Content Hash

对标题和正文片段做哈希，判断“内容是否相同”。

### Simhash

一种近重复文本指纹算法，适合判断“文案不同但语义非常接近”的内容。

### Event Cluster

事件簇。把多个来源对同一事件的报道归到一组，用于多源印证和优先级提升。

### Source Weight / Credibility

来源权重。表示站点的基线可信度或参考价值，不等于单条内容质量。

### Authority Tier

来源权威等级。离散层级，用于粗粒度优先级、canonical source 选取和规则修正。

### Stage 1 Hard Filter

第一层硬过滤。用规则快速丢弃明显垃圾内容，不消耗 LLM。

### Stage 2 Rule Score

第二层规则打分。根据来源权重、关键词、上下文等给事件排序。

### Stage 3 LLM Relevance

第三层 LLM 相关性评估。对候选事件做批量语义判断，决定是否值得入池或告警。

### Memory Pool

记忆池。将事件压缩后存入 Redis，用于后续跨事件关联分析。

### Correlation

关联推理。让 LLM 对一段时间内多个来源的事件做归纳，寻找因果链、主题演化和潜在洞察。

### ETag / Last-Modified

HTTP 缓存协商头。服务端用它告诉客户端内容有没有变化。合理使用可减少重复下载。

### WAF

Web Application Firewall，网站防护系统。会对异常频率、异常请求头或自动化行为进行阻断。

### Cloudflare Challenge

Cloudflare 的防护挑战页面。出现时通常说明站点不欢迎简单脚本抓取。Phase 1 不应对抗处理。

### Circuit Breaker / 熔断

当某个源连续失败时，临时停止访问，避免持续打坏站点或浪费资源。

### Backoff / 退避

请求失败后按递增间隔重试，避免立刻重复打同一个目标。

### Negative Cache

负缓存。对“短时间内大概率仍会失败”的结果做短 TTL 缓存，避免失败风暴。

### Conditional Fetch

带条件的抓取。客户端带上 `ETag` 或 `Last-Modified` 请求头，若内容未变化，服务端返回 `304 Not Modified`。

### Relay-only

指某些来源不适合直接抓取，只允许通过中继服务访问。该策略应谨慎使用，只保留给稳定高价值且稳定直连失败的少数域名。

---

## 17. 主要风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| RSS 只有标题无正文 | 压缩质量下降 | 降低阈值但加低质量惩罚；Phase 2 补详情页策略 |
| 某源 feed 失效 | 单源无数据 | fail-open，保留日志与失败计数 |
| 多个协程打爆同域 | 被 ban / 429 | per-domain lock + min interval |
| WAF/Cloudflare | 采集失败 | 不对抗，降级或剔除 |
| 去重不够 | 重复内容入池 | URL + content hash；Phase 3 加 simhash |
| 通用压缩 prompt 分类失真 | correlation 质量下降 | Phase 2 上 defense 专用 prompt |
| 记忆池膨胀 | Redis 压力升高 | Top-K 截断 + TTL + family 分层 |
| 配置字段与实现不一致 | 误导开发和运维 | 文档明确 Phase 生效字段 |
| 失败源持续被打 | 被封锁加重、资源浪费 | cooldown + negative cache + source health |
| 高风险源被过早纳入 seed | Phase 1 失败率和噪声失控 | allowlist + risk_level 约束 |

---

## 18. 实现顺序

```text
Step 1: app/models.py 增加 SourceType.DEFENSE
Step 2: app/config.py 增加 defense 配置
Step 3: app/defense/models.py
Step 4: source_loader.py
Step 5: collectors/base.py + collectors/rss.py
Step 6: source health + conditional fetch metadata
Step 7: normalizer.py + deduper.py + scorer.py + converter.py
Step 8: defense_news_rules.py
Step 9: sources/defense_news.yaml
Step 10: 验证 logs / Redis keys / memory pool / source health
```

注意：

- 不要在 Step 1 就把 Phase 2+ 的接口一起实现完
- 先确保 Phase 1 代码规模小、可验证

---

## 19. 验证方案

### 19.1 启动验证

```bash
./start.sh
```

验证点：

- rule 注册成功
- scheduler 正常调度
- source loader 读到 seed 源

### 19.2 手动触发

```bash
curl -X POST http://localhost:7777/debug/trigger/ingest_defense_news
```

观察：

- 每个 source 的 fetch 数量
- dedup 前后数量
- Stage 1 过滤后数量
- Stage 2 截断后数量
- memory pool 实际写入数量
- source health 是否更新
- 失败源是否进入 cooldown

### 19.3 Redis 验证

检查：

- `defense:dedup:url:*`
- `defense:dedup:content:*`
- `memory:events`
- `memory:dedup:defense:*`

### 19.4 性能验证

记录：

- run 总耗时
- 每源 fetch 耗时
- 超时数量
- 失败数量
- LLM 压缩 batch 数量
- cooldown 命中数量
- negative cache 命中数量

### 19.5 质量验证

抽样检查：

- 是否大量进入 ceremony / hiring / gallery 类噪声
- 重要军事新闻是否能进入 pool
- 官方源是否被错误压制
- 高风险源是否被错误纳入 allowlist

---

## 20. 文件变更预估

Phase 1 预计：

- 修改 3 到 4 个已有文件
- 新建 8 到 10 个文件
- 总代码量约 600 到 900 行

关键是保持边界清晰，而不是尽量把所有 phase 一次写进来。

---

## 21. 参考项目借鉴（Worldmonitor + Crucix）

分析了两个生产级 OSINT 系统（Worldmonitor — 30+ 信源实时全球情报仪表板，Crucix — 29 源并行情报引擎），提炼以下对本项目有价值的设计思想。

### 21.1 采集结果结构化（来自 Worldmonitor Seed Framework）

Worldmonitor 的 `runSeed()` 每次采集都产出完整的执行元数据（fetchedAt、recordCount、durationMs、status），写入 `seed-meta:*` 驱动健康监控。

**应用到本项目**：M1 models 增加以下数据模型，M3 RSS Collector 的返回值从 `list[RawEvent]` 升级为 `CollectorResult`。

```python
@dataclass
class SourceMeta:
    site_id: str
    fetched_at: datetime
    record_count: int
    duration_ms: int
    status: Literal["ok", "error", "timeout", "rate_limited"]
    error_message: str | None = None

@dataclass
class CollectorResult:
    events: list[RawEvent]
    meta: SourceMeta
    etag: str | None = None
    last_modified: str | None = None
```

### 21.2 源健康端点（来自 Worldmonitor Health Check）

**应用到本项目**：Phase 1 增加 `/debug/defense/health` 端点。

```json
GET /debug/defense/health
{
  "sources": [
    { "site_id": "breakingdefense", "status": "ok", "age_minutes": 15, "record_count": 20, "consecutive_failures": 0 },
    { "site_id": "army_mil", "status": "stale", "age_minutes": 135, "record_count": 0, "consecutive_failures": 3 }
  ],
  "summary": { "ok": 10, "stale": 1, "error": 1, "total": 12 }
}
```

数据来源于 PG `source_health` 表 + 每次 run 写入的 `SourceMeta`。

### 21.3 Delta 引擎（来自 Crucix Delta Engine）

Crucix 的 Delta 引擎不只是"新不新"的二值判断，而是多维度变化计算：数值%变化 + 计数绝对变化 + 新内容语义去重。

**应用到本项目**：M7 (Scorer) Stage 2 增加 Delta 维度。

```
Stage 2 打分 = credibility 基线
  + whitelist_bonus
  - blacklist_penalty
  - ceremony_penalty
  + mil_high_value_bonus
  + report_bonus
  + 源活跃度突增 (+0.2)    ← 新增: 同一 site_id 6h 内事件密度 > 基线 2x
  + 多源实体交叉 (+0.3)    ← 新增: 同一实体在 ≥3 个不同源出现
```

Phase 1 仅做基线记录（每次 run 的 stats 写入 PG `run_history`），Phase 2 实现完整 Delta 计算。

### 21.4 告警分层 + 衰减冷却（来自 Crucix Alert Pipeline）

Crucix 的告警管道：新信号 → 语义去重 → LLM 评估 tier → 规则回退 → 衰减冷却 → 速率限制 → 推送。

```
FLASH:    5min 冷却, 6次/h   — 重大突发
PRIORITY: 30min 冷却, 4次/h  — 重要但非紧急
ROUTINE:  1h 冷却, 2次/h     — 常规情报

衰减冷却: 同一信号重复触发时
  第 1 次: 立即告警
  第 2 次: 冷却 6h
  第 3 次: 冷却 12h
  第 4 次: 冷却 24h
```

**应用到本项目**：Phase 2 新增 `M14 defense/alert_evaluator.py`，采用此分层模型。Phase 1 不生成 Alert，不实现。

### 21.5 源隔离 + allSettled（来自 Crucix）

Crucix 用 `Promise.allSettled` 确保一个源失败不中断整体。

**应用到本项目**：确认 M10 rules 中使用 `asyncio.gather(*tasks, return_exceptions=True)`。每个源的执行结果封装为：

```python
@dataclass
class SourceFetchResult:
    spec: SourceSpec
    status: Literal["ok", "error", "timeout", "skipped"]
    events: list[RawEvent]
    meta: SourceMeta
    error: str | None = None
    duration_ms: int = 0
```

### 21.6 run_history 持久化（来自 Crucix hot.json）

Crucix 将最近 3 次运行的完整统计保存在 `hot.json`，用于 Delta 计算和趋势分析。

**应用到本项目**：PG 新增 `run_history` 表。

```sql
CREATE TABLE run_history (
    id           BIGSERIAL PRIMARY KEY,
    rule_name    TEXT NOT NULL,
    started_at   TIMESTAMPTZ NOT NULL,
    finished_at  TIMESTAMPTZ,
    status       TEXT,                -- ok / error / timeout
    stats_json   JSONB,               -- {total_fetched, deduped, filtered, scored, added}
    sources_json JSONB,               -- [{site_id, status, record_count, duration_ms}]
    created_at   TIMESTAMPTZ DEFAULT NOW()
);
```

`normalized_events` 表增加 `run_id TEXT` 字段，标记每条事件属于哪次运行。

### 21.7 不借鉴的部分

| 设计 | 来源 | 不借鉴的理由 |
|------|------|-------------|
| Proto RPC 代码生成 | Worldmonitor | 过重，现有 FastAPI + Pydantic 足够 |
| 负缓存哨兵值 | Worldmonitor | Phase 1 数据量小，去重 key TTL 足够 |
| 学习路由 | Worldmonitor | Phase 1 只有 RSS（URL 固定），Phase 3 HTML 采集器再引入 |
| SSE 实时推送 | Crucix | 现有飞书 Webhook 已够用 |
| WebGL Globe 前端 | Crucix | 超出范围 |
| Telegram 双向 Bot | Crucix | 现有飞书推送已够用 |
| 冷存储日档案 | Crucix | PG 已提供持久化，不需要文件级冷存储 |
| 防波浪涌 | Worldmonitor | Phase 1 单规则不会有并发缓存未命中，Phase 3 再考虑 |
