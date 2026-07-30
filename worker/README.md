# 后台 worker

后台 worker 与 Web 应用共享 `backend` 领域代码和锁定依赖，并以独立进程运行 PostgreSQL Job/Outbox 消费入口。

首版不引入 Redis、Celery 或独立微服务数据模型。具体进程入口将在对应 TDD task 中实现。
