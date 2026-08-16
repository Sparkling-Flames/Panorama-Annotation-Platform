## ADDED Requirements

### Requirement: 每次提交触发近实时评估
每个新 AnnotationRevision SHALL 异步触发一个版本化 SubmissionAssessment，立即计算当前已注册规则可用的 canonical 结构、scope/portal/geometry evidence、过程完整性和共识增量。外部 Manhattan 或正式 geometry 结论不属于 V1；只有未来独立 OpenSpec change 明确批准对应 authority、输入输出合同和注册方式后才能加入 Assessment，且不得用占位算法伪造。Revision 的提交成功不得依赖分析任务同步完成；失败的分析 MUST 可幂等重试并使任务显示 `verification_pending`。

#### Scenario: 分析服务暂时失败
- **WHEN** Revision 已经原子提交但增量分析任务失败
- **THEN** 工人可继续处理其他任务，系统保留 Revision、标记 verification_pending 并自动重试相同输入哈希

#### Scenario: 相同分析任务重试
- **WHEN** 同一 Revision、规则版本和输入哈希被重复处理
- **THEN** 系统复用或返回同一 Assessment，不重复更新画像证据

### Requirement: Scope observation 是证据而非多数真值
TaskAggregation MUST 把 `worker_scope_observation` 当作 task-level evidence，并由 WorkBatch 冻结的 ScopePolicy 判断能否自动 close。只有政策明确允许自动处置的 `representation_oos` reason、支持充分且不存在有效 geometry/portal 冲突时，系统才可自动 close；`needs_scope_review`、理由不一致或证据冲突 MUST 进入追加或抽检，不得用简单多数宣称最终真值。

#### Scenario: representation_oos 证据满足自动处置政策
- **WHEN** 独立 Revision 对政策允许的 representation reason 达到支持门槛，且不存在有效 geometry 或 portal 冲突
- **THEN** 系统生成绑定 ScopePolicy 和输入 manifest 的 TaskEligibilityArtifact，并保留每份 worker observation

#### Scenario: scope 与有效结构证据冲突
- **WHEN** `representation_oos` observation 与可用 geometry 或 portal evidence 形成实质冲突
- **THEN** 系统状态为 `needs_more`，达到上限后仍冲突则进入 unresolved 复核队列

### Requirement: Scope、Portal、Geometry 与 Evidence 分组件聚合
TaskAggregation SHALL 分别生成 scope、portal、geometry 和 evidence aggregation；单个或不稳定的 scope observation MUST NOT 导致有效 geometry、portal 或 partial attempt 被丢弃。最终目标 eligibility 和 protocol closure SHALL 从冻结的 target/partition/closure policies 派生，不得改写 physical observations。

#### Scenario: representation_oos Revision 含有效 portal
- **WHEN** 工人提交 `representation_oos`，同时提供可用墙体部分几何和直接可见 portal
- **THEN** scope aggregation 保留该 observation，portal/geometry aggregation 仍纳入对应组件，系统不整份丢弃 Revision

#### Scenario: 派生 enclosed target
- **WHEN** Task 已冻结并明确启用 target/partition/closure policy，且聚合后的 portal 按该 policy 需要形成封闭目标
- **THEN** 系统生成 `physical=false` 的 DerivedTargetArtifact，并保留 source portal 和政策版本

#### Scenario: 派生政策尚未启用
- **WHEN** Task 没有冻结完整的 target/partition/closure policy
- **THEN** 系统保留物理 Portal 共识但不生成占位 DerivedTargetArtifact，也不猜测虚拟闭合边

### Requirement: 动态追加并在稳定后停止
每个 WorkBatch SHALL 固定一个版本化 ConsensusPolicy，包含 `k_initial`、逐次追加步长、`k_max`、组件相似度版本/阈值、主簇最低支持、cluster margin 和异常升级规则。`consensus-policy-v2` 的默认值 SHALL 为 `k_initial=3`、每次追加 1 名、`k_max=5`、主簇最低支持 3、margin 最低 2；管理员可在新批次发布前调整 `k_max`，发布后冻结。旧 `consensus-policy-v1` MUST 保持原解释且不得用于 2D geometry/portal 自动 resolved；系统不得无限追加。

#### Scenario: 初始三份标注已稳定
- **WHEN** 三名独立工人的合格 Revision 已形成唯一、支持充分的主结果
- **THEN** 系统停止提出追加 Assignment 并标记 resolved

#### Scenario: 达到上限仍多峰
- **WHEN** 已达到该批次 k_max 且 geometry 或 scope 仍为稳定多峰或支持不足
- **THEN** 系统标记 unresolved 并进入人工复核，不继续消耗工人容量

### Requirement: Geometry 共识选择真实 medoid Revision
可比较的 geometry aggregation SHALL 使用冻结版本的几何相似度寻找最大内部一致簇，并检查主簇支持、次簇支持、margin、结构有效性和多峰状态。resolved 时 TaskConsensusArtifact MUST 指向主簇内一份真实、不可变的 medoid Revision，不得平均角点制造合成标注；scope eligibility 由独立 Artifact 表达。

`canonical-2d-consensus-v1` SHALL 只比较 canonical 2D observation，不产生正式 3D、Manhattan 或绝对正确性结论。Geometry 匹配忽略跨工人的 pair/point ID，保留 top/bottom 语义，按循环 pair 顺序允许起点平移和方向反转；水平 `u` 使用周期距离，pair 数不同不可比较。任一对应 `u/v` 最大轴向偏差不超过 `0.02` 才相似。Portal SHALL 与 geometry 分组件聚合：portal 数量和类型一致、四角可一对一匹配且最大轴向偏差不超过 `0.02`；双方都有 host edge 时必须映射到相应 pair，evidence status 由 evidence 组件处理。聚类 MUST 使用 complete-link，主簇支持至少 3 且相对第二簇 margin 至少 2；Geometry 与 Portal 分别保存真实 medoid Revision，并以提交时间、Revision ID 确定性解决并列。

#### Scenario: 唯一主簇稳定
- **WHEN** 最大几何簇满足支持和 margin 门槛且无禁止性结构错误
- **THEN** 系统生成绑定聚合规则版本、输入 Revision 列表和 medoid revision_id 的 TaskConsensusArtifact

#### Scenario: 五份输入形成 3:2 多峰
- **WHEN** `k_max=5` 且两个 complete-link 簇支持分别为 3 与 2
- **THEN** 主簇 margin 仅为 1，系统标记 unresolved，不以简单多数生成 resolved 结论

#### Scenario: Geometry 与 Portal 的代表来源不同
- **WHEN** Geometry 与 Portal 各自形成稳定主簇且各自的 medoid 来自不同 Revision
- **THEN** TaskConsensusArtifact 分别保存两个真实 medoid 指针，不平均或拼接为不存在的合成 Revision

#### Scenario: 管理员后来裁决
- **WHEN** 管理员创建 AdjudicatedRevision 替代自动 medoid 作为交付结果
- **THEN** 系统保留原 TaskConsensusArtifact，并通过独立交付指针记录裁决来源和时间

### Requirement: 每名工人只贡献一份独立证据
共识计算 MUST 对每名工人最多使用最新一份有效、未暴露外部反馈的 Revision。结构性不可用、外部系统事故待处置、反馈后返工和重复 Assignment 不得作为新的独立投票；其原始记录仍须保留。

#### Scenario: 工人在反馈前主动修订两次
- **WHEN** 同一工人存在多份未暴露外部反馈的有效 Revision
- **THEN** 当前共识只使用其最新一份，旧 Revision 保留但不增加独立 support

### Requirement: PreScreen 与 Calibration 暴露可审计且不重复 Trap
本要求及后续 Calibration/Profile/Routing 要求属于 post-POC 11.x，不得阻塞 Manual POC 的管理员直接分配。该能力启用后，新工人进入受该政策管理的生产批次前 SHALL 经过版本化 Trap PreScreen 和 admission 决定。CalibrationCampaign SHALL 组合 common anchor、diverse bridge 与按画像缺口选择的 precision probe；平台 MUST 使用 exposure ledger 阻止同一工人重复接触政策禁止复用的 Trap，并不得根据该工人已经看到的答案或当前结果选择后续 probe。

#### Scenario: 工人重复进入 PreScreen
- **WHEN** 候选 Trap 已在该工人的 exposure ledger 中且政策禁止重复
- **THEN** 系统排除该 Trap、记录排除理由并选择未暴露候选，不把重复答案当作独立 admission evidence

#### Scenario: 某能力组件支持不足
- **WHEN** 当前画像显示 portal type 组件未达到 minimum support
- **THEN** CalibrationPlanner 可生成相应 precision probe proposal，但只引用 Assignment 前已冻结的 feature 和 policy

### Requirement: CalibrationPolicy 冻结停止、回退与探测规则
每个 CalibrationCampaign MUST 固定 CalibrationPolicy，至少定义目标 profile component、各组件 minimum support、target uncertainty、block composition、anchor/bridge/exploration share、maximum blocks、stop rule、fallback rule、repeat exposure rule 和 drift probe interval。政策达到 stop rule 后 MUST 停止继续消耗校准任务；不足或异常时按明确 fallback 处置。

#### Scenario: 校准已满足支持和不确定性目标
- **WHEN** 所有目标组件达到冻结的 minimum support 和 target uncertainty
- **THEN** 系统停止生成新的 calibration block，并记录满足的政策版本和证据 cutoff

#### Scenario: 达到 maximum blocks 仍证据不足
- **WHEN** 工人在最大 block 数后仍未满足某组件门槛
- **THEN** 系统应用冻结 fallback rule，保留高不确定性状态且不伪造 qualified 结果

### Requirement: Evidence tier 的升级来源受限
WorkerEvidence MUST 标记 `verified | provisional | proxy` 及来源。只有稳定 anchor、排除本人的 LOO reference、独立 probe 或管理员 adjudication 可把对应 evidence 升级为 verified；provisional 主要用于补测或受收缩的弱调整，proxy 只能用于探索和风险提示。支持不足时对应正式 routing adjustment MUST 为零，单次失败不得造成永久降级。

#### Scenario: 只有模型一致 proxy
- **WHEN** 工人与模型输出高度一致但不存在 anchor、LOO、probe 或裁决参考
- **THEN** 系统只创建 proxy evidence，不提高 verified component 或正式生产 eligibility

#### Scenario: 独立 LOO 后升级
- **WHEN** 后续独立 Revision 使排除该工人的 reference 稳定
- **THEN** 系统幂等生成 verified evidence 和新 ProfileSnapshot，旧 provisional evidence 与 lineage 保留

### Requirement: LOO 共识形成质量证据
评价某工人时，系统 MUST 使用排除该工人后的稳定 scope/portal/geometry reference、独立 anchor/probe 或管理员裁决作为质量参考。reference 尚未稳定时，该工人的质量证据 SHALL 为 pending；后续 Task resolved 后系统自动回算，不要求管理员逐张审核。

#### Scenario: 第一名工人先提交
- **WHEN** 尚无足够独立 Revision 形成排除该工人的稳定参考
- **THEN** 系统立即记录结构和过程信号，但将准确性证据保持 pending

#### Scenario: 后续追加形成稳定共识
- **WHEN** 足够独立标注使该工人的 LOO 参考变为稳定
- **THEN** 系统幂等生成质量证据并更新新的 OnlineWorkerProfileSnapshot

### Requirement: 画像区分证据类型和适用范围
OnlineWorkerProfileSnapshot SHALL 是不可变、可追溯的物化视图，保存 base axes、版本化 conditional component、estimate/interval、任务与建筑 support、ordinary/stress support、evidence tier、适用域、来源 campaign、有效期、last evidence time 和 drift status。首版组件至少能表达总体质量、结构可靠性、portal detection/type、scope representation、undercoverage/overextension、corner topology、困难遮挡和 Manual/Semi correction；组件代码由 profile schema 版本解释，不得硬耦合为不可扩展列或强制压成一个总分。Active time、IP、模型一致、单次警告或样本量不足不得直接等同于正确率。

#### Scenario: 工人与模型高度一致但无独立参考
- **WHEN** Semi Revision 与 PredictionArtifact 接近但尚无 LOO 共识或裁决
- **THEN** 系统只记录模型一致代理信号，不提高已验证质量分量

#### Scenario: 新工人样本少
- **WHEN** 工人只有少量有效证据
- **THEN** 画像显示高不确定性并应用收缩，不把证据不足直接标成低质量

#### Scenario: 新增画像组件
- **WHEN** 后续研究确认新的条件能力组件
- **THEN** 系统发布新 profile schema/component code 和新快照，旧快照仍按原版本解释且不原地覆盖

### Requirement: 路由采用能力—难度匹配
AssignmentProposal SHALL 以交付质量为首要目标，使用 Assignment 前冻结的 TaskFeatureVector 和 ProfileSnapshot 综合任务难度/风险、工人 verified 能力及不确定性、模式经验、可用性、容量、重复暴露和批次约束。高能力且证据充分的工人主要承担困难、高风险和共识不稳定任务；质量略低但稳定的工人更多承担简单明确任务；新工人先接受 PreScreen、Calibration 或简单且可验证的生产任务。provisional/proxy 只能按 CalibrationPolicy 的限制影响探索或补测。

#### Scenario: 困难高风险任务
- **WHEN** 系统为高风险 Task 生成候选排序
- **THEN** 建议优先选择在兼容证据域中质量和结构可靠性支持充分的可用工人，并说明匹配原因

#### Scenario: 稳定但能力较低的工人
- **WHEN** 工人在当前域表现稳定但质量估计低于困难任务门槛
- **THEN** 系统更多建议其承担简单任务，并继续使用共识验证保证交付质量

### Requirement: 路由保留分层探索和晋级路径
系统 SHALL 保留可配置的少量 progression/drift probe 和探索 Assignment，用于检测工人进步、画像漂移和任务选择偏差。连续稳定后可逐步提高建议难度，表现下降时只能按政策临时限制并触发复测；不得把工人永久锁定在一个层级，也不得笼统随机分配而忽略质量风险。

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
批次发布后，该 WorkBatch 已启用并实际引用的 RoutingPolicy、ConsensusPolicy、ScopePolicy、CalibrationPolicy、阈值和算法版本 SHALL 固定；未启用的 post-POC policy 不得以空壳占位成为 POC 发布前置。新提交只能产生新的证据、画像快照和建议。改变规则 MUST 创建新政策版本并明确生效边界。新画像不得追溯改派已正式分配的任务，只影响后续 AssignmentProposal。

#### Scenario: 提交后画像改善
- **WHEN** 新 Revision 使工人画像更新
- **THEN** 尚未分配任务的新建议可引用新 snapshot，已分配 Assignment 保持原 worker 和决策证据

### Requirement: 路由决策可重放
每次 RoutingRun、AssignmentProposal 和批准后的 AssignmentManifest MUST 保存 assignment purpose、exploration/exploitation 标记、候选集、排除原因、排序、selection probability 或确定性选择规则、建议理由、使用的 profile components、ProfileSnapshot、TaskFeatureVector、政策版本、固定随机种子、容量前后值和管理员决定。相同冻结输入 SHALL 可重放得到相同建议；画像更新只能影响尚未正式分配的建议。

#### Scenario: 审计一次辅助分配
- **WHEN** 管理员查看历史 Assignment 的来源
- **THEN** 系统能展示当时可用候选、推荐顺序、能力—难度匹配理由及人工覆盖，而不是只显示最终工人
