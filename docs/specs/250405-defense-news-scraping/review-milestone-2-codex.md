# 里程碑 2 审查结论（Codex）

## 结论

**不通过**

## 必须修改的问题

### 1. `RSSCollector` 的 ETag / Last-Modified / negative cache 以实例内字典保存，跨 run 实际失效

- 位置：`app/defense/collectors/rss.py:21-26`
- 对照：`docs/specs/250405-defense-news-scraping/design.md:359-365`, `docs/specs/250405-defense-news-scraping/design.md:390-412`
- 问题：
  - `_etag_cache`、`_last_modified_cache`、`_negative_cache` 都挂在 collector 实例上。
  - 设计中的规则编排是每次执行时新建 `RSSCollector`，因此这些缓存不会跨 run 保留。
  - `negative cache` 设计要求存 Redis；当前实现只是进程内临时字典。
- 影响：
  - 条件请求无法在下一次调度命中，ETag / Last-Modified 形同未实现。
  - negative cache 无法抑制跨 run 的失败风暴，和设计目标不符。

### 2. 缺少 `Content-Length` 限制，未满足设计的 feed 安全边界

- 位置：`app/defense/collectors/rss.py:58-101`
- 对照：`docs/specs/250405-defense-news-scraping/design.md:369-375`
- 问题：
  - 当前直接 `feedparser.parse(resp.text)`，没有在读取或解析前检查 `Content-Length <= 5MB`。
  - 也没有对超大响应体做主动拒绝。
- 影响：
  - 大响应或异常响应会直接进入内存和解析器，存在资源消耗和稳定性风险。
  - 这是设计明确要求的安全措施，当前未落地。

### 3. RSS entry 降级处理与设计不一致，存在错误 source_id 和缺失发布时间

- 位置：`app/defense/collectors/rss.py:103-128`
- 对照：`docs/specs/250405-defense-news-scraping/design.md:377-385`
- 问题：
  - `id/guid` 缺失时，设计要求使用 `link` 的 hash；当前直接使用原始 `link`，未做 hash。
  - `link` 缺失时，设计要求使用 `id` 或跳过该 entry；当前在 `id` 和 `link` 都缺失时退化成 `md5(title)`，会把本应跳过的数据继续纳入。
  - `published` 缺失时，设计要求使用当前 UTC 时间；当前保留为 `None`。
- 影响：
  - `source_id` 稳定性和去重语义偏离设计。
  - 无链接 feed 项会混入不可靠事件，增加后续误判和重复风险。

### 4. `RSSCollector` 没有隔离 `feedparser.parse()` 异常

- 位置：`app/defense/collectors/rss.py:101`
- 对照：`docs/specs/250405-defense-news-scraping/design.md:369-375`
- 问题：
  - 设计要求 `try/except` 隔离解析异常，单源失败不影响其他。
  - 当前 `feedparser.parse(resp.text)` 没有解析异常保护。
- 影响：
  - 单个异常 feed 会直接把 collector 调用抛出到上层，而不是规范地返回 `CollectorResult(status="error")`。

### 5. `RSSCollector` 没有使用可配置的域名限速参数

- 位置：`app/defense/collectors/rss.py:50`
- 对照：`docs/specs/250405-defense-news-scraping/design.md:597-600`, `docs/specs/250405-defense-news-scraping/design.md:792-799`
- 问题：
  - 当前硬编码 `await self._limiter.wait_if_needed(domain, 10.0)`。
  - 设计里的限速间隔来自配置，不应在 collector 内写死。
- 影响：
  - collector 与配置层断裂，后续无法按环境调整抓取节奏。

### 6. `SourceLoader` 漏掉 `access.allow_fetch` 过滤

- 位置：`app/defense/source_loader.py:45-53`
- 对照：`docs/specs/250405-defense-news-scraping/design.md:324-337`
- 问题：
  - 当前只过滤 `spec.enabled`，没有过滤 `spec.access.allow_fetch`。
- 影响：
  - 被显式标记为不可抓取的源仍可能进入调度，违反设计约束。

## 通过项

- `SourceLoader` 已实现 `defense_*.yaml` 过滤和重复 `id` 拒绝：`app/defense/source_loader.py:23-23`, `app/defense/source_loader.py:48-52`
- `sources/defense_news.yaml` 语法和模型格式可被当前 `SourceLoader` 正常解析，当前加载结果为 10 个源。

## 复审结论

**不通过**

- 已修复：
  - `app/defense/collectors/rss.py` 已补上 `Content-Length` 检查、解析异常保护、entry 降级修正、可配置 `min_interval`
  - `app/defense/source_loader.py` 已补上 `access.allow_fetch` 过滤
- 仍未闭合：
  - `negative cache` 仍然是 `RSSCollector` 实例内 `_negative_cache` 字典：`app/defense/collectors/rss.py:25-33`
  - 设计仍明确要求 `Negative cache 存 Redis`：`docs/specs/250405-defense-news-scraping/design.md:390-412`

## 二次复审结论

**不通过**

- `RSSCollector` 已支持 Redis negative cache：`app/defense/collectors/rss.py:41-53`
- 仍有未闭合问题：
  - `Content-Length` 超限和实际 body 超限这两个 `error` 分支没有写入 negative cache：`app/defense/collectors/rss.py:125-141`
  - 当前只有请求异常、HTTP 4xx/5xx、parse 异常会调用 `_set_negative_cache_async()`：`app/defense/collectors/rss.py:87`, `app/defense/collectors/rss.py:114`, `app/defense/collectors/rss.py:147`

## 三次复审结论

**通过**

- `Content-Length` 超限和实际 body 超限分支已写入 negative cache：`app/defense/collectors/rss.py:125-141`
