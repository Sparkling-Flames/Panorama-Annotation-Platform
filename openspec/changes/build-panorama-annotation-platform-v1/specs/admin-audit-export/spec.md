## ADDED Requirements

### Requirement: 管理员查看轻量运营状态
系统 SHALL 向管理员提供低成本的运营计数，包括完成数、当前活跃工人数、保存失败、结构阻断、skip、媒体错误、事件延迟、修订数和 verification_pending 数量。首版不得自动把这些计数转成工人处罚、tier 或任务难度变更。

#### Scenario: 管理员打开运营页
- **WHEN** 管理员访问当前 WorkBatch 的运营视图
- **THEN** 系统显示最近可用的轻量计数及刷新时间，并明确区分 pending、submitted、resolved 和 unresolved

### Requirement: 按钮生成不可变 provisional MetricSnapshot
管理员点击“计算当前情况” SHALL 触发注册、版本化的分析作业并生成不可变 MetricSnapshot。Snapshot MUST 保存 cutoff、包含的 Revision/Assignment 集合或 manifest、规则和代码版本、平台/模型/schema 版本、support、缺失与 not-evaluable 数量、输入 hash、生成时间和状态；不得读取 CurrentDraft。

#### Scenario: 首次计算当前情况
- **WHEN** 管理员对开放批次点击计算
- **THEN** 系统异步生成只包含 cutoff 前已提交 Revision 的 Snapshot，并在完成后展示其完整覆盖信息

#### Scenario: 新提交到达后查看旧 Snapshot
- **WHEN** Snapshot 完成后又出现新 Revision
- **THEN** 旧 Snapshot 不发生变化，管理员必须重新计算才能获得新 cutoff 的结果

#### Scenario: 相同输入重复计算
- **WHEN** 输入 manifest、代码版本和规则版本均与已完成 Snapshot 相同
- **THEN** 系统可复用已有结果并明确指出复用来源

### Requirement: 复核队列由异常和抽检驱动
系统 SHALL 为 unresolved scope/geometry、多峰、严重结构问题、OOS 冲突、反复模板化 OOS 原因、画像漂移、系统错误和批次配置的随机抽检建立可筛选复核队列。稳定 OOS 或稳定几何不得被强制逐张人工复核。

#### Scenario: 达到共识上限仍冲突
- **WHEN** Task 在 k_max 后状态为 unresolved
- **THEN** 系统自动创建带输入 Revision、冲突摘要和规则版本的复核项

#### Scenario: 稳定 OOS 未命中抽检
- **WHEN** Task 达到稳定 OOS 共识且没有其他风险旗标
- **THEN** 系统允许其自动 resolved，不要求管理员先点击接受

### Requirement: 管理员指导与研究证据分离
管理员 SHALL 能记录一次轻量 GuidanceEvent，包含渠道、worker、batch/task/revision、指导类别、简短摘要、时间、管理员和确认状态。平台不实现完整聊天或微信/Upwork API；指导可在线下渠道进行。收到指导后的 Revision MUST 标记 feedback exposure。

#### Scenario: 管理员通过微信指导
- **WHEN** 管理员在线下完成指导并在平台登记
- **THEN** 系统保存最小 GuidanceEvent，工人确认后关联后续 DraftCycle，不复制完整聊天内容

### Requirement: Audit 只消费冻结输入且不可修改标注
系统 SHALL 仅运行注册、版本化、经过测试的 AuditRun 类型。每次 AuditRun MUST 绑定输入 Revision/Artifact hash、规则与代码版本并产生不可变 AuditArtifact；Audit 不得修改 Draft、Revision 或交付指针。

#### Scenario: 审计发现严重问题
- **WHEN** AuditArtifact 标记某 Revision 存在严重问题
- **THEN** 系统创建复核旗标或建议处置，但不直接移动角点或覆盖工人提交

### Requirement: 批次导出保持完整身份和版本
系统 SHALL 生成不可变 BatchExportSnapshot，至少包含 WorkBatch、Asset/MediaVariant 引用及 hash、Task、Assignment、Worker、所有纳入的 AnnotationRevision、元标签、Review/Adjudication、TaskConsensusArtifact、版本和导出时间。系统 SHALL 同时提供方便使用的 latest-submitted/selected-delivery 视图，但不得导出 CurrentDraft 或声称结果是 Final Gold/DatasetRelease。

#### Scenario: 导出开放批次
- **WHEN** 管理员对开放批次创建导出快照
- **THEN** 系统冻结 cutoff 前的已提交数据和 manifest，后续 Revision 不会改变该快照

#### Scenario: 导出平台身份
- **WHEN** 下游读取任一标注记录
- **THEN** 记录可追溯到 worker_id、task_id、assignment_id、revision_id、asset_id 和所用 schema/media/version，而非依赖行号或 Label Studio ID

### Requirement: 平台不处理工资与支付
首版 SHALL 提供提交数、接受数、OOS、skip、返工和时间等独立统计，但不得计算工资、生成付款决定或集成微信/Upwork 支付。管理员在平台外按图片数量结算。

#### Scenario: 管理员查看工人统计
- **WHEN** 管理员打开工人运营摘要
- **THEN** 系统分别显示可核验计数和时间，不显示自动应付金额

### Requirement: 审计日志覆盖高影响操作
账号管理、权限、Task 发布/取消、Assignment 创建/撤销、批次冻结/重开、Review、Adjudication、返工、路由覆盖、Snapshot/Export 和批量删除请求 MUST 产生不可变审计事件，包含操作者、目标、时间、原因和请求关联标识。

#### Scenario: 管理员批量撤销 Assignment
- **WHEN** 管理员确认批量撤销
- **THEN** 系统逐项保存目标和结果，并以同一操作关联标识记录原因

### Requirement: 备份与恢复采用小团队基线
生产数据库 SHALL 至少每日自动备份并保留 30 天；批次关闭、平台升级和批量删除前 MUST 创建额外备份或导出。COS 对象使用不可变名称并配置可恢复窗口。上线前以及备份方法或数据库主版本变化后 MUST 完成全量恢复测试；严重故障目标为下一工作日内恢复，不要求跨区域热备或 24x7 值守。

#### Scenario: 平台升级前
- **WHEN** 管理员启动生产升级
- **THEN** 系统或运维清单验证近期数据库备份和关键导出存在后才继续

#### Scenario: 恢复演练
- **WHEN** 执行规定的恢复测试
- **THEN** 系统验证账号、任务、Revision、事件、Artifact 和媒体引用完整性，并保存测试日期与结果
