## ADDED Requirements

### Requirement: Task 是不可变标注合同
系统 SHALL 为每个 Task 分配稳定、不透明且永不复用的 `task_id`。已发布 Task MUST 固定对该 Task 实际适用的 `asset_id`、标注模式、元标签 schema/copy 版本、显示政策、允许的媒体变体和可选 PredictionArtifact。`target_contract_id`、`partition_policy_id`、`closure_policy_id` 与 TaskFeatureVector 引用只在对应 post-POC 派生目标或辅助路由能力启用前成为必填冻结合同；Manual POC 不得为尚未启用的能力伪造占位引用。任何会改变工人所见、标注意义或目标派生方式的变化 MUST 创建新 Task。

#### Scenario: Manual 改为 Semi
- **WHEN** 管理员希望把已发布 Manual Task 改为 Semi
- **THEN** 系统保留原 Task 并要求创建绑定冻结 PredictionArtifact 的新 Task，不原地改变模式

#### Scenario: 修改显示合同
- **WHEN** 已发布 Task 的必填字段、prediction 暴露或标注规则需要变化
- **THEN** 系统创建具有新 `task_id` 的新 Task 合同，并保留旧 Task 供历史 Revision 解释，不在原 Task 上原地换版本

### Requirement: Task ID 不回收
草稿 Task 在发布前可被删除，但已经分配的 ID SHALL 进入 tombstone 且不得再次使用；已发布 Task 只能取消或由新 Task supersede，并 MUST 保存原因和替代关系。

#### Scenario: 删除误建草稿
- **WHEN** 管理员删除尚未发布且从未分配的 Task 草稿
- **THEN** 系统释放其业务内容但保留已分配标识的 tombstone，不把该 `task_id` 分给未来任务

#### Scenario: 取消已发布任务
- **WHEN** 管理员发现已发布 Task 合同错误
- **THEN** 系统将其取消或 supersede，撤销未完成 Assignment，并要求记录原因及可选 replacement_task_id

### Requirement: 外部来源键与平台标识分离
系统 SHALL 用 `external_task_key`、数据集来源和导入批次字段保存 MP3D、ZInD 或其他外部标识。平台 `task_id`、`assignment_id`、`revision_id` 和 `worker_id` MUST 独立生成，导出时 MUST 显式携带这些关联，且不得把 Label Studio 整数 ID 当作 canonical 主键。

#### Scenario: 导入含旧任务编号的数据
- **WHEN** 管理员导入带 `base_task_id` 或其他来源编号的 Asset
- **THEN** 系统将来源编号保存为外部键并生成新的平台标识，不改变来源键文本

### Requirement: WorkBatch 按顺序支持暂时跳过与强制回访
系统 SHALL 通过 WorkBatch 向一个工人提供多个 Assignment。每个 Assignment MUST 在同一 `WorkBatch + worker` 范围内保存唯一且创建后不可变的 `order_index`。工人可把当前项暂时跳过为 `deferred`，从而继续处理顺序中的后续项；系统不得限制工人只能拥有一个 in-progress Assignment。`needs_revisit` MUST 保留给复核或管理员要求的后续处理，不得作为普通暂时跳过的同义状态。

#### Scenario: 按顺序暂时跳过
- **WHEN** 工人对当前 Assignment 选择 Skip
- **THEN** 系统把该项标为 `deferred`，保存其 Draft 和活动时间，不创建 Revision、不改变为完成状态，并打开 `order_index` 更大的下一项未完成非 deferred Assignment

#### Scenario: 批次末尾返回最早暂缓任务
- **WHEN** 工人已处理完当前项之后所有未完成非 deferred Assignment
- **THEN** 系统把最小 `order_index` 的 deferred Assignment 作为下一项；工人也可在此前手动返回，并恢复其 CurrentDraft 或从最新 Revision 开始新的修订周期

#### Scenario: 暂缓任务不算完成
- **WHEN** 某工人的批次仍存在 `assigned`、`in_progress` 或 `deferred` Assignment
- **THEN** 系统不得把该工人的批次进度标为完成；重复暂时跳过不受次数上限约束，但不能绕过最终回访

### Requirement: Batch 与 Assignment 明确记录工作目的
本要求从 post-POC purpose/exposure-ledger 切片启用后适用；Manual POC 的管理员直接分配不得因这些尚未启用的字段受阻，也不得预填伪造 campaign/profile 引用。启用后 WorkBatch MUST 保存版本化目的 `prescreen | calibration | production | progression_probe | drift_probe | adjudication`；Assignment MUST 保存更细的目的 `trap | common_anchor | bridge | precision_probe | production | consensus_addition | progression_probe | drift_probe | rework`。适用时 Assignment MUST 冻结 `calibration_campaign_id`、`trap_bank_version`、`task_feature_vector_id`、`profile_snapshot_id_at_decision`、`routing_policy_id`、`consensus_policy_id` 和 `calibration_policy_id`，不得从提交结果事后反推分配目的。

#### Scenario: 生产图同时用于画像证据
- **WHEN** 系统把一个 production Assignment 的结果纳入后续 LOO evidence
- **THEN** Assignment 目的仍保持 `production`，证据来源另行记录，系统不得把历史分配改写为 calibration probe

#### Scenario: 生成 precision probe
- **WHEN** CalibrationPolicy 因某个画像组件支持不足而建议 precision probe
- **THEN** 经管理员批准的 Assignment 冻结 campaign、policy、feature 和决策时 profile 引用，并明确保存 `precision_probe`

### Requirement: TaskFeatureVector 在首次路由前冻结并版本化
用于 AssignmentProposal 的 TaskFeatureVector SHALL 在该 Assignment 创建前存在，并保存版本化难度、拓扑/遮挡/纹理/反射、seam/portal/vertical/wall-axis/ceiling 风险、模式风险和 `feature_source`。专家标签、模型 proxy 与历史审计来源 MUST 可区分；feature schema 或值发生不兼容变化时 MUST 创建新版本，且不得把当前工人的提交结果回填为其首次路由依据。

#### Scenario: 提交后发现任务更难
- **WHEN** 当前工人的 Revision 暴露了此前未知的 portal ambiguity
- **THEN** 系统可为未来建议生成带新来源的新 TaskFeatureVector 版本，但原 AssignmentManifest 继续引用分配前版本

### Requirement: Assignment 工作状态与复核状态分离
Assignment SHALL 独立维护 `work_state` 与按 Revision 绑定的 `review_state`。`work_state` 至少支持 `assigned | in_progress | submitted | blocked | revoked`；暂时跳过只改变 `queue_state=deferred`，不得生成独立完成状态。`review_state` 是最新工人 Revision 的投影，至少支持 `unreviewed | accepted | changes_requested | adjudicated | closed`；ReviewRecord 的真实结果只使用 `accepted | changes_requested`，`adjudicated` 与 `closed` 分别由独立裁决和交付/关闭状态派生。复核结果不得改写工人工作进度或旧 Revision。

#### Scenario: 已提交 Revision 要求修改
- **WHEN** 管理员对某 Revision 标记 `changes_requested`
- **THEN** 原 Assignment 仍保留 submitted 事实、原 Revision 不可变，系统把队列投影为 `needs_revisit`；只有后续明确创建 ReworkRequest 或修订周期时才产生可编辑 DraftCycle

### Requirement: 管理员手工分配和辅助建议
Manual POC SHALL 支持管理员直接创建 Assignment；系统生成可解释、可版本化 AssignmentProposal 的辅助分配在 post-POC 11.x 启用。任何系统建议 MUST 经管理员确认后才形成正式 Assignment；完整 V1 不得静默执行 `adaptive_auto`。

#### Scenario: 管理员批准建议
- **WHEN** 管理员审阅候选集、建议工人、理由和容量影响后批准 AssignmentProposal
- **THEN** 系统生成 AssignmentManifest 与正式 Assignment，并记录所用画像和路由政策版本

#### Scenario: 管理员覆盖建议
- **WHEN** 管理员选择不同工人并填写覆盖原因
- **THEN** 系统按管理员选择生成 Assignment，保留原建议和覆盖审计，不回写篡改建议结果

### Requirement: 同一 Task 支持独立复标
系统 SHALL 允许同一 Task 分配给多个互相独立的工人以形成动态共识。同一工人在无返工授权时不得获得同一 Task 的第二个独立 Assignment，且工人不得看到其他人的提交。

#### Scenario: 追加一名独立工人
- **WHEN** 共识状态要求追加且管理员批准新的建议
- **THEN** 系统为尚未接触该 Task 的合格工人创建 Assignment，并保持现有 Revision 隔离

### Requirement: Scope observation、暂时跳过与报告阻断明确分离
`worker_scope_observation` SHALL 是带证据的正式 Revision 内容；Skip SHALL 仅表示批次内暂时跳过并设置 `queue_state=deferred`。真正无法继续的情况 SHALL 创建独立 BlockReport，把 `work_state` 设为 `blocked`，并使用版本化固定原因代码：`technical_failure`、`image_unavailable`、`temporary_worker_issue`、`conflict_of_interest`、`unable_to_complete`、`other`。BlockReport MUST 保留 Draft 和活动时间且不创建完成 Revision；它对当前工人的队列是终态，但不计为已提交 evidence。

#### Scenario: 工人提交 representation_oos
- **WHEN** 工人完成 scope/geometry/portal 证据合同并正式提交 `representation_oos`
- **THEN** 系统创建 AnnotationRevision 并计入已提交 evidence，而不是暂时跳过、阻断报告或最终 Task truth

#### Scenario: 工人报告技术阻断
- **WHEN** 工人选择 `technical_failure` 并提供要求的说明
- **THEN** 系统保留 Draft 和活动时间、创建 BlockReport、不创建完成 Revision，并使该 Assignment 不再出现在工人的普通待办序列

#### Scenario: 管理员处置阻断
- **WHEN** 管理员查看一个 blocked Assignment
- **THEN** 系统要求管理员显式选择重开原 Assignment、为另一工人创建新 Assignment 或终止处置，并保存原因与审计记录；不得把 blocked 冒充 submitted

### Requirement: 批次冻结不自动提交草稿
管理员可关闭或冻结 WorkBatch。冻结后系统 MUST 禁止工人继续写入；已有最新提交 Revision 仍是正式结果，未提交的修订草稿 SHALL 被保存并标为未提交但不得自动转成 Revision，从未提交的 Assignment SHALL 保持 incomplete。管理员可显式重新开放批次或指定 Assignment。

#### Scenario: 批次关闭时存在返工草稿
- **WHEN** 管理员冻结批次且某工人只有未提交的返工草稿
- **THEN** 系统保留该草稿供审计和可能的重新开放，但正式结果仍指向冻结前最新提交 Revision
