# 里程碑 3 审查结论（Codex）

## 结论

**不通过**

## 必须修改的问题

### 1. `normalizer.py` 的 URL 规范化未按设计完成

- 位置：`app/defense/normalizer.py:19-29`
- 对照：`docs/specs/250405-defense-news-scraping/design.md:419-427`
- 问题：
  - 没有去除尾部 `/`
  - 没有移除 `source` 查询参数
  - 没有过滤非 `http/https` 协议并返回 `None`
  - 没有处理相对路径场景
- 影响：
  - `canonical_url` 不稳定，L1 URL 去重会漏判
  - 非 HTTP 链接可能进入后续管线

### 2. `normalizer.py` 的 `extraction_quality` 分层逻辑与设计不符

- 位置：`app/defense/normalizer.py:32-42`
- 对照：`docs/specs/250405-defense-news-scraping/design.md:432-438`
- 问题：
  - 当前按 `url/published/body` 是否存在打分
  - 设计要求按 `title + body/summary` 的长度分层：`>=200 -> 1.0`、`50-200 -> 0.7`、仅标题或 `<50 -> 0.4`
- 影响：
  - Stage 1 的质量门槛会误判，后续打分结果不可信

### 3. `normalizer.py` 的 `published_at` 处理存在边界错误

- 位置：`app/defense/normalizer.py:53-56`
- 对照：`docs/specs/250405-defense-news-scraping/design.md:444-449`
- 问题：
  - 当前只要时间大于 `now` 就回写为当前时间，设计要求是 `> now + 1h`
  - 没有处理无时区时间，若传入 naive `datetime`，与 aware `datetime` 比较会抛异常
- 影响：
  - 合法的轻微未来时间会被错误修正
  - naive 时间输入会直接触发运行时错误

### 4. `deduper.py` 的 L1/L2 判定逻辑错误，会放过重复事件

- 位置：`app/defense/deduper.py:20-37`
- 对照：`docs/specs/250405-defense-news-scraping/tasks.md:41-43`, `docs/specs/250405-defense-news-scraping/design.md:9-14`
- 问题：
  - 当前判定是 `if url_created or content_created: unique.append(event)`
  - 设计要求是 URL hash 重复要过滤、content hash 重复也要过滤；任一层命中重复都不应放行
- 影响：
  - 同 URL / 新内容哈希，或同内容 / 新 URL 的重复事件都会被错误保留

### 5. `deduper.py` 对空 `url_hash` 直接写固定 Redis key，造成错误去重和热点 key

- 位置：`app/defense/deduper.py:21-27`
- 对照：`docs/specs/250405-defense-news-scraping/design.md:514-516`
- 问题：
  - 当 `url_hash` 为空时，仍会写入 `defense:dedup:url:`
- 影响：
  - 所有无 URL 事件会共享同一个 L1 key，产生误去重
  - 还会制造单一热点 key，带来不必要的 Redis 压力

### 6. `scorer.py` 的 Stage 2 评分公式与设计不匹配

- 位置：`app/defense/scorer.py:42-55`
- 对照：`docs/specs/250405-defense-news-scraping/design.md:470-485`
- 问题：
  - 当前没有实现 `title_whitelist +0.3`
  - 当前没有实现 `junk_patterns -0.4`
  - 当前把 `source_weight` 和 `extraction_quality` 都按 `0.4` 权重计入，设计要求分别是 `0.2` 和 `0.1`
  - 当前 `authority_tier` 只实现了 `1/2/3` 的线性近似，未覆盖设计要求的 `1/2/3/4 -> +0.2/+0.1/0/-0.1`
  - 额外加了固定 `+0.1`，设计中不存在
- 影响：
  - `pre_score` 与设计基线脱节，Top-K 和告警阈值都会失真

### 7. `converter.py` 与记忆池消费接口不匹配，正文在入池时会丢失

- 位置：`app/defense/converter.py:10-29`
- 对照：
  - `docs/specs/250405-defense-news-scraping/tasks.md:46-48`
  - `app/memory/pool.py:62-69`
- 问题：
  - 当前转换器把正文写到 `event.data["body"]`
  - 记忆池压缩读取的是 `event.data["content"]` / `selftext`
- 影响：
  - defense 事件进入 `EventMemoryPool` 后，LLM 压缩基本只看到标题，看不到正文
  - 这是明确的跨任务集成断裂

## 通过项

- `scorer.py` 的 Stage 1 黑名单/垃圾词/白名单豁免顺序基本符合设计
- `deduper.py` 已使用 Redis pipeline 和可配置 TTL
- `converter.py` 已正确设置 `Event.source = SourceType.DEFENSE`

## 复审结论

**通过**

- `normalizer.py` 已补齐 URL 规范化、quality 分层、HTML 清洗、future/naive `published_at` 处理和 `dedup_keys`
- `deduper.py` 已按 L1/L2 双层去重语义实现 AND 判定，并跳过空 hash
- `scorer.py` 已对齐 Stage 1 / Stage 2 设计公式与 Top-K
- `converter.py` 已与 `EventMemoryPool` 的 `content` 字段消费接口对齐
