## ADDED Requirements

### Requirement: 首版支持桌面 Chrome 与 Edge
首版浏览器应用 SHALL 正式支持当前受维护的桌面 Chrome 和 Edge，不承诺 Safari、Firefox、移动浏览器或平板。工作区打开前 MUST 预检 WebGL、IndexedDB、必要浏览器 API、媒体可访问性和最低屏幕条件，并给出明确阻断或降级原因。

#### Scenario: 受支持浏览器通过预检
- **WHEN** 桌面 Chrome/Edge 具备要求的图形和本地存储能力
- **THEN** 系统允许进入标注工作区并记录 viewer/client/geometry 版本

#### Scenario: WebGL 不可用
- **WHEN** 设备无法生成首版要求的本地 3D
- **THEN** 系统阻止 in-scope 生产标注并提示更换设备或浏览器，不静默省略 3D 门槛

### Requirement: 中英文文案和 UTC 时间
首版 SHALL 提供管理员维护的简体中文和英文文案，不得在运行时依赖机器翻译。工人可选择 locale，Revision 保存 locale/copy_version。服务器时间 MUST 保存为 UTC，界面按用户本地时区展示并明确时区。

#### Scenario: 世界各地工人提交
- **WHEN** 不同时区的工人提交 Revision
- **THEN** 服务器保存可比较的 UTC 时间，界面按各自本地时区显示而不改变原始时间

### Requirement: 数据收集告知版本化
工人首次工作前 MUST 阅读并确认中英文版本化数据告知。告知 SHALL 列出账号/worker_id、Assignment/Draft/Revision/Review、active-time 粗粒度事件、错误与客户端预检、指导确认等收集内容；收集范围变化时 MUST 发布新 notice_version 并重新确认。

#### Scenario: 首次进入工作区
- **WHEN** 工人尚未确认当前 notice_version
- **THEN** 系统阻止进入生产工作区，直到其确认并保存时间与版本

### Requirement: 禁止超范围监控
系统 MUST 不收集详细鼠标轨迹、键盘内容、剪贴板、屏幕录像、摄像头或与平台无关的浏览活动。IP 和必要设备安全信息仅用于短期安全日志，不得直接进入工人质量画像。

#### Scenario: 质量画像生成
- **WHEN** 系统物化 OnlineWorkerProfileSnapshot
- **THEN** 输入 manifest 不含 IP、无关浏览数据或详细输入内容

### Requirement: 关键交互与引擎版本可追溯
Task、Revision、Preview、Activity、Assessment、Consensus、Profile、MetricSnapshot 和 Export SHALL 按适用范围记录 `platform_release_id`、`client_build_sha`、`viewer_version`、`geometry_engine_version`、`interaction_contract_version`、`active_time_rule_version`、schema/copy/model/assist 版本。跨不兼容版本的统计不得静默合并。

#### Scenario: Active-time 规则改变
- **WHEN** idle 阈值或计时合同发生变化
- **THEN** 系统发布新的 active_time_rule_version，分析按版本分层或显式转换，不直接与旧秒数无条件合并

### Requirement: 新平台与 HOHONET 运行时隔离
新平台 MUST 不修改 `D:\Work\HOHONET`、当前 Label Studio 实验、export_label、active_logs、协议或现有脚本，也不得通过软链接、子模块、共享运行时数据库或复制当前实验脚本形成隐式依赖。当前论文实验继续只在现有 Label Studio/HOHONET 流程执行。

#### Scenario: 新平台部署
- **WHEN** 平台构建或启动
- **THEN** 不需要访问 HOHONET 工作树、Label Studio 数据库或当前论文导出目录

### Requirement: 平台 canonical schema 不兼容优先
平台 SHALL 使用领域专用 canonical schema 和稳定 opaque IDs，不保证 Label Studio 通用 `result[]` 形状兼容。若未来论文数据需要进入平台分析体系，只能使用单独、一次性的离线转换器生成平台外研究 bundle；转换器不得把旧数据写入生产数据库或改变当前实验。

#### Scenario: 下游需要旧 LS 数据
- **WHEN** 未来分析确需复用当前论文数据
- **THEN** 独立转换工具读取冻结导出并生成带映射和 hash 的离线 bundle，新平台运行时保持无依赖

### Requirement: 未来适配器不阻塞首版
首版 SHALL 不实现桌面客户端、工人本地媒体包、CAD adapter、worker-facing A-line、云端在线推理、复杂自动路由、DatasetRelease 或 ModelRelease UI。架构可保留明确接口，但这些功能必须通过未来独立 OpenSpec change 获批。

#### Scenario: 请求未来桌面客户端
- **WHEN** 团队准备开发单窗口、单实例且复用 Web 编辑器的桌面端
- **THEN** 必须先创建新 OpenSpec change，首版浏览器行为不得被未批准代码改变

#### Scenario: 请求本地媒体包
- **WHEN** 未来工人希望挂载管理员打包的本地图片
- **THEN** 新 change 必须保证 manifest/hash 只解析已分配媒体，不能创建 Task、替换 Asset 或绕过后端同步

### Requirement: 全球访问首版以实测驱动优化
首版 SHALL 使用广州私有 COS 直连并记录按地区可分析的媒体首字节、下载失败和回退结果，但不得把精确 IP 纳入质量画像。CDN 或全球加速只有在真实测量证明必要且配置经过独立 change 后才能加入。

#### Scenario: 某地区媒体持续缓慢
- **WHEN** 管理员查看到某地区经匿名化聚合后的媒体性能持续不达标
- **THEN** 系统提供证据用于后续架构决策，但首版不自动改变媒体域名或启用未配置 CDN
