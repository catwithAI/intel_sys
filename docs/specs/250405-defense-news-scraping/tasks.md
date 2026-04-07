# 实施计划

## 里程碑 1：基础设施与数据模型（可独立验证）

- [ ] 1. 集成改造 — models.py + config.py + pyproject.toml
  - 在 `app/models.py` 添加 `SourceType.DEFENSE = "defense"`
  - 在 `app/config.py` 添加所有 defense 配置项 + PG 配置项 + defense 飞书 webhook
  - 在 `pyproject.toml` 添加 feedparser、asyncpg、pyyaml 依赖
  - 在 `app/engine/context.py` 添加 `app_state: Any = None` 字段
  - 同步更新 `docs/defense-integration-plan.md` Phase 1 范围为混合模式
  - _需求：8.1, 10_
  - _预期验证：SourceType.DEFENSE 可导入；config 字段有默认值；RuleContext 有 app_state_

- [ ] 2. 防务数据模型 — app/defense/models.py
  - 创建 `app/defense/__init__.py`
  - 创建 `app/defense/models.py`，包含 RawEvent、NormalizedEvent (dataclass)、SourceSpec 及子模型 (Pydantic)、CollectorResult (dataclass)
  - _需求：3, 1_
  - _预期验证：所有 dataclass/model 可实例化；SourceSpec 能解析 YAML 样本；extra="allow" 忽略未知字段_

- [ ] 3. 域名级限速器 — app/defense/rate_limiter.py
  - 实现 DomainRateLimiter 类：per-domain asyncio.Lock + monotonic time
  - _需求：2.4_
  - _预期验证：同域串行、跨域并行、间隔 ≥ min_interval_

🏁 **里程碑 1 检查点**：SourceType.DEFENSE 可导入；所有数据模型可实例化；RateLimiter 并发行为正确

## 里程碑 2：采集与加载（数据获取能力）

- [ ] 4. RSS 采集器 — app/defense/collectors/rss.py + registry.py
  - 创建 `app/defense/collectors/__init__.py`、`registry.py`
  - 实现 RSSCollector：httpx GET + feedparser 解析 + ETag/Last-Modified 缓存 + negative cache + Content-Length 限制
  - entry 缺字段降级处理（无 title 跳过、无 id 用 link hash、无 published 用 now）
  - _需求：2.1-2.6_
  - _预期验证：mock feed 能解析；304 返回 not_modified；超时/403 返回 error；negative cache 返回 skipped_

- [ ] 5. 信源加载器 + 种子源配置 — app/defense/source_loader.py + sources/defense_news.yaml
  - 实现 SourceLoader：只加载 defense_*.yaml、冲突检测、格式错误跳过
  - 验证候选种子源 RSS 可达性（curl 测试），不可达的替换为备选
  - 创建 sources/defense_news.yaml，包含 10 个已验证种子源
  - _需求：1.1-1.4_
  - _预期验证：加载正确数量的源；格式错误不崩溃；重复 id 被拒绝_

🏁 **里程碑 2 检查点**：SourceLoader 能加载 YAML；RSSCollector 能解析 mock feed；种子源 YAML 格式正确

## 里程碑 3：数据处理管线（核心业务逻辑）

- [ ] 6. 规范化器 — app/defense/normalizer.py
  - 实现 normalize() 函数：URL 规范化（保留 scheme、去追踪参数）、extraction_quality 分层、HTML 清洗、published_at 处理（未来时间修正）、dedup_keys 生成
  - _需求：3.1-3.6_
  - _预期验证：各 quality 级别正确；URL 追踪参数被去除；HTML 被清洗_

- [ ] 7. 双层去重器 — app/defense/deduper.py
  - 实现 Deduper：Redis pipeline 批量 L1 URL hash + L2 content hash 去重，TTL 可配置
  - _需求：4.1-4.4_
  - _预期验证：重复 url_hash 被过滤；重复 content_hash 被过滤；TTL 正确_

- [ ] 8. 两阶段过滤打分器 — app/defense/scorer.py
  - 实现 Scorer：Stage 1 硬过滤（quality < 0.4、title_blacklist 无豁免、junk_patterns 有白名单豁免）+ Stage 2 规则打分 + Top-K 截断
  - _需求：5.1-5.4_
  - _预期验证：blacklist 硬丢无豁免；junk+whitelist 共现保留；打分权重正确；Top-K 排序_

- [ ] 9. 事件转换器 — app/defense/converter.py
  - 实现 to_event()：NormalizedEvent → Event 字段映射
  - _需求：8.3_
  - _预期验证：Event.source=DEFENSE；data/metadata 字段映射正确_

🏁 **里程碑 3 检查点**：mock RawEvent → normalize → dedup → score → convert 完整管线可通

## 里程碑 4：持久化与健康管理

- [ ] 10. PostgreSQL 持久化 — app/defense/storage.py
  - 实现 DefenseStorage：init_tables()、insert_normalized_events() (append-only)、insert_run()、upsert_source_health()、get_source_health()
  - _需求：6.1-6.5_
  - _预期验证：建表成功；append-only 允许重复 URL；run_history 正确落库_

- [ ] 11. Source Health 管理器 — app/defense/health.py
  - 实现 SourceHealthManager：refresh_cache()、is_available()（含 lazy recovery）、record_success()、record_failure()
  - 状态机：ok → cooling_down → pending_disable
  - _需求：2.5, 9.1_
  - _预期验证：3 次失败进入 cooling_down；10 次失败进入 pending_disable；冷却期过后 lazy recovery_

🏁 **里程碑 4 检查点**：PG 读写正确；健康状态机转换正确；lazy recovery 生效

## 里程碑 5：规则编排与系统集成

- [ ] 12. 防务采集规则 — app/rules/defense_rules.py
  - 实现 ingest_defense_news 规则函数，串联完整 pipeline
  - 通过 ctx.app_state 获取 pg_pool 和 defense_delivery
  - 所有通过 Top-K 的事件写入 EventMemoryPool
  - pre_score ≥ 阈值的事件产出 Alert + Redis 存储
  - 结构化日志 + run_history 写入
  - _需求：8.2, 8.3, 9.2_
  - _预期验证：mock 端到端执行；事件入池；高分事件产出 Alert；run_history 落库_

- [ ] 13. main.py + debug.py 集成
  - lifespan 中初始化 PG 连接池（pg_dsn 为空时跳过）+ defense delivery + 建表
  - execute_rule 传入 app_state
  - debug.py 手动触发同步传入 app_state
  - 新增 /debug/defense/health 和 /debug/defense/runs 端点
  - _需求：6.1, 7.4, 8.4, 8.5, 9.1, 9.3_
  - _预期验证：PG 未配置时启动不崩溃；手动触发行为一致；health/runs API 返回正确_

- [ ] 14. 飞书防务卡片 — app/delivery/feishu.py
  - 新增 _format_defense_card() 和 _format_defense_digest_card()
  - _format_alert() 添加 DEFENSE 分支
  - send_batch() 添加 DEFENSE 路由
  - _需求：7.1-7.4_
  - _预期验证：单条卡片格式含标题/信源/国家/链接/摘要；digest 卡片正确_

🏁 **里程碑 5 检查点**：端到端 pipeline 可通过 /debug/trigger 手动触发；事件入池+高分告警推飞书；API 端点返回正确数据
