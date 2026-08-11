# 后台 worker

后台 worker 与 Web 应用共享 `backend` 领域代码和锁定依赖，并以独立进程运行 PostgreSQL Job/Outbox 消费入口：

```powershell
.\.venv\Scripts\python.exe backend\manage.py run_worker
```

该入口依次公平轮询分析、统计快照和批次导出队列；空闲时默认等待 1 秒，可通过 `--poll-seconds` 校准。首版不引入 Redis、Celery 或独立微服务数据模型。
