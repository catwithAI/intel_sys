---
name: dev
description: "Claude + Codex 平等协作工作流。严格 6 阶段：需求访谈 → 双方需求文档+交叉Review → 技术设计+双向验证 → 任务拆分+验证生成 → 逐任务开发+里程碑联合Review → 最终验证+PR。手动触发：/dev。"
user-invocable: true
hooks:
  PreToolUse:
    - matcher: "Write|Edit"
      hooks:
        - type: command
          command: "${CLAUDE_SKILL_DIR}/guard-source-code.sh"
  Stop:
    - hooks:
        - type: command
          command: "echo '⚠️ 结束前检查：1) 你是否已经自己跑完所有能自动化的测试（lint/build/typecheck/单元测试）？如果没有，现在立即执行。2) 端到端页面测试：如果有 browser automation 工具（如 claude-in-chrome），用它自己完成页面级验证，不要留给用户手动测试。3) 过程中产生的 requirement、design 的md文档也需要加入到commit中，且与真实代码保证一致'"
---

# Dev — Claude + Codex 平等协作工作流

通过平等的双模型协作实现交叉验证：Claude 和 Codex 都同时承担架构师和开发者角色。在需求阶段，双方各自独立生成需求文档并互相 review；在设计阶段，Claude 主导设计、Codex 验证可行性，然后 Codex 主导验证方案、Claude 验证完整性。两个不同模型的交叉签名，比单一模型自己写自己审更可靠。

## 核心原则

- **⛔ 源代码写入权限规则**：阶段 0-3 中，你没有直接修改源代码的权限，所有源代码操作必须通过 Codex MCP 工具执行（PreToolUse hook 会拦截）。**阶段 4（逐任务开发）中，Claude 作为开发者直接编写和修改源代码**，Codex 转为 review 角色。阶段 5 中，Claude 可直接修复验证失败的代码。在文档层面（`docs/specs/` 下），Claude 始终有写入权限
- **你可以写的文件**：仅限 `docs/specs/YYMMDD-{feature-name}/` 目录下的所有 `.md` 文件。完整清单见「文档路径约定」章节
- **平等协作的价值**：两个不同模型以平等的身份交叉检查，能发现单一模型的盲区。需求文档双方各自生成并互相 review；技术设计由一方主导、另一方验证；验证方案由另一方主导、第一方验证。这种交叉签名机制确保双方都对产出负责
- **Codex 通过 MCP 工具调用**：所有 Codex 调用使用 `mcp__codex__codex` MCP 工具执行，Codex 会同步返回结果。如需在同一会话中追加指令，使用 `mcp__codex__codex-reply` 传入 threadId 继续对话
- **阶段不可跳步**：必须按顺序执行，每个阶段完成后输出分隔线
- **文档语言**：所有 spec 文档（需求、设计、任务）必须用中文撰写
- **工具强制**：阶段 0 的提问必须通过 `AskUserQuestion` 工具发出，禁止用普通文本对话提问。因为 `AskUserQuestion` 会阻塞等待用户回答，防止 Claude 跳过等待自行继续
- **文件强制**：每个阶段产出的文档必须先用 Write 工具写入文件，再在对话中展示摘要。禁止只在对话中输出文档内容而不写文件——对话内容会随上下文压缩丢失，文件才是持久化的交付物。**例外**：阶段 2 的技术设计初稿在进入 Codex review 迭代前，先写入 `design-draft.md`（每轮迭代覆盖更新），待最终认可后再写入正式 `design.md`
- **自动打开文档**：每次写入 requirements.md 后，必须立即用编辑器打开，方便用户确认。design.md 和 tasks.md 仅在用户明确要求查看时才打开。优先使用 `zed`，fallback 到 `code`
- **Codex 降级**：如果 Codex MCP 工具调用失败（额度不足、超时、服务不可用等），按以下规则降级，不中断流程：
  - **阶段 1 降级**：Claude 独自生成需求文档（不再分 claude/codex 两份），直接写入 requirements.md，跳过交叉 review 流程，仅保留用户确认环节
  - **阶段 2 降级**：Claude 独自完成技术设计并写入 design.md，独自生成验证方案并写入 verification-plan.md，跳过迭代认可流程。向用户说明"Codex 不可用，设计和验证方案均由 Claude 单方完成，建议用户加强人工审查"
  - **阶段 3 降级**：Claude 独自生成任务拆分和验证用例，跳过 Codex 生成和交叉 review
  - **阶段 4 降级**：Claude 独自完成开发和自验，跳过 Codex review 环节；里程碑检查点仅由 Claude 单方 review
  - **阶段 5 降级**：Claude 自行完成最终验证和 PR，产出物不变
  - **降级时**：中间产物文件（requirements-claude.md 等）不生成，只产出最终产物

## 自动打开文档

写入 requirements.md 后立即用编辑器打开（因为需要用户确认）。design.md 和 tasks.md 仅在用户明确要求查看时才打开。

```bash
if command -v zed &>/dev/null; then
    zed "{文档绝对路径}"
elif command -v code &>/dev/null; then
    code "{文档绝对路径}"
fi
```

## Codex MCP 调用规范

所有 Codex 调用统一通过 MCP 工具执行：

### 启动 Codex 任务

使用 `mcp__codex__codex` 工具，参数说明：
- `prompt`：任务描述，末尾始终追加：`只输出代码、diff 或测试结果。不要解释、不要总结、不要废话。`
- `cwd`：当前项目的绝对路径
- `sandbox`：`danger-full-access`（编码和审查任务均需要写文件）
- `approval-policy`：`never`

### 继续 Codex 对话

如果需要在同一 Codex 会话中追加指令（如修正上一步的输出），使用 `mcp__codex__codex-reply` 工具，传入上次返回的 `threadId` 和新的 `prompt`。

### 获取结果

MCP 工具调用是同步的，Codex 完成后直接返回结果：
- **编码任务**：Codex 直接修改文件，Claude 通过 `git diff` 检查产出
- **Review 任务**：在 prompt 中要求 Codex 将审查结论写入指定文件（如 `docs/specs/YYMMDD-{feature-name}/review.md`），Claude 读取该文件获取结果

## 文档路径约定

所有文档统一存放在 `docs/specs/YYMMDD-{feature-name}/` 目录下：

**最终产物**（进入后续阶段和 commit 的文件）：
- `requirements.md` — 合并后的需求文档（EARS 方法论）
- `design.md` — 最终技术设计文档
- `verification-plan.md` — 验证方案
- `tasks.md` — 实施计划（含里程碑）
- `task-tests.md` — 每个任务和里程碑的验证规格（断言目标、测试文件路径、运行命令、可执行性标记）

**中间产物**（审计记录，保留但不进入后续阶段）：
- `requirements-claude.md` — Claude 版需求文档
- `requirements-codex.md` — Codex 版需求文档
- `review-req-claude.md` — Claude 对 Codex 需求文档的 review
- `review-req-codex.md` — Codex 对 Claude 需求文档的 review
- `review-req-final.md` — Codex 对合并需求文档的 final sign-off
- `design-draft.md` — 技术设计草稿（迭代过程中覆盖更新）
- `review-design.md` — Codex 对技术设计的 review
- `review-verification.md` — Claude 对验证方案的 review
- `review-task-tests.md` — Claude 对任务验证用例的 review
- `review-task-N.md` — Codex 对任务 N 代码的 review（N 为任务编号）
- `review-milestone-N-codex.md` — Codex 对里程碑 N 的 review

其中 `YYMMDD` 为当天日期，`{feature-name}` 由 Claude 根据需求内容生成简短的英文 kebab-case 名称（如 `user-auth`、`billing-dashboard`）。后续阶段统一使用同一个目录路径。

## 工作流

### 阶段 0：需求访谈

用户输入 `/dev` + 一段需求描述后，不要急着动手。用户的描述往往不完整或有隐含假设，你需要先快速对齐。

**做法：**
1. 阅读用户的需求描述
2. 使用 `AskUserQuestion` 工具提出 3-5 个精准问题（一次性提问，不要分多次），覆盖以下维度（按需选择，不必每个都问）：
   - **场景**：这个功能在什么场景下被使用？谁是主要用户？
   - **痛点**：现在没有这个功能，用户是怎么绕过的？最痛的点是什么？
   - **使用方式**：用户会怎么触发/使用这个功能？期望的交互流程是什么？
   - **边界**：这个需求的边界在哪？哪些是明确不做的？
   - **依赖**：是否依赖其他系统、API 或数据？
   - **验收**：你怎么判断这个做完了、做对了？
3. 等待用户回答后再进入阶段 1

问题要具体，不要问泛泛的"还有什么要补充的吗"。根据需求的复杂度调整问题数量——简单的 bug fix 可能只需 1-2 个问题，复杂功能需要更多。使用 `AskUserQuestion` 而不是普通文本输出，这样用户可以在工具的交互界面中快速回答。

```
--- 阶段 0 完成：需求已对齐 ---
```

### 阶段 1：需求文档（EARS 方法论）

基于阶段 0 的访谈结果，Claude 和 Codex 各自独立生成需求文档，然后交叉 review，最终合并为统一版本。

**文档格式**（EARS 方法论）：

```markdown
# 需求文档

## 简介

需求的简要描述

## 需求

### 需求 1 - 需求名称

**用户故事：** 作为 [用户类型]，我想要 [目标]，以便 [好处]

#### 验收标准

1. 当 <可选前提条件> 时，如果 <可选触发条件>，那么 <系统名称> 应该 <系统响应>
2. ...
```

**步骤：**

1. **Claude 生成需求文档**：Claude 基于访谈结果，按 EARS 格式撰写需求文档，写入 `docs/specs/YYMMDD-{feature-name}/requirements-claude.md`

2. **Codex 生成需求文档**：通过 Codex MCP 工具，让 Codex 独立生成需求文档。prompt：
```
基于以下访谈结果，使用 EARS 方法论生成需求文档。

{访谈结果的完整内容}

要求：
1. 按照用户故事 + 验收标准格式
2. 覆盖所有功能点和边界条件
3. 标注你认为可能有歧义或需要澄清的地方

将需求文档写入 docs/specs/YYMMDD-{feature-name}/requirements-codex.md。
只输出代码、diff 或测试结果。不要解释、不要总结、不要废话。
```

3. **Claude review Codex 版本**：Claude 读取 `requirements-codex.md`，与自己生成的 `requirements-claude.md` 对比，检查：
   - 是否遗漏了用户提到的关键需求
   - 验收标准是否可测试、是否完整
   - 是否有理解偏差
   - 两份文档的差异点
   - 将 review 意见写入 `docs/specs/YYMMDD-{feature-name}/review-req-claude.md`

4. **Codex review Claude 版本**：通过 Codex MCP 工具，让 Codex review Claude 的需求文档。prompt：
```
读取以下两份需求文档：
- docs/specs/YYMMDD-{feature-name}/requirements-claude.md（Claude 版）
- docs/specs/YYMMDD-{feature-name}/requirements-codex.md（你的版本）

检查：
1. 是否遗漏了关键需求或边界条件
2. 验收标准是否可测试、是否完整
3. 是否有理解偏差或歧义
4. 与你之前生成的版本有何不同

将 review 意见写入 docs/specs/YYMMDD-{feature-name}/review-req-codex.md。
只输出代码、diff 或测试结果。不要解释、不要总结、不要废话。
```

5. **合并统一版本**：Claude 对比两份需求文档（requirements-claude.md、requirements-codex.md）和两份 review 意见（review-req-claude.md、review-req-codex.md），合并为一份最终版本：
   - 双方一致的内容直接合并
   - 仅一方提到的有价值内容纳入并标注来源
   - **冲突或不确定的地方**：使用 `AskUserQuestion` 向用户征求意见，列出冲突点和双方观点
   - 合并版本写入 `docs/specs/YYMMDD-{feature-name}/requirements.md`

6. **Codex final sign-off**：通过 Codex MCP 工具，让 Codex 对合并后的需求文档做最终签字。prompt：
```
读取 docs/specs/YYMMDD-{feature-name}/requirements.md（合并后的最终需求文档）。

审查该文档是否完整、准确地反映了双方的意见。明确表态：
- "认可"：需求文档可以定稿
- "不认可"：列出必须修改的阻塞性问题

将结论写入 docs/specs/YYMMDD-{feature-name}/review-req-final.md。
只输出代码、diff 或测试结果。不要解释、不要总结、不要废话。
```

   如果 Codex 不认可：Claude 读取阻塞性问题，修改 requirements.md 后重新提交 sign-off。经过 3 轮仍未达成共识，升级到用户仲裁。

7. **用户确认**：向用户展示最终需求文档，询问确认：
   > "请检查以上需求文档（已合并 Claude 和 Codex 的观点），确认无误后我将进入技术设计阶段。如有修改意见请直接告诉我。
   >
   > 另外，等我写完技术设计后，你需要再确认一次，还是我直接开干？"

用户确认后，**自动打开文档**（参见「自动打开文档」）。

```
--- 阶段 1 完成：需求已确认，已写入 docs/specs/YYMMDD-{feature-name}/requirements.md ---
```

### 阶段 2：技术设计 + 双向验证

基于确认的需求文档，Claude 撰写技术设计，经过 Claude ↔ Codex 双向迭代验证后才最终定稿。

**设计文档内容包括**：
- 架构概览
- 技术栈与选型
- 需要修改的文件清单
- 数据库 / API 设计（如适用）
- 测试策略
- 安全考虑
- 适当使用 Mermaid 图表

**步骤：**

**第一步：Claude 撰写技术设计**

Claude 基于需求文档撰写技术设计，写入 `docs/specs/YYMMDD-{feature-name}/design-draft.md`（迭代过程中覆盖更新此文件）。

根据用户在阶段 1 的选择：
- **用户选了"需要确认"**：展示设计并等待确认，有修改就改完再确认
- **用户选了"直接开干"**：展示设计后直接进入 Codex review

**第二步：Codex review 设计（迭代直到认可）**

通过 Codex MCP 工具启动审查，prompt：
```
读取以下文档：
- docs/specs/YYMMDD-{feature-name}/requirements.md（需求文档）
- docs/specs/YYMMDD-{feature-name}/design-draft.md（技术设计草稿）

以架构师视角审查该技术方案，找出：
1. 遗漏的 edge cases
2. 架构设计问题（组件职责、耦合度、扩展性）
3. 潜在的兼容性风险
4. 技术选型的合理性
5. 与需求文档的对齐情况（是否有需求未被设计覆盖）

审查结论中必须明确表态：
- "认可"：设计可行，可以进入验证方案阶段
- "不认可"：列出必须修改的问题，修改后需重新审查

将审查结论写入 docs/specs/YYMMDD-{feature-name}/review-design.md。
只输出代码、diff 或测试结果。不要解释、不要总结、不要废话。
```

如果 Codex 不认可：
1. Claude 读取 review-design.md
2. Claude 基于 review 修改技术设计
3. 再次提交 Codex review（使用 codex-reply 继续同一会话）
4. 重复此循环直到 Codex 认可

**如果 Claude 与 Codex 意见持续不一致**（经过 3 轮迭代仍未达成共识）：
- 使用 `AskUserQuestion` 向用户展示双方观点和分歧点
- 由用户做最终裁决

**第三步：Codex 生成验证方案**

Codex 认可设计后，继续在同一会话中（使用 codex-reply），要求 Codex 生成验证方案：

```
你已经认可了技术设计。现在基于该设计和需求文档，生成验证方案，包括：
1. 单元测试计划：每个测试的目标、输入、预期输出
2. 集成测试计划：端到端场景和预期结果
3. 手动验证清单：需要人工确认的项目
4. 性能/安全验证（如适用）

将验证方案写入 docs/specs/YYMMDD-{feature-name}/verification-plan.md。
只输出代码、diff 或测试结果。不要解释、不要总结、不要废话。
```

**第四步：Claude review 验证方案（迭代直到认可）**

Claude 读取 verification-plan.md，以产品视角审查：
- 测试是否覆盖了所有验收标准
- 手动验证清单是否完整
- 测试优先级是否合理
- 是否有遗漏的关键场景

Claude 将 review 意见写入 `docs/specs/YYMMDD-{feature-name}/review-verification.md`（每轮覆盖更新）。

如果 Claude 不认可验证方案：
1. 将 review-verification.md 中的具体修改意见通过 Codex MCP 工具反馈给 Codex
2. Codex 修改验证方案（覆盖更新 verification-plan.md）
3. Claude 重新审查并更新 review-verification.md
4. 重复此循环直到 Claude 认可

**如果 Claude 与 Codex 意见持续不一致**（经过 3 轮迭代仍未达成共识）：
- 使用 `AskUserQuestion` 向用户展示双方观点和分歧点
- 由用户做最终裁决

**第五步：写入最终文档**

Claude 认可验证方案后：
1. 将技术设计（含验证方案摘要）写入 `docs/specs/YYMMDD-{feature-name}/design.md`
2. 确认 verification-plan.md 已就绪
3. 中间文件（requirements-claude.md、requirements-codex.md、review-req-claude.md、review-req-codex.md、review-req-final.md、design-draft.md、review-design.md、review-verification.md）保留作为审计记录

```
--- 阶段 2 完成：技术设计已通过双向验证，已写入 docs/specs/YYMMDD-{feature-name}/design.md ---
```

### 阶段 3：任务拆分 + 验证生成

用户已在阶段 1 确认过需求，此处**不需要再次确认**，直接开始。

**第一步：Claude 生成任务拆分**

基于 requirements.md、design.md 和 verification-plan.md，生成实施计划并写入 `docs/specs/YYMMDD-{feature-name}/tasks.md`。

**拆分原则**：
- **粒度尽可能小**：每个任务的完成难度应尽可能低，一个任务对应一个可独立验证的功能单元
- **可验证性**：每个任务完成后必须能通过具体的断言验证，而非"看起来对了"
- **里程碑驱动**：在任务序列中根据难度和代码依赖关系设置里程碑（如"核心业务逻辑完成"、"上下游接入前"、"集成完成"），里程碑是一组任务完成后的质量检查点

**文档格式：**

```markdown
# 实施计划

## 里程碑 1：{里程碑名称}（如：核心业务逻辑完成）

- [ ] 1. 任务描述
  - 具体子步骤 1
  - 具体子步骤 2
  - _需求：相关需求编号_
  - _预期验证：完成后应通过什么断言_

- [ ] 2. 另一个任务
  - 详细信息
  - _需求：相关需求编号_
  - _预期验证：完成后应通过什么断言_

🏁 **里程碑 1 检查点**：{通过条件描述}

## 里程碑 2：{里程碑名称}

- [ ] 3. ...

🏁 **里程碑 2 检查点**：{通过条件描述}
```

**第二步：Codex 生成任务和里程碑的验证方案**

通过 Codex MCP 工具，让 Codex 为每个任务和里程碑生成**断言级验证方案**。prompt：
```
读取以下文档：
- docs/specs/YYMMDD-{feature-name}/requirements.md（需求）
- docs/specs/YYMMDD-{feature-name}/design.md（技术设计）
- docs/specs/YYMMDD-{feature-name}/verification-plan.md（验证方案）
- docs/specs/YYMMDD-{feature-name}/tasks.md（实施计划）

为 tasks.md 中的每个任务和每个里程碑检查点生成验证方案，包含两部分：

**Part A: 验证规格**（写入 docs/specs/YYMMDD-{feature-name}/task-tests.md）
每个任务和里程碑必须包含：
- 断言目标：验证什么行为
- 测试文件路径：测试代码应落在项目中的哪个文件
- 运行命令：如何执行这个验证（如 pytest tests/test_xxx.py::test_task_1）
- 可执行性标记：
  - ✅ 可立即生成可执行测试（函数签名、依赖已明确）
  - ⏳ 需等前置任务完成后才能编写（记录前置条件和预期断言）

**Part B: 可执行测试文件**
对所有标记为 ✅ 的任务，直接在项目中创建/修改对应的测试文件，写入可执行的测试代码（含 assert）。
对标记为 ⏳ 的任务，在测试文件中写入 @pytest.mark.skip(reason="待任务 X 完成后启用") 的占位测试。

只输出代码、diff 或测试结果。不要解释、不要总结、不要废话。
```

**第三步：Claude review 验证用例（迭代直到认可）**

Claude 同时审查 **task-tests.md（验证规格）** 和 **Codex 实际创建/修改的测试文件（`git diff` 中的测试代码）**，检查：
- 每个任务是否都有对应的验证用例
- 断言是否能真正验证任务的完成（而非形式化的空断言）
- 里程碑验证是否覆盖了跨任务的集成场景
- **task-tests.md 中的规格与实际测试文件是否一致**（路径、导入、断言逻辑）
- **实际测试文件是否可执行**：Claude 运行所有标记为 ✅ 的测试命令，确认全部通过（✅ 语义上等于"可执行且应通过"，不接受 skip）
- 将 review 意见写入 `docs/specs/YYMMDD-{feature-name}/review-task-tests.md`

如果 Claude 不认可：
1. 将 review-task-tests.md 中的意见通过 Codex MCP 工具反馈给 Codex
2. Codex 修改验证用例
3. 重复此循环直到 Claude 认可

**如果经过 3 轮迭代仍未达成共识**，使用 `AskUserQuestion` 向用户展示分歧点，由用户裁决。

```
--- 阶段 3 完成：任务拆分和验证用例已就绪 ---
```

### 阶段 4：逐任务开发

按 tasks.md 中的任务顺序，**逐个任务**完成开发。每个任务经过 Claude 开发 → 自验 → Codex review → 修改收敛的完整循环。遇到里程碑时触发双方联合 review。

**⚠️ 禁止跳任务、禁止一次性实现多个任务。** 严格按顺序逐个推进。

**里程碑边界记录**：在进入每个里程碑的第一个任务前，记录当前 HEAD commit hash 为 `milestone_start_hash`。里程碑 review 时基于 `git diff {milestone_start_hash}..HEAD` 生成该里程碑范围内的完整 diff。

**单任务开发循环：**

对于 tasks.md 中的每个任务 N，执行以下循环：

1. **Claude 实现任务**：
   - Claude 基于 requirements.md、design.md 和 tasks.md 完成当前任务的代码编写
   - 如果 task-tests.md 中该任务标记为 ⏳（前置条件已满足），Claude 先将占位测试替换为可执行测试代码，再编写业务代码
   - **同步回写 task-tests.md**：将该任务的标记从 ⏳ 更新为 ✅，补齐实际的测试文件路径、运行命令、断言描述。确保 task-tests.md 始终反映真实的测试资产状态
   - **记录 task diff 起点**：前一个任务的 task commit 确保工作区干净。记录当前 HEAD commit hash 为 `task_start_hash`，后续 review 基于 `git diff {task_start_hash}..HEAD` 生成精确 diff

2. **Claude 自验**：运行 task-tests.md 中该任务指定的运行命令（如 `pytest tests/test_xxx.py::test_task_N`），确保断言全部通过。如果失败，Claude 自行修复代码直到验证通过

3. **Codex review**：通过 Codex MCP 工具，让 Codex review **仅当前任务的变更**（通过 `git diff {task_start_hash}..HEAD` 隔离当前任务的修改范围）。prompt：
```
审查以下 diff（即任务 N: {任务描述} 的变更）：

{git diff task_start_hash..HEAD 的输出}

检查：
1. Bug（逻辑错误、空指针、边界条件）
2. 安全漏洞（注入、XSS、敏感信息泄露）
3. 性能问题（N+1 查询、内存泄漏、不必要的计算）
4. 代码风格和可读性
5. 是否完整实现了任务描述中的所有要求

明确表态：
- "通过"：代码可以接受
- "不通过"：列出必须修改的问题

将审查结论写入 docs/specs/YYMMDD-{feature-name}/review-task-N.md。
只输出代码、diff 或测试结果。不要解释、不要总结、不要废话。
```

4. **Claude 处理 review 反馈**：
   - 如果 Codex 表态"通过" → 进入步骤 6
   - 如果 Codex 表态"不通过" → Claude 逐条评估 review 意见：
     - **认为所有意见合理**：Claude 修改代码 → 运行验证确保通过 → 再次提交 Codex review（步骤 3）
     - **对某条意见有质疑**：Claude 必须写出明确理由，使用 `AskUserQuestion` 向用户展示 Codex 的意见和 Claude 的质疑理由，由用户裁决

5. **迭代直到通过**：重复步骤 3-4，直到 Codex 表态"通过"

6. **完成任务并记录边界**：
   - 创建 task commit：`git add -A && git commit -m "task-N: {任务简述}"`（此 commit 用于标记 diff 边界，阶段 5 会按项目规范重新整理 commit 历史）
   - 向用户报告"✅ 任务 N/M 完成：{任务描述}"
   - 更新 tasks.md，将已完成的任务标记为 `[x]`

**里程碑检查点（强制质量门，不可跳过）：**

当一个里程碑下的所有任务都完成后，**必须**触发里程碑 review：

1. **运行里程碑验证**：运行 task-tests.md 中该里程碑指定的集成验证命令，确保断言全部通过

2. **Codex review**：通过 Codex MCP 工具，让 Codex review 里程碑范围内的所有变更。prompt：
```
审查以下 diff（里程碑 N: {里程碑名称} 范围内的所有变更）：

{git diff milestone_start_hash..HEAD 的输出}

以架构师视角全面审查，检查：
1. 跨任务的集成问题（接口不匹配、数据流断裂）
2. Bug（逻辑错误、空指针、边界条件）
3. 安全漏洞
4. 性能问题
5. 架构一致性（是否偏离 design.md）

明确表态：
- "通过"：里程碑代码可以接受
- "不通过"：列出必须修改的问题

将审查结论写入 docs/specs/YYMMDD-{feature-name}/review-milestone-N-codex.md。
只输出代码、diff 或测试结果。不要解释、不要总结、不要废话。
```

3. **Claude 处理 Codex review + 提示用户 /review**：
   - 如果 Codex 不通过：Claude 修复问题 → 运行验证 → 再次提交 Codex review，迭代直到通过
   - Codex 通过后，使用 `AskUserQuestion` 通知用户到达里程碑，**建议用户使用 `/review` 进行人工检查**：
   > "🏁 里程碑 N：{里程碑名称} — Codex review 已通过。
   >
   > 建议你使用 `/review` 对当前代码进行人工检查。如果你信任自动化 review 结果，也可以选择继续。"

4. **处理用户 /review 结果**（如果用户执行了 /review）：
   - Claude 逐条评估 /review 产出的问题
   - **认为意见合理**：修复代码 → 运行验证
   - **对某条意见有质疑**：使用 `AskUserQuestion` 向用户展示质疑理由，由用户裁决
   - 修复完成后向用户报告

5. **里程碑通过**：向用户报告"🏁 里程碑 N 通过：{里程碑名称}"

全部任务和里程碑完成后，向用户报告修改了哪些文件。

```
--- 阶段 4 完成：所有任务开发完毕 ---
```

### 阶段 5：最终验证 + PR

**第一步：自动化验证**

Claude 直接运行所有能自动化的验证，不要列成 TODO 让用户自己跑。读取 CLAUDE.md 获取项目的命令，依次执行：
1. Lint 检查
2. Build 构建
3. 单元测试 / 集成测试
4. TypeScript 类型检查（如项目有 typecheck 命令）
5. 其他 CLAUDE.md 中列出的检查命令

如果任何步骤失败，Claude 修复后重新验证，直到全部通过。原则：凡是能在终端跑的测试，Claude 都必须自己跑完，不留给用户。

**第二步：Claude 人性化 Review**

你（Claude）对最终代码做 review，关注：
- 代码可读性和命名
- 是否符合项目的既有风格和约定
- 是否有过度工程或遗漏
- 变更是否完整覆盖了需求文档中的所有验收标准

然后读取项目的 CLAUDE.md，按照其中的 Git/PR 规范生成：

1. **端到端产品测试**：如果有 browser automation 工具（如 claude-in-chrome 的 MCP 工具），自己启动浏览器完成页面级验证（导航、点击、表单填写、截图对比等），不要留给用户。只有当没有 browser automation 工具时，才列出需要用户手动验证的测试项
2. **Commit message**：严格遵守项目 CLAUDE.md 中定义的提交信息格式（前缀、scope、语言等）
3. **PR 描述**：严格遵守项目 CLAUDE.md 中定义的 PR 格式规范。使用 `--body-file` 或 heredoc 避免换行符问题。如果 PR 对应某个 issue，在描述开头加 `Closes #<issue号>`

**第三步：自动提交 PR 并监控**

开发和 Review 全部完成后，自动执行：

1. **整理 commit 历史并推送**：阶段 4 产生的 task commit 是临时边界标记。按项目 CLAUDE.md 的 commit 规范，将所有变更整理为符合规范的 commit（通常 squash 为 1-3 个语义化 commit），包含 docs/specs/ 下的文档，推送到远程
2. **创建 PR**：使用 `gh pr create` 创建 PR，标题和描述按阶段 5 第二步生成的内容。从 `gh pr create` 的输出中提取 PR 编号
3. **启动 babysit-pr**：PR 创建成功后，立即调用 `/babysit-pr {pr_number}` 自动监控 review 和 CI。如果 PR 创建失败则停止并告知用户

```
--- 阶段 5 完成：PR 已创建并进入自动监控 ---
```
