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
- [x] 2.6 【Green·控制面】实现工作区 acquire/renew、接管、租约及客户端多页/断网状态，使 2.5 通过；Draft、Revision、Activity 等真实写入及 recovery copy 在对应后续切片验收。
- [x] 2.7 验证 token 不进入 localStorage、账号禁用/改密撤销旧会话、工人接口不返回画像排名；证据为浏览器安全 E2E 与响应 schema 测试。

## 3. Asset、MediaVariant 与 COS 导入交付切片

- [ ] 3.1 【Red】为 Asset/MediaVariant 稳定 ID、内容 hash、COS version/长度/CRC64、尺寸/格式/角色、数据库绕过不可变发布和规范化坐标映射编写数据库合同测试（`media-ingestion-delivery`）。
- [ ] 3.2 【Green】实现 Asset/MediaVariant 模型、PostgreSQL 不可变约束、可信 manifest 登记和 canonical 映射，使发布后覆盖、非等比普通变体和 hash/version drift 均失败；PostgreSQL 专属证据必须在 PostgreSQL 17 实际运行，不以 SQLite 或 skip 代替。
- [x] 3.3 【Red】为相同 source key/hash 幂等导入、预览取消不消耗正式 Task 编写服务测试。
- [x] 3.4 【Green】实现仅浏览受信 manifest 已登记 COS 版本的候选配对/预览/发布向导后端与最小 UI，不要求手写 JSON；允许同一 Asset 追加新的不可变同角色 MediaVariant，但禁止同一 source key 改变已登记元数据；运行幂等与重复导入回归。
- [x] 3.5 【Red】为仅接受已拼接等距柱状图、拒绝 skybox 集和不兼容变体编写导入校验测试。
- [x] 3.6 【Green】实现格式/比例/映射校验与稳定错误码，不引入 HOHONET/MP3D/ZInD 预处理运行时。
- [ ] 3.7 【Red】基于真实 Assignment→Task→MediaVariant 关系，为不泄露 foreign/missing 响应、绑定精确 COS version 的对象级短期签名、过期续签、压缩图优先、高清无漂移切换、单变体回退和双失败 `image_unavailable` 编写 API/E2E 测试。
- [ ] 3.8 【Green】在真实工人媒体端点按领域关系约束查询并实现绑定已登记 COS version 的私有 COS 直连交付与前端媒体状态机，证明图片字节不经应用服务器且首版不依赖 CDN。
- [ ] 3.9 验证工人端没有创建/替换 Asset 的上传路径；待 3.8 的真实端点落地后，以端点测试固定 canonical 绑定，不预建无调用方抽象。

## 4. Task、WorkBatch、Assignment 与状态切片

- [x] 4.1 【Red】为 Task 不可变合同、Task ID tombstone/不回收、cancel/supersede、external_task_key 分离，以及显式新轮次复用既有 Asset 但创建新 Task 编写状态与数据库测试（`task-batch-assignment`）。
- [x] 4.2 【Green】实现 Task 生命周期、Manual/Semi 显示合同字段和显式轮次，并接通媒体导入向导的新轮次创建意图，使 4.1 通过。
- [x] 4.3 【Red】为 WorkBatch 多 Assignment、ready/deferred/needs_revisit 切换、不限制单 in-progress、仅访问本人 Assignment、foreign/missing 不泄露及撤销会话拒绝业务请求编写服务/API/E2E 测试。
- [x] 4.4 【Green】实现按工人归属约束的批次/Assignment API 与切换 UI，验证困难任务不会卡住整个批次；CurrentDraft 的读取与恢复留在 6.1/6.2 验收。
- [ ] 4.5 【Red】为 work_state/review_state 分离、同 Task 多独立工人、同工人重复暴露阻断编写状态机测试。
- [ ] 4.6 【Green】实现 Assignment 与独立暴露约束，重构状态转换为显式领域服务并运行回归。
- [ ] 4.7 【Red】为 OOS 正式提交与固定 skip 原因、skip 保留草稿/时间但不建 Revision、重新入队策略编写合同测试。
- [ ] 4.8 【Green】实现 skip/OOS 分离、批次 skip 限额和管理员处置视图。
- [ ] 4.9 【Red/Green】实现批次 freeze/reopen：测试并确保未提交草稿不自动提交、最新 Revision 保持正式、从未提交项仍 incomplete。

## 5. Canonical AnnotationState 与 2D 编辑器切片

- [x] 5.1 【Red】为 point pair 独立 top/bottom 坐标、稳定 pair/point ID、order_index、seam anchor、规范化 u/v 和确定性 state_sha 编写属性/边界测试（`annotation-contract`）。
- [x] 5.2 【Green】实现 canonical schema、序列化、hash 与服务器校验；证明不会平均 top/bottom x，跨 seam 顺序可重放。
- [x] 5.3 【Red】为重复 ID、非有限/越界坐标、不连续 order、坏 seam 引用和非法字段编写服务器拒绝测试。
- [ ] 5.4 【Green】实现稳定错误码与 pair/field 定位，使恶意客户端不能绕过校验。
- [ ] 5.5 【Red】为 MetaSchema 稳定代码、schema_version/copy_version/locale、Manual 禁止 model_issue 和旧版本冻结编写合同测试。
- [ ] 5.6 【Green】实现首版固定元标签表单与中英文 copy 加载，不构建通用表单设计器。
- [ ] 5.7 【Red】为 scope 二元、OOS reason、best_effort_complete/partial/not_drawable 三种尝试状态及无最低时间门槛编写测试。
- [ ] 5.8 【Green】实现 OOS 编辑/提交 UI 与分支校验，证明 partial/not_drawable 不会被迫伪造闭合几何。
- [x] 5.9 【Red/Green】实现 2D point pair 增删拖动、顺序/seam 编辑和本地 Undo/Redo；编辑器仅在工作区状态为 editable 时挂载，冲突 tab 不得编辑；组件测试验证稳定 ID 与派生 wall/BEV 不进入 canonical 状态。本项只验收内存组件，真实 Assignment/CurrentDraft 接入和持久化留在 6.1/6.2。

## 6. Draft、Revision、Review 与返工切片

- [ ] 6.1 【Red】为每 DraftCycle 单 CurrentDraft、防抖 autosave、draft_version 乐观并发和保存状态，以及真实 Assignment 归属、session、workspace token、tab_id、未过期租约和接管状态校验编写 API/E2E 测试（`draft-revision-review`）。
- [ ] 6.2 【Green】在 CurrentDraft 读写的真实事务边界内实现领域归属与工作区校验，并实现服务器 CurrentDraft 与前端保存状态机；旧客户端写入必须进入冲突而非 last-write-wins。
- [ ] 6.3 【Red】为首次提交事务、expected state_sha、幂等键、响应丢失重试不重复建 Revision，以及接管后旧设备提交失败编写并发测试。
- [ ] 6.4 【Green】在提交事务内重新校验 Assignment 归属与工作区状态，实现 Revision 冻结服务并证明已提交内容不能 UPDATE/DELETE。
- [ ] 6.5 【Red】为批次开放时从最新 Revision 新建修订周期、旧 Review 仍绑定旧 Revision 编写测试。
- [ ] 6.6 【Green】实现修订 UI 和周期归因，运行 Draft/Revision 全回归。
- [ ] 6.7 【Red】为 ReviewRecord、changes_requested、新工人 Revision unreviewed、管理员 AdjudicatedRevision 不冒充工人，以及管理员真实敏感读取审计编写测试。
- [ ] 6.8 【Green】实现管理员复核/裁决路径、独立交付指针和不可变敏感读取审计。
- [ ] 6.9 【Red】为 OOS→in-scope ReworkRequest、通知与返工仅本人可读、只暴露文字不暴露他人几何、feedback_exposed 和 overdue 编写权限/E2E 测试。
- [ ] 6.10 【Green】实现按 Assignment 归属约束的选择性返工与初始/返工证据分离，并验证普通点移动历史不持久化到服务器。

## 7. 本地 3D 与验证切片

- [ ] 7.1 【Red】为受支持设备预检、2D DraftState→只读 3D、3D 操作不回写 canonical 编写前端/几何测试（`preview-validation`）。
- [ ] 7.2 【Green】实现本地 geometry worker 和只读 3D viewer，记录 geometry_engine_version。
- [ ] 7.3 【Red】用假时钟测试 pointer move 不重建、pointerup/add/delete/order/Undo/Redo 后防抖重建、旧 state_sha 结果被丢弃。
- [ ] 7.4 【Green】实现动作完成触发和取消/过期机制，完成拖动性能基准。
- [ ] 7.5 【Red】为 in-scope 提交必须有同 state_sha 成功 Preview、OOS partial/not_drawable 例外编写端到端门槛测试。
- [ ] 7.6 【Green】接入提交事务并确保客户端伪造 PreviewResult 不能绕过服务器合同。
- [ ] 7.7 【Red/Green】实现硬结构错误与软警告分级、pair 定位和严重 Manhattan 明确确认；测试其不平均、不自动移动、不绝对阻断合法非标准布局。
- [ ] 7.8 建立代表性 MP3D/ZInD 几何夹具和跨浏览器视觉/数值回归，记录无法在 CI 覆盖的 GPU 差异。

## 8. Active time 与离线恢复切片

- [x] 8.1 【Red】为 15 秒 idle、30 秒 heartbeat、focus/visibility、允许 interaction type 和 page-open 不计时编写前端状态机假时钟测试（`activity-offline`）。
- [x] 8.2 【Green】实现粗粒度 ActivityEvent 生成和 active lease，不采集 pointer 坐标、按键内容或累计秒数。（本项完成前端状态机与不可变客户端事件信封；`server_received_at`、领域/工作区校验和服务端派生仍由 8.3/8.4 完成，真实 Assignment 页面挂接随 6.1/6.2 完成。）
- [ ] 8.3 【Red】为 event_id 幂等、sequence、客户端单调时钟、服务端区间推导、休眠/超长间隔封顶、离线重放、多工作区去重，以及 Activity 写入的 session、Assignment 归属和有效工作区校验编写后端属性测试。
- [ ] 8.4 【Green】在 Activity 接收事务内执行领域归属与工作区校验，实现事件接收与版本化派生器，分别输出 initial/revision/rework/unsubmitted time。
- [ ] 8.5 【Red】为 IndexedDB Draft patch/事件/Undo 队列、断网继续编辑、离线提交禁用编写浏览器 E2E。
- [ ] 8.6 【Green】实现离线模式与恢复同步；验证 token 不入 localStorage、完整媒体不成为 Draft 数据。
- [ ] 8.7 【Red/Green】实现 base_version 未变自动同步和 takeover/freeze/server-change 冲突恢复副本，禁止自动 merge/覆盖。
- [ ] 8.8 验证 active time 只作为时间/运营上下文，API 和管理员 UI 不生成工资或以时间单独判罚。

## 9. PredictionArtifact 与 Assist 边界切片

- [ ] 9.1 【Red】为 Manual 所有响应/缓存无 prediction/model risk/model_issue/assist payload 编写 schema 与浏览器缓存测试（`prediction-assist`）。
- [ ] 9.2 【Green】实现按 Task mode 分离的序列化与前端状态，证明同 Asset 的 Semi 数据不会污染 Manual。
- [ ] 9.3 【Red】为 PredictionArtifact 模型/checkpoint/config/artifact hash、asset/coordinate 匹配、发布冻结和更换 prediction 新 Task 编写测试。
- [ ] 9.4 【Green】实现管理员本地推理结果解析、叠加预览、确认冻结；不实现运行时推理。
- [ ] 9.5 【Red/Green】实现 Semi 缺失/损坏/不兼容 prediction 技术阻断且绝不静默降级 Manual。
- [ ] 9.6 【Red】为 AssistArtifact input_state_sha 竞态、单候选、显式 Apply/Ignore/Undo 事件合同编写纯领域测试。
- [ ] 9.7 【Green】实现禁用状态的数据合同与 feature gate；普通工人请求 A-line 必须被拒绝，不实现 worker-facing A-line UI。
- [ ] 9.8 建立第三方/Label Studio 代码复用许可证清单；未完成逐文件审查前不复制代码，证据为来源和许可证检查记录。

## 10. Job、动态共识与异常复核切片

- [ ] 10.1 【Red】为 Revision 提交事务写唯一 outbox、worker 抢占、失败重试和 input hash 幂等编写数据库并发测试（`adaptive-consensus-routing`）。
- [ ] 10.2 【Green】实现 PostgreSQL Job/Outbox 与独立 worker，不引入 Redis/Celery；暴露积压、失败和 attempt 指标。
- [ ] 10.3 【Red】为每名工人最新未反馈有效 Revision 去重、无效/外部事故处置和反馈后 Revision 排除编写 eligible manifest 测试。
- [ ] 10.4 【Green】实现不可变 SubmissionAssessment 和 eligible input manifest。
- [ ] 10.5 【Red】为 scope-first 聚合、稳定 OOS、OOS/in-scope 冲突、needs_more/unresolved 编写固定与属性测试。
- [ ] 10.6 【Green】实现增量 scope 状态机与 OOS 异常复核入口。
- [ ] 10.7 【Red】为 geometry 聚类、唯一主簇、次簇/margin、多峰、真实 medoid 指针和禁止平均角点编写测试。
- [ ] 10.8 【Green】实现版本化 geometry 聚合与 TaskConsensusArtifact，管理员 Adjudication 不覆盖旧共识 Artifact。
- [ ] 10.9 【Red/Green】实现批次 ConsensusPolicy：默认 3→逐一追加→5、发布前可配置、稳定停止、达到上限不无限追加；输出追加 AssignmentProposal 而非首版自动 Assignment。
- [ ] 10.10 用故意共同错误、多峰、OOS 滥用和结构失败夹具验证共识边界，确保稳定多数不被测试伪装成绝对 GT。

## 11. LOO 画像与辅助路由切片

- [ ] 11.1 【Red】为排除本人后的 scope/geometry 共识、pending→resolved 自动回算、锚点/裁决证据编写 LOO 测试。
- [ ] 11.2 【Green】实现不可变 WorkerEvidence 与 OnlineWorkerProfileSnapshot，保证重复分析不重复计数。
- [ ] 11.3 【Red】为 Manual/Semi、数据集/任务域、平台/模型/几何版本、support/uncertainty、质量/结构/OOS/返工分量编写画像 schema 测试。
- [ ] 11.4 【Green】实现分层收缩画像；active time、IP、模型一致和单次警告不得进入已验证准确率。
- [ ] 11.5 【Red】为高能力→困难、稳定略低→简单、新人→简单+校准、进步 probe、暂时降级和 exploration=0 警告编写路由政策测试。
- [ ] 11.6 【Green】实现能力—难度匹配和分层探索建议，工人端不暴露分数/tier。
- [ ] 11.7 【Red】为 RoutingRun→AssignmentProposal→管理员批准/覆盖→AssignmentManifest、固定 seed、容量和候选排除编写可重放测试。
- [ ] 11.8 【Green】实现 manual/assisted UI 与审计；adaptive_auto 枚举存在但 feature gate 硬关闭。
- [ ] 11.9 【Red/Green】验证批次内 policy/threshold 固定、画像滚动只影响未分配建议、已分配 Assignment 不追溯改派。

## 12. 管理员统计、指导、审计与导出切片

- [ ] 12.1 【Red】为轻量运营计数的 submitted/resolved/unresolved/pending、保存/媒体/事件错误编写查询测试（`admin-audit-export`）。
- [ ] 12.2 【Green】实现管理员运营页，不自动生成 tier、处罚或路由变更。
- [ ] 12.3 【Red】为“计算当前情况”的 cutoff、仅 Revision、input manifest/hash、版本/support/missing/not-evaluable、相同输入复用和旧快照不变编写 Job 测试。
- [ ] 12.4 【Green】实现 MetricSnapshot 后台作业与管理员状态/结果 UI。
- [ ] 12.5 【Red/Green】实现 unresolved、多峰、OOS 冲突、模板化原因、画像漂移与随机抽检复核队列；稳定未抽检 OOS 不要求逐张审核。
- [ ] 12.6 【Red/Green】实现最小 GuidanceEvent 和确认/feedback exposure，通知仅投递并返回给目标工人，不集成微信/Upwork 聊天内容或支付。
- [ ] 12.7 【Red】为注册 AuditRun、冻结 hash 输入、不可变 Artifact、审计不能修改 Revision 编写权限和副作用测试。
- [ ] 12.8 【Green】实现注册审计框架及至少结构/Manhattan/OOS/时间完整性审计类型；拒绝任意脚本。
- [ ] 12.9 【Red】为 BatchExportSnapshot 全身份/版本/媒体 hash/Revision/Review/Consensus、latest/selected 视图和排除 Draft 编写 schema/golden 测试。
- [ ] 12.10 【Green】实现异步导出和 manifest 校验；输出明确标记为批次快照而非 Final Gold/DatasetRelease。
- [ ] 12.11 验证高影响管理员操作均产生 actor/target/reason/correlation 审计事件，敏感字段经过日志脱敏。

## 13. 国际化、隐私、运维和恢复切片

- [ ] 13.1 【Red/Green】实现 Chrome/Edge 设备预检、中英文管理员维护 copy、worker locale、UTC 存储/本地时区显示（`platform-boundaries`）。
- [ ] 13.2 【Red】为 notice_version 首次确认/变更重确认、禁止超范围数据和 IP 不进画像编写 API/E2E/manifest 测试。
- [ ] 13.3 【Green】实现中英文数据告知、确认记录、日志保留边界和隐私过滤。
- [ ] 13.4 【Red/Green】为 platform/client/viewer/geometry/interaction/active-time/schema/model 版本贯穿 Task→Export 编写跨域追踪测试。
- [ ] 13.5 编写生产部署与回滚配置：Web + worker + PostgreSQL + 私有 COS，禁止生产密钥入库；运行预发布负载测试后记录容量结论而不预设虚假带宽。
- [ ] 13.6 配置数据库每日备份 30 天、关键操作前额外备份/导出和 COS 不可变/恢复窗口；证据为上线前完整恢复演练。
- [ ] 13.7 验证全球测试节点的压缩/高清首字节、失败和回退指标可按匿名化地区聚合；CDN 仍不在首版依赖中。
- [x] 13.8 添加仓库隔离测试，确保构建、测试、部署和运行不读取 `D:\Work\HOHONET` 或 Label Studio 数据库。（CI 扫描当前及未来执行面、构建/依赖清单和软链接目标；真实生产部署验收仍由 13.5/14.7 完成。）

## 14. 端到端验收与上线门槛

- [ ] 14.1 建立 worker 主流程 E2E：登录改密→打开批次→压缩图→2D/元标签→动作后 3D→autosave→提交→修订→切下一项。
- [ ] 14.2 建立 Semi E2E：管理员导入本地 prediction→冻结 Task→工人读取→Manual 反泄漏验证→提交与共识。
- [ ] 14.3 建立离线/冲突 E2E：断网编辑与计时→恢复同步；另一设备接管时保留 recovery copy 且不覆盖。
- [ ] 14.4 建立动态质量 E2E：3 名独立工人→scope/geometry 稳定或追加至 5→LOO 回算画像→能力—难度建议→管理员批准。
- [ ] 14.5 建立 OOS 返工 E2E：OOS 冲突→管理员裁定 in-scope→不暴露他人几何的 ReworkRequest→feedback-exposed Revision。
- [ ] 14.6 在真实 Assignment、Revision 和媒体端点运行对象级权限矩阵，并运行 CSRF/session、签名 URL、并发提交、Job 幂等、日志泄露和备份恢复安全回归；不得以 FakeOwnedResource 或控制面续租请求替代业务路径证据。
- [ ] 14.7 运行全部后端、前端、E2E、几何 golden、OpenSpec strict validation 和依赖/许可证检查，保存版本化验收报告。
- [ ] 14.8 仅在用户验收通过后同步 `openspec/specs` 并 archive change；未通过项保持未勾选，不得以削弱测试或删除断言宣称完成。
