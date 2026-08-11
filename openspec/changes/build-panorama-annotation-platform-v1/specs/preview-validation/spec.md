## ADDED Requirements

### Requirement: POC wireframe 在工人设备本地派生且仅供参考
POC SHALL 使用现有浏览器能力从当前内存中的 2D DraftState 本地生成 `poc-wireframe-v1`，不得为此增加专用渲染依赖或向应用服务器上传渲染任务。PreviewResult MUST 绑定 `state_sha`、`engine_version=poc-wireframe-v1` 和 `authority=informational`；预览必须只读，不能生成或修改 canonical 编辑，也不得宣称论文级几何质量。

#### Scenario: 本地生成信息性 wireframe
- **WHEN** 工人完成一次合法几何编辑且设备通过预检
- **THEN** 浏览器从当前 DraftState 生成带当前 state_sha 的信息性 wireframe，不上传渲染任务或修改 2D 点

### Requirement: 只在动作完成后刷新 POC wireframe
系统 SHALL 在 `pointerup`、新增/删除点对、顺序调整、Undo 或 Redo 等离散动作完成后，经短防抖刷新 wireframe；持续拖动的每个 pointer move 不得触发重建。新动作发生时，未完成的旧结果 MUST 被丢弃。

#### Scenario: 持续拖动角点
- **WHEN** 工人尚未释放正在拖动的角点
- **THEN** 系统更新 2D 交互反馈但不反复重建 wireframe

#### Scenario: 旧预览晚于新状态返回
- **WHEN** 状态 A 的异步预览在工人已完成状态 B 后才返回
- **THEN** 系统根据状态哈希丢弃 A，不把旧预览标为当前

### Requirement: POC wireframe 不得成为提交门槛
POC 提交 SHALL 只依赖服务器端 canonical、Assignment、workspace、批次、并发和幂等合同。wireframe 成功、失败、缺失、过期或客户端伪造 MUST NOT 改变 Draft/Revision 的接受结果；`partial` 与 `not_drawable` 仍执行其适用的字段、有限性和引用校验。

#### Scenario: 预览后又移动角点
- **WHEN** 工人在成功预览后修改几何但尚未生成新预览
- **THEN** 界面把旧预览标为过期，但服务器仍只按当前 canonical 与业务合同判断提交

#### Scenario: partial 提交
- **WHEN** 工人按合同保存部分点对或 portal evidence 并选择 `partial`
- **THEN** 系统不要求伪造有效闭合 3D，但仍执行适用于部分状态的字段和有限性校验

### Requirement: POC 只执行确定性的 canonical 校验
系统 MUST 对非有限/越界坐标、缺失或重复 ID、不完整 pair、非法顺序或引用及不满足当前 schema 的状态执行硬阻断。POC wireframe 失败、自交、Manhattan plausibility、角点距离、高度和外观启发式不得冒充已确认的提交门槛。

#### Scenario: 非法 canonical 状态
- **WHEN** 当前状态包含非有限坐标、重复 ID 或坏引用
- **THEN** 系统以稳定错误码阻止提交并定位相关 field、pair_id 或 point_id

#### Scenario: POC wireframe 生成失败
- **WHEN** canonical 状态合法但信息性 wireframe 无法生成
- **THEN** 界面明确显示预览不可用，且不得因此修改点位或拒绝正式提交

### Requirement: 正式 geometry authority 必须在 POC 后单独确认
论文级 geometry engine、Manhattan/A-line 诊断及任何由其驱动的提交门槛 MUST 等待专家工具和算法合同稳定，并通过后续明确确认的 OpenSpec amendment 与代表性 golden 验证后才能启用。正式实现仍不得从派生几何自动回写 canonical 点位；当前 change 的 POC wireframe authority 不得被静默升级。

#### Scenario: 请求把 POC wireframe 作为质量门槛
- **WHEN** 尚未发布经确认和 golden 验证的正式 geometry engine
- **THEN** 系统保持 `authority=informational`，不得依据 wireframe 结果阻止提交或生成质量结论

### Requirement: 校验错误必须定位到具体对象
POC canonical 校验 SHALL 返回稳定错误代码、严重级别以及适用的 field、`pair_id`、`point_id` 或 `portal_id`，工人界面 SHALL 能从消息定位对应 2D 对象，不得只显示无上下文的错误。

#### Scenario: Portal 引用不存在的 host edge
- **WHEN** 某 PortalObservation 引用了当前 canonical 状态中不存在的 host edge
- **THEN** 界面说明涉及的 portal_id 和引用字段并提供定位入口
