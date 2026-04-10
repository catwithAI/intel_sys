# 防务资讯采集管线技术方案复审结论

## 1. 结论

**认可**。

当前 `design-draft.md` 已解决前两轮 review 提出的阻塞问题，设计具备进入验证方案阶段的条件。

## 2. 逐项复核结果

### 2.1 已验证通过

- **3.1 与 `defense-integration-plan.md` 不一致**：第 0 节已新增基线文档同步声明，明确 Phase 1 采用混合模式，并要求实现阶段同步更新 `docs/defense-integration-plan.md`。
- **3.2 种子源“已验证”不成立**：已改为“候选种子源附录”，并把 RSS 可达性验证明确为实现前置 gate，不再错误宣称已验证。
- **3.3 Negative cache 误记失败**：negative cache 命中已改为 `status="skipped"`，pipeline 中单独分支处理，不再进入 `record_failure`。
- **3.4 `title_blacklist` 未落地**：已明确 `title_blacklist` 属于 Stage 1 硬过滤，且无白名单豁免；同时与 `junk_patterns` 的语义边界已写清。

### 2.2 Minor 项已验证通过

- **cooling_down 自动恢复机制**：已明确在 `is_available()` 内部执行 lazy recovery。
- **入池表述修正**：已改为“所有通过 Stage 2 + Top-K 的事件入池”。
- **run_history.status 判定**：已明确 `ok / partial / error` 的判定逻辑，并在 pipeline 中落了对应分支。

## 3. 综合判断

本版设计在以下方面已经达到可实施状态：

- 阶段目标、主链路和状态机定义一致。
- 运行时依赖注入、PG append-only 持久化、source health 单一事实源方案清晰。
- SourceLoader、Negative cache、Stage 1/Stage 2 规则边界、候选种子源 gate 机制均已闭环。
- 主要 edge cases、兼容性风险和组件职责问题已收敛到可控范围。

## 4. 进入下一阶段前的执行约束

以下事项不再作为阻塞项，但应在实现阶段严格执行：

- 任务 1 中同步更新 `docs/defense-integration-plan.md`，使基线文档与本设计保持一致。
- M11 实现前先完成候选 seed RSS 可达性验证；验证失败的源按附录中的替换策略替换。

## 5. 复审结论

**认可**：设计可行，可以进入验证方案阶段。
