# 里程碑 4 审查结论（Codex）

## 结论

**不通过**

## 必须修改的问题

### 1. `SourceHealthManager.is_available()` 的 lazy recovery 只改内存缓存，没有回写 PostgreSQL

- 位置：`app/defense/health.py:34-53`
- 对照：`docs/specs/250405-defense-news-scraping/design.md:268-278`
- 问题：
  - 设计要求 `source_health` 表是权威状态源，lazy recovery 在 `is_available()` 触发后应把状态恢复为 `ok` 并重置 `consecutive_failures`
  - 当前只修改 `_cache`，没有调用 `upsert_source_health`
- 影响：
  - 内存状态和 PG 状态分裂，刷新缓存后又会回到旧状态
  - 违反“PG 是单一事实源”的设计约束

### 2. `record_success()` / `record_failure()` 没有维护 `total_fetches` / `total_failures`

- 位置：`app/defense/health.py:55-87`
- 对照：`docs/specs/250405-defense-news-scraping/design.md:304-315`
- 问题：
  - `source_health` 表结构包含 `total_fetches`、`total_failures`
  - 当前成功和失败路径都没有更新这些字段
- 影响：
  - 健康统计失真，表结构里的计数字段失去意义
  - 后续 debug/health API 无法反映真实运行情况

### 3. `record_success()` / `record_failure()` 用整条覆盖式缓存更新，容易丢失已有字段

- 位置：`app/defense/health.py:64-65`, `app/defense/health.py:88`
- 问题：
  - 当前 `_cache[site_id] = {"site_id": site_id, **payload}`
  - 这会丢掉已有的 `total_fetches`、`total_failures`、`disabled_reason`、`cooldown_until` 等字段
- 影响：
  - 状态迁移后缓存内容不完整，后续逻辑依赖旧字段时会出错或失真

### 4. `upsert_source_health()` 采用先查再改的两段式写法，存在并发竞态

- 位置：`app/defense/storage.py:135-159`
- 对照：`docs/specs/250405-defense-news-scraping/design.md:278`
- 问题：
  - 当前先 `SELECT` 再 `UPDATE/INSERT`
  - 并发情况下两个协程可能同时判定记录不存在，随后其中一个 `INSERT` 失败
- 影响：
  - 健康状态写入不具备原子性
  - 这类状态表更适合单条 `INSERT ... ON CONFLICT DO UPDATE`

### 5. `run_history` 写入语义偏离 append-only 设计

- 位置：`app/defense/storage.py:121-133`
- 对照：`docs/specs/250405-defense-news-scraping/design.md:531-549`
- 问题：
  - 当前 `insert_run()` 使用 `ON CONFLICT (id) DO UPDATE`
  - 设计给出的 `run_history` 是普通 append-only 运行记录表，没有要求 upsert
- 影响：
  - 同一个 `run_id` 的重复写入会覆盖历史记录，不符合运行历史的审计语义

## 通过项

- `storage.py` 的 `normalized_events`、`run_history`、`source_health` 基础表结构整体接近设计，`normalized_events` 保持 append-only，无 `UNIQUE(canonical_url)` 约束
- `insert_normalized_events()` 已按批量 append-only 方式写入 `normalized_events`
- `refresh_cache()`、`record_success()`、`record_failure()` 的基本状态迁移方向与设计一致：`ok -> cooling_down -> pending_disable`

## 复审结论

**通过**

- `health.py` 已补上 lazy recovery 回写、计数器维护和缓存 merge 更新
- `storage.py` 已将 `upsert_source_health()` 改为原子 `ON CONFLICT DO UPDATE`，`insert_run()` 改为 append-only `INSERT`
