## Context

当前生产标注依赖云端 Label Studio 社区版、用户脚本、独立 3D viewer 和客户端累计时间。它能支撑当前论文实验，但难以可靠约束“工人只见本人任务”、Manual/Semi 数据隔离、规范化领域几何、提交后可修订、断网计时、动态追加共识和在线画像。新平台必须在 `D:\Work\Panorama-Annotation-Platform` 独立建设，当前 `D:\Work\HOHONET`、Label Studio 和论文协议继续原样运行。

使用者规模预期为小团队和约几十名全球工人，不需要企业级组织、支付、聊天、微服务或实时大屏。媒体体积远大于标注 JSON，因此媒体由私有 COS 直连，应用层集中处理身份、事务、签名、草稿、Revision、事件、共识和审计。工人只编辑 2D 顶/底角点对和元标签；3D、墙面、BEV、Manhattan 诊断、共识和画像都是可重建派生物。

本 change 是完整 V1 规划，不是实施授权。用户确认 proposal/specs/design/tasks 后，后续对话才能按 TDD 开始业务实现。

## Goals / Non-Goals

**Goals:**

- 构建领域专用、轻量且可审计的浏览器标注平台。
- 保证 worker/task/assignment/revision/media/prediction 的稳定身份、不可变历史和版本解释能力。
- 在工人设备本地提供动作完成后刷新、只读且与提交状态哈希绑定的 3D。
- 用服务端事件区间推导 active time，支持离线编辑和离线计时恢复。
- 用动态追加、两阶段共识和 LOO 证据减少逐图人工审计，并滚动更新 provisional 工人画像。
- 首版支持管理员手工分配与辅助建议，保留未来自动自适应路由的清晰升级边界。
- 让未来分析工具直接消费平台 canonical schema；旧论文数据如需使用则通过平台外转换 bundle 适配。

**Non-Goals:**

- 不迁移、不修改也不替代当前论文实验运行时。
- 不构建通用标注模板平台，不兼容 Label Studio 任意 `result[]` 类型。
- 不实现公共注册、企业组织树、工资支付、聊天、24x7 运维或跨区域热备。
- 不在首版实现 CAD、桌面客户端、工人本地媒体包、worker-facing A-line、云端在线推理、adaptive_auto、DatasetRelease/ModelRelease UI。
- 不把多人共识宣称为绝对真值；异常、多峰和抽检仍进入管理员复核。

## Decisions

### 1. 采用模块化单体而非微服务

平台采用一个后端代码库、一个浏览器前端和一个 PostgreSQL 主数据库。后端按领域模块分隔：identity、media、work、annotation、activity、prediction、consensus、routing、review、analytics、audit。模块通过显式服务接口和数据库事务协作，不通过共享可变 JSON 或跨服务消息拼接业务状态。

推荐实现栈为 Python/Django 类事务型 Web 后端、TypeScript/React 类 SPA、PostgreSQL、腾讯云 COS 和浏览器 WebGL/Three.js 类渲染层；实施时选择受维护版本并锁定依赖。技术栈变化不能改变 capability specs。

选择理由：几十名工人的并发不值得承担微服务、消息中间件和分布式事务成本；账户权限、Revision 冻结、幂等提交和 Assignment 状态更适合单数据库事务。与“只买 COS”相比，平台仍需要计算和关系数据库承担协调；但媒体直连 COS 后，应用服务器主要承载小体积 API 流量。

备选方案：

- 继续改造 Label Studio：通用模型、CE 可见性和脚本依赖会持续约束领域合同，拒绝。
- 完全 serverless：可作为未来部署形态，但长事务、数据库连接、后台分析和本地调试复杂度更高，不作为 V1 架构前提。
- 微服务 + Redis/Celery：当前负载和团队规模不足以抵消运维成本，首版不采用。

### 2. PostgreSQL 是业务真源，COS 只保存媒体和大 Artifact

PostgreSQL 保存账号、Task、Assignment、Draft、Revision、Review、ActivityEvent、Job、Consensus、Profile、Audit 和 manifest 元数据。COS 使用不可变对象键保存图像、导入原文件、较大导出和分析 Artifact；数据库保存对象键、hash、尺寸、版本和权限关系。浏览器只在后端授权后获得短期签名 URL，图片字节不经过应用服务器。

媒体完整性登记采用已确认的受信 manifest 方案：V1 COS 存储桶必须启用版本控制，受信上传工具在本地计算 SHA-256、尺寸和格式，并输出包含不可覆盖对象键、COS `version_id`、内容长度及 COS 服务端 CRC64 的机器生成 manifest。平台注册时对精确版本执行 HEAD 并核对版本、长度和 CRC64 后保存登记；自定义 `x-cos-meta-content-sha256` 只作一致性辅助，不作为信任根。管理员向导只浏览已登记候选，预览、发布和后续签名 URL 均绑定登记的精确版本。内容变化必须使用新的不可变对象键和新的 `media_variant_id`；V1 不让应用 Web 请求流式读取大图计算 SHA-256。

所有稳定 ID 使用不透明、不可复用的应用标识。外部数据集键与平台 ID 分栏；Task 删除通过 tombstone、cancel/supersede 表达。数据库约束负责唯一性、外键、不可变发布对象和一人一份独立共识证据等硬不变量。

备选方案：把任务和标注也存 COS JSON。它不适合并发草稿、权限查询、幂等提交、Revision 关系和实时画像更新，拒绝。

### 3. Canonical AnnotationState 使用确定性序列化与状态哈希

Canonical AnnotationState 只含：版本化元标签、有序 point pairs、稳定 pair/point ID、top/bottom 独立规范化坐标、order_index 和 seam anchor。实现定义规范 JSON 序列化：稳定字段顺序、明确数值精度与非法非有限数拒绝，然后计算 `state_sha`。Preview、submit、Prediction、Assist、Consensus 和 Export 均通过 hash 绑定精确输入。

墙面、单一 pair x、像素坐标、BEV 和 3D 不入 canonical 状态。媒体变体只要映射到同一规范化坐标系即可切换；裁剪、seam 改变或非等比变形必须显式建模。

备选方案：沿用 Label Studio `result[]`。它允许多类型自由组合且 ID/顺序语义不足，会把领域不变量推给分析脚本，拒绝。

### 4. Draft 可变、Revision 与 Artifact 不可变

每个 Assignment 的每个 DraftCycle 只有一个 CurrentDraft，使用 `draft_version` 和 base hash 做乐观并发。提交事务验证权限、工作区 token、批次状态、Draft hash、3D preview hash、服务器规则和幂等键，然后冻结完整 AnnotationRevision 并关闭该 DraftCycle。批次开放时，新修订从最新允许 Revision 新建 DraftCycle；原 Revision 永远不重新变为可变。

ReviewRecord 指向特定 Revision。管理员修正创建 AdjudicatedRevision；OOS 返工创建 feedback-exposed 新 Revision。普通点移动命令只存在当前设备 IndexedDB，服务器保存最新 DraftSnapshot 与必要事件，不实现全量事件溯源。

### 5. 前端分为 2D 编辑真源与只读 3D 派生层

2D 编辑器负责点对、顺序、seam、元标签和本地 Undo/Redo。3D worker 在离散动作完成并防抖后读取不可变 DraftState 副本，返回带 state_sha/engine_version 的 PreviewResult；新状态使旧结果失效。普通 pointer move 不触发完整重建。

提交门槛分三层：

1. canonical/schema 硬校验；
2. in-scope 结构与当前 hash 3D 成功硬校验；
3. Manhattan 和其他 plausibility 软警告 + 明确确认。

算法不得从 3D 或警告自动回写角点。错误返回 pair_id/point_id 供 2D 定位。

### 6. Manual 与 Semi 使用服务端显示合同隔离

Task mode 决定 display policy，而不是管理员选择相似前端模板。Manual 序列化器、查询和缓存键从类型层面排除 Prediction payload；Semi 在发布时必须绑定冻结 PredictionArtifact。Prediction 由管理员从本地推理输出导入、校验和预览，运行时不调用模型。

未来 A-line 使用独立注册 Assist engine 和 AssistArtifact，不嵌进 Revision 模型。V1 可实现合同、feature flag 和审计事件，但普通工人入口关闭。任何复用 Label Studio 开源实现前必须逐文件核验许可证、保留必要声明，并用平台领域接口包裹；不得复制其通用数据模型作为 canonical schema。

### 7. Active time 使用租约事件而非客户端累计秒数

前端用活动状态机把可见性、焦点、允许交互、15 秒 idle 和 30 秒 heartbeat 转成有序 ActivityEvent。客户端单调时钟用于测量间隔，服务器接收时间用于排序与异常判断；服务器按 active lease、session、sequence 和 event_id 去重、封顶和派生。

ActivityEvent 不保存 pointer 坐标或键盘内容。多标签页由浏览器协调加服务器 `active_workspace_token/active_lease` 双重限制。时间首先归属于 Assignment + DraftCycle，再派生 initial/revision/rework/unsubmitted 指标。付款保持平台外按图片结算。

### 8. 离线采用 IndexedDB patch queue，不做离线正式提交

前端保存已加载任务的当前 DraftState、未确认 patch、ActivityEvent 和 Undo 栈。恢复连接时只有 workspace token 有效、批次开放且服务器 draft_version 与 base 相同才自动同步；否则保留只读 recovery copy，禁止自动 merge 或 last-write-wins。认证令牌不进 localStorage，媒体缓存不成为正式草稿数据。

离线提交被禁用，因为服务器无法确认批次、权限、Prediction/Preview hash 和并发 Revision。未来桌面端仍复用同一同步与冲突合同。

### 9. 通过 PostgreSQL Job/Outbox 实现近实时分析

Revision 提交事务同时写入唯一 Job/Outbox 记录。独立轻量 worker 进程使用数据库抢占和幂等键处理 SubmissionAssessment、TaskAggregation、ProfileMaterialization、MetricSnapshot 和 Export。普通 Web 请求不执行 bootstrap、批量 SHA、完整导出或备份验证。

首版不引入 Redis/Celery；定时维护使用 management command + systemd timer/cron + PostgreSQL advisory lock。Job 保存状态、attempt、input hash、code version、错误和产物 manifest。若未来吞吐实测超出数据库队列能力，再通过独立 change 引入消息中间件。

### 10. Adaptive Consensus Verification 是两阶段增量状态机

每个 TaskAggregate 维护 eligible input manifest、scope state、geometry state、k、主/次簇支持、margin、terminal state 和 policy version。处理顺序固定：

1. 去重到每名工人最新未反馈有效 Revision；
2. 处置结构无效与外部系统错误；
3. 判断 scope；
4. scope=in_scope 时聚类 geometry；
5. resolved 时指向真实 medoid Revision；
6. needs_more 时生成追加 AssignmentProposal；
7. k_max 后仍不稳定则 unresolved review。

默认 3→逐一追加→5 是批次初始配置，不是全局硬编码。所有阈值、相似度和候选选择版本化。稳定 OOS 可自动 resolved；scope 冲突进入追加/复核。多人一致仍可能共同犯错，因此保留稳定锚点、分层探索和随机抽检入口。

### 11. OnlineWorkerProfile 使用 LOO、收缩和适用域

SubmissionAssessment 先产生即时过程/结构信号；准确性证据等待排除该工人的稳定共识、锚点或裁决。Task 后续 resolved 时，系统回算先前 pending evidence。ProfileSnapshot 是不可变物化视图，按 Manual/Semi、数据集/任务域、platform/model/geometry 版本分层，携带 support 和 uncertainty。

Active time、模型一致和单次警告只作为上下文/代理信号。反馈后返工单列。管理员看到 provisional 状态，工人看不到分数或 tier。

### 12. 路由分为 Run、Proposal、Manifest 三层

RoutingRun 冻结任务特征、候选集、ProfileSnapshot、可用性、容量、重复暴露、政策版本和 seed；生成 AssignmentProposal；管理员批准或覆盖后生成 AssignmentManifest 和 Assignment。V1 只开放 manual/assisted，adaptive_auto 为关闭的枚举和 feature gate。

排序目标不是“高分工人做所有题”，而是能力—难度匹配：高支持高能力者主要处理难题/争议；稳定略低者更多处理简单题；新人通过简单题和稳定校准建立证据。分层探索防止永久锁层，晋级/降级只影响未来建议。批次内算法/阈值固定，画像可滚动更新；规则变化生成新版本和明确生效边界。

### 13. 管理分析分成实时运营计数与按需 Snapshot

运营页读取增量计数/Job 状态，不运行昂贵统计。管理员按钮创建不可变 MetricSnapshot，输入只含 cutoff 前 Revision 和版本 manifest，输出 support、缺失/not-evaluable、画像、共识和运营统计。相同输入可缓存，旧 Snapshot 不随新数据变化。

AuditRun 只允许注册类型，读取冻结 hash，输出不可变 Artifact，不能编辑标注。BatchExportSnapshot 输出全部身份、Revision、Review、Consensus、Media/Schema/Version 引用，并附 latest/selected convenience view；不宣称 Final Gold。

### 14. 部署、网络和恢复保持轻量

参考部署为一个小型应用运行环境（可为单机容器或等价托管运行时）运行 Web 与一个后台 worker，外加托管 PostgreSQL 和私有广州 COS。前端静态资源可由同一应用或静态托管提供；首版 REST + 短轮询足够，不要求 WebSocket。应用带宽不承载图片，实际 CPU/内存/带宽规格通过预发布负载测试确定，不在规范中猜测固定值。

全球工人首版直连广州 COS，采集匿名化地区性能指标后再决定 CDN/全球加速。数据库每日备份 30 天，关键操作前额外备份/导出；恢复测试验证关系记录和 COS 引用。部署使用向前兼容数据库迁移，先扩展 schema、部署兼容代码、再启用功能；回滚不删除新 Revision 或审计数据。

### 15. 安全、隐私和可观测性按风险最小化

后端统一执行对象级权限；密码不可回读；签名 URL 最小对象范围和短 TTL；日志不输出密码、令牌、签名参数、完整 OOS 私密文本或媒体内容。生产日志使用 request/job/event 关联 ID，记录 API 错误率、保存冲突、签名失败、Job 延迟、事件积压、3D 客户端错误和 COS 性能。

中英文 notice 说明最小数据收集。IP/设备安全数据短期保留且不进质量画像。高影响管理员操作进入不可变 AuditEvent。

## 已确认决策覆盖矩阵

| Grill 已确认主题 | 正式规范域 | 设计落点 |
|---|---|---|
| 与当前论文/LS 完全隔离，未来单独转换 | `platform-boundaries` | 1、3、6、14 |
| 浏览器首版，Chrome/Edge；桌面端未来 WebView/单实例 | `platform-boundaries`、`identity-access` | 5、8、14 |
| 工人只编辑 2D/元标签，保存 point pair 顺序和 seam | `annotation-contract` | 3、5 |
| 3D 本地只读，动作结束后刷新，提交绑定当前 hash | `preview-validation` | 3、5 |
| 严重结构硬阻断，Manhattan 警告确认不自动改点 | `preview-validation` | 5 |
| img_v 优先、高清切换、规范化坐标、COS 直连无 CDN | `media-ingestion-delivery` | 2、3、14 |
| 首版只收已拼接全景，不引入不确定预处理参数 | `media-ingestion-delivery` | 2 |
| 管理员可视化幂等导入，不手写 JSON，Task ID 不回收 | `media-ingestion-delivery`、`task-batch-assignment` | 2 |
| 未来工人本地包只挂载已分配媒体 | `media-ingestion-delivery`、`platform-boundaries` | 2、8 |
| Manual 网络层完全无 prediction；Semi 读取本地导入冻结 Artifact | `prediction-assist` | 6 |
| 领域 ID 绑定人/任务/Assignment/Revision，不以 LS ID 为真源 | `identity-access`、`task-batch-assignment` | 2、3 |
| 无公共注册、临时密码强制修改、管理员不可见原密码 | `identity-access` | 15 |
| 单活动工作区、显式接管、多标签页保护、未来桌面仍有服务端锁 | `identity-access`、`activity-offline` | 7、8、15 |
| 批量任务可切换，困难任务 deferred，提交后批次开放仍可修订 | `task-batch-assignment`、`draft-revision-review` | 4 |
| Draft 可变、Revision/Review/Adjudication 不可变且状态分离 | `draft-revision-review`、`task-batch-assignment` | 4 |
| OOS 二元但要原因和尝试状态；OOS 与 skip 分离 | `annotation-contract`、`task-batch-assignment` | 10 |
| OOS 裁定 in-scope 后可要求返工且不暴露他人几何 | `draft-revision-review` | 4、11 |
| 元标签 schema/copy/locale 版本化，字段未来可小概率新增 | `annotation-contract`、`platform-boundaries` | 3、15 |
| active time 不是页面时长，断网计时，服务端推导，不用于工资 | `activity-offline` | 7、8 |
| 不永久记录普通移动轨迹，必要 Assist/警告/指导事件保留 | `draft-revision-review`、`activity-offline` | 4、7、15 |
| 每次提交近实时分析，动态追加，稳定后停止，无需逐张人工审计 | `adaptive-consensus-routing` | 9、10、11 |
| scope 先于 geometry；LOO 回算画像；medoid 不平均角点 | `adaptive-consensus-routing` | 10、11 |
| 低能力稳定者多做简单题，高能力者多做难题，保留晋级探索 | `adaptive-consensus-routing` | 12 |
| manual/assisted 首版，adaptive_auto 后续，管理员覆盖可审计 | `task-batch-assignment`、`adaptive-consensus-routing` | 12 |
| 管理员按钮计算不可变当前快照，实时仅运营轻指标 | `admin-audit-export` | 13 |
| 微信/Upwork 可指导但平台不做聊天/支付 | `admin-audit-export` | 13、15 |
| 当前审计流未来原生重做，不运行任意论文脚本 | `prediction-assist`、`admin-audit-export` | 6、13 |
| A-line 原生但分阶段，worker-facing 不阻塞首版 | `prediction-assist`、`platform-boundaries` | 6 |
| 批次导出不等于 Final Gold；Dataset/Model Release 延后 | `admin-audit-export`、`platform-boundaries` | 13 |
| 小团队备份基线，不做企业级热备 | `admin-audit-export` | 14 |
| 中英文、UTC/本地时区、全球工人和最小隐私告知 | `platform-boundaries` | 14、15 |

## Risks / Trade-offs

- [广州 COS 对远距离工人延迟较高] → 压缩图优先、高清后台加载、记录地区聚合性能；依据实测通过未来 change 决定 CDN，而不在首版引入混乱配置。
- [多人共识可能共同出错] → 使用 LOO、锚点、分层探索、随机抽检和 unresolved 复核；不把稳定共识称为绝对 GT。
- [在线画像形成自我强化路由] → 保存 uncertainty/support，批次内政策固定，保留跨难度探测与候选重放，首版只生成建议。
- [默认 3→5 增加标注成本] → 数值为批次配置，稳定即停；低风险批次可在证据支持下用新政策版本调整，不能无限追加。
- [数据库 Job 队列发生积压] → 指标监控、幂等重试、advisory lock 和独立 worker；超过实测门槛再引入消息中间件。
- [浏览器崩溃或多设备覆盖草稿] → 服务端 autosave + IndexedDB patch + 乐观并发 + 单工作区接管；冲突不自动 merge。
- [浏览器本地 WebGL 差异影响 3D] → Chrome/Edge 支持矩阵、设备预检、固定 geometry engine、状态 hash 和回归图形测试。
- [复用开源代码引入许可证或模型耦合] → 逐文件许可证审查和来源清单；优先复用理念/算法接口，不复制 Label Studio canonical 模型。
- [平台范围一次过大] → tasks 采用垂直切片与阶段 gate；未来 CAD/桌面/A-line/Release 明确不阻塞 V1。
- [隐私与画像引发误用] → 工人不见排名，时间/IP不作质量真值，管理员界面显示支持和不确定性，数据告知版本化。

## Migration Plan

1. 在独立仓库建立测试、开发环境和空数据库，不读取 HOHONET/LS 运行时。
2. 先实现身份、领域模型、权限和不可变 ID/Revision 基础，再实现媒体与编辑闭环。
3. 以少量非论文生产数据完成浏览器、3D、离线、时间、Submission/Revision 验收。
4. 增加 Prediction、动态共识、画像和 assisted routing，先 shadow 输出再允许管理员采用建议。
5. 完成管理员 Snapshot、Audit、Export、备份恢复和全球访问试测后才宣布 V1 可用于新重标批次。
6. 当前论文实验始终留在 Label Studio；不存在生产数据回填或双写迁移。

回滚策略：应用版本可回退到仍兼容已扩展 schema 的前一版本；新 Revision、ActivityEvent、AuditEvent 和 Artifact 不删除。功能开关可关闭 Prediction、Consensus 建议或路由建议，但不得修改历史对象。破坏性 schema 收缩不在 V1 正常部署路径中。

## Open Questions

以下不是未决产品语义，而是实施前通过测试/试点确定并版本化的参数：

- geometry similarity、scope 支持、cluster margin、Manhattan 严重警告和 pair 偏差的初始数值。
- 不同数据集/模式的 TaskDifficulty 特征和分层探索配额初值。
- COS 全球性能验收阈值以及是否需要未来 CDN change。
- 具体托管运行环境与预发布负载测试后所需 CPU、内存、数据库连接和应用带宽。
- 任何 Label Studio 代码复用前的许可证、来源和最小必要范围审查。

这些参数不得通过实现者静默决定；必须以版本化配置、测试证据和管理员可见说明进入首个实施 change 的验收记录。
