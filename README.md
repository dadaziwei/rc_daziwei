# 内部 API 通知投递系统设计文档

本项目是一个面向生产演进的 MVP：业务系统提交 outbound HTTP notification 任务，本服务负责先持久化任务，再异步、尽可能可靠地投递到外部供应商 API。

第一版重点不是覆盖所有生产能力，而是在控制复杂度的前提下，把可靠投递系统的核心链路做扎实：持久化、幂等创建、异步投递、失败重试、状态查询和投递历史记录。

## Quick Start

当前版本实现了健康检查接口和 notification 创建接口。API 只负责持久化通知任务，不会直接调用外部供应商 API；worker 暂未实现。

环境要求：

- Python 3.11+
- Docker Compose

本地启动：

```bash
cp .env.example .env
make install
make db-up
make migrate
make test
make run
```

健康检查：

```bash
curl http://localhost:8000/health
```

预期响应：

```json
{"status":"ok"}
```

创建 notification：

```bash
curl -X POST http://localhost:8000/notifications \
  -H "Content-Type: application/json" \
  -d '{
    "idempotency_key": "order-123-inventory-notify",
    "source_system": "order-service",
    "event_type": "order.paid",
    "target_url": "https://vendor.example.com/api/notify",
    "method": "POST",
    "headers": {
      "X-Source": "order-service"
    },
    "body": {
      "order_id": "123",
      "sku": "A001",
      "quantity": 1
    }
  }'
```

首次创建返回 `201 Created`：

```json
{
  "id": 1,
  "status": "pending",
  "created_at": "2026-05-14T14:00:00Z"
}
```

如果 `idempotency_key` 已存在，接口不会创建新记录，而是返回已有 notification。第一版只支持 `method = "POST"`，`target_url` 必须是 `http` 或 `https`。

常用命令：

- `make install`: 安装本地开发依赖。
- `make test`: 运行 pytest。
- `make run`: 启动 FastAPI 开发服务。
- `make db-up`: 启动本地 PostgreSQL。
- `make db-down`: 停止本地 Docker Compose 服务。
- `make migrate`: 执行 Alembic migration。

## 1. Problem Understanding

这是一个内部 outbound HTTP notification delivery service。内部业务系统不会直接调用外部供应商 API，而是向本服务提交通知任务，例如某个业务事件需要同步给供应商。

业务系统只关心“通知任务已经被可靠接收并进入投递流程”，不直接依赖外部 API 的即时返回值。外部 API 的成功、失败、超时、重试和排查由本服务负责管理。

系统目标是尽可能可靠地把通知投递到外部供应商 API。这里的“可靠”不是 exactly-once，而是：

- 接收请求后先持久化，再返回成功。
- 外部投递由 worker 异步执行。
- 失败后按有限次数重试。
- 每次投递 attempt 都有记录。
- 最终状态可查询、可排查、可人工恢复。

这个定位让业务系统从外部供应商的不稳定性中解耦出来，也让通知投递成为一个可观测、可运维的内部基础能力。

## 2. Goals

- 接收内部业务系统提交的通知请求。
- 持久化 `notification`，确保 API 返回成功前任务已经落库。
- 由异步 worker 投递外部 HTTP API。
- 对 timeout、网络错误和可重试 HTTP 状态进行有限重试。
- 支持通知状态查询，便于业务系统和运维排查。
- 支持幂等创建，避免同一业务事件重复生成多个通知任务。
- 保存每次投递 attempt 的请求结果、错误信息和耗时等排查信息。
- 保持代码简单、可读、容易本地运行，并为未来生产化演进保留空间。

## 3. Non-goals

- 不保证 exactly-once delivery。
- 不做复杂供应商协议适配平台，第一版只处理通用 outbound HTTP 投递。
- 不做完整管理后台。
- 不做多租户权限系统。
- 第一版不引入 Kafka 或 RabbitMQ。
- 不处理外部供应商的业务语义，例如供应商返回 200 但业务字段表示失败时的领域判断。
- 不做登录鉴权系统。
- 不做前端页面。

这些 non-goals 是主动收敛范围。当前阶段要证明的是可靠投递核心链路，而不是一次性搭建完整平台。

## 4. Delivery Semantics

系统明确选择 at-least-once delivery。也就是说，在可恢复失败场景下，系统会倾向于再次投递，以提高最终送达概率。

不选择 exactly-once 的核心原因是外部 HTTP 调用存在不可消除的不确定性。典型场景是：

1. Worker 向供应商 API 发送请求。
2. 供应商已经收到并处理了请求。
3. Worker 在读取响应前发生 timeout、连接断开或进程崩溃。

在这个场景下，本服务无法可靠判断供应商是否已经处理成功。如果完全不重试，可能丢通知；如果重试，可能产生重复投递。因此系统选择 at-least-once，并把重复投递风险明确暴露给上下游设计。

重复投递风险需要通过幂等机制降低：

- 内部请求使用 `idempotency_key`，避免同一业务事件在本服务内重复创建通知任务。
- 通知 payload 中应包含业务侧稳定的 `event_id` 或等价字段，便于外部供应商或下游系统去重。
- 对于支持幂等键的供应商 API，后续可以把 `event_id` 或供应商专用 idempotency key 透传给对方。

这个语义符合生产系统里常见的外部 HTTP 投递约束：系统保证尽力可靠投递和完整记录，但不承诺跨系统 exactly-once。

## 5. MVP Architecture

第一版架构使用 FastAPI + PostgreSQL + Delivery Worker。

```text
Internal Services -> FastAPI -> PostgreSQL -> Delivery Worker -> External Vendor API
```

核心流程：

1. 内部业务系统调用 FastAPI 创建通知任务。
2. API 在同一个事务中校验幂等键并持久化 `notifications` 记录。
3. API 返回创建成功或已存在的通知任务信息。
4. Delivery Worker 周期性从 PostgreSQL 查询 due notifications。
5. Worker 领取可投递任务，调用外部 HTTP API。
6. Worker 写入 `delivery_attempts`，并更新 `notifications` 当前状态、attempt 计数、最后错误和下次重试时间。

PostgreSQL 是 system of record。它保存通知任务当前状态、重试调度信息和完整投递历史。Worker 不依赖内存状态判断任务是否完成，因此进程重启后可以继续从数据库恢复工作。

第一版可以使用单 worker polling。未来当需要多个 worker 并发领取任务时，可以通过 `FOR UPDATE SKIP LOCKED` 避免多个 worker 同时处理同一个 notification。

## 6. Failure Handling

外部 HTTP 投递结果按工程可操作性分类，而不是追求过度复杂的供应商语义判断。

成功：

- HTTP `2xx` 视为投递成功。
- 成功后 notification 状态进入 `succeeded`，不再重试。

可重试失败：

- 请求 timeout。
- 网络错误，例如 DNS、连接失败、连接中断。
- HTTP `408`。
- HTTP `429`。
- HTTP `5xx`。

这些场景通常代表临时性问题。系统会记录 attempt，并根据简单指数退避计算 `next_retry_at`。重试必须有最大次数，不能无限重试。

不可重试失败：

- 大多数 HTTP `4xx` 视为 non-retryable，例如 `400`、`401`、`403`、`404`、`422`。

这些状态通常表示请求格式、认证配置、资源或业务输入存在问题，继续重试大概率只会放大流量和噪音。系统应记录最后错误，并进入失败终态或 non-retryable failed 状态。

最大重试次数：

- 当 retryable failure 达到最大重试次数后，notification 进入 `failed`。
- `failed` 不表示永远不可恢复，而是表示自动重试已经停止，后续需要人工排查或未来的手动重放能力处理。

## 7. Data Model Draft

### `notifications`

保存通知任务的当前状态和重试调度信息。

建议字段：

- `id`: 主键。
- `idempotency_key`: 内部幂等键，对同一业务事件保持唯一。
- `event_id`: 业务事件 ID，便于上下游排查和去重。
- `target_url`: 外部供应商 API 地址。
- `http_method`: HTTP 方法，第一版可限制为 `POST`。
- `headers`: 需要发送给供应商的 HTTP headers，注意不能记录敏感密钥明文。
- `payload`: 请求体 JSON。
- `status`: 当前状态，例如 `pending`、`in_progress`、`succeeded`、`retry_scheduled`、`failed`。
- `attempt_count`: 已执行投递次数。
- `max_attempts`: 最大投递次数。
- `next_retry_at`: 下次可投递时间。
- `last_error`: 最近一次错误摘要。
- `created_at`: 创建时间。
- `updated_at`: 更新时间。

关键约束和索引：

- `idempotency_key` 唯一约束。
- `(status, next_retry_at)` 索引，用于 worker 查询 due notifications。
- `event_id` 普通索引，用于业务排查。

### `delivery_attempts`

保存每次外部投递 attempt 的历史记录。

建议字段：

- `id`: 主键。
- `notification_id`: 外键，关联 `notifications.id`。
- `attempt_number`: 第几次投递。
- `status`: 本次 attempt 结果，例如 `success`、`retryable_failure`、`non_retryable_failure`。
- `http_status_code`: 外部 API 返回状态码，网络错误时为空。
- `response_body`: 外部响应摘要或截断后的响应体。
- `error_message`: timeout、network error 或异常摘要。
- `duration_ms`: 本次 HTTP 调用耗时。
- `created_at`: attempt 创建时间。

设计原则：

- `notifications` 保存当前状态，方便快速查询和调度。
- `delivery_attempts` 保存历史事实，方便审计和排查。
- 不依赖日志作为唯一证据；日志可以丢，数据库记录必须保留关键事实。

## 8. Technology Choices

### Python + FastAPI

Python 适合快速实现清晰的业务流程，生态成熟，面试作业和 production-oriented MVP 都容易评审。FastAPI 提供类型友好的 HTTP API 开发体验，自动请求校验和 OpenAPI 文档对接口调试也很有帮助。

### PostgreSQL

PostgreSQL 作为主数据库和 system of record，适合保存通知状态、重试元数据和投递历史。它支持事务、唯一约束、行级锁、索引和未来的多 worker 任务领取模式。

### SQLAlchemy + Alembic

SQLAlchemy 用于表达数据模型和数据库访问逻辑，避免散落的 SQL 拼接。Alembic 用于管理 schema migration，让数据库结构变化可追踪、可复现，也符合生产化项目的基本要求。

### httpx

httpx 是现代 Python HTTP client，支持明确 timeout、连接池和同步/异步调用方式。第一版无论 worker 使用同步还是异步实现，都必须为所有外部请求设置 timeout。

### pytest

pytest 适合覆盖核心行为测试，例如持久化后返回、幂等创建、retryable/non-retryable 分类、重试次数上限、attempt 记录和状态流转。

### Docker Compose

Docker Compose 用于本地启动 PostgreSQL 和服务，降低评审者运行成本。第一版不引入 Kubernetes，避免把部署复杂度提前带入 MVP。

## 9. Database and Message Queue Trade-offs

### 为什么不用 SQLite

SQLite 适合本地 demo，因为它轻量、无需单独服务、启动成本低。但这个项目定位不是一次性 demo，而是 production-oriented MVP。

SQLite 在以下方面不适合作为第一版主数据库：

- 并发写入能力有限。
- 多 worker 任务领取和锁竞争模型不适合未来演进。
- 缺少真实生产环境里常用的 PostgreSQL 行级锁、索引和运维语义。
- 很难体现可靠投递系统对 system of record 的要求。

因此第一版直接使用 PostgreSQL，减少从 demo 数据库迁移到生产数据库时的设计偏差。

### 为什么当前选择 PostgreSQL-backed job queue

当前版本选择 PostgreSQL-backed job queue，是因为 notification 本来就必须先持久化。既然数据库已经保存任务状态，就可以先用数据库状态驱动 worker 投递。

这个选择的工程收益：

- `idempotency_key` 可以通过唯一约束保证幂等创建。
- 创建任务、更新状态、记录 attempt 都可以放在事务边界内处理。
- 状态查询不依赖额外组件。
- `next_retry_at` 可以直接表达重试调度。
- `delivery_attempts` 可以完整保留投递历史。
- 未来可以通过 `FOR UPDATE SKIP LOCKED` 支持多个 worker 并发领取 due notifications。
- 本地部署复杂度低于直接引入 MQ。

这个方案不是说数据库队列永远最好，而是它最匹配当前 MVP 的主要矛盾：先把可靠投递状态机做正确。

### 为什么第一版不直接引入 Kafka/RabbitMQ

第一版不直接引入 Kafka 或 RabbitMQ，是主动控制复杂度，不是否定 MQ。

当前问题核心是可靠外部 HTTP 投递，而不是高吞吐事件流处理。即使引入 MQ，PostgreSQL 仍然需要保存 notification 状态、attempt_count、next_retry_at、last_error 和 delivery_attempts。MQ 不能替代任务状态数据库。

过早引入 MQ 会额外带来：

- DB 与 MQ 的一致性问题。
- retry topic 或延迟队列设计。
- dead-letter queue 设计。
- consumer offset 或 ack 语义处理。
- 本地开发和评审运行成本上升。
- 更多失败模式和排查路径。

对于当前 MVP，这些复杂度会分散对核心可靠投递链路的关注。

### Kafka/RabbitMQ/SQS 各自适合什么场景

RabbitMQ 更适合传统任务队列场景，尤其是需要 ack、重试、routing、dead-letter exchange 等队列语义时。

SQS 适合云上托管任务队列，运维成本低，天然适合削峰、异步任务分发和 DLQ。它牺牲一部分本地可控性，但减少基础设施维护负担。

Kafka 更适合高吞吐事件流、多消费者订阅、事件回放和统一事件平台。它适合把 notification 事件纳入更大的事件流体系，但不是当前 MVP 的必要前提。

### 未来如何通过 Outbox Pattern 引入 MQ

未来如果数据库 polling 成为瓶颈，或通知量明显增长，可以引入 Outbox Pattern。

当前架构：

```text
FastAPI -> PostgreSQL -> Worker polling -> External API
```

未来架构：

```text
FastAPI -> PostgreSQL + outbox_events -> Outbox Dispatcher -> Kafka/RabbitMQ/SQS -> Worker Pool -> External API
```

演进方式：

1. FastAPI 在创建 notification 的同一数据库事务中写入 `outbox_events`。
2. Outbox Dispatcher 扫描未发布事件并发送到 Kafka/RabbitMQ/SQS。
3. Worker Pool 从 MQ 消费任务并投递外部 API。
4. Worker 仍然把状态、错误和 attempt 历史写回 PostgreSQL。
5. MQ 负责 dispatch、削峰和扩展，PostgreSQL 继续负责事实记录和恢复依据。

这个设计避免“数据库提交成功但 MQ 发布失败”或“MQ 发布成功但数据库事务回滚”的一致性问题。

## 10. Future Evolution

后续演进应围绕实际瓶颈逐步展开，而不是提前堆叠基础设施。

- 多 worker：提高投递吞吐，但需要确保任务领取互斥。
- `FOR UPDATE SKIP LOCKED`：支持多个 worker 从 PostgreSQL 并发领取 due notifications。
- Dead-letter queue：对自动重试耗尽的任务提供隔离、排查和人工处理入口。
- Vendor-level rate limit：按供应商维度限制并发和 QPS，避免触发对方限流或封禁。
- Circuit breaker：当某个供应商持续失败时临时熔断，减少无效请求和重试风暴。
- Metrics / alerting：暴露成功率、失败率、重试量、延迟、积压量、供应商错误分布等指标。
- Management console：提供查询、筛选、手动重放、标记处理等运维能力。
- `outbox_events` + MQ：在 DB polling 成为瓶颈或需要事件流能力时，引入 Outbox Dispatcher 和 Kafka/RabbitMQ/SQS。

这些能力都应建立在第一版清晰的数据模型和状态机之上。只要 PostgreSQL 中的 notification 状态和 delivery_attempts 历史足够可靠，系统就有空间逐步演进，而不需要在 MVP 阶段一次性承担完整生产平台的复杂度。
