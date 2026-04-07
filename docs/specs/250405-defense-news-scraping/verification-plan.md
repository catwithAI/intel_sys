# 防务资讯采集管线验证方案

## 1. 验证范围

本验证方案覆盖以下目标：

- YAML 声明式信源注册
- RSS 采集器
- 事件规范化
- Redis 双层去重
- 两阶段过滤打分
- PostgreSQL 持久化
- 飞书独立机器人推送
- 与现有规则/调度/API/记忆池集成
- 可观测性与调试
- 配置管理

## 2. 单元测试计划

### 2.1 `app/defense/source_loader.py`

| 测试 ID | 目标 | 输入 | 预期输出 |
|--------|------|------|---------|
| UT-SL-001 | 只加载 `enabled=true` 且 `access.allow_fetch=true` 的 source | 单个 YAML 文件，包含 3 条 source：1 条正常、1 条 `enabled=false`、1 条 `allow_fetch=false` | 仅返回正常 source；返回数量为 1 |
| UT-SL-002 | 忽略 Phase 2+ 预留字段 | source 含 `schedule`、`alert_policy`、未知字段 | SourceSpec 校验通过；字段被忽略；无异常 |
| UT-SL-003 | 单条格式错误不影响其他条目 | 同一 YAML 中 1 条缺少 `id`，1 条合法 | 返回合法 source；记录 warning；不中断 |
| UT-SL-004 | 顶层非 list 时 fail-open | YAML 顶层为 dict | 返回空列表；记录 error |
| UT-SL-005 | 只扫描 `defense_*.yaml` | 目录下有 `defense_news.yaml`、`github.yaml`、`misc.yml` | 仅加载 `defense_*.yaml` |
| UT-SL-006 | 重复 `id` 冲突检测 | 两个 `defense_*.yaml` 中存在相同 `id` | 后出现条目被拒绝加载；记录 error；返回去重后的 source 集合 |
| UT-SL-007 | 重复 URL 只告警不阻塞 | 两条 source 使用同一 RSS URL，不同 `id` | 两条都被加载；记录 warning |

### 2.2 `app/defense/rate_limiter.py`

| 测试 ID | 目标 | 输入 | 预期输出 |
|--------|------|------|---------|
| UT-RL-001 | 同域名请求间隔生效 | 同一 domain 连续调用两次 `wait_if_needed()`，`min_interval=0.2` | 第二次等待约 0.2s |
| UT-RL-002 | 不同域名互不阻塞 | 两个不同 domain 并发调用 | 两次调用均无额外串行等待 |
| UT-RL-003 | 同域并发请求被串行化 | 同一 domain 并发发起 3 次调用 | 实际访问顺序串行；相邻访问间隔不小于 `min_interval` |

### 2.3 `app/defense/collectors/rss.py`

| 测试 ID | 目标 | 输入 | 预期输出 |
|--------|------|------|---------|
| UT-RSS-001 | 成功解析 RSS feed | mock 200 RSS XML，含 2 条 entry | 返回 `CollectorResult(status="ok")`；`events` 数量为 2；`record_count=2` |
| UT-RSS-002 | `max_entries` 截断 | mock 50 条 entry，`max_entries=30` | 返回事件数量为 30 |
| UT-RSS-003 | 条件请求头生效 | 第一次响应带 `ETag` / `Last-Modified`，第二次请求 | 第二次请求头包含 `If-None-Match` / `If-Modified-Since` |
| UT-RSS-004 | 304 跳过解析 | mock 响应状态 304 | 返回 `status="not_modified"`；`events=[]` |
| UT-RSS-005 | 超时隔离 | mock 超时异常 | 返回 `status="error"` 或抛出后由上层捕获；错误信息完整 |
| UT-RSS-006 | 403/5xx 错误处理 | mock 403、500 响应 | 返回 `status="error"`；不产生 events |
| UT-RSS-007 | negative cache 命中不计失败 | Redis 中预置 `defense:neg:{site_id}` | 返回 `status="skipped"`、`skipped_reason="negative_cache"` |
| UT-RSS-008 | Content-Length 过大时拒绝解析 | mock `Content-Length > 5MB` | 返回 `status="error"`；不调用 feedparser |
| UT-RSS-009 | entry 缺 `title` 时跳过 | RSS 含 1 条无标题 entry，1 条正常 entry | 最终仅保留正常 entry |
| UT-RSS-010 | entry 缺 `id`/`guid` 时回退到 `link` hash | 仅提供 `link` | 生成 `source_id`；事件被保留 |

### 2.4 `app/defense/normalizer.py`

| 测试 ID | 目标 | 输入 | 预期输出 |
|--------|------|------|---------|
| UT-NM-001 | 长摘要判定为高质量 | 标题 + 300 字 body | `extraction_quality=1.0` |
| UT-NM-002 | 短摘要判定为中质量 | 标题 + 80 字 body | `extraction_quality=0.7` |
| UT-NM-003 | 仅标题判定为低质量但保留 | 标题，无 body | `extraction_quality=0.4` |
| UT-NM-004 | 缺失发布时间时回退到 now | `published_at=None` | 输出 `published_at` 接近当前 UTC 时间 |
| UT-NM-005 | URL 规范化去追踪参数 | URL 含 `utm_*`、`fbclid`、fragment | `canonical_url` 去除追踪参数和 fragment |
| UT-NM-006 | 保留原始 scheme | `http://example.com?a=1` | `canonical_url` 仍为 `http://...`，不强制转 https |
| UT-NM-007 | 非 http(s) URL 返回空 canonical | `mailto:` 或 `javascript:` | `canonical_url is None` |
| UT-NM-008 | 生成 `url_hash` 与 `content_hash` | 标题、body、canonical_url 给定 | `dedup_keys` 含 `url_hash`、`content_hash`；长度与算法符合预期 |
| UT-NM-009 | HTML 清洗 | body 含 HTML 标签和实体 | 输出 body 去标签且解码实体 |
| UT-NM-010 | 未来时间修正 | `published_at > now + 1h` | 输出时间被修正为接近 now |

### 2.5 `app/defense/deduper.py`

| 测试 ID | 目标 | 输入 | 预期输出 |
|--------|------|------|---------|
| UT-DD-001 | L1 URL 去重 | 两个事件 `url_hash` 相同 | 仅保留 1 条 |
| UT-DD-002 | L2 内容去重 | 两个事件 `content_hash` 相同，URL 不同 | 仅保留 1 条 |
| UT-DD-003 | TTL 正确写入 | 新事件入 dedup | Redis key TTL 为 `defense_dedup_ttl` |
| UT-DD-004 | pipeline 批量执行 | 一批 20 条事件 | 使用 pipeline；结果数量符合预期 |
| UT-DD-005 | 缺失 `url_hash` 时只走内容去重 | `url_hash=None`，`content_hash` 存在 | 不报错；可按内容去重 |

### 2.6 `app/defense/scorer.py`

| 测试 ID | 目标 | 输入 | 预期输出 |
|--------|------|------|---------|
| UT-SC-001 | `extraction_quality < 0.4` 被 Stage 1 丢弃 | 质量 0.0 的事件 | Stage 1 输出为空 |
| UT-SC-002 | `title_blacklist` 硬过滤且无豁免 | 标题含 `sponsored`，同时含 whitelist 词 | 仍被丢弃 |
| UT-SC-003 | `junk_patterns` 命中但有 whitelist 豁免 | 标题含 `change of command` 且含 `missile` | Stage 1 保留 |
| UT-SC-004 | `junk_patterns` 命中且无 whitelist 豁免 | 标题含 `photo gallery` | Stage 1 丢弃 |
| UT-SC-005 | Stage 2 whitelist 加分 | 标题含 `hypersonic` | `pre_score` 增加 +0.3 |
| UT-SC-006 | Stage 2 junk 扣分 | 标题含 `ceremony` 且通过 Stage 1 豁免 | `pre_score` 扣减 -0.4 |
| UT-SC-007 | credibility 和 quality 计分 | credibility=0.8, quality=1.0 | `pre_score` 含 `0.8*0.2 + 1.0*0.1` |
| UT-SC-008 | authority_tier 修正 | authority_tier=1/2/3/4 | 分别对应 +0.2/+0.1/0/-0.1 |
| UT-SC-009 | Top-K 截断排序 | 300 条不同分数事件，`k=200` | 输出 200 条，按 `pre_score desc` 排序 |

### 2.7 `app/defense/converter.py`

| 测试 ID | 目标 | 输入 | 预期输出 |
|--------|------|------|---------|
| UT-CV-001 | 正确映射到 `Event` | 完整 `NormalizedEvent` | 输出 `Event.source=SourceType.DEFENSE`，字段映射正确 |
| UT-CV-002 | metadata 携带去重和站点信息 | `site_id/site_name/canonical_url/dedup_keys` | 这些字段存在于 `Event.metadata` |
| UT-CV-003 | 时间回退正确 | `published_at=None` | `Event.timestamp` 使用当前时间 |

### 2.8 `app/defense/health.py`

| 测试 ID | 目标 | 输入 | 预期输出 |
|--------|------|------|---------|
| UT-HM-001 | 成功后重置失败计数 | 预置 `consecutive_failures=2`，调用 `record_success()` | 状态为 `ok`，失败计数归零 |
| UT-HM-002 | 连续失败 3 次进入冷却 | 连续 3 次 `record_failure()` | 状态变为 `cooling_down`，`cooldown_until` 被设置 |
| UT-HM-003 | 连续失败 10 次标记待禁用 | 连续 10 次 `record_failure()` | 状态变为 `pending_disable` |
| UT-HM-004 | lazy recovery 生效 | 状态为 `cooling_down` 且 `cooldown_until < now`，调用 `is_available()` | 自动恢复为 `ok`，返回可用 |
| UT-HM-005 | `pending_disable` 不可用 | 状态为 `pending_disable` | `is_available()` 返回 false |

### 2.9 `app/defense/storage.py`

| 测试 ID | 目标 | 输入 | 预期输出 |
|--------|------|------|---------|
| UT-ST-001 | 初始化建表 | 调用 `init_tables()` | `normalized_events`、`source_health`、`run_history` 存在 |
| UT-ST-002 | append-only 插入 normalized_events | 插入两条相同 canonical_url 的事件 | 两条都成功写入 |
| UT-ST-003 | `run_id/url_hash/content_hash` 正确落库 | 插入 1 条事件 | 三字段可查询 |
| UT-ST-004 | `insert_run()` 正确写入状态与 stats | run_status=`partial` | `run_history` 中状态和 JSONB 与输入一致 |
| UT-ST-005 | `get_source_health()` 返回完整字段 | 插入 2 条 source_health 记录 | 查询结果包含状态、失败次数、时间字段 |

### 2.10 `app/delivery/feishu.py`

| 测试 ID | 目标 | 输入 | 预期输出 |
|--------|------|------|---------|
| UT-FD-001 | defense 单条卡片格式正确 | 1 条 `Alert(source=DEFENSE)` | 卡片包含标题、信源名称、国家、发布时间、原文链接、摘要 |
| UT-FD-002 | defense digest 卡片格式正确 | 3 条 defense alerts | 生成摘要卡片，列出标题与链接 |
| UT-FD-003 | 未配置 webhook 时 Noop 降级 | `NoopDelivery.send_batch()` | 返回成功或无异常，不发网络请求 |

### 2.11 `app/main.py` / `app/engine/context.py`

| 测试 ID | 目标 | 输入 | 预期输出 |
|--------|------|------|---------|
| UT-MN-001 | `RuleContext.app_state` 注入成功 | 调用 `execute_rule(..., app_state=mock_state)` | rule 中可访问 `ctx.app_state.pg_pool` / `ctx.app_state.defense_delivery` |
| UT-MN-002 | PG 未配置时可降级启动 | `pg_dsn` 为空 | 应用启动成功；`pg_pool=None`；不崩溃 |
| UT-MN-003 | defense webhook 未配置时降级 | `feishu_defense_webhook_url` 为空 | `app.state.defense_delivery` 为 `NoopDelivery` |

## 3. 集成测试计划

### 3.1 端到端主成功路径

| 测试 ID | 场景 | 步骤 | 预期结果 |
|--------|------|------|---------|
| IT-E2E-001 | 单次成功采集并入池+推送 | mock 2 个 feed，返回 10 条高质量事件，其中 3 条 `pre_score >= threshold`；执行 `ingest_defense_news` | `normalized_events` 落库 10 条；去重/过滤后事件进入记忆池；生成 3 条 alerts；Redis `alerts:defense` 有 3 条；飞书 `send_batch()` 被调用一次；`run_history.status="ok"` |
| IT-E2E-002 | 混合模式验证 | mock 8 条通过 Stage 2+Top-K，其中 2 条高分、6 条低分 | 8 条都写入记忆池；仅 2 条生成 Alert |
| IT-E2E-003 | append-only 验证 | 两次 run 抓到相同 canonical_url 事件 | `normalized_events` 保留两条记录；Redis 去重只允许后续阶段处理一条 |

### 3.2 错误隔离与健康状态

| 测试 ID | 场景 | 步骤 | 预期结果 |
|--------|------|------|---------|
| IT-E2E-004 | 单源超时不影响其他源 | 3 个 feed：1 个超时，2 个成功 | 成功源正常入池；失败源记入 health；run_status=`partial` |
| IT-E2E-005 | 连续失败进入冷却 | 同一 source 连续 3 次返回 500 | 第 3 次后 `source_health.status="cooling_down"` |
| IT-E2E-006 | 连续失败达到待禁用 | 同一 source 连续 10 次失败 | `source_health.status="pending_disable"` |
| IT-E2E-007 | cooling_down lazy recovery | 预置 `cooldown_until < now` 后再执行规则 | `is_available()` 自动恢复；source 被重新纳入采集 |
| IT-E2E-008 | negative cache 不推动失败计数 | 第 1 次真实失败后命中 2 次 negative cache | `consecutive_failures` 仅增加 1，不增加到 3 |

### 3.3 条件请求与去重

| 测试 ID | 场景 | 步骤 | 预期结果 |
|--------|------|------|---------|
| IT-E2E-009 | 第二次请求返回 304 | 第一次抓取 200 + ETag，第二次抓取 304 | 第二次不解析 feed，不新增 events；`sources_not_modified` 增加 |
| IT-E2E-010 | URL/content 双层去重 | feed 中包含同 URL 与同内容不同 URL 的重复项 | 后续阶段仅保留 1 条；dedup key TTL 正确 |

### 3.4 配置与降级

| 测试 ID | 场景 | 步骤 | 预期结果 |
|--------|------|------|---------|
| IT-E2E-011 | defense webhook 未配置 | 清空 `feishu_defense_webhook_url` 执行规则 | 不报错；alerts 仍写入 Redis；不发飞书 |
| IT-E2E-012 | PG 未配置 | 清空 `pg_dsn` 执行规则 | 规则仍可运行；不写 PG；记忆池/alerts 链路可用 |
| IT-E2E-013 | YAML 热更新生效 | 第一次 run 使用 2 个 source；修改 YAML 新增第 3 个 source；第二次 run | 第二次 run 自动加载新增 source，无需 reload |

### 3.5 集成 API 与调试

| 测试 ID | 场景 | 步骤 | 预期结果 |
|--------|------|------|---------|
| IT-E2E-014 | `GET /alerts/defense` 查询 defense 告警 | 先执行产生 alerts 的 run，再调用 API | 返回 defense alerts 列表和 total |
| IT-E2E-015 | `/debug/trigger/ingest_defense_news` 手动触发 | 通过 debug 端点触发 | 规则成功执行；能访问 `ctx.app_state`；结果与 scheduler 路径一致 |
| IT-E2E-016 | `/debug/defense/health` 查询健康状态 | 构造 ok/cooling_down/pending_disable 三类 source | API 返回各状态及统计汇总 |
| IT-E2E-017 | `/debug/defense/runs` 查询运行历史 | 执行 2 次 run | API 返回最近 run_history，含 status/stats |

## 4. 手动验证清单

### 4.1 种子源可达性与内容质量

- 使用候选 seed 列表逐一访问 RSS URL。
- 确认返回为有效 RSS/Atom XML。
- 确认最近 7 天内有新 entry。
- 确认无 Cloudflare challenge、403、重定向到登录页、明显 paywall。
- 对每个通过验证的 seed 记录：URL、国家、authority_tier、验证时间、样本条数。
- 对验证失败的 seed 按设计附录替换为备选源。

### 4.2 飞书卡片人工验收

- 确认 defense 单条卡片标题、信源名称、国家、发布时间、原文链接、正文摘要均展示正确。
- 确认 defense digest 卡片能展示多条 alert 标题与链接。
- 确认卡片中文案、链接跳转和折叠区内容可读。
- 确认 defense webhook 与现有普通 webhook 隔离，不串群、不串消息。

### 4.3 调试与观测

- 手动触发 `/debug/trigger/ingest_defense_news`，确认执行成功。
- 查看 `/debug/scheduler`，确认 defense 规则已注册并按 `interval:1800s` 调度。
- 查看 `/debug/defense/health`，确认状态字段、失败次数、冷却信息正确。
- 查看 `/debug/defense/runs`，确认 run_history 统计与实际执行相符。
- 查看应用日志，确认结构化日志包含：源数量、成功数、失败数、原始事件数、去重后数量、过滤后数量、入池数量、告警数量、run_id。

### 4.4 降级场景

- 关闭 defense webhook 后重新执行，确认系统无异常，消息不发送。
- 关闭 PG 后重新执行，确认系统可继续采集并入池。
- 人工将某个 source 连续失败 3 次，确认进入冷却。
- 人工将某个 source 连续失败 10 次，确认进入待禁用。

## 5. 性能验证

### 5.1 性能基线

| 测试 ID | 目标 | 方法 | 预期结果 |
|--------|------|------|---------|
| PT-001 | 单次 run 总时长 | 10 个 seed、每源 30 条、全局并发 5 | 总耗时 ≤ 150s |
| PT-002 | RSS 采集阶段时长 | mock 10 源，含限速与条件请求 | 采集阶段 ≤ 90s |
| PT-003 | PG 批量写入时长 | 单次写入 300 条 normalized_events | ≤ 5s |
| PT-004 | 去重阶段时长 | 300 条事件，Redis pipeline | ≤ 2s |
| PT-005 | YAML 加载开销 | 扫描并解析 `defense_*.yaml` | ≤ 10ms |

### 5.2 并发与限速

| 测试 ID | 目标 | 方法 | 预期结果 |
|--------|------|------|---------|
| PT-006 | 全局并发上限有效 | 统计同一时刻活跃采集协程数 | 不超过 `defense_rss_concurrency` |
| PT-007 | 同域最小间隔有效 | 2 个同域 source 连续抓取 | 相邻请求间隔 ≥ `defense_domain_min_interval` |
| PT-008 | 高重复率场景性能 | 300 条事件中 80% 为重复 | 去重性能无明显退化；后续阶段仅处理唯一事件 |

## 6. 安全验证

### 6.1 输入与解析安全

| 测试 ID | 目标 | 方法 | 预期结果 |
|--------|------|------|---------|
| ST-001 | YAML 非法输入隔离 | 构造格式错误、缺字段、未知字段 YAML | 单条失败不影响其他 source；无进程崩溃 |
| ST-002 | HTML/脚本内容不会污染下游 | RSS summary 含 HTML 标签、脚本片段 | 输出 body 已清洗；卡片中不出现原始标签脚本 |
| ST-003 | 非 http(s) 链接被拒绝规范化 | RSS link 为 `javascript:`、`mailto:` | `canonical_url` 为空；不参与 URL 去重 |

### 6.2 存储与网络安全

| 测试 ID | 目标 | 方法 | 预期结果 |
|--------|------|------|---------|
| ST-004 | PG 参数化写入 | 标题/body 含引号、SQL 关键字 | 正常落库；无 SQL 注入风险 |
| ST-005 | defense webhook 配置隔离 | 同时配置普通 webhook 和 defense webhook | defense alerts 仅走 defense webhook |
| ST-006 | negative cache 防止失败风暴 | 对失败源持续高频触发抓取 | negative cache 生效；不会持续打源站 |

## 7. 验收映射

| 需求 | 验证项 |
|------|-------|
| 需求 1 YAML 声明式注册 | UT-SL-001~007, IT-E2E-013, 手动 4.1 |
| 需求 2 RSS 采集器 | UT-RL-001~003, UT-RSS-001~010, IT-E2E-004~010, PT-006~008 |
| 需求 3 事件规范化 | UT-NM-001~010 |
| 需求 4 双层去重 | UT-DD-001~005, IT-E2E-010 |
| 需求 5 两阶段过滤打分 | UT-SC-001~009, IT-E2E-001~002 |
| 需求 6 PostgreSQL 持久化 | UT-ST-001~005, IT-E2E-001, IT-E2E-003, IT-E2E-012 |
| 需求 7 飞书独立机器人 | UT-FD-001~003, IT-E2E-001, IT-E2E-011, 手动 4.2 |
| 需求 8 系统集成 | UT-CV-001~003, UT-MN-001~003, IT-E2E-014~016 |
| 需求 9 可观测性与调试 | IT-E2E-016~017, 手动 4.3 |
| 需求 10 配置管理 | UT-MN-002~003, IT-E2E-011~013 |

## 8. 通过标准

- 所有单元测试通过。
- 所有集成测试通过。
- 手动验证清单全部打勾。
- 性能指标满足第 5 节预算。
- 安全验证未发现阻塞性问题。
- 候选 seed RSS 验证完成并形成最终可用 seed 列表后，方可进入实现验收。
