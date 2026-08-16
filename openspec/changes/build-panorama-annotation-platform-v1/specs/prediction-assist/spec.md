## ADDED Requirements

### Requirement: Manual 模式在网络层不暴露 Prediction
Manual Task 的显示政策 MUST 固定 `prediction_exposed=false`、`model_issue_enabled=false` 和 `assist_enabled=false`。任何返回 Manual Assignment、Draft 或 Revision 的接口、客户端初始化状态、浏览器缓存和预取响应均不得包含 prediction 坐标、模型风险或 Semi 初始化数据；仅用 CSS 隐藏不符合要求。

#### Scenario: 工人打开 Manual Task
- **WHEN** 客户端请求一个 Manual Assignment
- **THEN** 响应和客户端状态均不含 PredictionArtifact payload、模型坐标、模型风险或 model_issue 字段

#### Scenario: 同一 Asset 同时存在 Semi Task
- **WHEN** 同一 Asset 另有绑定 prediction 的 Semi Task
- **THEN** Manual Assignment 仍不得预取、缓存或复用 Semi Task 的初始化数据

### Requirement: Semi 只加载冻结 PredictionArtifact
Semi Task MUST 绑定一个在发布前验证并冻结的 PredictionArtifact。Artifact SHALL 保存稳定标识、asset_id、canonical 坐标状态、模型名称和版本、checkpoint hash、推理配置及其 hash、artifact hash、创建时间和导入来源；发布后不得原地替换。

#### Scenario: 发布合法 Semi Task
- **WHEN** 管理员选择通过校验且与 Asset/坐标合同匹配的冻结 PredictionArtifact
- **THEN** 系统把该 artifact_id 和 hash 固定到新 Semi Task，并向获授权工人提供相同初始化

#### Scenario: 更换 checkpoint 结果
- **WHEN** 管理员希望使用另一 checkpoint 的预测
- **THEN** 系统创建新的 PredictionArtifact 和 Task 合同，不改变旧 Semi Task 或历史 Revision

### Requirement: Prediction 由管理员导入本地推理输出
首版平台 SHALL 提供导入和叠加预览流程，使管理员能上传或选择本地模型推理输出并将其转换为 canonical PredictionArtifact；管理员无需手工编写平台 JSON。首版不得在任务打开或普通服务器请求中运行云端模型推理。

#### Scenario: 本地预测导入成功
- **WHEN** 管理员导入可解析、哈希完整且与目标 Asset 匹配的本地推理结果
- **THEN** 系统显示 2D 叠加预览和验证摘要，管理员确认后冻结 Artifact

#### Scenario: 平台运行时缺少模型
- **WHEN** 工人打开已发布 Semi Task
- **THEN** 系统直接读取冻结 Artifact，不要求服务器或工人设备安装推理模型

### Requirement: Prediction 错误不得静默降级
Semi Task 的 PredictionArtifact 缺失、哈希错误、坐标不兼容或无法解析时 SHALL 技术阻断该 Task，且不得静默转为 Manual。若管理员需要 Manual 流程，MUST 创建新的 Manual Task ID。

#### Scenario: Artifact 哈希不匹配
- **WHEN** Semi Assignment 加载时发现 Artifact 内容与冻结 hash 不一致
- **THEN** 系统禁止编辑初始化和正式提交，产生可审计技术错误并通知管理员

### Requirement: Assist 候选绑定输入状态
系统 SHALL 保留默认关闭的版本化 AssistArtifact 平台合同，至少包含 assignment_id、base_revision_id、input_state_sha、engine_version、candidate_id 和 candidate_state_sha。候选只有在当前状态哈希仍等于 input_state_sha 时才可 Apply；候选不得自动移动正式点。该合同只定义平台内部的状态竞态与显式应用语义，不是任何外部 A-line/Manhattan 引擎的适配器或输出 schema；未来外部集成必须经独立 OpenSpec change 决定映射、扩展或替换方式。

#### Scenario: 候选返回前状态已改变
- **WHEN** Assist 候选基于状态 A 计算，但客户端当前已经处于状态 B
- **THEN** 系统使候选失效并要求重新计算，不允许 Apply 到状态 B

### Requirement: 首版不集成外部 A-line/Manhattan
Manual 在线 POC SHALL 不提供 Manhattan 诊断、worker-facing A-line 或外部工具适配器。V1 只保留默认关闭的通用 AssistArtifact/事件语义，不冻结外部工具的代码版本、算法、产物 schema 或 rollout。未来启用必须先通过独立 OpenSpec change 重新确认真实合同、权限、验证证据和阶段门槛。

#### Scenario: 普通工人请求未注册外部候选
- **WHEN** 首版普通工人客户端尝试调用 A-line 或其他未注册外部 Assist
- **THEN** 系统拒绝请求，且不返回隐藏候选或自动修改几何

### Requirement: Assist 操作保留不可变证据
未来任何已启用 Assist UI MUST 同时只呈现一个候选，并要求工人显式 Apply 或 Ignore；Apply、Ignore 以及对 Apply 的 Undo SHALL 追加不可变事件，Undo 不得删除 Apply 历史。

#### Scenario: 工人撤销已应用候选
- **WHEN** 工人 Apply 后执行 Undo
- **THEN** canonical DraftState 恢复到相应前态，同时事件流保留原 Apply 和新的逆操作记录

### Requirement: 平台禁止任意分析脚本执行
系统 SHALL 只运行已注册、版本化且通过测试的 prediction、assist 或 audit 类型；不得把 HOHONET 当前论文脚本复制为平台运行时依赖，也不得提供管理员任意上传脚本并执行的能力。

#### Scenario: 管理员提交未注册脚本
- **WHEN** 管理员尝试把任意 Python 文件作为 Audit 或 Assist 运行
- **THEN** 系统拒绝执行并要求使用已注册类型和版本
