# 生产进程与回滚

`Procfile` 是与托管厂商无关的最小进程合同：release 执行向前迁移，web 运行 WSGI，worker 公平轮询分析、统计快照和批次导出队列。PostgreSQL 与私有 COS 由运行环境注入现有 settings 要求的变量；不得把真实值写入仓库或业务数据库。

发布时使用不可变应用版本，先确认数据库备份，再依次执行 release、启动 web/worker 并验证工作区与 job 指标。并发数、轮询间隔和机器规格只在真实预发布负载测试后确定；默认值不构成容量结论。

## Supabase PostgreSQL 安全边界

Supabase 在本项目中只提供托管 PostgreSQL；浏览器和前端不得通过 Supabase Data API、`anon`、`authenticated` 或 `service_role` 直接访问业务对象。生产项目应在 Dashboard 的 API 设置中禁用 Data API，或至少移除 `public` exposed schema。若未来要启用 Data API，必须通过独立 change 设计专用 schema、最小 GRANT、RLS policy 和端到端越权测试，不能直接暴露 Django 的 `public` schema。

Django 是数据库迁移的唯一 authority；不要用 Supabase migration history 重放同一批 DDL。`work.0028_harden_postgres_security` 会在 PostgreSQL 上固定所有平台触发器函数的 `search_path`，撤销 `PUBLIC` 与已存在 Supabase API 角色对 `public` 中表、序列和函数的权限，并收紧当前迁移 owner 的后续默认权限。release 必须持续使用创建这些业务对象的同一数据库 owner 执行迁移。

2026-08-13 已对尚未部署 `work.0028` 的生产 Supabase 执行一次 break-glass 迁移 `emergency_restrict_django_public_api_roles`，立即收回既有暴露面；只读复核结果为 API 角色有效表/序列/函数权限均为 0、危险 postgres default grants 为 0、mutable platform trigger functions 为 0，security advisor 无告警。该记录不是新的常规迁移 authority：合并部署后仍须正常运行 Django `work.0028`，让幂等安全状态进入 `django_migrations`，并停止通过 Supabase migration history 添加平台 DDL。

上线后应以只读查询复核：所有 `public` 业务表均无上述 API 角色的有效权限，所有平台触发器函数均具有固定 `search_path=pg_catalog, public`，Supabase security advisor 不再报告这些函数的 mutable search path。仓库 CI 会在临时 PostgreSQL 17 中创建同名 NOLOGIN 角色并验证迁移、完整回滚往返和有效权限；这不替代生产项目的 Dashboard 设置核验。

回滚时重新部署上一不可变应用版本，同时让数据库保持向前迁移；禁止把生产数据库反向 migrate 到旧 schema。每次迁移必须先扩展并保持上一应用版本兼容，若做不到则拆成后续清理发布。回滚后验证登录、工作区读写及 worker backlog，再记录所用应用版本和数据库迁移状态。
