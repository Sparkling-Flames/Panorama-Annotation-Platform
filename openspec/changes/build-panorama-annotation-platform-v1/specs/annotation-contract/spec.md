## ADDED Requirements

### Requirement: 规范状态只包含 2D 几何和元标签
工人可编辑的 canonical AnnotationState SHALL 仅包含相机所在房间的 2D 顶/底角点对、角点对顺序、seam anchor 和版本化元标签。墙面、单一 pair x、BEV、3D 网格、质量统计和模型诊断均 MUST 作为派生物，不得回写替代 canonical 状态。

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
系统 SHALL 使用平台领域专用、管理员不可在运行时任意搭建的 MetaSchema。首版字段至少包括所有模式的 `scope` 与 `difficulty`，以及仅 Semi 模式的 `model_issue`。Revision MUST 保存稳定选项代码、`schema_version`、各语言 `copy_version` 和提交 locale，不得把界面显示文本作为字段值。

#### Scenario: 仅修改展示文案
- **WHEN** 管理员发布不改变语义、必填性或选项代码的中英文文案修订
- **THEN** 系统增加对应 `copy_version`，旧 Revision 继续绑定旧文案版本而不改变 schema_version

#### Scenario: 新增元标签选项
- **WHEN** 极少数情况下需要新增选项或改变字段含义
- **THEN** 系统发布新的 schema_version，新 Task 才可使用，旧 Task 和 Revision 的解释保持冻结

#### Scenario: Manual Task 请求 model_issue
- **WHEN** Manual Task 的客户端尝试提交 `model_issue`
- **THEN** 服务器拒绝该未授权字段，且 Manual 界面不显示该字段

### Requirement: Scope 仅区分 in_scope 与 oos
首版 `scope` MUST 只使用稳定代码 `in_scope | oos`，不得强制工人选择 OOS 语义子类型。系统 MAY 保存文字原因，但不得从文字原因自动生成未确认的 OOS 分类。

#### Scenario: 工人无法精确归类 OOS
- **WHEN** 工人确认图片为 OOS 但无法可靠判定具体类型
- **THEN** 系统允许其使用统一 `oos` scope，并通过 OOS 证据合同说明实际观察

### Requirement: OOS 必须包含认真尝试证据
OOS Revision MUST 包含非空 `oos_reason_text` 和 `geometry_attempt_status`，后者仅允许 `best_effort_complete | partial | not_drawable`，表示完成程度而非 OOS 子类型。系统不得设置最低用时硬门槛。

#### Scenario: best_effort_complete OOS
- **WHEN** 工人选择 `best_effort_complete`
- **THEN** 系统要求其提交有序角点对并通过与该几何适用的 3D 和结构检查，同时仍把正式 scope 保存为 OOS

#### Scenario: partial OOS
- **WHEN** 工人只能确定部分角点并选择 `partial`
- **THEN** 系统保存可确定的点对和顺序，但不要求伪造闭合布局，也不把部分几何当作正式 in-scope geometry

#### Scenario: not_drawable OOS
- **WHEN** 工人选择 `not_drawable`
- **THEN** 系统要求具体文字原因，但不强迫其创建虚假角点或 3D 布局

#### Scenario: OOS 原因缺失
- **WHEN** 工人尝试提交 OOS 且原因为空或只有空白字符
- **THEN** 系统硬阻止提交并定位到缺失字段

### Requirement: 服务器验证 canonical 数据
服务器 MUST 独立验证坐标有限性、范围、稳定 ID 唯一性、pair 完整性、order_index 连续性、seam anchor 引用、scope 与元标签 schema。客户端验证通过不得替代服务器验证。

#### Scenario: 客户端绕过字段校验
- **WHEN** 客户端直接提交重复 point_id 或不连续 order_index
- **THEN** 服务器拒绝创建 Revision，返回稳定错误代码和可定位的 pair/字段信息
