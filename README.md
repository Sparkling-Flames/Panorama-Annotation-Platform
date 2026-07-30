# Panorama Annotation Platform

这是一个与 HOHONET 论文实验主线隔离的轻量级全景标注平台仓库。

当前正在按 TDD 实施用户已批准的首版 change：`build-panorama-annotation-platform-v1`，已完成部分 identity/media 基础、数据库迁移、API、前端与测试。change 仍处于 apply 阶段，尚未完成验收、sync 或 archive。

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
