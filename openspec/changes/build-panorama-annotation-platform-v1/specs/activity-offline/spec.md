## ADDED Requirements

### Requirement: Active time 表示真实标注活动
系统 SHALL 仅把页面可见、窗口有焦点且最近存在允许交互的时间计入 active time。首版规则使用 15 秒 idle 阈值和 30 秒 heartbeat；单纯打开页面、后台标签页或超过 idle 的静止时间不得继续累计。

#### Scenario: 页面保持打开但无人操作
- **WHEN** 工人超过 15 秒没有允许交互
- **THEN** 服务器推导的 active interval 在阈值处封顶，直到新的有效交互恢复

#### Scenario: 工人切换到其他应用
- **WHEN** 页面失焦或变为不可见
- **THEN** 客户端立即发送或排队状态事件，服务器不把不可见间隔计为 active time

### Requirement: 服务器从事件区间推导时间
客户端 MUST 上报不可变且可幂等重放的 ActivityEvent，而不得提交可信的累计秒数。事件至少包含 `event_id`、assignment_id、draft_cycle_id、client_session_id、active_lease_id、sequence_no、event_type、client_monotonic_ms、server_received_at、visibility、focus、interaction_type、client_build_sha 和 active_time_rule_version。服务器 SHALL 去重、排序并对超长间隔、休眠、时钟异常和多工作区封顶。

#### Scenario: 重复重放事件
- **WHEN** 网络重试使相同 event_id 被上传多次
- **THEN** 服务器只使用一次该事件，derived active time 不重复增加

#### Scenario: 设备休眠后恢复
- **WHEN** 两个 heartbeat 的单调时钟间隔异常长
- **THEN** 服务器依据规则封顶或切断 interval，并记录可审计的派生原因

### Requirement: 允许的活动类型明确
2D 编辑、角点顺序调整、Undo/Redo、元标签填写、用于判断图片的 zoom/pan 和主动 3D 检查 SHALL 计为允许交互。系统不得通过鼠标微动、后台心跳或自动 3D 重建本身人为延长 active time。

#### Scenario: 工人检查 3D
- **WHEN** 工人在可见且聚焦页面主动旋转或缩放只读 3D 进行检查
- **THEN** 该检查可恢复活动租约并计入对应 DraftCycle

### Requirement: 时间按 Assignment 与 DraftCycle 归属
系统 SHALL 分别保存初次提交周期、各修订周期、反馈后返工周期和未提交周期的 derived active time。提交时当前周期关联该 Revision；批次关闭或放弃后未提交时间仍保留并标记 `unsubmitted`，不得并入初始质量时间。

#### Scenario: 提交后再次修订
- **WHEN** 工人提交 Revision 1 后开始并提交 Revision 2
- **THEN** 系统分别报告 initial time、revision time 和 total time，不把两周期合并为一次初始标注

### Requirement: 已加载任务支持离线编辑和计时
网络断开时，工人 SHALL 能继续编辑已经完整加载的 Assignment。客户端 MUST 在 IndexedDB 保存未确认 Draft patch、必要 DraftState、活动事件和本地 Undo 栈；不得缓存认证令牌或把完整媒体复制进正式草稿数据。

#### Scenario: 标注中途断网
- **WHEN** 工人已加载图片后失去网络
- **THEN** 编辑器进入离线状态、继续保存本地草稿和活动事件，并明确禁用正式提交

#### Scenario: 重新联网且服务器基线未变
- **WHEN** 客户端恢复网络、工作区仍有效且服务器 draft_version 等于本地 base_version
- **THEN** 系统幂等上传事件并同步本地 Draft patch，确认后清理已同步队列

### Requirement: 离线冲突不得自动覆盖
若重新联网时服务器 CurrentDraft、Assignment 状态、批次冻结状态或工作区持有者已变化，系统 MUST 禁止自动合并或覆盖。系统 SHALL 保留本地恢复副本，显示服务器版本与本地版本的时间/基线差异，并要求明确恢复处置。

#### Scenario: 另一设备已接管并修改草稿
- **WHEN** 旧设备离线编辑后恢复连接，而新设备已经提交更新
- **THEN** 旧设备写入被拒绝，本地草稿可导出或供管理员恢复，但不得覆盖服务器状态

### Requirement: Active time 不决定付款或单独判罚
系统 SHALL 把 active time 用于了解标注耗时、运营诊断和带上下文的画像分析；平台不得根据时间自动计算工资，且不得把时间作为唯一作弊、质量或惩罚依据。按图片付款在平台外执行。

#### Scenario: 工人用时异常短
- **WHEN** 某提交 active time 很短但结构与共识证据尚未完成
- **THEN** 系统可产生运营风险信号，但不得自动判错、拒付或降低正式质量分
