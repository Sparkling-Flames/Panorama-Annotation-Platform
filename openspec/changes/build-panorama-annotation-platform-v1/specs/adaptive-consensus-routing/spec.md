## ADDED Requirements

### Requirement: 每次提交触发近实时评估
每个新 AnnotationRevision SHALL 异步触发一个版本化 SubmissionAssessment，立即计算可用的结构、Manhattan、OOS、过程完整性和共识增量。Revision 的提交成功不得依赖分析任务同步完成；失败的分析 MUST 可幂等重试并使任务显示 `verification_pending`。

#### Scenario: 分析服务暂时失败
- **WHEN** Revision 已经原子提交但增量分析任务失败
- **THEN** 工人可继续处理其他任务，系统保留 Revision、标记 verification_pending 并自动重试相同输入哈希

#### Scenario: 相同分析任务重试
- **WHEN** 同一 Revision、规则版本和输入哈希被重复处理
- **THEN** 系统复用或返回同一 Assessment，不重复更新画像证据

### Requirement: 共识先判断 scope 再判断 geometry
Adaptive Consensus Verification MUST 先基于独立、合格 Revision 判断 `in_scope | oos` 共识；只有稳定为 in_scope 时才运行 geometry 聚类。OOS 与合法几何冲突时不得把 OOS 当成普通几何簇，也不得用简单单票多数在支持不足时强行裁定。

#### Scenario: scope 稳定为 in_scope
- **WHEN** 独立 Revision 达到版本化 scope 支持和稳定门槛且主结果为 in_scope
- **THEN** 系统进入 geometry 聚类并保留每个 OOS 异议作为画像和复核证据

#### Scenario: scope 稳定为 oos
- **WHEN** 独立 OOS Revision 达到最低支持、原因和尝试状态均完整且不存在超阈值冲突
- **THEN** 系统可自动形成 OOS TaskConsensusArtifact，而无需管理员逐张审计

#### Scenario: OOS 与有效几何冲突
- **WHEN** scope 证据不稳定或 OOS 与 in-scope 几何形成实质冲突
- **THEN** 系统状态为 needs_more，达到上限后仍冲突则进入 unresolved 复核队列

### Requirement: 动态追加并在稳定后停止
每个 WorkBatch SHALL 固定一个版本化 ConsensusPolicy，包含 `k_initial`、逐次追加步长、`k_max`、scope/geometry 稳定门槛和异常升级规则。高质量重标的默认建议值 SHALL 为 `k_initial=3`、每次追加 1 名、`k_max=5`，管理员可在批次发布前配置；系统不得无限追加。

#### Scenario: 初始三份标注已稳定
- **WHEN** 三名独立工人的合格 Revision 已形成唯一、支持充分的主结果
- **THEN** 系统停止提出追加 Assignment 并标记 resolved

#### Scenario: 达到上限仍多峰
- **WHEN** 已达到该批次 k_max 且 geometry 或 scope 仍为稳定多峰或支持不足
- **THEN** 系统标记 unresolved 并进入人工复核，不继续消耗工人容量

### Requirement: Geometry 共识选择真实 medoid Revision
in-scope geometry 聚合 SHALL 使用冻结版本的几何相似度寻找最大内部一致簇，并检查主簇支持、次簇支持、margin、结构有效性和多峰状态。resolved 时 TaskConsensusArtifact MUST 指向主簇内一份真实、不可变的 medoid Revision，不得平均角点制造合成标注。

#### Scenario: 唯一主簇稳定
- **WHEN** 最大几何簇满足支持和 margin 门槛且无禁止性结构错误
- **THEN** 系统生成绑定聚合规则版本、输入 Revision 列表和 medoid revision_id 的 TaskConsensusArtifact

#### Scenario: 管理员后来裁决
- **WHEN** 管理员创建 AdjudicatedRevision 替代自动 medoid 作为交付结果
- **THEN** 系统保留原 TaskConsensusArtifact，并通过独立交付指针记录裁决来源和时间

### Requirement: 每名工人只贡献一份独立证据
共识计算 MUST 对每名工人最多使用最新一份有效、未暴露外部反馈的 Revision。结构性不可用、外部系统事故待处置、反馈后返工和重复 Assignment 不得作为新的独立投票；其原始记录仍须保留。

#### Scenario: 工人在反馈前主动修订两次
- **WHEN** 同一工人存在多份未暴露外部反馈的有效 Revision
- **THEN** 当前共识只使用其最新一份，旧 Revision 保留但不增加独立 support

### Requirement: LOO 共识形成质量证据
评价某工人时，系统 MUST 使用排除该工人后的稳定 scope/geometry 共识或独立锚点、管理员裁决作为质量参考。共识尚未稳定时，该工人的质量证据 SHALL 为 pending；后续任务 resolved 后系统自动回算，不要求管理员逐张审核。

#### Scenario: 第一名工人先提交
- **WHEN** 尚无足够独立 Revision 形成排除该工人的稳定参考
- **THEN** 系统立即记录结构和过程信号，但将准确性证据保持 pending

#### Scenario: 后续追加形成稳定共识
- **WHEN** 足够独立标注使该工人的 LOO 参考变为稳定
- **THEN** 系统幂等生成质量证据并更新新的 OnlineWorkerProfileSnapshot

### Requirement: 画像区分证据类型和适用范围
OnlineWorkerProfileSnapshot SHALL 版本化保存支持量、不确定性、证据时间范围、Manual/Semi 模式、数据集或任务域、平台/客户端/几何/模型版本，以及质量、结构可靠性、scope/OOS 判断和反馈后返工表现等分量。系统不得把 active time、模型一致、单次警告或样本量不足直接等同于正确率，也不得强制压成一个不可解释总分。

#### Scenario: 工人与模型高度一致但无独立参考
- **WHEN** Semi Revision 与 PredictionArtifact 接近但尚无 LOO 共识或裁决
- **THEN** 系统只记录模型一致代理信号，不提高已验证质量分量

#### Scenario: 新工人样本少
- **WHEN** 工人只有少量有效证据
- **THEN** 画像显示高不确定性并应用收缩，不把证据不足直接标成低质量

### Requirement: 路由采用能力—难度匹配
AssignmentProposal SHALL 以交付质量为首要目标综合任务难度/风险、工人已验证能力及不确定性、模式经验、可用性、容量、重复暴露和批次约束。高能力且证据充分的工人主要承担困难、高风险和共识不稳定任务；质量略低但稳定的工人更多承担简单明确任务；新工人先接受简单任务与稳定校准任务。

#### Scenario: 困难高风险任务
- **WHEN** 系统为高风险 Task 生成候选排序
- **THEN** 建议优先选择在兼容证据域中质量和结构可靠性支持充分的可用工人，并说明匹配原因

#### Scenario: 稳定但能力较低的工人
- **WHEN** 工人在当前域表现稳定但质量估计低于困难任务门槛
- **THEN** 系统更多建议其承担简单任务，并继续使用共识验证保证交付质量

### Requirement: 路由保留分层探索和晋级路径
系统 SHALL 保留可配置的少量跨难度校准/探索 Assignment，用于检测工人进步、画像漂移和任务选择偏差。连续稳定后可逐步提高建议难度，表现下降时可暂时降低；不得把工人永久锁定在一个层级，也不得笼统随机分配而忽略质量风险。

#### Scenario: 简单任务持续稳定
- **WHEN** 工人在足够支持下连续满足当前难度的质量和结构门槛
- **THEN** 系统可建议少量更高难度校准任务，并在建议中标记 `progression_probe`

#### Scenario: 管理员关闭探索
- **WHEN** 管理员把批次探索配额设为零
- **THEN** 系统允许保存但显示画像固化和选择偏差风险警告，并记录政策版本

### Requirement: 首版路由模式受控
平台 SHALL 建模 `manual | assisted | adaptive_auto` 三种路由模式；首版只开放 manual 和 assisted。adaptive_auto MUST 保持禁用，直到独立 OpenSpec change、验证证据和管理员启用共同满足。

#### Scenario: 首版请求 adaptive_auto
- **WHEN** 管理员尝试为批次启用 adaptive_auto
- **THEN** 系统拒绝激活并提示该能力尚未获批，不静默生成正式 Assignment

### Requirement: 批次内规则固定而画像滚动
批次发布后 RoutingPolicy、ConsensusPolicy、阈值和算法版本 SHALL 固定；新提交只能产生新的画像快照和建议。改变规则 MUST 创建新政策版本并明确生效边界。新画像不得追溯改派已正式分配的任务，只影响后续 AssignmentProposal。

#### Scenario: 提交后画像改善
- **WHEN** 新 Revision 使工人画像更新
- **THEN** 尚未分配任务的新建议可引用新 snapshot，已分配 Assignment 保持原 worker 和决策证据

### Requirement: 路由决策可重放
每次 RoutingRun、AssignmentProposal 和批准后的 AssignmentManifest MUST 保存候选集、排除原因、排序、建议理由、画像 snapshot、任务特征、政策版本、固定随机种子、容量前后值和管理员决定。相同冻结输入 SHALL 可重放得到相同建议。

#### Scenario: 审计一次辅助分配
- **WHEN** 管理员查看历史 Assignment 的来源
- **THEN** 系统能展示当时可用候选、推荐顺序、能力—难度匹配理由及人工覆盖，而不是只显示最终工人
