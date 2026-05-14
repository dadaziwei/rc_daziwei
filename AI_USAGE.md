# AI 使用说明

本文说明本项目中 AI 的实际使用情况。整体上，AI 主要用于加速需求拆解、文档初稿、代码骨架和测试补充；关键的系统边界、可靠性语义和复杂度取舍由人判断、筛选和确认。

## 1. AI 提供了哪些帮助

AI 在本项目中主要提供了以下帮助：

- 帮助拆解需求：把“内部 API 通知系统”拆成 API 接收、数据库持久化、幂等创建、异步 worker、失败重试、状态查询和投递 attempt 记录等阶段。
- 帮助识别系统边界：区分第一版必须实现的可靠投递核心链路，以及 Kafka/RabbitMQ、管理后台、多租户权限等未来演进项。
- 帮助整理 README 结构：生成并迭代了 Problem Understanding、Goals、Non-goals、Delivery Semantics、MVP Architecture、Failure Handling、Database and Message Queue Trade-offs、Future Evolution、Quick Start 和 Local Demo 等章节。
- 帮助生成代码骨架：创建 FastAPI 项目结构、SQLAlchemy/Alembic 基础配置、Docker Compose、本地 Makefile、健康检查接口和基础 pytest 配置。
- 帮助补充测试：围绕 idempotency、URL/method 校验、delivery_attempts、worker retry policy、max_attempts、response_body_preview 截断、next_retry_at 等可靠性场景补充测试。
- 帮助审查失败重试策略：协助把 2xx、timeout、network error、408、429、5xx、常见 4xx 等情况分成 success、retryable failure 和 non-retryable failure，并把重试次数上限和指数退避写入实现与文档。

这些帮助提高了实现速度，也减少了遗漏边界条件的概率，但 AI 输出不是直接无条件采用的结果。

## 2. AI 给出过但没有采纳的建议

在设计讨论和实现过程中，AI 曾给出或容易延伸出一些更完整但更复杂的方案，本项目第一版没有采纳。具体包括：

- 没有第一版引入 Kafka/RabbitMQ：消息队列只写入未来演进路径，没有作为当前 MVP 的必需组件。
- 没有追求 exactly-once：外部 HTTP 超时场景下无法可靠判断对方是否已经处理，因此第一版明确选择 at-least-once。
- 没有做复杂供应商模板系统：没有实现供应商级协议模板、字段映射、签名适配、响应语义解析等平台化能力。
- 没有做管理后台：第一版只提供 API 和数据库记录，不实现可视化运营后台、人工重放页面或复杂筛选界面。
- 没有引入 Kubernetes / Prometheus / Grafana：这些属于生产部署和观测体系的后续建设，不进入当前作业实现范围。
- 没有做过早的多租户权限系统：当前项目假设是内部服务，第一版不做租户隔离、用户系统、角色权限或鉴权链路。

这些能力并不是没有价值，而是不适合放进当前第一版。

## 3. 为什么没有采纳

没有采纳上述建议，主要是因为当前作业重点是可靠投递核心链路，而不是完整生产平台。

第一版需要证明的是：

- API 接收请求后先持久化 notification。
- `idempotency_key` 能避免重复创建同一业务事件。
- Worker 能异步投递外部 HTTP。
- 失败能按有限次数重试。
- 每次投递都有 `delivery_attempts` 记录。
- 状态、错误、attempt_count、next_retry_at 能被查询和排查。

在 MVP 阶段控制复杂度更重要。PostgreSQL-backed job queue 已经足以表达核心设计：notification 本来就需要持久化，PostgreSQL 能提供唯一约束、事务、状态查询、重试调度和未来 `FOR UPDATE SKIP LOCKED` 的多 worker 演进空间。

Kafka/RabbitMQ 是未来演进项，不是第一版必需项。如果第一版直接引入 MQ，会额外引入 DB-MQ 一致性、retry topic、DLQ、consumer offset、本地部署复杂度等问题，反而容易掩盖可靠投递状态机本身是否清晰。

## 4. 哪些关键决策是人做的

以下关键决策由人做出，AI 主要提供补充说明、实现草稿和检查建议：

- 选择 at-least-once delivery，而不是 exactly-once。
- 选择 PostgreSQL 作为 system of record，保存 notification 状态和 delivery_attempts 历史。
- 选择第一版不引入 MQ，用 PostgreSQL-backed job queue 表达核心链路。
- 选择 API 和 worker 分离：API 只负责持久化，worker 负责外部 HTTP 投递。
- 选择把 Kafka/RabbitMQ/SQS 写入未来演进，而不是当前实现。
- 选择控制功能边界，不做管理后台、多租户权限、复杂供应商模板系统或 Kubernetes 部署。

这些决策体现的是工程取舍：先把可靠投递的最小闭环做正确，再根据真实瓶颈演进。

## 5. 反思

AI 很适合生成初稿、补代码和测试，尤其适合把已经明确的设计约束快速转成 README、项目骨架、API、repository、worker 和 pytest 用例。

但系统边界、可靠性语义、复杂度取舍必须由人判断。比如是否追求 exactly-once、是否第一版引入 MQ、是否实现管理后台，这些问题不能只看“技术上能不能做”，还要看当前阶段最需要证明什么。

本项目使用 AI 的重点不是让 AI 代替工程判断，而是加速实现和帮助审查遗漏。AI 输出经过了筛选、修正和取舍：适合 MVP 的部分被采纳，不适合当前阶段的复杂能力被保留到未来演进说明中。
