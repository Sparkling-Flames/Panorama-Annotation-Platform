> 当前实施前沿：先完成 Manual 在线 POC（真实 Assignment/媒体 → 2D/Scope/Portal → autosave → `poc-wireframe-v1` 信息性预览 → 不可变 Revision → 管理员读取）。PreScreen、Calibration、画像和动态路由为 post-POC；外部 geometry/Manhattan/A-line 集成不属于本 V1 change，必须等待其合同稳定后另开 change，不得提前创建适配器、无调用方模型或提交门槛。

## 1. 实施准入与工程基线

- [x] 1.1 在开始业务代码前重新运行 `openspec validate --all --strict`、读取本 change 全部 artifacts，并在交付记录中确认用户已批准实施；证据为零失败校验和明确的 change 名称。
- [x] 1.2 建立后端、前端、后台 worker 和端到端测试的最小目录/依赖锁定，确保不读取或链接 `D:\Work\HOHONET`；证据为干净安装与隔离检查。
- [x] 1.3 先建立后端测试、前端单元/组件测试和浏览器 E2E 测试入口，再添加空应用启动路径；证据为三类测试命令可执行且基线通过。
- [x] 1.4 配置格式化、静态类型、安全扫描和 CI 门槛，禁止提交密钥、COS 签名参数和认证 token；证据为故意违规夹具能使 CI 失败。
- [x] 1.5 建立 OpenSpec requirement/scenario 到测试 ID 的追踪约定，并为 11 个 capability 生成覆盖清单；证据为缺失 scenario 映射时校验失败。

## 2. 稳定标识、账号与权限切片

- [x] 2.1 【Red】为账号无公共注册、稳定非 PII worker_id、临时密码首次改密、密码不可回读和会话撤销编写失败的 API/模型测试（`identity-access`）。
- [x] 2.2 【Green】实现最小账号、角色、密码重置、禁用与审计模型，使 2.1 测试通过；不得实现管理员查看当前密码。
- [x] 2.3 【Red·策略原语】为对象访问的共同安全语义编写单元测试，覆盖 owner 允许、foreign 与 missing 返回一致的不泄露响应，以及管理员敏感读取审计；本项不作为真实 Assignment、Revision 或媒体端点的场景证据。
- [x] 2.4 【Green·策略原语】实现 2.3 的最小共同响应与审计语义；真实资源必须按领域关系执行查询范围约束，不为复用原语而伪造统一 owner 字段，端点集成在对应后续切片验收。
- [x] 2.5 【Red·控制面】为单 active_workspace_token、显式接管令牌轮换、旧令牌续租失败、同浏览器不同 tab 冲突、租约过期重取及管理员例外编写并发测试；本项不把续租请求称为业务写入。
- [x] 2.6 【Green·控制面】实现工作区 acquire/renew、接管、租约及客户端多页/断网状态，使 2.5 通过；Draft、Revision、Activity 等真实写入及 recovery copy 在对应后续切片验收。（同一标签页刷新复用仅限当前 tab 的非密钥实例标识，真实 Rework E2E 已证明不会被误判为第二标签页；认证 token 仍不进入客户端存储。）
- [x] 2.7 验证 token 不进入 localStorage、账号禁用/改密撤销旧会话、工人接口不返回画像排名；证据为浏览器安全 E2E 与响应 schema 测试。

## 3. Asset、MediaVariant 与 COS 导入交付切片

- [x] 3.1 【Red】为 Asset/MediaVariant 稳定 ID、内容 hash、COS version/长度/CRC64、尺寸/格式/角色、数据库绕过不可变发布和规范化坐标映射编写数据库合同测试（`media-ingestion-delivery`）。（模型、导入、manifest、COS 漂移、映射及原始 SQL/ORM 绕过测试均已落地，场景 ID 已纳入 traceability validator。）
- [x] 3.2 【Green】实现 Asset/MediaVariant 模型、PostgreSQL 不可变约束、可信 manifest 登记和 canonical 映射，使发布后覆盖、非等比普通变体和 hash/version drift 均失败；PostgreSQL 专属证据必须在 PostgreSQL 17 实际运行，不以 SQLite 或 skip 代替。（真实 PostgreSQL 17 已通过发布后字段 UPDATE/DELETE、已登记对象 UPDATE/DELETE、并发幂等导入与完整迁移往返专测。）
- [x] 3.3 【Red】为相同 source key/hash 幂等导入、预览取消不消耗正式 Task 编写服务测试。
- [x] 3.4 【Green】实现仅浏览受信 manifest 已登记 COS 版本的候选配对/预览/发布向导后端与最小 UI，不要求手写 JSON；允许同一 Asset 追加新的不可变同角色 MediaVariant，但禁止同一 source key 改变已登记元数据；运行幂等与重复导入回归。
- [x] 3.5 【Red】为仅接受已拼接等距柱状图、拒绝 skybox 集和不兼容变体编写导入校验测试。
- [x] 3.6 【Green】实现格式/比例/映射校验与稳定错误码，不引入 HOHONET/MP3D/ZInD 预处理运行时。
- [x] 3.7 【Red】基于真实 Assignment→Task→MediaVariant 关系，为不泄露 foreign/missing 响应、绑定精确 COS version 的对象级短期签名、到期前主动续签、压缩图优先、高清无漂移切换、单变体回退和双失败 `image_unavailable` 编写 API/E2E/组件测试；真实 Workspace 测试证明高清切换保留编辑状态，双失败不挂载编辑器和提交入口。
- [x] 3.8 【Green】在真实工人媒体端点按领域关系约束查询并实现绑定已登记 COS version 的私有 COS 直连交付与前端媒体状态机，证明图片字节不经应用服务器且首版不依赖 CDN。
- [x] 3.9 验证工人端没有创建/替换 Asset 的上传路径；以 3.8 的真实端点测试固定 canonical 绑定，不预建无调用方抽象。

## 4. Task、WorkBatch、Assignment 与状态切片

- [x] 4.1 【Red】为 Task 不可变合同、Task ID tombstone/不回收、cancel/supersede、external_task_key 分离，以及显式新轮次复用既有 Asset 但创建新 Task 编写状态与数据库测试（`task-batch-assignment`）。
- [x] 4.2 【Green】实现 Task 生命周期、Manual/Semi 显示合同字段和显式轮次，并接通媒体导入向导的新轮次创建意图，使 4.1 通过。
- [x] 4.3 【Red】为 WorkBatch 多 Assignment、ready/deferred/needs_revisit 切换、不限制单 in-progress、仅访问本人 Assignment、foreign/missing 不泄露及撤销会话拒绝业务请求编写服务/API/E2E 测试。
- [x] 4.4 【Green】实现按工人归属约束的批次/Assignment API 与切换 UI，验证困难任务不会卡住整个批次；CurrentDraft 的读取与恢复留在 6.1/6.2 验收。
- [x] 4.5 【Red】为 work_state/review_state 分离、同 Task 多独立工人、同工人重复暴露阻断编写状态机测试。
- [x] 4.6 【Green】实现 Assignment 与独立暴露约束，重构状态转换为显式领域服务并运行回归。
- [x] 4.7 【Red】为每名工人的 Assignment `order_index`、Skip→`deferred` 且保留 Draft/时间/工作状态、顺序前进、批次末尾回到最早 deferred、仍有 deferred 不算完成，以及 `needs_revisit` 不作为普通 Skip 编写服务/API/组件测试。
- [x] 4.8 【Green】实现按稳定顺序的暂时跳过与强制回访；不设置 Skip 次数上限、不创建终局 Skip 记录。另将真正无法继续实现为独立 BlockReport 与 `work_state=blocked`，由管理员明确重开、换人或终止；不得把 `representation_oos` 或 blocked 冒充 submitted。（当前管理员处置通过服务/API 完成；管理界面归后续管理员工作台。）
- [x] 4.9 【Red/Green】实现批次 freeze/reopen：测试并确保未提交草稿不自动提交、最新 Revision 保持正式、从未提交项仍 incomplete。

## 5. Canonical AnnotationState 与 2D 编辑器切片

- [x] 5.1 【Red】为 point pair 独立 top/bottom 坐标、稳定 pair/point ID、order_index、seam anchor、规范化 u/v 和确定性 state_sha 编写属性/边界测试（`annotation-contract`）。
- [x] 5.2 【Green】实现 canonical schema、序列化、hash 与服务器校验；证明不会平均 top/bottom x，跨 seam 顺序可重放。
- [x] 5.3 【Red】为重复 ID、非有限/越界坐标、不连续 order、坏 seam 引用和非法字段编写服务器拒绝测试。
- [x] 5.4 【Green】实现稳定错误码与 field/pair/point/portal 定位，使恶意客户端不能绕过校验。
- [x] 5.5 【Red】为 `annotation-meta-v1` 的 Difficulty/Model Issue 稳定代码、互斥组合、schema_version/copy_version/locale、中英文语义一致、Manual 禁止 model_issue 和旧版本冻结编写合同测试。
- [x] 5.6 【Green】实现首版固定元标签表单与 `annotation-meta-copy-v1` 中英文 copy；不构建通用表单设计器，元标签选项或语义变化只发布新 schema_version 和新 Task，纯文案变化只发布新 copy_version。
- [x] 5.7 【Red】为 `annotatable | needs_scope_review | representation_oos`、固定 reason codes、独立 `best_effort_complete | partial | not_drawable` 及无最低时间门槛编写组合合同测试。
- [x] 5.8 【Green】实现 worker scope/attempt 编辑与分支校验，证明 observation 不等于最终 eligibility，partial/not_drawable 不会被迫伪造闭合几何。
- [x] 5.9 【Red/Green】实现 2D point pair 增删拖动、顺序/seam 编辑和本地 Undo/Redo；编辑器仅在工作区状态为 editable 时挂载，冲突 tab 不得编辑；组件测试验证稳定 ID 与派生 wall/BEV 不进入 canonical 状态。本项只验收内存组件，真实 Assignment/CurrentDraft 接入和持久化留在 6.1/6.2。
- [x] 5.10 【Red】为稳定 portal ID、四点规范化 geometry、door/architectural_opening/window/open_connection/unknown、evidence status、host edge 引用与跨 seam/媒体重放编写 canonical/编辑器测试。
- [x] 5.11 【Green】实现 POC 最小 PortalObservation 编辑与服务器校验；jamb/top/bottom 由四点派生，不在本切片实现 protocol closure、DerivedTargetArtifact、floor-plan/cell graph 或 BIM。
- [x] 5.12 【Red】为点级局部放大镜编写组件测试：pointer move 只更新瞬态媒体裁剪与坐标，取消不改变 Draft，只有显式 pointerup 产生一次可撤销移动。
- [x] 5.13 【Green】实现无新增依赖的 SVG 点级放大镜并复用现有 pair 连线；不保存裁剪、不吸附、不自动拉直、不联动其他点，也不把辅助元数据写入 canonical。

> V1 边界：point-level occlusion/evidence 未获批，不是本 change 的待办；未来若确认产品语义，必须以独立 change、新 schema_version 和新 Task 进入，不能追写 `annotation-meta-v1` 或旧 Revision。

## 6. Draft、Revision、Review 与返工切片

- [x] 6.1 【Red】为每 DraftCycle 单 CurrentDraft、防抖 autosave、draft_version 乐观并发和保存状态，以及真实 Assignment 归属、session、workspace token、tab_id、未过期租约和接管状态校验编写 API/E2E 测试（`draft-revision-review`）。
- [x] 6.2 【Green】在 CurrentDraft 读写的真实事务边界内实现领域归属与工作区校验，并实现服务器 CurrentDraft 与前端保存状态机；旧客户端写入必须进入冲突而非 last-write-wins。
- [x] 6.3 【Red】为首次提交事务、expected state_sha、幂等键、响应丢失重试不重复建 Revision，以及接管后旧设备提交失败编写并发测试。
- [x] 6.4 【Green】在提交事务内重新校验 Assignment 归属与工作区状态，实现 Revision 冻结服务并证明已提交内容不能 UPDATE/DELETE。（真实 PostgreSQL 17 迁移往返与 Revision 原始 SQL/ORM 不可变专测已通过。）
- [x] 6.5 【Red·POC】为批次开放且尚无外部复核时，从最新 Revision 新建 DraftCycle、提交递增 Revision、保持新提交 `unreviewed`，以及越权/终止状态拒绝编写测试；旧 ReviewRecord 绑定由 6.7/6.8 验收，不为本项伪造 Review。
- [x] 6.6 【Green·POC】实现修订入口、最新 Revision 复制与周期归因，运行 Draft/Revision 回归；ReviewRecord、changes_requested 与裁决仍留在 6.7/6.8。
- [x] 6.7 【Red】为管理员专用且追加式不可变的 ReviewRecord、`accepted | changes_requested`、改判 supersedes、新工人 Revision unreviewed、Task 级互斥交付指针、管理员 AdjudicatedRevision 不冒充工人，以及管理员真实敏感读取审计编写测试；完整 ReworkRequest 留在 6.9/6.10。
- [x] 6.8 【Green】实现管理员整份 Revision 复核、裁决与 TaskDeliverySelection；`changes_requested` 只投影 `needs_revisit` 并保留 submitted，裁决保存完整 canonical/哈希/来源 Revision，新提交不自动移动既有交付指针。
- [x] 6.9 【Red】为 needs_scope_review/representation_oos→annotatable ReworkRequest、通知与返工仅本人可读、只暴露裁定文字不暴露他人几何/portal、feedback_exposed 和 overdue 编写权限/E2E 测试。（后端真实 API、foreign/missing 不泄露、逾期、反馈归因和 worker 通知 UI 单测已完成；Chromium E2E 进一步验证窄 ReworkRequest payload、裁定 geometry/portal 不进入工人 Draft 及 feedback-exposed Revision。）
- [x] 6.10 【Green】实现按 Assignment 归属约束的选择性返工与初始/返工证据分离，并验证普通点移动历史不持久化到服务器。（后端选择性返工、前端入口、active-time 分桶、共识排除和普通 pointer move 不持久化已完成；真实浏览器完成 `representation_oos`→管理员 annotatable 裁定→返工提交，原 Revision 保持不变。）

## 7. 本地信息性预览与验证切片

- [x] 7.1 【Red】为 2D DraftState→`poc-wireframe-v1` 只读预览、`authority=informational`、绑定 `state_sha` 且不回写 canonical 编写最小前端测试（`preview-validation`）。
- [x] 7.2 【Green】使用现有浏览器能力实现无新增依赖的 POC wireframe；它不声明论文级几何质量，也不需要服务端渲染。
- [x] 7.3 【Red】用假时钟测试 pointer move 不重建、pointerup/add/delete/order/Undo/Redo 后防抖刷新，以及旧 `state_sha` 结果被丢弃。
- [x] 7.4 【Green】实现动作完成触发和取消/过期机制，只保留 POC 所需的最小状态机。
- [x] 7.5 【Red】为 wireframe 成功、失败、缺失或伪造均不得改变 canonical 校验和提交结果编写 Draft/Revision 集成测试。
- [x] 7.6 【Green】把信息性 wireframe 挂入真实 Assignment 编辑器；提交事务不读取其成功状态，只在本地 PreviewResult/UI 暴露可追溯 `engine_version`/`authority`，Revision 不持久化这些非权威预览元数据。

> V1 边界：正式 geometry engine、外部 A-line/Manhattan、专家 3D/结构产物及其 golden/authority 均不在本 change 验收范围；未来独立 change 必须基于当时稳定的真实合同重新规划，不能假定当前字段或输出形状。

## 8. Active time 与离线恢复切片

- [x] 8.1 【Red】为 15 秒 idle、30 秒 heartbeat、focus/visibility、允许 interaction type 和 page-open 不计时编写前端状态机假时钟测试（`activity-offline`）。
- [x] 8.2 【Green】实现粗粒度 ActivityEvent 生成和 active lease，不采集 pointer 坐标、按键内容或累计秒数。（本项完成前端状态机与不可变客户端事件信封；`server_received_at`、领域/工作区校验和服务端派生仍由 8.3/8.4 完成，真实 Assignment 页面挂接随 6.1/6.2 完成。）
- [x] 8.3 【Red】为 event_id 幂等、sequence、客户端单调时钟、`client_wall_time_ms`、休眠/超长间隔封顶、跨 session 区间并集去重、异常墙钟保守少算、Task 冻结 v1，以及 Activity 写入的 session、Assignment 归属和有效工作区校验编写后端属性测试；离线持久队列仍由 8.5/8.6 验收。
- [x] 8.4 【Green】在 Activity 接收事务内执行领域归属、工作区与 Task 规则版本校验，实现仅含 `active-time-v1` 的最小版本分派和按 DraftCycle 的跨 session 区间并集，分别输出 initial/revision/rework/unsubmitted time；不预建 v2。
- [x] 8.5 【Red】为 IndexedDB Draft patch/事件/Undo 队列、断网继续编辑、离线提交禁用编写浏览器 E2E。
- [x] 8.6 【Green】实现离线模式与恢复同步；验证 token 不入 localStorage、完整媒体不成为 Draft 数据。
- [x] 8.7 【Red/Green】实现 base_version 未变自动同步和 takeover/freeze/server-change 冲突恢复副本，禁止自动 merge/覆盖；冲突副本保持只读并可导出，不含 token 或媒体内容。
- [x] 8.8 验证 active time 只作为时间/运营上下文，API 和管理员 UI 不生成工资或以时间单独判罚。

## 9. PredictionArtifact 与 Assist 边界切片

- [x] 9.1 【Red】为 Manual Assignment、Draft、Revision 响应和浏览器存储无 prediction/model risk/model_issue/assist payload 编写 schema 与组件缓存测试（`prediction-assist`）；同 Asset Semi 隔离仍由 9.2 在真实 PredictionArtifact 落地后验收。
- [x] 9.2 【Green】实现按 Task mode 分离的序列化与前端状态，证明同 Asset 的 Semi 数据不会污染 Manual。
- [x] 9.3 【Red】为 PredictionArtifact 模型/checkpoint/config/artifact hash、asset/coordinate 匹配、发布冻结和更换 prediction 新 Task 编写测试。
- [x] 9.4 【Green】实现管理员本地推理结果解析、叠加预览、确认冻结；不实现运行时推理。
- [x] 9.5 【Red/Green】实现 Semi 缺失/损坏/不兼容 prediction 技术阻断且绝不静默降级 Manual。
- [x] 9.6 【Red】为 AssistArtifact input_state_sha 竞态、单候选、显式 Apply/Ignore/Undo 事件合同编写纯领域测试。
- [x] 9.7 【Green】实现默认禁用的通用 Assist 数据合同与 feature gate；普通工人请求未注册外部 Assist 必须被拒绝。该休眠合同不是 A-line 适配器，不实现 worker-facing 外部工具 UI 或冻结其输出 schema。
- [x] 9.8 建立第三方/Label Studio 代码复用许可证清单；未完成逐文件审查前不复制代码，证据为来源和许可证检查记录。

## 10. Post-POC：Job、动态共识与异常复核切片

- [x] 10.1 【Red】为 Revision 提交事务写唯一 outbox、worker 抢占、失败重试和 input hash 幂等编写数据库并发测试（`adaptive-consensus-routing`）。
- [x] 10.2 【Green】实现 PostgreSQL Job/Outbox 与独立 worker，不引入 Redis/Celery；暴露积压、失败和 attempt 指标。（Revision 与唯一 AnalysisJob 同事务、`SKIP LOCKED` 唯一抢占、失败重试/幂等、管理员 metrics、独立 Procfile worker 及三队列公平轮询均已通过；真实 PostgreSQL 17 并发专测已通过。）
- [x] 10.3 【Red】为每名工人最新未反馈有效 Revision 去重、无效/外部事故处置、反馈后 Revision 排除，以及 scope/portal/geometry/evidence 分组件 eligible manifest 编写测试。
- [x] 10.4 【Green】实现不可变 SubmissionAssessment、eligible input manifest 和组件级聚合输入，不因单个 scope observation 丢弃有效 geometry/portal。（真实 PostgreSQL 17 迁移往返、触发器 UPDATE/DELETE 拒绝及组件输入专测已通过。）
- [x] 10.5 【Red】为 ScopePolicy 自动处置白名单、representation evidence 支持、needs_scope_review、有效 geometry/portal 冲突、needs_more/unresolved 编写固定与属性测试。
- [x] 10.6 【Green】实现增量 scope evidence 聚合与 TaskEligibilityArtifact；只有冻结政策允许且无有效结构冲突时可自动 close。（真实 PostgreSQL 17 冻结政策、不可变 Artifact 与 worker 集成专测已通过。）
- [x] 10.7 【Red】为 `canonical-2d-consensus-v1` 的周期 geometry/portal complete-link 聚类、唯一主簇、最少 3 支持、margin≥2、3:2 多峰、分组件真实 medoid 指针和禁止平均角点编写测试。
- [x] 10.8 【Green】新增 `consensus-policy-v2` 并保留旧 v1 解释；实现版本化 portal/geometry aggregation 与不可变 TaskConsensusArtifact。默认 `k_max=5` 但继续由新 WorkBatch 发布前配置；管理员 Adjudication 不覆盖旧 Artifact。（真实 PostgreSQL 17 不可变 Artifact 与 Job→Artifact 集成专测已通过。）
- [x] 10.9 【Red/Green】实现批次 ConsensusPolicy：默认 3→逐一追加→5、发布前可配置、稳定停止、达到上限不无限追加；输出 purpose=`consensus_addition` 的 AssignmentProposal 而非首版自动 Assignment。
- [x] 10.10 用故意共同错误、多峰、scope reason 滥用、有效 partial geometry 和结构失败夹具验证边界，确保稳定多数不被测试伪装成绝对 GT。
- [ ] 10.11 【政策后置】仅在 Task 冻结且明确启用 target/partition/closure policy 后，为 `physical=false` DerivedTargetArtifact 编写测试并实现 source portal/政策谱系；未确认政策时不得生成占位 Artifact。

## 11. Post-POC：PreScreen、Calibration、画像与辅助路由切片

- [ ] 11.1 【Red】为 WorkBatch/Assignment purpose、适用政策/campaign/profile/feature 引用和禁止事后改写 purpose 编写模型与服务测试。
- [ ] 11.2 【Green】实现显式 purpose 与 exposure ledger，使 production、consensus addition、Trap、anchor/bridge/probe、rework 和 adjudication 可审计且禁止政策不允许的重复 Trap。
- [ ] 11.3 【Red/Green】实现 Assignment 前冻结的版本化 TaskFeatureVector，区分 expert/model proxy/historical audit 来源；测试当前工人的提交不能回填为其首次路由输入。
- [ ] 11.4 【Red】为 Trap PreScreen/admission、重复 Trap 暴露阻断和失败/不足证据不伪造准入编写政策与 exposure ledger 测试。
- [ ] 11.5 【Green】实现最小 PreScreen 与 CalibrationCampaign 状态机，只生成建议/结果，不把 Trap 答案或工人分数暴露给工人。
- [ ] 11.6 【Red】为 CalibrationPolicy 的 common anchor、diverse bridge、precision/progression/drift probe、minimum support、target uncertainty、maximum blocks、stop/fallback 和 repeat exposure rule 编写测试。
- [ ] 11.7 【Green】实现按画像缺口生成校准 block 的 CalibrationPlanner；probe 只能使用 Assignment 前冻结输入，达到 stop rule 后停止追加。
- [ ] 11.8 【Red】为 verified/provisional/proxy 来源、排除本人后的 scope/portal/geometry LOO、pending→resolved 回算、anchor/probe/裁决升级编写证据测试。
- [ ] 11.9 【Green】实现不可变 WorkerEvidence 与 lineage，保证重复分析不重复计数，proxy/provisional 不冒充 verified。
- [ ] 11.10 【Red】为版本化 base axes/conditional components、estimate/interval、任务/建筑与 ordinary/stress support、Manual/Semi/域/版本、drift 和返工分量编写 ProfileSnapshot schema 测试。
- [ ] 11.11 【Green】实现不可变、分层收缩的 OnlineWorkerProfileSnapshot；active time、IP、模型一致和单次警告不得进入 verified accuracy，support 不足时正式 adjustment 为零。
- [ ] 11.12 【Red】为 TaskFeatureVector×verified component 能力—难度匹配、新人校准、progression/drift probe、临时限制恢复和 exploration=0 警告编写路由政策测试。
- [ ] 11.13 【Green】实现能力—难度与分层探索建议，工人端不暴露分数/tier；provisional/proxy 仅按政策触发补测或弱调整。
- [ ] 11.14 【Red】为 RoutingRun→AssignmentProposal→管理员批准/覆盖→AssignmentManifest、assignment purpose、exploration/exploitation、候选集、选择概率/规则、profile components、feature/policy、固定 seed 和容量编写可重放测试。
- [ ] 11.15 【Green】实现 manual/assisted UI 与审计；adaptive_auto 枚举存在但 feature gate 硬关闭。
- [ ] 11.16 【Red/Green】验证批次内 Routing/Consensus/Scope/Calibration policy 固定、画像滚动只影响未分配建议、已分配 Assignment 与 decision manifest 不追溯改派。

## 12. 管理员统计、指导、审计与导出切片

- [x] 12.1 【Red】为轻量运营计数的 submitted/resolved/unresolved/pending、保存/媒体/事件错误编写查询测试（`admin-audit-export`）。
- [x] 12.2 【Green】实现管理员运营页，不自动生成 tier、处罚或路由变更。
- [x] 12.3 【Red】为“计算当前情况”的 cutoff、仅 Revision、input manifest/hash、版本/support/missing/not-evaluable、相同输入复用和旧快照不变编写 Job 测试。
- [x] 12.4 【Green】实现 MetricSnapshot 后台作业与管理员状态/结果 UI。
- [ ] 12.5 【Red/Green】实现 unresolved、多峰、scope 与有效 portal/geometry 冲突、模板化 reason、画像漂移与随机抽检复核队列；满足冻结 ScopePolicy 且未命中抽检的 Task 不要求逐张审核。（已实现由真实 `TaskAggregate`、`requires_review` AuditArtifact 和 OperationalIssue 驱动的复核队列、管理员读取/按 reason code 筛选、输入 Revision/冲突摘要/冻结规则版本；真实 OperationalIssue 进入管理员复核队列的 Playwright 路径已通过；反复模板化 reason、画像漂移与随机抽检仍未实现。）
- [x] 12.6 【Red/Green】实现最小 GuidanceEvent 和确认/feedback exposure，仅目标工人可查询并确认；未确认投递不得计作 feedback exposure，不提供工人回复、对话线程，也不集成微信/Upwork 聊天内容或支付。
- [x] 12.7 【Red】为注册 AuditRun、冻结 hash 输入、不可变 Artifact、审计不能修改 Revision 编写权限和副作用测试。
- [x] 12.8 【Green】实现注册审计框架及 canonical 结构、scope-portal、时间完整性审计类型；拒绝任意脚本。外部 Manhattan/结构审计不属于 V1，只能由未来独立 change 注册，不以占位算法冒充。
- [x] 12.9 【Red】为 BatchExportSnapshot 全身份/版本/媒体 hash/Revision/Review/Consensus、latest/selected 视图和排除 Draft 编写 schema/golden 测试。
- [x] 12.10 【Green】实现异步导出和 manifest 校验；输出明确标记为批次快照而非 Final Gold/DatasetRelease。（真实 PostgreSQL 17 不可变导出快照、worker 与管理员 API/审计专测已通过。）
- [ ] 12.11 验证高影响管理员操作均产生 actor/target/reason/correlation 审计事件，敏感字段经过日志脱敏。（通用领域目标/原因/关联标识，以及当前 Task 发布、Assignment 创建、批次 freeze/reopen、Review、Adjudication、返工和 Snapshot 路径已完成；Task 取消/撤销、路由覆盖、Export 与批量删除须随真实端点补齐，不以占位事件冒充。）

## 13. 国际化、隐私、运维和恢复切片

- [x] 13.1 【Red/Green】实现 Chrome/Edge 设备预检、中英文管理员维护 copy、worker locale、UTC 存储/本地时区显示（`platform-boundaries`）。当前在线 POC 的最低工作区宽度集中定义为 1024px，可在后续平台 release 调整；WebGL/IndexedDB 缺失仅降级。viewer/client/geometry 版本持久追踪仍由 13.4 完成。
- [ ] 13.2 【Red】为 notice_version 首次确认/变更重确认、禁止超范围数据和 IP 不进画像编写 API/E2E/manifest 测试。（API/组件测试、真实工作区阻断与 Playwright notice 确认已完成；画像 manifest 证据待 11.x 画像落地后完成，不伪造空画像。）
- [ ] 13.3 【Green】实现中英文数据告知、确认记录、日志保留边界和隐私过滤。（中英文 `data-notice-v1`、幂等确认、审计与版本变更重确认已完成；日志保留政策和 11.x 画像隐私过滤尚未完成。）
- [x] 13.4 【Red/Green】为 platform/client/viewer/geometry/interaction/active-time/schema/model 的适用版本从来源对象追溯到 Export 编写跨域测试；未启用能力不填占位版本，本地非权威 PreviewResult 不复制进 Revision。（Revision 冻结 platform/client/viewer/interaction，Task/Export 追踪 active-time/schema/copy 与实际 Semi model；历史缺失值不回填，Manual、信息性 geometry 与未启用 assist/profile 不造占位。未来真正发布 active-time v2 时再完成 PAP-PBD-SC-006，不以假 v2 冒充。）
- [ ] 13.5 编写生产部署与回滚配置：Web + worker + PostgreSQL + 私有 COS，禁止生产密钥入库；运行预发布负载测试后记录容量结论而不预设虚假带宽。（生产 settings 已对允许主机、Django 密钥、PostgreSQL 密码和私有 COS 配置执行环境变量 fail-fast，CI 使用非生产夹具值；统一 Procfile release/Web/Gunicorn/worker、生产依赖哈希锁与 forward-only 回滚合同已完成；真实预发布负载和容量结论仍待部署环境落地。）
- [ ] 13.6 配置数据库每日备份 30 天、关键操作前额外备份/导出和 COS 不可变/恢复窗口；证据为上线前完整恢复演练。
- [ ] 13.7 验证全球测试节点的压缩/高清首字节、失败和回退指标可按匿名化地区聚合；CDN 仍不在首版依赖中。
- [x] 13.8 添加仓库隔离测试，确保构建、测试、部署和运行不读取 `D:\Work\HOHONET` 或 Label Studio 数据库。（CI 扫描当前及未来执行面、构建/依赖清单和软链接目标；真实生产部署验收仍由 13.5/14.7 完成。）
- [x] 13.9 【Red/Green】把 Supabase 定位为仅托管 PostgreSQL：固定 21 个触发器函数的 `search_path`，撤销 `PUBLIC`/`anon`/`authenticated`/`service_role` 对 Django `public` 对象及当前 owner 默认对象的权限，并在临时 PostgreSQL 17 中用同名角色验证迁移、回滚往返和有效权限均安全。（云端 PostgreSQL 17 已通过函数配置、有效权限、默认权限及 0028→0001→0028 往返专测。）
- [x] 13.10 在生产 Supabase Dashboard 禁用 Data API 或从 exposed schema 移除 `public`，经 Django release migration 部署 13.9 后只读复核对象权限和 security advisor；不得用 Supabase migration history 形成第二套 DDL authority。（2026-08-16 已确认 Data API disabled，并由 Django 正式登记 `work.0028`：迁移总数 57、0028 恰好 1 条。部署后 `anon`/`authenticated`/`service_role` 的表/序列/函数有效权限均为 0，21 个函数 mutable search_path 与 security definer 均为 0，`public` schema CREATE 均关闭，Security Advisor 零告警；全部 Django public 对象和连接 owner 为 `postgres`，其危险默认授权为 0。）

## 14. 端到端验收与上线门槛

- [ ] 14.1 【POC 验收】建立 Manual worker 主流程 E2E：登录改密→真实 Assignment/媒体→2D/Scope/Portal→autosave→信息性 `poc-wireframe-v1`→幂等提交不可变 Revision→管理员按权限读取同一 `state_sha`；预览不得成为提交门槛。
- [ ] 14.2 建立 Semi E2E：管理员导入本地 prediction→冻结 Task→工人读取→Manual 反泄漏验证→提交与共识。
- [ ] 14.3 建立离线/冲突 E2E：断网编辑与计时→恢复同步；另一设备接管时保留 recovery copy 且不覆盖。
- [ ] 14.4 建立动态质量 E2E：新工人→Trap PreScreen→common-anchor Calibration→production→scope/portal/geometry component aggregation→LOO 回算 provisional/verified profile→progression probe→能力—难度建议→管理员批准→后续画像更新不追溯改派。
- [x] 14.5 建立 Scope 返工 E2E：representation/geometry 冲突→管理员裁定 annotatable→不暴露他人几何/portal 的 ReworkRequest→feedback-exposed Revision。（Chromium 使用真实 Assignment、`representation_oos` Revision、Review、AdjudicatedRevision 与 ReworkRequest；管理员裁定中的不同 point/window 不进入工人端，返工从工人自己的 point/door 开始，新 Revision 为 `feedback_exposed=true`，原 Revision 不变。）
- [ ] 14.6 在真实 Assignment、Revision 和媒体端点运行对象级权限矩阵，并运行 CSRF/session、签名 URL、并发提交、Job 幂等、日志泄露和备份恢复安全回归；不得以 FakeOwnedResource 或控制面续租请求替代业务路径证据。
- [ ] 14.7 运行全部后端、前端、E2E、当前已发布 canonical/预览能力的 golden、OpenSpec strict validation 和依赖/许可证检查，保存版本化验收报告；不得以未获批的外部 geometry/A-line 能力阻塞 V1。
- [ ] 14.8 仅在用户验收通过后同步 `openspec/specs` 并 archive change；未通过项保持未勾选，不得以削弱测试或删除断言宣称完成。
