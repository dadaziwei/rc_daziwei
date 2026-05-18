# AI 使用说明

本项目使用 AI 加速开发，但关键决策由人做出。

## AI 辅助的部分

- 需求拆解：将"内部通知系统"拆为 API 接收、持久化、幂等创建、异步 worker、重试、状态查询等阶段
- 文档：生成 README 结构（Problem、Goals、Non-goals、Architecture 等章节）并迭代
- 代码骨架：FastAPI 项目结构、SQLAlchemy/Alembic 配置、Docker Compose、Makefile
- 测试补充：idempotency、URL/method 校验、delivery_attempts、retry policy、max_attempts 等
- 失败策略审查：将 HTTP 状态分入 success / retryable / non-retryable 三类

## 未采纳的 AI 建议

| 建议 | 原因 |
|------|------|
| 引入 Kafka/RabbitMQ | 第一版 PostgreSQL job queue 足够，MQ 增加不必要的复杂度 |
| 追求 exactly-once | HTTP 超时下无法判断对方是否已处理 |
| 供应商模板系统 | MVP 只需通用 HTTP 投递 |
| Celery/Redis 作为 worker | 额外引入组件，增加一致性边界 |
| 管理后台 / 前端 | 非当前阶段目标 |
| Kubernetes / Prometheus | 留给生产部署演进 |

## 人做出的关键决策

- 选择 **at-least-once** 投递语义
- PostgreSQL 作为 system of record
- 第一版不引入 MQ，用 PostgreSQL-backed job queue 表达核心链路
- API 与 worker 分离：API 只持久化，worker 负责投递
- 控制功能边界：不做管理后台、多租户权限、供应商模板系统
- Worker 从一次性脚本演进为常驻进程，支持 graceful shutdown
- 增加手动 retry API，但保留 `attempt_count` 不重置
- SSRF 风险写入 README，提供可选 `TARGET_HOST_ALLOWLIST`

**核心原则**：AI 适合生成初稿和加速实现，但系统边界、可靠性语义和复杂度取舍必须由人判断。
