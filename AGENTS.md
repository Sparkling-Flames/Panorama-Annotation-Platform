# 协作与规范约束

## 当前阶段

- 本仓库正在 TDD 实施用户已批准的 OpenSpec change `build-panorama-annotation-platform-v1`，已落地部分 identity/media 基础、数据库迁移、API、前端与测试；change 仍处于 apply 阶段，尚未完成验收、sync 或 archive。
- 本仓库与 `D:\Work\HOHONET` 独立。除非用户通过已确认的 OpenSpec change 明确授权，不得修改 HOHONET，也不得通过复制运行时数据、子模块或软链接形成隐式耦合。
- 默认使用简体中文沟通和编写规范；代码标识及必要技术术语可使用英文。

## 职责边界

- Codex 是唯一的开发执行主体，负责代码探索、计划、实现、测试、受控子代理调用和代码审查。
- 不安装或引入 Superpowers、Trellis、Spec Kit 及其他 Agent 编排框架。
- 不创建第二套 PRD、任务规范或项目记忆系统。

## 正式真源

1. `openspec/specs/` 保存当前有效的产品行为规范。
2. `openspec/changes/` 保存待确认或待实施的变更工件。
3. ADR 仅记录难以逆转且存在真实权衡的架构理由，不能新增、删除或覆盖 OpenSpec 要求。

对话、Grill 记录、README、代码和测试都不能替代 OpenSpec。若它们与 OpenSpec 冲突，必须显式报告并先修正规范或实现，禁止静默选择一方。

## Grill 使用规则

- `grill-with-docs` 只在用户显式要求时，用于复杂功能或重要架构决策开始前的质询。
- Grill 负责发现歧义、遗漏、异常路径和术语冲突：可查事实由 Codex 自行核实，产品决策逐项向用户提出，并给出推荐答案。
- 未经用户确认，不得把 Grill 讨论直接转成实现；确认后的决定必须整理进对应 OpenSpec change 或当前 spec。
- 默认不创建 `CONTEXT.md`、并行 PRD 或独立任务文档。只有满足 ADR 门槛且不改变 OpenSpec 语义时，才可补充简洁 ADR。

## 推荐流程

`显式 Grill（按需） → OpenSpec proposal/specs/design/tasks → 用户确认 → Codex TDD 实施与测试 → 验收 → OpenSpec sync/archive`

- 未经用户确认，不得从 OpenSpec 规划阶段进入业务实现。
- OpenSpec 只管理产品行为和变更工件，不接管 Codex 的实施、子代理、Git、测试或审查流程。

## TDD 与验证

- 行为功能和缺陷修复遵循 Red → Green → Refactor：先提交能因目标行为缺失而失败的测试，再做最小实现，最后重构并运行定向测试与必要回归。
- 不得削弱断言、删除有效覆盖、硬编码测试答案、伪造生产输入或绕过真实路径来制造通过结果。
- 纯文档、配置和脚手架变更使用结构校验、CLI doctor/validate 和 Git diff 检查；不得为了宣称 TDD 而添加无意义测试。
- 每次交付说明修改内容、未改变的边界、验证结果、未运行项和剩余风险。
