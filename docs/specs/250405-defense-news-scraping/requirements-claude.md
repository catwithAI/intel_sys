# 需求文档（Claude 版）

## 简介

基于已有 intel_sys 项目，接入防务资讯类信源（103 个网站中的种子源），实现 RSS 采集 → 规范化 → 去重 → 过滤打分 → 存储 → 飞书推送的完整链路。沿用 `docs/defense-integration-plan.md` 的架构设计，Phase 1 只做 RSS 采集器，通过 YAML 声明式配置管理信源。

## 需求

### 需求 1 - YAML 声明式信源注册

**用户故事：** 作为系统运维人员，我想要通过 YAML 配置文件声明防务信源，以便新增或修改信源时只需改配置而不改代码。

#### 验收标准

1. 当系统启动时，如果 `sources/` 目录下存在 YAML 配置文件，那么系统应该自动加载所有 `enabled=true` 且 `access.allow_fetch=true` 的信源
2. 当 YAML 配置中某条信源格式错误时，那么系统应该跳过该条并记录警告日志，不影响其他信源的加载
3. 当配置文件中包含 Phase 2+ 预留字段（如 `schedule`、`alert_policy`）时，那么系统应该忽略这些字段，不报错
4. 系统应该提供至少 10 个经过验证的种子源 RSS 配置（从 103 个防务资讯网站中筛选）

### 需求 2 - RSS 采集器

**用户故事：** 作为情报分析人员，我想要系统自动采集防务资讯网站的 RSS 订阅内容，以便及时获取最新防务动态。

#### 验收标准

1. 当定时任务触发时（默认 30 分钟间隔），那么 RSS 采集器应该并发采集所有已加载的信源
2. 当 RSS 响应包含 `ETag` 或 `Last-Modified` 头时，那么采集器应该在下次请求时携带对应的条件请求头，服务器返回 304 时跳过解析
3. 当某个信源采集失败（超时、403、5xx）时，那么系统应该记录错误并跳过该源，不影响其他源的采集
4. 当同一域名存在多个信源时，那么系统应该控制同域名请求间隔（默认 10 秒），避免被封禁
5. 当信源连续失败 3 次时，那么系统应该对该源进入冷却期（6 小时暂停采集），连续失败 10 次标记为待禁用
6. 系统应该限制全局并发数（默认 5），单源单次采集条目不超过 `max_entries`（默认 30）

### 需求 3 - 事件规范化

**用户故事：** 作为数据处理管线，我需要将不同 RSS 源的异构数据转换为统一的 NormalizedEvent 格式，以便后续去重和打分逻辑能统一处理。

#### 验收标准

1. 当 RSS entry 包含标题和正文/摘要时，那么规范化器应该输出 `extraction_quality=1.0` 的 NormalizedEvent
2. 当 RSS entry 只有标题和简短的 feed summary 时，那么规范化器应该输出 `extraction_quality=0.7`
3. 当 RSS entry 只有标题无正文时，那么规范化器应该输出 `extraction_quality=0.4`（不丢弃）
4. 当 RSS entry 缺少 `published_at` 时，那么规范化器应该使用当前时间作为 fallback
5. 规范化器应该对 URL 进行规范化处理（去除追踪参数、统一 scheme），生成 `canonical_url`
6. 规范化器应该生成 `dedup_keys`，包含 `url_hash`（canonical_url 的 MD5）和 `content_hash`（title + body[:1000] 的 MD5）

### 需求 4 - 双层去重

**用户故事：** 作为系统运维人员，我想要系统自动过滤重复内容，以便避免同一新闻被多次处理和推送。

#### 验收标准

1. 当新事件的 `url_hash` 已存在于 Redis 去重集合中时，那么系统应该丢弃该事件（L1 URL 去重）
2. 当新事件的 `content_hash` 已存在于 Redis 去重集合中时，那么系统应该丢弃该事件（L2 内容去重）
3. 去重 key 的 TTL 应该为 7 天（防止无限膨胀）
4. 去重操作应该使用 Redis pipeline 批量执行，提高性能

### 需求 5 - 两阶段过滤打分

**用户故事：** 作为情报分析人员，我想要系统自动过滤低价值内容并对有价值内容打分排序，以便优先关注最重要的防务动态。

#### 验收标准

1. **Stage 1 硬过滤**：当事件标题或正文匹配 `title_blacklist` 或全局 `junk_patterns`（如 careers、retirement、photo gallery、change of command）时，那么系统应该直接丢弃
2. **Stage 1 硬过滤**：当事件 `extraction_quality < 0.4` 时，那么系统应该丢弃
3. **Stage 2 规则打分**：系统应该根据以下规则计算 `pre_score`：
   - 标题匹配 `title_whitelist` 关键词：+0.3
   - 标题匹配 `title_blacklist` 关键词：-0.5
   - 匹配仪式/任命等 junk pattern：-0.4
   - 信源权威度（`credibility`）：+credibility * 0.2
   - `extraction_quality`：+quality * 0.1
4. 打分后应该按 `pre_score` 降序排列，取 Top-K（默认 200）进入下一阶段

### 需求 6 - PostgreSQL 持久化

**用户故事：** 作为系统运维人员，我想要将采集到的规范化事件持久化到 PostgreSQL，以便支持长期存储、回溯查询和审计。

#### 验收标准

1. 系统启动时应该初始化 asyncpg 连接池，配置通过 `pg_dsn`、`pg_pool_min`、`pg_pool_max` 环境变量管理
2. NormalizedEvent 应该在去重**之前**批量写入 `normalized_events` 表（即使被去重丢弃也有记录）
3. 系统应该创建 `source_health` 表，每次采集后更新信源健康状态（成功/失败次数、连续失败数、最后成功/失败时间）
4. 系统应该创建 `run_history` 表，记录每次采集运行的元数据（开始/结束时间、状态、统计信息）
5. 写入 PG 应该是异步非阻塞的，不影响主采集管线性能

### 需求 7 - 飞书独立机器人推送

**用户故事：** 作为情报分析人员，我想要防务情报通过独立的飞书群机器人推送，以便与现有的 Polymarket/GitHub/HN 告警分开接收。

#### 验收标准

1. 系统应该支持配置独立的飞书 Webhook URL（`FEISHU_DEFENSE_WEBHOOK_URL`），与现有 `FEISHU_WEBHOOK_URL` 分开
2. 当防务事件通过过滤后，那么系统应该通过独立的飞书机器人推送卡片消息
3. 防务卡片应该包含：标题、信源名称、国家、发布时间、原文链接、正文摘要
4. 当 `FEISHU_DEFENSE_WEBHOOK_URL` 未配置时，那么系统应该降级为 NoopDelivery，不推送但不报错

### 需求 8 - 与现有系统集成

**用户故事：** 作为开发者，我想要防务信源作为新管线无缝集成到现有 intel_sys 架构中，以便复用已有的调度、状态管理和 API 基础设施。

#### 验收标准

1. 新增 `SourceType.DEFENSE` 枚举值，用于标识防务信源
2. 防务采集规则应该通过现有 `@rule_registry.register()` 装饰器注册，由 APScheduler 统一调度
3. 防务事件最终应该转换为现有 `Event` 模型，可进入记忆池参与跨事件关联推理
4. 系统应该提供 `GET /alerts/defense` API 端点查询防务告警
5. 调试端点（`/debug/trigger/`、`/debug/scheduler/`）应该支持防务规则的手动触发和调度控制

### 需求 9 - 可观测性与调试

**用户故事：** 作为系统运维人员，我想要能够监控防务采集管线的运行状态，以便及时发现和排查问题。

#### 验收标准

1. 系统应该提供 `/debug/defense/health` 端点，展示所有信源的健康状态（成功率、连续失败数、是否在冷却期）
2. 每次采集运行应该记录结构化日志，包含：采集源数、成功数、失败数、原始事件数、去重后事件数、过滤后事件数、入池事件数
3. 采集运行历史应该可通过 API 查询（`run_history` 表）

### 需求 10 - 配置管理

**用户故事：** 作为系统运维人员，我想要通过环境变量统一管理防务模块的配置参数，以便在不同环境间灵活调整。

#### 验收标准

1. 系统应该支持以下配置项（通过 `app/config.py` 的 Pydantic Settings）：
   - `defense_rss_interval`：RSS 采集间隔（默认 1800 秒）
   - `defense_rss_concurrency`：全局并发数（默认 5）
   - `defense_domain_min_interval`：同域名最小间隔（默认 10 秒）
   - `defense_rss_timeout`：单源超时（默认 15 秒）
   - `defense_topk`：过滤后保留条目数（默认 200）
   - `defense_dedup_ttl`：去重 key TTL（默认 604800 秒 / 7 天）
   - `pg_dsn`：PostgreSQL 连接字符串
   - `pg_pool_min` / `pg_pool_max`：连接池大小
   - `FEISHU_DEFENSE_WEBHOOK_URL`：防务飞书 Webhook
2. 所有配置项应该有合理的默认值，未配置时系统能正常启动（PG 除外，可选降级）
