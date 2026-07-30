## ADDED Requirements

### Requirement: Task 是不可变标注合同
系统 SHALL 为每个 Task 分配稳定、不透明且永不复用的 `task_id`。已发布 Task MUST 固定 `asset_id`、标注模式、元标签 schema/copy 版本、显示政策、允许的媒体变体和可选 PredictionArtifact；任何会改变工人所见或标注意义的变化 MUST 创建新 Task。

#### Scenario: Manual 改为 Semi
- **WHEN** 管理员希望把已发布 Manual Task 改为 Semi
- **THEN** 系统保留原 Task 并要求创建绑定冻结 PredictionArtifact 的新 Task，不原地改变模式

#### Scenario: 修改显示合同
- **WHEN** 已发布 Task 的必填字段、prediction 暴露或标注规则需要变化
- **THEN** 系统创建新版本 Task 或新的 Task，并保留旧 Task 供历史 Revision 解释

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

### Requirement: WorkBatch 允许多任务并行工作
系统 SHALL 通过 WorkBatch 向一个工人提供多个 Assignment，并允许工人在 `ready`、`deferred` 和 `needs_revisit` 项之间切换。系统不得要求工人完成当前最难任务后才能访问批次中的其他任务，也不得限制工人只能拥有一个 in-progress Assignment。

#### Scenario: 暂缓困难任务
- **WHEN** 工人将当前困难任务标记为 `deferred`
- **THEN** 系统保存其草稿和时间，并允许其打开批次中的另一项任务

#### Scenario: 返回先前任务
- **WHEN** 批次仍开放且工人稍后理解了先前图片的标法
- **THEN** 系统允许其恢复该 Assignment 的当前草稿或从最新 Revision 开始新的修订周期

### Requirement: Assignment 工作状态与复核状态分离
Assignment SHALL 独立维护 `work_state` 与按 Revision 绑定的 `review_state`。`work_state` 至少支持 `assigned | in_progress | submitted | skipped | revoked`；`review_state` 至少支持 `unreviewed | accepted | changes_requested | adjudicated | closed`。复核结果不得改写工人工作进度或旧 Revision。

#### Scenario: 已提交 Revision 要求修改
- **WHEN** 管理员对某 Revision 标记 `changes_requested`
- **THEN** 原 Assignment 仍保留 submitted 事实，系统另建可编辑返工周期且原 Revision 不可变

### Requirement: 管理员手工分配和辅助建议
首版 SHALL 支持管理员直接创建 Assignment，也 SHALL 支持系统生成可解释、可版本化的 AssignmentProposal。任何系统建议 MUST 经管理员确认后才形成正式 Assignment；首版不得静默执行 `adaptive_auto`。

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

### Requirement: OOS 与 Skip 明确分离
`oos` SHALL 是带证据的正式 scope 结果；`skip` SHALL 表示工人未完成标注。Skip 原因 MUST 来自版本化固定代码集：`technical_failure`、`image_unavailable`、`temporary_worker_issue`、`conflict_of_interest`、`unable_to_complete`、`other`，并受批次策略的次数、重新入队和复核规则约束。

#### Scenario: 工人提交 OOS
- **WHEN** 工人完成 OOS 证据合同并正式提交
- **THEN** 系统创建 OOS AnnotationRevision，计入已提交结果而不是 skip

#### Scenario: 工人 Skip 技术故障
- **WHEN** 工人选择 `technical_failure` 并提供要求的说明
- **THEN** 系统保留草稿和时间、记录 skip、不创建完成 Revision，并按批次政策建议重新分配

### Requirement: 批次冻结不自动提交草稿
管理员可关闭或冻结 WorkBatch。冻结后系统 MUST 禁止工人继续写入；已有最新提交 Revision 仍是正式结果，未提交的修订草稿 SHALL 被保存并标为未提交但不得自动转成 Revision，从未提交的 Assignment SHALL 保持 incomplete。管理员可显式重新开放批次或指定 Assignment。

#### Scenario: 批次关闭时存在返工草稿
- **WHEN** 管理员冻结批次且某工人只有未提交的返工草稿
- **THEN** 系统保留该草稿供审计和可能的重新开放，但正式结果仍指向冻结前最新提交 Revision
