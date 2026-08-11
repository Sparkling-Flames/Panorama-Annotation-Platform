# Panorama Annotation Platform

这是一个与 HOHONET 论文实验主线隔离的轻量级全景标注平台仓库。

当前正在按 TDD 实施用户已批准的首版 change：`build-panorama-annotation-platform-v1`，已完成部分 identity/media 基础、数据库迁移、API、前端与测试。change 仍处于 apply 阶段，尚未完成验收、sync 或 archive。

## 规范与协作

- apply 阶段的已批准行为：`openspec/changes/build-panorama-annotation-platform-v1/`
- 验收并 sync 后的当前主规范：`openspec/specs/`
- 执行约束：`AGENTS.md`
- Grill：仅在用户显式要求时质询；确认后的决定写入 OpenSpec，不单独形成 PRD。

标准流程：

`Grill（按需） → OpenSpec proposal/specs/design/tasks → 用户确认 → TDD 实施与测试 → 验收 → sync/archive`

## 本地工具

- Node.js `>= 24.0.0 < 25`
- OpenSpec CLI（本仓库由 `1.6.0` 初始化）
- Codex 用户级技能：`grill-with-docs`、`grilling`、`domain-modeling`

基础检查：

```powershell
openspec.cmd doctor
openspec.cmd list
openspec.cmd validate --all --strict --json
git status --short
```

## 生产配置边界

生产模式（`DJANGO_DEBUG` 不是 `true`）不会生成或回退到默认密钥，启动前必须通过运行环境注入 `DJANGO_SECRET_KEY`、`DJANGO_ALLOWED_HOSTS`、`POSTGRES_PASSWORD`、`COS_BUCKET`、`COS_REGION`、`COS_SECRET_ID` 和 `COS_SECRET_KEY`。`COS_SESSION_TOKEN` 仅在使用临时凭据时注入；任何真实值都不得提交到仓库或写入业务数据库。
