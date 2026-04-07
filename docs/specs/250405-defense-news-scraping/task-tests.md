# 任务级验证规格

## 里程碑 1：基础设施与数据模型

### 任务 1 — 集成改造

- 断言目标：`SourceType.DEFENSE` 可导入；`Settings` 暴露 defense/PG/webhook 配置；`RuleContext` 含 `app_state`；`pyproject.toml` 含 `feedparser/asyncpg/pyyaml`；`docs/defense-integration-plan.md` 已同步到混合模式基线
- 测试文件路径：`tests/defense_tasks/test_m1_infra_and_models.py`
- 运行命令：`pytest tests/defense_tasks/test_m1_infra_and_models.py::test_task_1_integration_surface -q`
- 可执行性标记：✅ 可立即生成可执行测试

### 任务 2 — 防务数据模型

- 断言目标：`RawEvent`、`NormalizedEvent`、`CollectorResult` 可实例化；`SourceSpec` 可解析样本 YAML；`extra="allow"` 不因预留字段报错
- 测试文件路径：`tests/defense_tasks/test_m1_infra_and_models.py`
- 运行命令：`pytest tests/defense_tasks/test_m1_infra_and_models.py::test_task_2_defense_models_contract -q`
- 可执行性标记：✅ 可立即生成可执行测试

### 任务 3 — 域名级限速器

- 断言目标：同域请求串行；跨域并行；最小间隔满足 `min_interval`
- 测试文件路径：`tests/defense_tasks/test_m1_infra_and_models.py`
- 运行命令：`pytest tests/defense_tasks/test_m1_infra_and_models.py::test_task_3_rate_limiter_behaviour -q`
- 可执行性标记：✅ 可立即生成可执行测试

### 里程碑 1 检查点

- 断言目标：里程碑 1 完成后，基础枚举、配置、上下文和 RateLimiter 行为均可用
- 测试文件路径：`tests/defense_tasks/test_m1_infra_and_models.py`
- 运行命令：`pytest tests/defense_tasks/test_m1_infra_and_models.py::test_milestone_1_checkpoint -q`
- 可执行性标记：✅ 可立即生成可执行测试

## 里程碑 2：采集与加载

### 任务 4 — RSS 采集器

- 断言目标：RSSCollector 能解析 mock feed；304 返回 `not_modified`；negative cache 返回 `skipped`
- 测试文件路径：`tests/defense_tasks/test_m2_collection_and_loading.py`
- 运行命令：`pytest tests/defense_tasks/test_m2_collection_and_loading.py::test_task_4_rss_collector_contract -q`
- 可执行性标记：✅ 可立即生成可执行测试

### 任务 5 — 信源加载器 + 种子源配置

- 断言目标：只加载 `defense_*.yaml`；格式错误条目跳过；重复 `id` 拒绝；`sources/defense_news.yaml` 存在且可作为种子配置入口
- 测试文件路径：`tests/defense_tasks/test_m2_collection_and_loading.py`
- 运行命令：`pytest tests/defense_tasks/test_m2_collection_and_loading.py::test_task_5_source_loader_and_seed_config -q`
- 可执行性标记：✅ 可立即生成可执行测试

### 里程碑 2 检查点

- 断言目标：里程碑 2 完成后，YAML 可被加载，RSSCollector 可消费 mock feed
- 测试文件路径：`tests/defense_tasks/test_m2_collection_and_loading.py`
- 运行命令：`pytest tests/defense_tasks/test_m2_collection_and_loading.py::test_milestone_2_checkpoint -q`
- 可执行性标记：✅ 可立即生成可执行测试

## 里程碑 3：数据处理管线

### 任务 6 — 规范化器

- 断言目标：quality 分层正确；URL 规范化正确；HTML 清洗正确；`dedup_keys` 正确生成
- 测试文件路径：`tests/defense_tasks/test_m3_pipeline_core.py`
- 运行命令：`pytest tests/defense_tasks/test_m3_pipeline_core.py::test_task_6_normalizer_contract -q`
- 可执行性标记：✅ 可立即生成可执行测试

### 任务 7 — 双层去重器

- 断言目标：重复 `url_hash` / `content_hash` 被过滤；TTL 正确写入；批量接口可运行
- 测试文件路径：`tests/defense_tasks/test_m3_pipeline_core.py`
- 运行命令：`pytest tests/defense_tasks/test_m3_pipeline_core.py::test_task_7_deduper_contract -q`
- 可执行性标记：✅ 可立即生成可执行测试

### 任务 8 — 两阶段过滤打分器

- 断言目标：`title_blacklist` Stage 1 硬丢；`junk_patterns + whitelist` 共现保留；Stage 2 打分和 Top-K 排序正确
- 测试文件路径：`tests/defense_tasks/test_m3_pipeline_core.py`
- 运行命令：`pytest tests/defense_tasks/test_m3_pipeline_core.py::test_task_8_scorer_contract -q`
- 可执行性标记：✅ 可立即生成可执行测试

### 任务 9 — 事件转换器

- 断言目标：`NormalizedEvent -> Event` 映射正确；`Event.source=DEFENSE`；metadata 携带站点与去重信息
- 测试文件路径：`tests/defense_tasks/test_m3_pipeline_core.py`
- 运行命令：`pytest tests/defense_tasks/test_m3_pipeline_core.py::test_task_9_converter_contract -q`
- 可执行性标记：✅ 可立即生成可执行测试

### 里程碑 3 检查点

- 断言目标：mock `RawEvent -> normalize -> dedup -> score -> convert` 管线可通
- 测试文件路径：`tests/defense_tasks/test_m3_pipeline_core.py`
- 运行命令：`pytest tests/defense_tasks/test_m3_pipeline_core.py::test_milestone_3_checkpoint_pipeline -q`
- 可执行性标记：✅ 可立即生成可执行测试

## 里程碑 4：持久化与健康管理

### 任务 10 — PostgreSQL 持久化

- 断言目标：建表成功；append-only 允许重复 URL；`run_history` 正确落库
- 测试文件路径：`tests/defense_tasks/test_m4_storage_and_health.py`
- 运行命令：`pytest tests/defense_tasks/test_m4_storage_and_health.py::test_task_10_storage_contract_placeholder -q`
- 可执行性标记：⏳ 需等前置任务完成后才能编写
- 前置条件：任务 1、2 完成；`app/defense/storage.py` 实现完成；测试所需 PG fixture 或 fake pool 契约稳定
- 预期断言：`init_tables()` 建表；`insert_normalized_events()` append-only；`insert_run()` / `get_source_health()` 行为稳定

### 任务 11 — Source Health 管理器

- 断言目标：3 次失败进入 `cooling_down`；10 次失败进入 `pending_disable`；`is_available()` 触发 lazy recovery
- 测试文件路径：`tests/defense_tasks/test_m4_storage_and_health.py`
- 运行命令：`pytest tests/defense_tasks/test_m4_storage_and_health.py::test_task_11_source_health_manager_contract -q`
- 可执行性标记：✅ 可立即生成可执行测试

### 里程碑 4 检查点

- 断言目标：PG 读写和健康状态机可联动验证
- 测试文件路径：`tests/defense_tasks/test_m4_storage_and_health.py`
- 运行命令：`pytest tests/defense_tasks/test_m4_storage_and_health.py::test_milestone_4_checkpoint_placeholder -q`
- 可执行性标记：⏳ 需等前置任务完成后才能编写
- 前置条件：任务 10、11 完成
- 预期断言：`source_health` 与 `run_history` 在真实存储下联动正确

## 里程碑 5：规则编排与系统集成

### 任务 12 — 防务采集规则

- 断言目标：规则串联完整 pipeline；事件入池；高分事件产出 Alert；`run_history` 写入
- 测试文件路径：`tests/defense_tasks/test_m5_rule_and_delivery.py`
- 运行命令：`pytest tests/defense_tasks/test_m5_rule_and_delivery.py::test_task_12_rule_pipeline_placeholder -q`
- 可执行性标记：⏳ 需等前置任务完成后才能编写
- 前置条件：任务 4-11 完成；`app/rules/defense_rules.py` 实现完成
- 预期断言：mock 端到端执行后，pool/alerts/PG/logging 链路完整

### 任务 13 — `main.py` + `debug.py` 集成

- 断言目标：应用可在 PG 未配置时启动；`execute_rule` / debug 手动触发能传递 `app_state`；health/runs API 可访问
- 测试文件路径：`tests/defense_tasks/test_m5_rule_and_delivery.py`
- 运行命令：`pytest tests/defense_tasks/test_m5_rule_and_delivery.py::test_task_13_app_integration_placeholder -q`
- 可执行性标记：⏳ 需等前置任务完成后才能编写
- 前置条件：任务 1、10、11、12 完成；应用路由和 lifespan 修改完成
- 预期断言：启动、触发、API 查询三条链路一致

### 任务 14 — 飞书防务卡片

- 断言目标：单条 defense card 包含标题/信源/国家/链接/摘要；digest card 可列出多条 alert
- 测试文件路径：`tests/defense_tasks/test_m5_rule_and_delivery.py`
- 运行命令：`pytest tests/defense_tasks/test_m5_rule_and_delivery.py::test_task_14_feishu_defense_cards_contract -q`
- 可执行性标记：✅ 可立即生成可执行测试

### 里程碑 5 检查点

- 断言目标：通过 `/debug/trigger` 手动触发端到端 pipeline；事件入池且高分告警推送；API 返回正确
- 测试文件路径：`tests/defense_tasks/test_m5_rule_and_delivery.py`
- 运行命令：`pytest tests/defense_tasks/test_m5_rule_and_delivery.py::test_milestone_5_checkpoint_placeholder -q`
- 可执行性标记：⏳ 需等前置任务完成后才能编写
- 前置条件：任务 12、13、14 完成
- 预期断言：手动触发、scheduler、alerts API、debug API 结果一致
