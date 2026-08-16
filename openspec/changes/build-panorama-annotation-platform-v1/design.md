## Context

当前生产标注依赖云端 Label Studio 社区版、用户脚本、独立 3D viewer 和客户端累计时间。它能支撑当前论文实验，但难以可靠约束“工人只见本人任务”、Manual/Semi 数据隔离、规范化领域几何、提交后可修订、断网计时、动态追加共识和在线画像。新平台必须在 `D:\Work\Panorama-Annotation-Platform` 独立建设，当前 `D:\Work\HOHONET`、Label Studio 和论文协议继续原样运行。

使用者规模预期为小团队和约几十名全球工人，不需要企业级组织、支付、聊天、微服务或实时大屏。媒体体积远大于标注 JSON，因此媒体由私有 COS 直连，应用层集中处理身份、事务、签名、草稿、Revision、事件、共识和审计。工人只编辑 2D 顶/底角点对、portal observation 和元标签；3D、墙面、BEV、protocol closure、最终 eligibility、Manhattan 诊断、共识和画像都是可重建派生物。

本 change 是正在 apply 的完整 V1 规划。当前已确认的实施前沿是 Manual 在线 POC；post-POC 决策保留在同一 change 中，但不得提前产生无调用方模型或门槛。

## Goals / Non-Goals

**Goals:**

- 构建领域专用、轻量且可审计的浏览器标注平台。
- 先贯通 Manual Assignment、真实媒体、2D/Scope/Portal、autosave、信息性 wireframe、不可变 Revision 与管理员读取的在线 POC。
- 保证 worker/task/assignment/revision/media/prediction 的稳定身份、不可变历史和版本解释能力。
- 在工人设备本地提供动作完成后刷新、只读且绑定当前 Draft `state_sha` 的信息性 wireframe。
- 在 2D 真源上提供瞬态点级放大和明确 pair 关联，使工人看清边界但不引入自动吸附或隐式几何写回。
- 用服务端事件区间推导 active time，支持离线编辑和离线计时恢复。
- 用动态追加、scope/portal/geometry/evidence 组件级聚合和 LOO 证据减少逐图人工审计，并滚动更新 provisional 工人画像。
- POC 后再用 Trap PreScreen、CalibrationCampaign、anchor/bridge/probe、LOO 与裁决建立可追溯的 verified/provisional/proxy 工人证据，并把校准与 task-level 聚合分开。
- 首版支持管理员手工分配与辅助建议，保留未来自动自适应路由的清晰升级边界。
- 让未来分析工具直接消费平台 canonical schema；旧论文数据如需使用则通过平台外转换 bundle 适配。

**Non-Goals:**

- 不迁移、不修改也不替代当前论文实验运行时。
- 不构建通用标注模板平台，不兼容 Label Studio 任意 `result[]` 类型。
- 不实现公共注册、企业组织树、工资支付、聊天、24x7 运维或跨区域热备。
- 不在首版实现 CAD、桌面客户端、工人本地媒体包、外部 A-line/Manhattan 集成或其产物合同、自动 snapping/拉直/拓扑修改、云端在线推理、adaptive_auto、DatasetRelease/ModelRelease UI。
- 不在首版实现 BIM、多房间 floor-plan reconstruction 或把 portal 自动等同于 cell cut。
- 不把多人共识宣称为绝对真值；异常、多峰和抽检仍进入管理员复核。

## Decisions

### 1. 采用模块化单体而非微服务

平台采用一个后端代码库、一个浏览器前端和一个 PostgreSQL 主数据库。后端按领域模块分隔：identity、media、work、annotation、activity、prediction、consensus、routing、review、analytics、audit。模块通过显式服务接口和数据库事务协作，不通过共享可变 JSON 或跨服务消息拼接业务状态。

已批准的实现栈固定为 Django 事务型 Web 后端、TypeScript/React SPA、PostgreSQL、腾讯云 COS、常驻 Web 进程和独立常驻 worker，按一个模块化单体代码库部署。POC wireframe 与点级放大镜使用现有浏览器/SVG 能力；WebGL/Three.js 类渲染层只在未来独立 change 确认正式 geometry authority 后评估。实施时锁定受维护版本，技术栈变化不能改变 capability specs。

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

Canonical AnnotationState 只含工人直接提交的版本化元标签、有序 point pairs、稳定 pair/point ID、top/bottom 独立规范化坐标、order_index、seam anchor、三态 `worker_scope_observation`、独立 `geometry_attempt_status`、POC 的 `geometry_attempt_reason_text`、Difficulty、按模式隔离的 Model Issue 和 PortalObservation。`geometry_attempt_reason_text` 只在 `not_drawable` 时保存非空说明，不复用 Scope reason 选项。Portal canonical 只保存稳定 `portal_id`、类型、四个规范化边界点、evidence status 与可选 host edge reference；jamb/top/bottom 线段由四点派生，避免重复几何发生漂移。实现定义规范 JSON 序列化：稳定字段顺序、明确数值精度与非法非有限数拒绝，然后计算 `state_sha`。Preview、submit、Prediction、Assist、Consensus 和 Export 均通过 hash 绑定精确输入。

墙面、单一 pair x、像素坐标、BEV、3D、cell cut 和 protocol closure 不入 canonical 状态。Manual POC 不预填尚未启用的 target/partition/closure policy 或 TaskFeatureVector 占位引用；只有在对应 post-POC 派生目标或辅助路由能力启用前，Task 才必须冻结其实际使用的政策/特征版本。DerivedTargetArtifact 明确保存 `physical=false` 和 source portal/policy。媒体变体只要映射到同一规范化坐标系即可切换；裁剪、seam 改变或非等比变形必须显式建模。

备选方案：沿用 Label Studio `result[]`。它允许多类型自由组合且 ID/顺序语义不足，会把领域不变量推给分析脚本，拒绝。

### 4. Draft 可变、Revision 与 Artifact 不可变

每个 Assignment 在同一 `WorkBatch + worker` 范围内保存不可变 `order_index`。工人的 Skip 操作只把当前 Assignment 的 `queue_state` 设为 `deferred`；`work_state` 保持 `assigned` 或 `in_progress`，CurrentDraft 和 Activity 继续归属于原 Assignment，且不创建 Revision 或单独的终局 Skip 记录。默认导航先选择当前项之后最小 `order_index` 的未完成非 deferred 项；没有此类后续项时回到整个批次中最早的 deferred 项。工人可提前手动返回或再次暂缓，但只要仍存在 `assigned`、`in_progress` 或 `deferred` Assignment，该工人的批次进度就不是完成。`needs_revisit` 保留给复核或管理员要求的后续处理，不作为普通 Skip 的第二种表达。

真正无法继续的情况使用独立 BlockReport 和 `work_state=blocked`。阻断不创建完成 Revision，也不计为已提交证据；对工人队列它是终态，管理员必须显式选择重开原 Assignment、换人重新分配或终止处置。该路径与暂时跳过共用现有 Assignment/Draft/Activity 关系，但不共用名称、状态或次数限制；首版不为普通暂时跳过设置配额。

每个 Assignment 的每个 DraftCycle 只有一个 CurrentDraft，使用 `draft_version` 和 base hash 做乐观并发。POC 提交事务验证权限、工作区 token、批次状态、Draft hash、服务器 canonical 规则和幂等键，然后冻结完整 AnnotationRevision 并关闭该 DraftCycle；信息性 wireframe 不参与授权或提交判定。工人可在安全切换点选择 `zh-CN | en`；选择会驱动 Task 固定的元标签 copy 并原样冻结为 Revision 的 `submission_locale`，未保存或提交进行中不得切换。平台固定操作文案继续由管理员维护为语义一致的中英双语，不依赖机器翻译。POC 历史 Task 的 `poc-contract-v0`/`poc-bilingual-copy-v0` 占位引用永不复用；5.5/5.6 后的新 Task 使用首个正式 `annotation-meta-v1`/`annotation-meta-copy-v1`，旧 Task 和 Revision 继续按原版本解释。首版 registry 显式保存固定字段、选项、模式和双语 copy；不复制 Label Studio XML、不在运行时依赖 HOHONET，也不构建通用表单设计器。批次开放时，新修订从最新允许 Revision 新建 DraftCycle；原 Revision永远不重新变为可变。

新提交的 Revision 另冻结实际使用的 `platform_release_id`、`client_build_sha`、2D viewer 与 interaction contract；历史缺失值保持缺失，不以当前值回填。批次导出沿 Task/Revision/PredictionArtifact 谱系输出实际适用版本，Manual、未启用 assist/profile 以及仅本地信息性的 wireframe geometry 元数据均不填占位，也不复制进 Revision。

ReviewRecord 指向特定 Revision，首版 reviewer 仅为管理员，结果只使用 `accepted | changes_requested`。复核采用追加式不可变记录；改判时新记录显式引用被替代记录。`changes_requested` 必须保存原因并把 Assignment 投影为 `queue_state=needs_revisit`，但完整 ReworkRequest、期限与反馈暴露仍由 6.9/6.10 实现。管理员修正 canonical 时创建归因于管理员、保存完整状态/哈希/来源 Revision 的 AdjudicatedRevision；TaskDeliverySelection 是 Task 级独立交付指针，只能指向某个工人 Revision 或 AdjudicatedRevision。新工人 Revision 初始为 `unreviewed`，不会静默移动已有交付指针。首版复核整份 Revision，scope/portal/geometry 分组件证据留给 10.x。scope observation 被裁决后的返工创建 feedback-exposed 新 Revision。普通点移动命令只存在当前设备 IndexedDB，服务器保存最新 DraftSnapshot 与必要事件，不实现全量事件溯源。

Active time 首版只实现 `active-time-v1`，Task 在发布前冻结该版本。每个 ActivityEvent 同时携带用于单 session 排序的 `client_monotonic_ms` 和用于跨 session 对齐的 `client_wall_time_ms`；服务器先按 v1 把有效交互派生为最长 15 秒的区间，再按 DraftCycle 对所有 session 区间求并集。墙钟非有限、倒退或与接收时间明显异常时记录原因并保守少算，不得因此增加时间。当前实现不预建 v2；未来规则只通过新版本和新 Task 生效，旧 Task/事件始终按原版本重放。

### 5. 前端分为 2D 编辑真源与只读派生预览层

2D 编辑器负责点对、顺序、seam、Scope/Portal、元标签和本地 Undo/Redo。POC 以无新增依赖的 `poc-wireframe-v1` 在离散动作完成并防抖后读取不可变 DraftState 副本，返回带 `state_sha`、`engine_version` 和 `authority=informational` 的 PreviewResult；新状态使旧结果失效，普通 pointer move 不触发重建。预览始终只读，不得回写 canonical。

点级放大镜、pair 连线和 seam 标记属于平台本地只读辅助层。它们只能读取当前已授权 MediaVariant 与内存 DraftState，瞬态裁剪和 pointer preview 不持久化；只有工人明确完成 add/delete/move/order/seam 操作时，2D 真源才产生一个可撤销状态变更。取消拖动或关闭辅助必须保持 `state_sha` 不变。辅助层不得执行 snapping、自动竖直化、Manhattan 对齐、多点联动或 topology completion。

POC 提交门槛只包含：

1. canonical/schema 硬校验；
2. 与 attempt 状态相适用的字段、有限性、ID 和引用校验；
3. Assignment、workspace、批次、并发和幂等事务校验。

正式论文级 geometry engine、Manhattan/A-line 诊断和提交硬门槛不属于 V1。只有外部工具合同稳定、独立 OpenSpec change 获批并通过代表性 golden 验证后，才可新增对应能力或升级 authority；不得把 POC wireframe 冒充质量验证。

### 6. Manual 与 Semi 使用服务端显示合同隔离

Task mode 决定 display policy，而不是管理员选择相似前端模板。Manual 序列化器、查询和缓存键从类型层面排除 Prediction payload；Semi 在发布时必须绑定冻结 PredictionArtifact。Prediction 由管理员从本地推理输出导入、校验和预览，运行时不调用模型。

V1 只保留已实现且默认关闭的通用 AssistArtifact 状态绑定、feature gate 和审计语义；它不是 A-line 适配器，也不承诺任何外部引擎可直接映射。外部 A-line/Manhattan 的类型不得进入 Revision、canonical schema 或核心领域服务。待外部合同稳定后，独立 OpenSpec change 必须重新决定是否通过平台边界适配器映射、版本化扩展或替换该休眠合同；任何外部结果默认只能形成独立 Artifact，不能回写 Revision。V1 不创建适配器、专家输出模型或占位字段。任何复用 Label Studio 开源实现前必须逐文件核验许可证、保留必要声明，并用平台领域接口包裹；不得复制其通用数据模型作为 canonical schema。

### 7. Active time 使用租约事件而非客户端累计秒数

前端用活动状态机把可见性、焦点、允许交互、15 秒 idle 和 30 秒 heartbeat 转成有序 ActivityEvent。客户端单调时钟用于测量间隔，服务器接收时间用于排序与异常判断；服务器按 active lease、session、sequence 和 event_id 去重、封顶和派生。

ActivityEvent 不保存 pointer 坐标或键盘内容。多标签页由浏览器协调加服务器 `active_workspace_token/active_lease` 双重限制。时间首先归属于 Assignment + DraftCycle，再派生 initial/revision/rework/unsubmitted 指标。付款保持平台外按图片结算。

### 8. 离线采用 IndexedDB patch queue，不做离线正式提交

前端保存已加载任务的当前 DraftState、未确认 patch、ActivityEvent 和 Undo 栈。恢复连接时只有 workspace token 有效、批次开放且服务器 draft_version 与 base 相同才自动同步；否则保留只读 recovery copy，禁止自动 merge 或 last-write-wins。认证令牌不进 localStorage，媒体缓存不成为正式草稿数据。

离线提交被禁用，因为服务器无法确认批次、权限、Prediction hash 和并发 Revision。未来正式 geometry authority 如进入提交门槛，再由对应 amendment 明确其离线边界。未来桌面端仍复用同一同步与冲突合同。

### 9. 通过 PostgreSQL Job/Outbox 实现近实时分析

Revision 提交事务同时写入唯一 Job/Outbox 记录。独立轻量 worker 进程使用数据库抢占和幂等键处理 SubmissionAssessment、TaskAggregation、WorkerEvidence/Profile materialization、MetricSnapshot 和 Export。普通 Web 请求不执行 bootstrap、校准规划、批量 SHA、完整导出或备份验证。

首版不引入 Redis/Celery；定时维护使用 management command + systemd timer/cron + PostgreSQL advisory lock。Job 保存状态、attempt、input hash、code version、错误和产物 manifest。若未来吞吐实测超出数据库队列能力，再通过独立 change 引入消息中间件。

### 10. Post-POC TaskAggregation 按 Scope、Portal、Geometry 与 Evidence 分组件推进

每个 TaskAggregate 维护 eligible input manifest、scope/portal/geometry/evidence state、k、主/次簇支持、margin、terminal state 和 policy version。处理顺序固定：

1. 去重到每名工人最新未反馈有效 Revision；
2. 处置结构无效与外部系统错误；
3. 分别聚合 scope、portal、geometry 与 evidence；
4. 由冻结 ScopePolicy 判断 representation evidence 是否允许自动 close；
5. geometry/portal resolved 时分别指向真实 medoid Revision；只有 Task 已冻结并明确启用 target/partition/closure policy 后，才另行生成 DerivedTargetArtifact；
6. needs_more 时生成 `consensus_addition` AssignmentProposal；
7. k_max 后仍不稳定则 unresolved review。

单个 scope observation 不会使 geometry 或 portal evidence 整份失效。只有政策允许的 representation reason、支持充分且不存在有效结构冲突时才可自动 close；其他情况进入追加、抽检或裁决。默认 3→逐一追加→5 是批次初始配置，不是全局硬编码。多人一致仍可能共同犯错，因此保留 anchor、LOO、分层探索和随机抽检入口。

首个运行规则固定为 `consensus-policy-v2` + `canonical-2d-consensus-v1`。Geometry 仅比较结构有效且非空的 canonical 2D point pairs：忽略跨工人的稳定 ID，保留 top/bottom 语义，按循环顺序允许起点平移和方向反转，并以全景周期水平差计算所有对应点的最大轴向偏差。Pair 数不同视为不可比较；`partial` 的有效几何可以参与，空几何不冒充 geometry evidence。Portal 独立比较：portal 数量与类型一致、四角一对一匹配、双方存在的 host edge 必须映射到对应 pair；evidence status 留在 evidence 组件，不混入位置距离。

首版 geometry 与 portal 的最大轴向偏差均为 `0.02`，聚类采用 complete-link（同簇任意两份都必须相似），主簇最少支持 3，且相对第二簇 margin 最少为 2。`3:0`、`3:1` 可稳定，`2:1` 继续追加，`3:2` 在上限时 unresolved。medoid 是簇内总距离最小的真实 Revision，并以提交时间、Revision ID 作确定性并列裁决；Geometry 与 Portal 各自保存 medoid，禁止把不同工人的组件平均或拼成合成 Revision。上述数值是 WorkBatch 冻结字段而非全局常量；默认 `k_max=5`，管理员可在新批次发布前调整，历史批次不变。

### 11. Post-POC Calibration 与 Profile 使用版本化证据和不可变快照

WorkBatch/Assignment 显式冻结 prescreen、calibration、production、probe、consensus addition、rework 或 adjudication 用途。新工人先通过 Trap PreScreen；CalibrationCampaign 按 CalibrationPolicy 组合 common anchor、diverse bridge 和画像缺口对应的 precision probe。Exposure ledger 阻止政策禁止的重复 Trap；probe 选择只使用分配前已冻结的 TaskFeatureVector、政策和画像，不读取该 Assignment 的未来结果。

SubmissionAssessment 先产生即时过程/结构信号；准确性证据等待排除该工人的稳定 scope/portal/geometry reference、anchor、独立 probe 或裁决。WorkerEvidence 明确标记 verified/provisional/proxy 与 lineage：verified 才能正式改变能力排序，provisional 主要触发补测或弱收缩调整，proxy 只用于探索/风险提示。支持不足时组件 adjustment 为零，单次失败不永久降级。

OnlineWorkerProfileSnapshot 是不可变物化视图。它使用版本化 component code 保存 base axes 与有限条件组件的 estimate、interval、任务/建筑及 ordinary/stress support、evidence tier、适用域、来源 campaign、有效期、last evidence time 和 drift status；不为尚未证实的未来组件提前增加固定数据库列。Active time、IP、模型一致和单次警告只作为上下文/代理信号。反馈后返工单列，工人看不到分数或 tier。

### 12. Post-POC TaskFeatureVector 与 Routing Run/Proposal/Manifest 分层冻结

用于首次路由的 TaskFeatureVector 必须在 Assignment 前存在，按版本表达难度、拓扑、遮挡、纹理/反射、seam、portal、vertical/wall-axis/ceiling 和模式风险，并区分 expert、model proxy 与 historical audit 来源。后续 Revision 可生成供未来建议使用的新 feature 版本，但不得回填为原 Assignment 的决策输入。

RoutingRun 冻结 assignment purpose、TaskFeatureVector、候选集、ProfileSnapshot、使用的 profile component、可用性、容量、重复暴露、exploration/exploitation、selection probability 或确定性规则、政策版本和 seed；生成 AssignmentProposal；管理员批准或覆盖后生成 AssignmentManifest 和 Assignment。V1 只开放 manual/assisted，adaptive_auto 为关闭的枚举和 feature gate。

排序目标不是“高分工人做所有题”，而是能力—难度匹配：verified 且支持充分的能力主要影响兼容难题/争议排序；稳定略低者更多承担简单明确任务；新人通过 PreScreen、Calibration 和可验证简单任务建立证据。progression/drift probe 防止永久锁层，临时限制必须能通过新证据恢复。批次内算法/阈值固定，画像滚动只影响未分配建议；规则变化生成新版本和明确生效边界。

### 13. 管理分析分成实时运营计数与按需 Snapshot

运营页读取增量计数/Job 状态，不运行昂贵统计。管理员按钮创建不可变 MetricSnapshot，输入只含 cutoff 前 Revision 和版本 manifest，输出 support、缺失/not-evaluable、画像、共识和运营统计。相同输入可缓存，旧 Snapshot 不随新数据变化。

AuditRun 只允许注册类型，读取冻结 hash，输出不可变 Artifact，不能编辑标注。BatchExportSnapshot 输出全部身份、Revision、Review、Consensus、Media/Schema/Version 引用，并附 latest/selected convenience view；不宣称 Final Gold。

### 14. 部署、网络和恢复保持轻量

参考部署为一个小型应用运行环境（可为单机容器或等价托管运行时）运行 Web 与一个后台 worker，外加托管 PostgreSQL 和私有广州 COS。前端静态资源可由同一应用或静态托管提供；首版 REST + 短轮询足够，不要求 WebSocket。应用带宽不承载图片，实际 CPU/内存/带宽规格通过预发布负载测试确定，不在规范中猜测固定值。

全球工人首版直连广州 COS，采集匿名化地区性能指标后再决定 CDN/全球加速。数据库每日备份 30 天，关键操作前额外备份/导出；恢复测试验证关系记录和 COS 引用。部署使用向前兼容数据库迁移，先扩展 schema、部署兼容代码、再启用功能；回滚不删除新 Revision 或审计数据。

### 15. 安全、隐私和可观测性按风险最小化

后端统一执行对象级权限；密码不可回读；签名 URL 最小对象范围和短 TTL；日志不输出密码、令牌、签名参数、完整 scope `other` 私密文本或媒体内容。生产日志使用 request/job/event 关联 ID，记录 API 错误率、保存冲突、签名失败、Job 延迟、事件积压、3D 客户端错误和 COS 性能。

Supabase 仅作为 Django 直连的托管 PostgreSQL，不是浏览器数据访问层。生产项目关闭 Data API 或从 exposed schema 移除 `public`；`anon`、`authenticated`、`service_role` 和 `PUBLIC` 不持有 Django 业务表、序列或函数权限，迁移 owner 的 default privileges 同步收紧。所有 PL/pgSQL 触发器函数固定 `search_path=pg_catalog, public`，且 `public` schema 不允许非 owner 创建对象。Django migration graph 是唯一 DDL authority；若未来启用 Supabase Data API，必须用独立 schema、最小 GRANT、RLS 和对象级越权 E2E 另行验收，不在现有 `public` schema 上临时放权。

`data-notice-v1` 由服务端固定中英文 copy 和五类最小收集范围；工人必须保存当前版本的幂等确认后才能取得或写入工作区，版本变化后重新确认。确认请求不接收 IP 或设备字段；IP/必要设备安全数据只进入独立短期安全日志且不进质量画像。高影响管理员操作进入不可变 AuditEvent。

## 已确认决策覆盖矩阵

| Grill 已确认主题 | 正式规范域 | 设计落点 |
|---|---|---|
| 与当前论文/LS 完全隔离，未来单独转换 | `platform-boundaries` | 1、3、6、14 |
| 浏览器首版，Chrome/Edge；桌面端未来 WebView/单实例 | `platform-boundaries`、`identity-access` | 5、8、14 |
| 工人只编辑 2D/元标签，保存 point pair、portal、scope observation、顺序和 seam | `annotation-contract` | 3、5 |
| POC wireframe 本地只读、动作结束后刷新、绑定当前 hash，但不参与提交门槛 | `preview-validation` | 3、5 |
| 外部 geometry/Manhattan authority 不属于 V1；合同稳定后须经独立 change 与 golden 验收 | `preview-validation`、`prediction-assist` | 5、6 |
| img_v 优先、高清切换、规范化坐标、COS 直连无 CDN | `media-ingestion-delivery` | 2、3、14 |
| 首版只收已拼接全景，不引入不确定预处理参数 | `media-ingestion-delivery` | 2 |
| 管理员可视化幂等导入，不手写 JSON，Task ID 不回收 | `media-ingestion-delivery`、`task-batch-assignment` | 2 |
| 未来工人本地包只挂载已分配媒体 | `media-ingestion-delivery`、`platform-boundaries` | 2、8 |
| Manual 网络层完全无 prediction；Semi 读取本地导入冻结 Artifact | `prediction-assist` | 6 |
| 领域 ID 绑定人/任务/Assignment/Revision，不以 LS ID 为真源 | `identity-access`、`task-batch-assignment` | 2、3 |
| 无公共注册、临时密码强制修改、管理员不可见原密码 | `identity-access` | 15 |
| 单活动工作区、显式接管、多标签页保护、未来桌面仍有服务端锁 | `identity-access`、`activity-offline` | 7、8、15 |
| Assignment 按工人批次稳定排序；Skip 只作 deferred，完成后续项后必须回访；真正无法继续单独报告阻断 | `task-batch-assignment`、`draft-revision-review` | 4 |
| Draft 可变、Revision/Review/Adjudication 不可变且状态分离 | `draft-revision-review`、`task-batch-assignment` | 4 |
| Scope 三态 observation、固定 reason 与 attempt 独立；正式 evidence、暂时跳过与阻断报告三者分离 | `annotation-contract`、`task-batch-assignment` | 3、10 |
| Portal 保存物理观察；partition/closure policy 派生目标且虚拟边不污染 physical wall | `annotation-contract`、`adaptive-consensus-routing` | 3、10 |
| Scope observation 被裁定后可要求返工且不暴露他人几何/portal | `draft-revision-review` | 4、11 |
| 元标签 schema/copy/locale 版本化，字段未来可小概率新增 | `annotation-contract`、`platform-boundaries` | 3、15 |
| active time 不是页面时长，断网计时，服务端推导，不用于工资 | `activity-offline` | 7、8 |
| 不永久记录普通移动轨迹，必要 Assist/警告/指导事件保留 | `draft-revision-review`、`activity-offline` | 4、7、15 |
| 每次提交近实时分析，动态追加，稳定后停止，无需逐张人工审计 | `adaptive-consensus-routing` | 9、10、11 |
| scope/portal/geometry/evidence 分组件；LOO 回算画像；medoid 不平均角点 | `adaptive-consensus-routing` | 10、11 |
| PreScreen、CalibrationCampaign、anchor/bridge/probe 与 evidence tier | `task-batch-assignment`、`adaptive-consensus-routing` | 11、12 |
| 低能力稳定者多做简单题，高能力者多做难题，保留晋级探索 | `adaptive-consensus-routing` | 12 |
| manual/assisted 首版，adaptive_auto 后续，管理员覆盖可审计 | `task-batch-assignment`、`adaptive-consensus-routing` | 12 |
| 管理员按钮计算不可变当前快照，实时仅运营轻指标 | `admin-audit-export` | 13 |
| GuidanceEvent 仅向目标工人投递/展示并由其确认；微信/Upwork 可指导，但平台不提供回复线程、聊天或支付 | `admin-audit-export` | 13、15 |
| 当前审计流未来原生重做，不运行任意论文脚本 | `prediction-assist`、`admin-audit-export` | 6、13 |
| 外部 A-line/Manhattan 不与 V1 耦合；未来是否适配由独立 change 决定 | `prediction-assist`、`platform-boundaries` | 6 |
| 批次导出不等于 Final Gold；Dataset/Model Release 延后 | `admin-audit-export`、`platform-boundaries` | 13 |
| 小团队备份基线，不做企业级热备 | `admin-audit-export` | 14 |
| 中英文、UTC/本地时区、全球工人和最小隐私告知 | `platform-boundaries` | 14、15 |

## Risks / Trade-offs

- [广州 COS 对远距离工人延迟较高] → 压缩图优先、高清后台加载、记录地区聚合性能；依据实测通过未来 change 决定 CDN，而不在首版引入混乱配置。
- [多人共识可能共同出错] → 使用 LOO、锚点、分层探索、随机抽检和 unresolved 复核；不把稳定共识称为绝对 GT。
- [在线画像形成自我强化路由] → 保存 uncertainty/support，批次内政策固定，保留跨难度探测与候选重放，首版只生成建议。
- [Trap 重复暴露或结果驱动选 probe 污染校准] → 保存 exposure ledger，probe 只读取 Assignment 前冻结输入，重复暴露规则随 CalibrationPolicy 固定。
- [Portal、Scope 和 eligibility 被混成同一真值] → canonical 只保存工人 physical observation，TaskAggregation 分组件，eligibility/closure 由版本化 policy 派生。
- [POC wireframe 被误认为论文级几何验证] → 固定 `engine_version=poc-wireframe-v1` 与 `authority=informational`，提交事务忽略其成功或失败；正式 authority 必须经后续 amendment 和 golden 验证。
- [默认 3→5 增加标注成本] → 数值为批次配置，稳定即停；低风险批次可在证据支持下用新政策版本调整，不能无限追加。
- [数据库 Job 队列发生积压] → 指标监控、幂等重试、advisory lock 和独立 worker；超过实测门槛再引入消息中间件。
- [浏览器崩溃或多设备覆盖草稿] → 服务端 autosave + IndexedDB patch + 乐观并发 + 单工作区接管；冲突不自动 merge。
- [Post-POC 浏览器本地 WebGL 差异影响正式 3D] → 在 geometry authority 获确认后建立 Chrome/Edge 支持矩阵、设备预检、固定 engine、状态 hash 和回归图形测试；POC wireframe 不依赖 WebGL。
- [复用开源代码引入许可证或模型耦合] → 逐文件许可证审查和来源清单；优先复用理念/算法接口，不复制 Label Studio canonical 模型。
- [平台范围一次过大] → tasks 采用垂直切片与阶段 gate；CAD、桌面端、外部 A-line/Manhattan 和 Release 均移出 V1 change。
- [隐私与画像引发误用] → 工人不见排名，时间/IP不作质量真值，管理员界面显示支持和不确定性，数据告知版本化。

## Migration Plan

1. 在独立仓库建立测试、开发环境和空数据库，不读取 HOHONET/LS 运行时。
2. 先实现身份、领域模型、权限和不可变 ID 基础，再在首个 CurrentDraft/Revision 前完成 scope/portal canonical 合同；当前没有旧 AnnotationRevision 需要迁移或追溯改写。
3. 以少量非论文生产数据完成 Manual 在线 POC 的浏览器、信息性 wireframe、Submission/Revision 验收；离线与 active time 可继续按后续切片补齐。
4. 增加 Prediction、component-level aggregation、PreScreen/Calibration、画像和 assisted routing，先 shadow 输出再允许管理员采用建议。
5. 完成管理员 Snapshot、Audit、Export、备份恢复和全球访问试测后才宣布 V1 可用于新重标批次。
6. 当前论文实验始终留在 Label Studio；不存在生产数据回填或双写迁移。

回滚策略：应用版本可回退到仍兼容已扩展 schema 的前一版本；新 Revision、ActivityEvent、AuditEvent 和 Artifact 不删除。功能开关可关闭 Prediction、Consensus 建议或路由建议，但不得修改历史对象。破坏性 schema 收缩不在 V1 正常部署路径中。

## Open Questions

以下不是未决产品语义，而是实施前通过测试/试点确定并版本化的参数：

- ScopePolicy 新增自动处置 reason 的试点白名单。Manhattan、正式 3D 与由正式 geometry authority 定义的 pair 质量阈值不在 V1 内确定；它们必须等待外部合同稳定后的独立 change。`canonical-2d-consensus-v1` 的运营相似度参数已在本 amendment 中确认，不得被解释为正式几何质量门槛。
- 不同数据集/模式的 TaskFeatureVector、CalibrationPolicy 支持/不确定性门槛和分层探索配额初值。
- COS 全球性能验收阈值以及是否需要未来 CDN change。
- 具体托管运行环境与预发布负载测试后所需 CPU、内存、数据库连接和应用带宽。
- 任何 Label Studio 代码复用前的许可证、来源和最小必要范围审查。

这些参数不得通过实现者静默决定；必须以版本化配置、测试证据和管理员可见说明进入首个实施 change 的验收记录。
