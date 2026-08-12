## ADDED Requirements

### Requirement: 规范状态只包含工人提交的 2D 观察和元标签
工人可编辑的 canonical AnnotationState SHALL 仅包含相机所在空间的 2D 顶/底角点对、角点对顺序、seam anchor、PortalObservation、`worker_scope_observation`、`geometry_attempt_status` 和版本化元标签。墙面、单一 pair x、BEV、3D 网格、protocol closure、TaskEligibilityArtifact、质量统计和模型诊断均 MUST 作为派生物，不得回写替代 canonical 状态。

#### Scenario: 工人在 3D 视图操作
- **WHEN** 工人拖动、点击或检查只读 3D 预览
- **THEN** 系统不得由该操作改变任何 canonical 角点或元标签

#### Scenario: 导出墙面
- **WHEN** 导出器需要墙面或 BEV 表示
- **THEN** 系统从指定 Revision 的有序角点对派生结果，并同时保留来源 Revision 与引擎版本

### Requirement: 顶底角点分别保存
每个角点对 SHALL 保存稳定且不可在同一标注生命周期内复用的 `pair_id`、`order_index`、独立 `top.point_id`、`top.u/v`、`bottom.point_id` 和 `bottom.u/v`。系统 MUST 保留 top 与 bottom 的原始横向差异，不得平均成单一 x。

#### Scenario: 顶底点横坐标不同
- **WHEN** 工人提交 top.u 与 bottom.u 略有差异的合法角点对
- **THEN** 系统分别保存两点坐标，并把 pair x 或墙面仅作为带版本的派生值

#### Scenario: Undo 后恢复角点对
- **WHEN** 工人删除角点对后执行 Undo
- **THEN** 编辑器恢复原 `pair_id`、point_id、坐标和顺序，而不是创建语义上不同的新点对

### Requirement: 规范化坐标与 seam 合同
canonical 坐标 SHALL 使用与分辨率无关的规范化 `u/v`：`u` 按全景水平周期解释并归一到 `[0,1)`，`v` 归一到 `[0,1]`。AnnotationState MUST 保存 `seam_anchor_pair_id` 和循环顺序；显示像素坐标只可作为带 MediaVariant 尺寸的派生或审计信息。

#### Scenario: 切换媒体分辨率
- **WHEN** 工人从压缩图切换到高清图
- **THEN** 同一规范化坐标映射到新分辨率且 canonical AnnotationState 不发生变化

#### Scenario: 角点跨越全景 seam
- **WHEN** 有序角点对从接近 `u=1` 循环到接近 `u=0`
- **THEN** 系统依据 seam anchor 保留一个确定的循环顺序，不通过普通数值排序破坏房间拓扑

### Requirement: 角点顺序可在 2D 中显式维护
系统 SHALL 在 2D 编辑器中显示并允许受控调整角点对循环顺序和 seam anchor。顺序变化 MUST 保留稳定 ID、进入 Undo/Redo 并触发新的状态哈希；3D 视图不得改变顺序。

#### Scenario: 工人修正错误顺序
- **WHEN** 工人在 2D 中交换两个相邻角点对的顺序
- **THEN** 系统仅更新 order_index/seam 相关状态、保留点坐标和 ID，并重新验证拓扑

### Requirement: 元标签采用固定版本化 schema
系统 SHALL 使用平台领域专用、管理员不可在运行时任意搭建的 MetaSchema。首个正式版本固定为 `schema_version=annotation-meta-v1`、`copy_version=annotation-meta-copy-v1`，字段包括所有模式的 `worker_scope_observation` 与必填多选 `difficulty`，以及仅 Semi 模式的必填多选 `model_issue`。`difficulty` MUST 只接受稳定代码 `trivial | occlusion | low_texture | seam | reflection | low_quality`，其中 `trivial` 与其他项互斥；`model_issue` MUST 只接受稳定代码 `acceptable | overextend_adjacent | underextend | over_parsing | corner_drift | corner_duplicate | topology_failure | fail`，其中 `acceptable` 与具体问题互斥，并且只描述编辑前的冻结 Prediction。Revision MUST 保存稳定选项代码、`schema_version`、`copy_version` 和提交 locale，不得把界面显示文本作为字段值。一个 copy release SHALL 同时提供语义一致的 `zh-CN` 与 `en` 文案；Task 固定使用的 copy_version，Revision 另存实际提交 locale。

#### Scenario: 仅修改展示文案
- **WHEN** 管理员发布不改变语义、必填性或选项代码的中英文文案修订
- **THEN** 系统增加对应 `copy_version`，旧 Revision 继续绑定旧文案版本而不改变 schema_version

#### Scenario: 新增元标签选项
- **WHEN** 极少数情况下需要新增选项或改变字段含义
- **THEN** 系统发布新的 schema_version，新 Task 才可使用，旧 Task 和 Revision 的解释保持冻结

#### Scenario: 互斥代码被同时提交
- **WHEN** 客户端同时提交 `trivial` 与其他 difficulty，或同时提交 `acceptable` 与具体 model issue
- **THEN** 服务器拒绝该组合并返回稳定字段错误，不能依赖前端隐藏选项

#### Scenario: Manual Task 请求 model_issue
- **WHEN** Manual Task 的客户端尝试提交 `model_issue`
- **THEN** 服务器拒绝该未授权字段，且 Manual 界面不显示该字段

### Requirement: Worker Scope 是三态观察而非最终 Task truth
首版 `worker_scope_observation` MUST 只使用稳定代码 `annotatable | needs_scope_review | representation_oos`。该值表示工人对当前表示能否按合同完成的观察，不得直接覆盖最终 Task eligibility。`needs_scope_review` 或 `representation_oos` MUST 至少选择一个版本化 reason code：`non_manhattan | multi_level_floor | multi_level_ceiling | internal_void | camera_cell_ambiguous | portal_ambiguous | insufficient_evidence | severe_image_artifact | other`；选择 `other` 时 MUST 提供非空说明。

#### Scenario: 工人无法确认表示是否兼容
- **WHEN** 当前图像可能可标，但 portal、camera cell 或隐藏 topology 证据不足
- **THEN** 工人提交 `needs_scope_review` 和对应 reason code，系统保留其几何与 portal 观察且不把该值当作最终 Task truth

#### Scenario: 当前表示不适用
- **WHEN** 工人判断目标表示无法表达当前结构
- **THEN** 工人提交 `representation_oos`、固定 reason code 和已有证据，最终处置仍由版本化 ScopePolicy、共识或裁决派生

### Requirement: Geometry attempt 与 Scope observation 独立
每个 Revision MUST 保存 `geometry_attempt_status`，仅允许 `best_effort_complete | partial | not_drawable`。该字段只表示工人完成几何的程度，不表示最终 eligibility，也不得由 `worker_scope_observation` 自动推断。POC 使用独立的 `geometry_attempt_reason_text` 保存 `not_drawable` 的非空说明，不复用 Scope reason，也不在选项尚未确认时伪造原因枚举；其他 attempt 状态下该字段 MUST 为空。系统不得设置最低用时硬门槛。

#### Scenario: best_effort_complete 非 annotatable 观察
- **WHEN** 工人选择 `best_effort_complete` 且 scope observation 不是 `annotatable`
- **THEN** 系统仍保存其完整有序角点和 portal 证据，并执行适用于该几何的结构检查

#### Scenario: partial 几何
- **WHEN** 工人只能确定部分结构并选择 `partial`
- **THEN** 系统保存可确定的点对、顺序和 portal observation，但不要求伪造闭合布局或把部分几何当作最终 eligible 结果

#### Scenario: not_drawable 几何
- **WHEN** 工人选择 `not_drawable`
- **THEN** POC 要求非空 `geometry_attempt_reason_text`，但不强迫其创建虚假角点、portal 或 3D 布局；未来固定原因选项须经新 schema_version 确认

### Requirement: PortalObservation 保存稳定 2D 几何与证据
每个 PortalObservation SHALL 保存稳定且不可在同一标注生命周期内复用的 `portal_id`、类型 `door | architectural_opening | window | open_connection | unknown`、四个规范化边界点、证据状态 `direct_visible | inferred | ambiguous` 和可选 `host_edge_ref`。左/右 jamb 与 top/bottom 边 SHALL 从四点确定性派生，避免重复 canonical 几何。`architectural_opening` 需要稳定 aperture plane；只有连续 open-plan 且无稳定截面的观察 MUST 使用 `open_connection`，不得仅凭家具、功能或地板变化推断 opening。

#### Scenario: 标注可见门洞
- **WHEN** 工人看到具有稳定截面的 doorway 或 opening
- **THEN** 系统保存稳定 portal ID、四点边界、类型和 evidence status，并以规范化坐标跨媒体变体重放

#### Scenario: 开放平面没有稳定截面
- **WHEN** 相邻区域连续开放且工人无法确定 aperture plane
- **THEN** 系统允许 `open_connection` 或 `unknown` 并保存 `ambiguous` 证据，不强迫伪造 architectural opening

### Requirement: Physical boundary 与派生 protocol closure 分离
PortalObservation SHALL 只描述物理结构观察。是否由 portal 切分目标 cell MUST 由 Task 冻结的 `partition_policy_id` 决定；用于封闭目标的 DerivedTargetArtifact MUST 保存 `target_contract_id`、`partition_policy_id`、`closure_policy_id`、可选 `source_portal_id` 和 `physical=false`。protocol closure MUST NOT 写回 physical-wall canonical ontology。

#### Scenario: 同一 opening 使用不同分区政策
- **WHEN** 两个新 Task 对同一 Asset 采用不同的冻结 partition policy
- **THEN** 两者复用同一物理 portal observation，但分别派生目标 cell；旧 Task 和 Revision 的解释不改变

#### Scenario: 导出虚拟闭合边
- **WHEN** 下游导出 enclosed target 所需的 protocol closure
- **THEN** 导出明确标记 `physical=false` 并追溯 source portal/policy，不把虚拟边混入 physical-wall 训练标签

### Requirement: 工人端精度辅助没有 canonical authority
2D 编辑器 MAY 从当前已授权 MediaVariant 和内存 DraftState 派生点级局部放大、pair 连线、seam 标记以及被动证据或结构提示。所有辅助结果 MUST 是瞬态只读输出，不得新增、删除、吸附、拉直、批量移动、重排或补全 canonical 点。只有工人显式完成 add、delete、move、order 或 seam 操作才可改变 DraftState、`state_sha` 和 Undo/Redo；Manual 不得因辅助层预填 Prediction。点级遮挡/evidence 若未来进入 canonical，MUST 使用新的 schema_version 和新 Task，不能追写当前 Revision。

#### Scenario: 拖动时检查局部细节
- **WHEN** 工人拖动某个 top 或 bottom 点并使用局部放大镜检查当前全景像素
- **THEN** pointer move 只更新瞬态裁剪与坐标反馈，释放指针后才把工人明确选择的位置作为一个可撤销移动提交

#### Scenario: 取消精度辅助检查
- **WHEN** 工人在放大检查期间取消拖动或关闭被动提示
- **THEN** 系统移除瞬态辅助且保持 DraftState、`state_sha`、稳定 ID 和历史栈不变，不自动 snapping 或写回建议点位

### Requirement: 服务器验证 canonical 数据
服务器 MUST 独立验证坐标有限性、范围、稳定 pair/point/portal ID 唯一性、pair 与 portal 完整性、order_index 连续性、seam/host edge 引用、worker scope/reason/attempt 组合及元标签 schema。客户端验证通过不得替代服务器验证。

#### Scenario: 客户端绕过字段校验
- **WHEN** 客户端直接提交重复 point_id 或不连续 order_index
- **THEN** 服务器拒绝创建 Revision，返回稳定错误代码和可定位的 pair/字段信息
