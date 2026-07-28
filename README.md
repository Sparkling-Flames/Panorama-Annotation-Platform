# Panorama Annotation Platform

这是一个与 HOHONET 论文实验主线隔离的轻量级全景标注平台仓库。

当前仅完成开发治理初始化：仓库中有 OpenSpec 与 Codex 工作流配置，但刻意没有应用代码、业务规范、待实施 change、数据库结构或部署配置。任何平台功能都应先经过 OpenSpec 规划和用户确认。

## 规范与协作

- 当前有效行为：`openspec/specs/`
- 待实施变更：`openspec/changes/`
- 执行约束：`AGENTS.md`
- Grill：仅在用户显式要求时质询；确认后的决定写入 OpenSpec，不单独形成 PRD。

标准流程：

`Grill（按需） → OpenSpec proposal/specs/design/tasks → 用户确认 → TDD 实施与测试 → 验收 → sync/archive`

## 本地工具

- Node.js `>= 20.19.0`
- OpenSpec CLI（本仓库由 `1.6.0` 初始化）
- Codex 用户级技能：`grill-with-docs`、`grilling`、`domain-modeling`

基础检查：

```powershell
openspec.cmd doctor
openspec.cmd list
openspec.cmd validate --all --strict --json
git status --short
```
