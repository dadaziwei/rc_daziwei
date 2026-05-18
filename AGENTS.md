# AGENTS.md

AI agent 协作规则，适用于本项目的所有 Codex 任务。

## 项目背景

面试作业：设计并实现内部 API 通知投递系统。业务系统提交外部 HTTP 通知请求，本服务负责可靠投递到目标供应商 API。

定位：production-oriented MVP — 实现可靠投递核心链路，控制复杂度，为未来演进留空间。

## 技术约束

- Python 3.11+ / FastAPI / PostgreSQL / SQLAlchemy / Alembic / httpx / pytest
- 第一版**不引入**：Kafka、RabbitMQ、Redis、Celery、Kubernetes、前端、登录鉴权、管理后台

## 设计约束

- 投递语义：**at-least-once**（不追求 exactly-once）
- API 必须先持久化 notification 再返回成功
- 外部 HTTP 投递由 worker 异步执行，必须设 timeout
- 失败重试有最大次数上限，使用指数退避
- 支持 `idempotency_key` 避免重复创建
- 每次投递必须有 `delivery_attempts` 记录
- 保持代码简洁可读，避免过度抽象

## 协作规则

- 每个阶段只完成一个清晰的任务
- 修改文件前简要说明计划
- 修改后运行相关测试
- 每次完成后创建一次 git commit（Conventional Commits 风格）
- 不提交 `.env`、secrets、tokens、虚拟环境、缓存文件
- 需求不明确时优先采用合理的 MVP 假设并记录在 README 中
