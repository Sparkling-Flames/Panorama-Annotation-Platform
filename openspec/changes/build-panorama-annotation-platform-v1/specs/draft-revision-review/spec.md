## ADDED Requirements

### Requirement: 每个编辑周期只有一个可变 CurrentDraft
Assignment 的每个 DraftCycle SHALL 至多存在一个服务器端 CurrentDraft。系统 SHALL 在几何或元标签变化后进行防抖自动保存，并向工人显示 `未保存 | 保存中 | 已保存 | 离线 | 冲突` 状态。CurrentDraft 可变，但任何已提交 AnnotationRevision MUST 不可变。

#### Scenario: 正常自动保存
- **WHEN** 工人完成一次编辑且防抖窗口结束
- **THEN** 系统以乐观并发版本保存 CurrentDraft，并在服务器确认后显示已保存

#### Scenario: 旧客户端覆盖新草稿
- **WHEN** 客户端基于过期 draft_version 尝试保存
- **THEN** 系统拒绝静默覆盖、返回冲突，并保留本地恢复副本

### Requirement: 首次提交冻结 Revision
工人在首次提交前 SHALL 直接编辑初始 CurrentDraft；首次正式提交 MUST 以原子事务冻结 `Revision 1`，保存完整 canonical 状态、状态哈希、Assignment/Worker/Task 标识、版本和提交时间。提交成功后不得修改该 Revision。

#### Scenario: 首次提交成功
- **WHEN** 当前 Draft 通过服务器 canonical 与业务事务校验；信息性 POC wireframe 无论成功、失败或缺失都不构成提交门槛
- **THEN** 系统创建不可变 Revision 1、关联初始 DraftCycle，并返回明确提交结果

### Requirement: 提交请求幂等
每次提交 MUST 携带幂等键和预期 Draft 状态哈希。相同幂等键的重试 SHALL 返回同一 Revision；不同内容不得复用幂等键，双击或网络重试不得产生重复 Revision。

#### Scenario: 提交响应丢失后重试
- **WHEN** 服务器已创建 Revision 但客户端未收到响应并以相同幂等键重试
- **THEN** 系统返回原 Revision，不创建 Revision 2

### Requirement: 批次开放期间允许新增修订
至少有一次提交且 WorkBatch 仍开放时，工人 SHALL 能从最新允许的 Revision 创建新的 CurrentDraft 并提交后续不可变 Revision。旧 Revision、旧复核结果和旧活动时间不得被覆盖。

#### Scenario: 工人主动修正先前标注
- **WHEN** 工人在未收到外部反馈且批次仍开放时选择修订最新 Revision
- **THEN** 系统复制其 canonical 状态到新的 DraftCycle，提交后创建递增 Revision 并将其标为新的未复核提交

### Requirement: Undo/Redo 是客户端草稿能力
编辑器 SHALL 为 2D 几何、顺序和元标签提供 Undo/Redo，并在 IndexedDB 保存当前设备的未同步命令历史。服务器不得永久保存普通点移动轨迹；跨设备恢复只保证恢复最新 CurrentDraft，不保证恢复完整历史 Undo 栈。

#### Scenario: 换设备继续编辑
- **WHEN** 工人在另一设备接管并打开已有 CurrentDraft
- **THEN** 系统恢复服务器确认的几何和元标签，但明确不承诺恢复原设备的全部 Undo/Redo 命令

### Requirement: 复核绑定具体 Revision
每个 ReviewRecord MUST 指向一个不可变 Revision，并保存管理员 reviewer、`accepted | changes_requested` 结果、原因、时间和规则版本。ReviewRecord MUST 采用追加式不可变记录；管理员改判时创建引用上一记录的新 ReviewRecord，不得覆盖历史。`changes_requested` MUST 包含非空原因并把 Assignment 的当前复核投影设为 `changes_requested`、队列设为 `needs_revisit`，但完整 ReworkRequest、期限和 feedback exposure 仍由独立返工合同创建。管理员不得以工人身份修改 Revision；管理员直接修正 canonical 时 MUST 创建独立、归因于管理员的 AdjudicatedRevision，保存完整状态、状态哈希、原因、规则版本和全部来源 Revision。Task 级交付指针 MUST 显式且互斥地指向一个工人 Revision 或 AdjudicatedRevision；新工人 Revision 不得自动移动既有交付指针。首版 ReviewRecord 评价整份 Revision，分组件评价留给后续聚合合同。

#### Scenario: 管理员接受或要求修改
- **WHEN** 管理员对一份工人 Revision 提交 `accepted` 或带非空原因的 `changes_requested`
- **THEN** 系统追加不可变 ReviewRecord；接受时投影为 accepted，要求修改时保留 submitted 事实并把 Assignment 置为 `needs_revisit`，且不修改旧 Revision

#### Scenario: 管理员改判复核结果
- **WHEN** 管理员需要纠正一份已有 ReviewRecord 的结果
- **THEN** 系统创建引用被替代记录的新 ReviewRecord，以最新记录投影当前复核状态，并永久保留旧记录

#### Scenario: 管理员裁决几何
- **WHEN** 管理员需要把交付结果改为不同于任何工人 Revision 的几何
- **THEN** 系统创建归因于管理员的 AdjudicatedRevision、保留全部来源 Revision，并使 Task 级交付指针明确指向裁决结果

#### Scenario: 工人提交新 Revision
- **WHEN** 已复核 Assignment 出现新的工人 Revision
- **THEN** 新 Revision 初始为 unreviewed，旧 ReviewRecord 继续只约束旧 Revision，既有交付指针保持不变直到管理员重新选择

### Requirement: Scope 观察被裁决后支持选择性返工
管理员或冻结政策把 Task 最终裁定为 annotatable 后 SHALL 能对曾提交 `needs_scope_review` 或 `representation_oos` 的工人创建可选 ReworkRequest。原 Revision MUST 永久保留；返工只告知最终处置和必要文字指导，不得暴露其他工人的正式几何或 portal。

#### Scenario: 工人完成 Scope 返工
- **WHEN** 工人接受 ReworkRequest 并重新提交 annotatable 几何
- **THEN** 系统创建新的 feedback-exposed Revision，分别记录返工时间和结果，且不删除初始 scope observation evidence

#### Scenario: 管理员从平台创建 Scope-only 返工
- **WHEN** 管理员在复核队列中选择一份 `needs_scope_review` 或 `representation_oos` Revision，确认最终处置为 annotatable，并提交截止时间和必要文字指导
- **THEN** 系统在同一事务内追加 `changes_requested` ReviewRecord、创建只把该源 Revision 的 Scope 改为 annotatable 且原样保留其 geometry/portal 的 AdjudicatedRevision，并创建 ReworkRequest；该操作不得接收客户端 geometry/portal，不得创建或移动 TaskDeliverySelection，任一步失败不得留下部分记录

#### Scenario: 返工逾期
- **WHEN** ReworkRequest 超过期限仍未提交
- **THEN** 系统标记 `rework_overdue` 并保留原始证据，不伪造完成 Revision

### Requirement: 反馈暴露与独立证据分离
系统 MUST 保存 `initial_submission_revision`、`review_feedback_exposed_at`、feedback-exposed Revision 和最终 AdjudicatedRevision 的关系。用于独立共识和初始能力画像时，每名工人最多使用最新一份未暴露外部反馈的有效 Revision；反馈后返工只能进入返工表现和交付分析。

#### Scenario: 返工结果与初始质量统计
- **WHEN** 工人根据管理员 annotatable 提示完成质量很高的返工
- **THEN** 系统可改善其返工表现指标，但不得回写抹去初始 scope observation 或将返工作为独立初始质量证据

### Requirement: 只永久记录必要过程证据
服务器 SHALL 永久保存提交、scope/portal evidence、BlockReport 及管理员处置、Review、Adjudication、管理员操作、指导暴露以及当前已注册 Assist 的 Apply/Ignore/Undo 等必要事件；普通暂时跳过只需保存当前 `queue_state=deferred`，不得为每次重复点击建立终局 Skip 记录。外部专家工具事件的保留合同必须由其未来独立 OpenSpec change 定义。系统不得永久保存普通鼠标轨迹、每次点移动或键盘内容。

#### Scenario: 工人连续拖动角点
- **WHEN** 工人在一次编辑动作中产生大量 pointer move
- **THEN** 服务器只保存最终草稿状态和用于计时的粗粒度交互，不保存完整移动轨迹
