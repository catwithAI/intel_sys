# 里程碑 1 审查结论（Codex）

## 结论

**不通过**

## 必须修改的问题

### 1. `SourceSpec` 接口与设计不匹配，后续任务会直接断链

- 位置：`app/defense/models.py:50-66`
- 对照：`docs/specs/250405-defense-news-scraping/design.md:193-236`
- 问题：
  - 当前只定义了 `SourceFilters`，缺少设计要求的 `SourceAccess`、`SourceDedup`、`SourceFetch`、`SourceExtra` 子模型。
  - 当前 `SourceSpec` 缺少 `enabled`、`family`、`tier`、`access`、`dedup`、`fetch`、`extra` 等字段，却额外引入了 `schedule`；而 `docs/defense-integration-plan.md:151-156` 已明确 Phase 1 不做 per-source `schedule` 动态调度。
  - 由于 `extra="allow"`，设计中的 `access/fetch/extra` YAML 片段会被接收成裸 `dict`，不是设计约定的强类型子模型；后续按设计访问 `spec.access.allow_fetch`、`spec.fetch.negative_ttl_sec`、`spec.extra.name` 会直接失败。
- 影响：
  - `SourceLoader` 的 enabled/access 过滤无法按设计落地。
  - `RSSCollector` 无法从 `spec.fetch` 读取 `timeout/max_entries/negative_ttl_sec`。
  - `Normalizer`/`Converter` 无法稳定拿到 `site_name` 等元数据。

### 2. `CollectorResult` 字段不完整，无法支撑 collector/health/run-history 接口

- 位置：`app/defense/models.py:69-81`
- 对照：`docs/specs/250405-defense-news-scraping/design.md:240-260`
- 问题：
  - 当前模型只有 `site_id/events/status/duration_ms/record_count`。
  - 设计要求的 `http_status`、`etag`、`last_modified`、`error`、`skipped_reason` 全部缺失。
- 影响：
  - `304 not_modified`、negative cache `skipped`、错误诊断、健康状态更新和 run history 统计都拿不到设计约定的上下文。
  - 里程碑 2/4 的接口将被迫返工或临时扩字段，破坏基础模型稳定性。

### 3. `NormalizedEvent` 的 URL 可空性定义错误，边界条件会触发错误数据或空值崩溃

- 位置：`app/defense/models.py:25-40`
- 对照：`docs/specs/250405-defense-news-scraping/design.md:169-183`
- 问题：
  - 当前将 `url`、`canonical_url` 定义为必填 `str`。
  - 设计明确要求二者为 `str | None`，因为 RSS entry 允许缺失 `link`，collector 会走降级路径。
- 影响：
  - 后续 normalizer 要么构造伪 URL 填洞，要么在无链接 feed 上抛错。
  - 这属于明确的数据模型边界错误，不应在后续任务再修补。

### 4. 配置集不完整，且默认值明显偏离设计的风险控制和性能预算

- 位置：`app/config.py:101-117`
- 对照：
  - `docs/specs/250405-defense-news-scraping/tasks.md:5-10`
  - `docs/specs/250405-defense-news-scraping/design.md:792-811`
  - `docs/defense-integration-plan.md:677-689`
- 问题：
  - 任务要求“添加所有 defense 配置项 + PG 配置项 + defense 飞书 webhook”，但当前缺少 `defense_max_entries_per_source`、`defense_cooldown_hours`、`defense_max_consecutive_failures`、`defense_disable_threshold` 等设计字段。
  - 当前默认值与设计/预算显著不一致：
    - `defense_rss_concurrency`: 10，而设计为 5
    - `defense_domain_min_interval`: 2.0，而设计为 10.0
    - `defense_rss_timeout`: 30.0，而设计为 15.0
    - `defense_topk`: 20，而设计为 200
- 影响：
  - 健康状态机和 collector 配置所需参数不齐，后续任务无法按设计实现。
  - 当前并发/限速默认值会把“防封优先”的设计前提反过来，属于性能与安全预算偏差。

### 5. `docs/defense-integration-plan.md` 未完成任务要求的基线同步，架构基准仍自相矛盾

- 位置：
  - `docs/defense-integration-plan.md:136-149`
  - `docs/defense-integration-plan.md:994`
  - 任务要求见 `docs/specs/250405-defense-news-scraping/tasks.md:9`
- 问题：
  - 文档前部已写成 Phase 1 为 `memory-first + selective alert`，并明确高分事件产出 Alert。
  - 但后部仍保留“Phase 1 不直接发 alert”。
- 影响：
  - 这正是设计复审里要求在任务 1 同步修正的基线冲突。
  - 里程碑 1 作为后续实现依据，文档仍然矛盾，属于架构一致性阻塞项。

## 其他检查

- 安全：本次 diff 未发现独立新增的高危漏洞；阻塞项主要是模型/配置契约不完整导致的后续实现风险。
- 性能：阻塞项 4 已覆盖当前默认值对抓取风险控制和性能预算的偏离。

## 复审结论

**通过**

- `app/defense/models.py` 已补齐 `SourceAccess`、`SourceDedup`、`SourceFetch`、`SourceExtra`，`SourceSpec` 与 `CollectorResult` 已对齐设计约定，`NormalizedEvent.url/canonical_url` 已改为可空。
- `app/config.py` 已补齐里程碑 1 要求的 defense/PG 配置项，默认值已回到设计中的风险控制与性能预算。
- `docs/defense-integration-plan.md` 已消除 Phase 1 alert 策略的自相矛盾描述。
