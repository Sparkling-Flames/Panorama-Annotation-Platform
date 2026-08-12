## Why

当前工作依赖云端 Label Studio 社区版、篡改猴脚本和外置 3D/计时能力，难以实现工人任务隔离、可靠的活动时间、领域专用几何合同、动态追加验证和可审计的在线画像。需要建设一个与 HOHONET 论文实验主线物理隔离的轻量级全景标注平台，为后续高质量数据集重标提供稳定、可扩展且不依赖当前实验运行时的产品基础。

## Amendment（2026-08-09）：Manual 在线 POC、Scope 与 Portal

本 amendment 在现有 active change 内补全 Scope 证据和 portal 几何合同，并把实施前沿收敛为 Manual 在线标注闭环 POC，不创建第二套 PRD 或并行 change。相关 Draft、Revision 和元标签尚未进入生产实现，因此直接修订 V1 规划，不伪造旧 Revision 迁移；PreScreen、Calibration、画像与自适应路由保留为 post-POC 任务，不阻塞 POC。

## Amendment（2026-08-10）：有序 Assignment 与暂时跳过

本 amendment 澄清工人所说的 Skip 是批次内的“暂时跳过”，不是放弃标注。每名工人在一个 WorkBatch 内的 Assignment 具有稳定顺序；Skip 仅把当前项标为 `deferred`，保存 Draft 和活动时间，不创建 Revision，也不把工作状态改成完成。工人完成后续任务后必须回到最早的 deferred 项，仍有 deferred 项时该工人的批次进度不得视为完成。真正无法继续的情况独立命名为“报告阻断”，由管理员决定重开、换人或终止，不与 Skip 共用状态或文案。

## Amendment（2026-08-10）：Revision 复核、裁决与活动时间 v1

本 amendment 固定首版复核与计时边界。POC 仅由管理员对具体不可变 Revision 追加复核记录；`accepted` 与 `changes_requested` 是复核结果，管理员直接修改 canonical 时创建独立 AdjudicatedRevision，Task 级交付指针必须显式选择工人 Revision 或裁决结果。活动时间仅实现并冻结 v1：事件同时保存单调时间与客户端墙钟时间，服务器对每个 15 秒封顶的活动区间求并集以避免跨 session 重复计时；未来规则变化只能随新 Task 发布新版本，旧 Task 不得被新算法重算。

## Amendment（2026-08-11）：首版 Canonical 2D 组件共识

本 amendment 固定 post-POC 首个可运行的 `canonical-2d-consensus-v1`：它只比较工人直接提交的规范化 2D point pair 与 PortalObservation，不宣称正式 3D、Manhattan 或绝对真值。WorkBatch 使用新增且冻结的 `consensus-policy-v2` 保存阈值、支持度、margin 与仍可按新批次调整的 `k_max`；旧 `consensus-policy-v1` 不被原地重新解释。Geometry 与 Portal 独立聚类并分别引用真实 medoid Revision，不平均角点。尚未确认 target/partition/closure policy 时不生成 DerivedTargetArtifact；该派生能力保留为后续明确政策切片。

## Amendment（2026-08-13）：A-line 理念借鉴与精度辅助边界

本 amendment 采纳 Manhattan 仓库 A-line 分支总结中的可迁移理念，但不复制其代码、数据合同或运行时。平台继续只标相机当前所在空间，唯一 canonical geometry 仍是有序 top/bottom point pair；墙面、3D、Manhattan residual 和 completion candidate 都是派生结果。工人端可增加点级局部放大、明确 pair 关联和被动证据/结构提示，但这些辅助层没有 canonical authority，不得吸附、自动拉直、批量移动、改拓扑或回写点位。Manual 必须从空白状态开始；Semi 只读取管理员预先导入并冻结的 PredictionArtifact。点级遮挡/evidence 状态会在新 schema_version 及兼容迁移获得单独验收后进入后续版本，不追写现有 Revision。

## What Changes

- 建立管理员创建账号、工人仅访问本人任务、单活动工作区和接管恢复的权限合同。
- 建立领域专用的 `Asset`、`MediaVariant`、`Task`、`WorkBatch`、`Assignment`、`CurrentDraft`、不可变 `AnnotationRevision`、复核与裁决模型；不采用 Label Studio 通用 `result[]` 作为平台真源。
- 提供管理员可视化导入、私有 COS 短期签名直连、压缩图优先与高清图切换，并以规范化坐标保持媒体变体间几何一致。
- 当前实施前沿是 Manual 在线 POC：真实 Assignment 与媒体 → 2D/Scope/Portal → autosave → 信息性只读 wireframe → 不可变 Revision → 管理员读取；不以未来质量系统阻塞闭环。
- 提供仅可编辑 2D 顶/底角点对、portal observation 及版本化元标签的浏览器编辑器，保存稳定标识、顺序、seam、三态 `worker_scope_observation`、尝试状态和证据；墙面、BEV、3D、protocol closure 与最终 eligibility 均为派生结果。
- 在 2D 编辑器提供只读点级放大镜、明确 pair 连线和坐标反馈；辅助只读取当前媒体与内存状态，取消检查不改变 Draft，显式操作才进入状态哈希和 Undo/Redo。
- 首个正式 MetaSchema 沿用已核对的 Difficulty 与 Semi-only Model Issue 稳定代码，Scope 继续使用本 change 的三态观察合同；中英文 copy 同版本语义一致，后续选项或语义调整通过新 schema_version 和新 Task 发布，不覆盖旧版本。
- POC 提供动作结束后在工人设备本地生成的 `poc-wireframe-v1` 只读预览，authority 固定为 informational，不参与提交门槛或正式几何质量结论；论文级 geometry、Manhattan 诊断及提交门槛后移。
- 提供自动草稿、IndexedDB 离线降级、Undo/Redo、按稳定 Assignment 顺序工作的批量任务切换、暂时跳过后强制回访、重复提交幂等、提交后继续修订及管理员发起返工。
- 以内存活动、粗粒度事件和服务器推导实现真实 `active_time`；支持已加载任务离线编辑与离线计时，但禁止离线正式提交。
- 支持 Manual 与 Semi 显示合同隔离，以及管理员导入本地模型输出形成冻结 `PredictionArtifact`；平台首版不进行云端模型推理。
- Post-POC 引入组件级 `Adaptive Consensus Verification`：分别聚合 scope、portal、geometry 与 evidence，动态追加独立标注并在稳定后停止；工人 observation 只作为证据，是否自动 close 由冻结 ScopePolicy 决定。
- Post-POC 引入显式 PreScreen、Calibration、Production、Probe 与 Adjudication 用途，使用 common anchor、diverse bridge、precision/progression/drift probe、LOO 或裁决形成 `verified | provisional | proxy` 分层证据和不可变画像快照。
- Post-POC 引入 Assignment 前冻结的版本化 TaskFeatureVector、CalibrationPolicy 和 exposure ledger；路由继续只生成可解释建议并保留候选集、选择规则/概率、探索目的和决策时画像，不开放 `adaptive_auto`。
- 提供管理员手工分配与系统辅助建议、按需生成不可变统计快照、异常复核队列、审计日志和按批次导出快照。
- 建立中英双语、数据收集告知、最小化安全日志、备份恢复与版本追踪合同。
- **BREAKING**：未来平台数据导出以平台领域 ID 和版本化 canonical schema 为准，不保证与 Label Studio 导出形状兼容；如需使用旧论文分析，只通过独立、一次性的离线转换器适配。
- 明确首版非目标：不迁移当前 HOHONET/Label Studio 实验，不开放公共注册，不实现支付、聊天、CAD/BIM、桌面客户端、多房间 floor-plan reconstruction、worker-facing Manhattan A-line、自动 snapping/隐式点移动、云端在线推理、完全自动自适应路由或 Dataset/Model Release 管理；不把多数共识宣称为绝对真值。
- 本 change 继续作为唯一实施真源；先按已确认 POC 前沿推进，post-POC 内容在到达对应任务前不得抢跑。

## Capabilities

### New Capabilities

- `identity-access`: 管理员分发账号、工人数据隔离、密码与会话、角色权限及安全审计。
- `media-ingestion-delivery`: 领域资产、媒体变体、可视化导入、COS 交付、哈希固定与失败恢复。
- `task-batch-assignment`: 任务合同、批次/Assignment 明确用途与稳定顺序、版本化任务特征与政策引用、手工/辅助分配、暂时跳过与回访、独立技术阻断。
- `annotation-contract`: 有序顶/底角点对、规范化坐标、seam、版本化元标签、三态 worker scope evidence、portal observation、无写回精度辅助、派生 closure 边界与服务器校验。
- `draft-revision-review`: 草稿、Undo/Redo、幂等提交、不可变修订、复核、裁决和反馈后返工。
- `preview-validation`: POC 信息性本地 wireframe、动作完成触发与状态哈希；正式 geometry/Manhattan authority 后移。
- `activity-offline`: 活跃时间事件、服务器推导、离线队列、草稿冲突和恢复行为。
- `prediction-assist`: Manual/Semi 隔离、冻结 PredictionArtifact 导入和未来 A-line AssistArtifact 边界。
- `adaptive-consensus-routing`: Post-POC 的组件级动态共识、PreScreen/Calibration、LOO/anchor 证据、不可变在线画像、能力—难度匹配及路由防反馈循环；设计中保持 TaskAggregation、Worker Calibration/Profile 与 Routing 边界独立。
- `admin-audit-export`: 管理员运营视图、按需统计快照、复核队列、指导记录、审计、批次导出与备份恢复。
- `platform-boundaries`: 浏览器与国际化支持、隐私告知、版本追踪、HOHONET 隔离及未来适配边界。

### Modified Capabilities

无；当前仓库尚无已生效业务规范。

## Impact

- 将新增浏览器前端、应用后端、关系数据库、后台作业、对象存储适配、分析模块和测试基础设施，但具体技术边界由 `design.md` 约束。
- 私有 COS 保存媒体；POC 数据库只落账号、任务、草稿、修订及 portal/scope evidence 等闭环必需数据，校准暴露、共识、画像和路由决策随 post-POC 任务再落地。应用后端负责授权、签名、事务与协调，不代理大体积媒体字节。
- 全球工人首版直接访问广州 COS，平台记录媒体与网络失败以支持后续基于实测数据的加速决策；首版不引入 CDN。
- `D:\Work\HOHONET`、现有 Label Studio、导出、活动日志和论文协议均不被修改，也不成为新平台运行时依赖。
- 实施规模较大，任务必须按垂直切片和 TDD 分阶段交付；Manual 在线 POC 不得被未来 Calibration、画像、正式 geometry、CAD、桌面端、A-line 或模型发布功能阻塞。
