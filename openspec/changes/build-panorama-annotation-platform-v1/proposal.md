## Why

当前工作依赖云端 Label Studio 社区版、篡改猴脚本和外置 3D/计时能力，难以实现工人任务隔离、可靠的活动时间、领域专用几何合同、动态追加验证和可审计的在线画像。需要建设一个与 HOHONET 论文实验主线物理隔离的轻量级全景标注平台，为后续高质量数据集重标提供稳定、可扩展且不依赖当前实验运行时的产品基础。

## What Changes

- 建立管理员创建账号、工人仅访问本人任务、单活动工作区和接管恢复的权限合同。
- 建立领域专用的 `Asset`、`MediaVariant`、`Task`、`WorkBatch`、`Assignment`、`CurrentDraft`、不可变 `AnnotationRevision`、复核与裁决模型；不采用 Label Studio 通用 `result[]` 作为平台真源。
- 提供管理员可视化导入、私有 COS 短期签名直连、压缩图优先与高清图切换，并以规范化坐标保持媒体变体间几何一致。
- 提供仅可编辑 2D 顶/底角点对及版本化元标签的浏览器编辑器，保存稳定点对标识、顺序和 seam anchor；墙面、BEV 与 3D 均为派生结果。
- 提供动作结束后在工人设备本地生成的只读 3D 预览、结构硬阻断和严重 Manhattan 偏差警告。
- 提供自动草稿、IndexedDB 离线降级、Undo/Redo、批量任务切换、重复提交幂等、提交后继续修订及管理员发起返工。
- 以内存活动、粗粒度事件和服务器推导实现真实 `active_time`；支持已加载任务离线编辑与离线计时，但禁止离线正式提交。
- 支持 Manual 与 Semi 显示合同隔离，以及管理员导入本地模型输出形成冻结 `PredictionArtifact`；平台首版不进行云端模型推理。
- 引入 `Adaptive Consensus Verification`：scope 优先、geometry 次之的两阶段共识，动态追加独立标注，稳定后停止，LOO 质量证据、在线 provisional 画像和能力—难度匹配建议。
- 提供管理员手工分配与系统辅助建议、按需生成不可变统计快照、异常复核队列、审计日志和按批次导出快照。
- 建立中英双语、数据收集告知、最小化安全日志、备份恢复与版本追踪合同。
- **BREAKING**：未来平台数据导出以平台领域 ID 和版本化 canonical schema 为准，不保证与 Label Studio 导出形状兼容；如需使用旧论文分析，只通过独立、一次性的离线转换器适配。
- 明确首版非目标：不迁移当前 HOHONET/Label Studio 实验，不开放公共注册，不实现支付、聊天、CAD、桌面客户端、worker-facing Manhattan A-line、云端在线推理、完全自动自适应路由或 Dataset/Model Release 管理。
- 本 change 只形成待确认的规范、设计和 TDD 任务；用户再次确认前不得实施业务功能。

## Capabilities

### New Capabilities

- `identity-access`: 管理员分发账号、工人数据隔离、密码与会话、角色权限及安全审计。
- `media-ingestion-delivery`: 领域资产、媒体变体、可视化导入、COS 交付、哈希固定与失败恢复。
- `task-batch-assignment`: 任务合同、批次生命周期、手工/辅助分配、任务切换、跳过与技术阻断。
- `annotation-contract`: 有序顶/底角点对、规范化坐标、seam、元标签、OOS 证据与服务器校验。
- `draft-revision-review`: 草稿、Undo/Redo、幂等提交、不可变修订、复核、裁决和反馈后返工。
- `preview-validation`: 本地只读 3D、动作完成触发、状态哈希、结构硬阻断与 Manhattan 警告。
- `activity-offline`: 活跃时间事件、服务器推导、离线队列、草稿冲突和恢复行为。
- `prediction-assist`: Manual/Semi 隔离、冻结 PredictionArtifact 导入和未来 A-line AssistArtifact 边界。
- `adaptive-consensus-routing`: 动态追加、两阶段共识、LOO 在线画像、能力—难度匹配及路由防反馈循环。
- `admin-audit-export`: 管理员运营视图、按需统计快照、复核队列、指导记录、审计、批次导出与备份恢复。
- `platform-boundaries`: 浏览器与国际化支持、隐私告知、版本追踪、HOHONET 隔离及未来适配边界。

### Modified Capabilities

无；当前仓库尚无已生效业务规范。

## Impact

- 将新增浏览器前端、应用后端、关系数据库、后台作业、对象存储适配、分析模块和测试基础设施，但具体技术边界由 `design.md` 约束。
- 私有 COS 保存媒体；数据库保存账号、任务、草稿、修订、事件、共识、画像、审计和快照。应用后端负责授权、签名、事务与协调，不代理大体积媒体字节。
- 全球工人首版直接访问广州 COS，平台记录媒体与网络失败以支持后续基于实测数据的加速决策；首版不引入 CDN。
- `D:\Work\HOHONET`、现有 Label Studio、导出、活动日志和论文协议均不被修改，也不成为新平台运行时依赖。
- 实施规模较大，任务必须按垂直切片和 TDD 分阶段交付；首个可用版本不得被未来 CAD、桌面端、A-line 或正式模型发布功能阻塞。
