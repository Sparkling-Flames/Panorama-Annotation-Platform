## ADDED Requirements

### Requirement: 3D 预览在工人设备本地派生
首版 3D SHALL 在受支持浏览器中使用工人设备的图形能力，从当前内存中的 2D DraftState 本地生成。应用服务器不得为普通交互逐帧渲染 3D，3D 视图必须只读且不能生成 canonical 编辑。

#### Scenario: 本地生成预览
- **WHEN** 工人完成一次合法几何编辑且设备通过预检
- **THEN** 浏览器从当前 DraftState 生成 3D 预览，不上传渲染任务或修改 2D 点

### Requirement: 只在动作完成后刷新 3D
系统 SHALL 在 `pointerup`、新增/删除点对、顺序调整、Undo 或 Redo 等离散动作完成后，经短防抖刷新 3D；持续拖动的每个 pointer move 不得触发正式重建。新动作发生时，未完成的旧重建结果 MUST 被丢弃。

#### Scenario: 持续拖动角点
- **WHEN** 工人尚未释放正在拖动的角点
- **THEN** 系统更新 2D 交互反馈但不反复提交完整 3D 重建

#### Scenario: 旧预览晚于新状态返回
- **WHEN** 状态 A 的异步预览在工人已完成状态 B 后才返回
- **THEN** 系统根据状态哈希丢弃 A，不把旧预览标为当前

### Requirement: In-scope 提交绑定当前预览哈希
每次 PreviewResult MUST 绑定 `state_sha` 和 geometry_engine_version。`in_scope` 正式提交仅在当前 DraftState 已成功产生相同 `state_sha` 的 3D PreviewResult 时允许；OOS 的 `partial` 或 `not_drawable` 不受闭合 3D 门槛约束。

#### Scenario: 预览后又移动角点
- **WHEN** 工人在成功预览后修改几何但尚未生成新预览
- **THEN** 系统阻止 in-scope 提交并要求完成当前状态预览

#### Scenario: OOS partial 提交
- **WHEN** OOS 工人按合同保存部分点对并选择 `partial`
- **THEN** 系统不要求伪造有效闭合 3D，但仍执行适用于部分状态的字段和有限性校验

### Requirement: 结构错误分为硬阻断和软警告
系统 MUST 对非有限/越界坐标、缺失或重复 ID、不完整 pair、顶底方向反转、无法形成基本闭合、明确自交、pair collapse 和当前状态 3D 生成失败执行硬阻断。top/bottom 横向偏差、角点过近、高度异常、过度解析和外观可疑等 SHALL 作为版本化软警告，除非另有已确认 schema 明确升级为硬规则。

#### Scenario: 明确自交
- **WHEN** 当前有序几何形成明确自交
- **THEN** 系统阻止 in-scope 提交，指出涉及的 pair_id 并允许工人定位修正

#### Scenario: top/bottom 横向偏差较大
- **WHEN** 某 pair 超过版本化软警告阈值但仍可形成有效结构
- **THEN** 系统显示定位到该 pair 的警告，但不擅自平均坐标或自动移动角点

### Requirement: 严重 Manhattan 偏差必须提醒
系统 SHALL 对严重不符合版本化 Manhattan 诊断规则的几何显示可定位警告，并在提交前要求工人显式确认已检查。Manhattan plausibility 不得作为普通合法非标准布局的绝对硬阻断，也不得自动修改正式点。

#### Scenario: 工人保留非标准布局
- **WHEN** 严重 Manhattan 警告触发但工人确认该布局应保持现状
- **THEN** 系统记录警告、规则版本、受影响 pair 和确认事件，并允许在其他硬门槛通过后提交

### Requirement: 错误必须定位到具体对象
结构验证和 Manhattan 诊断 SHALL 返回稳定错误代码、严重级别以及相关 `pair_id`/point_id，工人界面 SHALL 能从消息定位对应 2D 对象，不得只显示无上下文的 `Topology invalid`。

#### Scenario: 相邻墙面冲突
- **WHEN** Pair 4 与 Pair 5 导致拓扑冲突
- **THEN** 界面说明涉及 Pair 4/5 并提供定位入口
