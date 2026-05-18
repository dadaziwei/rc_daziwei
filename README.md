# 内部 API 通知投递系统

业务系统提交 outbound HTTP notification 任务，本服务负责持久化后异步投递到外部供应商 API，失败自动重试，全程可追溯。

## Quick Start

```bash
cp .env.example .env
make db-up          # 启动 PostgreSQL
make install        # 安装依赖
make migrate        # 初始化数据库
make run            # 启动 FastAPI (占用终端)
```

健康检查：`curl http://localhost:8000/health` → `{"status":"ok"}`

OpenAPI 文档：`http://localhost:8000/docs`

运行测试：`make test` / 停止数据库：`make db-down`

常用命令：`make db-up | install | migrate | run | worker | test | db-down`

## 使用示例

```bash
# 创建通知
curl -X POST http://localhost:8000/notifications \
  -H "Content-Type: application/json" \
  -d '{
    "idempotency_key": "order-123-inventory-notify",
    "source_system": "order-service",
    "event_type": "order.paid",
    "target_url": "http://127.0.0.1:9000/mock-notify",
    "method": "POST",
    "headers": {"X-Source": "order-service"},
    "body": {"order_id": "123", "sku": "A001", "quantity": 1}
  }'
# → 201 Created，返回 notification id + status

# 手动运行 worker 投递
make worker
# 或：python -m notification_service.cli worker --limit 10

# 查询状态
curl http://localhost:8000/notifications/1
# → 返回状态、attempt_count、next_retry_at、投递历史等

# 手动重试已失败的通知
curl -X POST http://localhost:8000/notifications/1/retry
```

## 架构

```
业务系统 → FastAPI → PostgreSQL ← Delivery Worker → 外部供应商 API
```

- **API** 只负责持久化通知任务，不直接调用外部 API
- **Worker** 从 PostgreSQL 周期性拉取 due tasks，执行 HTTP 投递，记录结果
- **PostgreSQL** 是 system of record：保存任务状态、重试调度、全部投递历史

## 核心设计

**投递语义：at-least-once**。外部 HTTP 超时场景下无法可靠判断对方是否已处理，因此不追求 exactly-once。通过 `idempotency_key` 避免同一业务事件重复创建通知。

**失败处理**：
- 成功：`2xx`
- 可重试失败：timeout、network error、`408`、`429`、`5xx` → 指数退避重试
- 不可重试失败：`400`、`401`、`403`、`404`、`422` → 直接标记失败
- 重试间隔：第 1 次 1min → 第 2 次 5min → 第 3 次 15min → 第 4 次 1h → 第 5 次 3h
- 达最大次数后进入 `failed` 终态

**状态流转**：`pending → processing → success / pending(重试) / failed`

**Worker 任务领取**：使用 PostgreSQL `FOR UPDATE SKIP LOCKED`，避免多 worker 重复处理。

## 数据模型

`notifications` — 任务当前状态与重试调度：
- `idempotency_key`（唯一约束）、`source_system`、`event_type`
- `target_url`、`method`、`headers`、`body`
- `status`、`attempt_count`、`max_attempts`、`next_retry_at`、`last_error`

`delivery_attempts` — 每次投递的历史记录：
- `notification_id`、`attempt_number`、`status_code`
- `success`、`error_message`、`response_body_preview`、`duration_ms`

## 技术选型

| 组件 | 选择 | 原因 |
|------|------|------|
| Web 框架 | FastAPI | 类型友好、自动校验、OpenAPI 文档 |
| 数据库 | PostgreSQL | 事务、行级锁、多 worker 并发领取 |
| ORM / 迁移 | SQLAlchemy + Alembic | 模型清晰、迁移可追溯 |
| HTTP 客户端 | httpx | 明确 timeout、连接池 |
| 测试 | pytest | 覆盖核心状态机 |
| 消息队列 | 第一版不用 | 当前瓶颈不在消息分发，PostgreSQL 足以驱动 |

消息队列（Kafka/RabbitMQ/SQS）的引入路径见 [Future Evolution](#未来演进)。

## 环境变量

- `HTTP_TIMEOUT_SECONDS` — 外部请求超时
- `WORKER_BATCH_SIZE` — worker 每轮领取最大任务数
- `WORKER_POLL_INTERVAL_SECONDS` — worker 轮询间隔
- `MAX_ATTEMPTS` — 默认最大投递次数
- `TARGET_HOST_ALLOWLIST` — 可选的目标 host 白名单（生产必须配置）

## 运维

```sql
-- 积压任务
SELECT id, source_system, event_type, next_retry_at, attempt_count
FROM notifications WHERE status = 'pending' ORDER BY next_retry_at;

-- 失败任务
SELECT id, source_system, event_type, attempt_count, max_attempts, last_error
FROM notifications WHERE status = 'failed' ORDER BY updated_at DESC;

-- 卡在 processing 的任务
SELECT id, source_system, event_type, updated_at
FROM notifications WHERE status = 'processing' ORDER BY updated_at;
```

手动重试：`curl -X POST http://localhost:8000/notifications/{id}/retry`（仅 `failed` 状态可用）

## 已知局限

- processing 超时无自动回收（worker 崩溃后需人工介入）
- 无管理后台，排查通过 API + 数据库查询
- 无供应商模板系统、多租户权限
- 本地默认未启用 `TARGET_HOST_ALLOWLIST`，生产需补 SSRF 防护

## 未来演进

后续演进围绕实际瓶颈展开，不提前堆砌基础设施：

- **processing 超时回收**：卡住的任务自动恢复为 pending
- **Outbox Pattern + MQ**：当数据库 polling 成为瓶颈时，在通知创建的事务中写入 `outbox_events`，由 Dispatcher 发到 Kafka/RabbitMQ/SQS，Worker Pool 消费
- **Vendor rate limiting / circuit breaker**：按供应商限流熔断
- **Metrics / alerting**：成功率、延迟、积压量等指标
- **管理控制台**：查询、筛选、手动重放

## 关于 AI 使用

本项目使用 AI 辅助开发（需求拆解、文档初稿、代码骨架、测试补充），但关键决策由人做出：at-least-once 语义、PostgreSQL 作为 system of record、第一版不引入 MQ、不做管理后台和权限系统。详见仓库中的 [AI_USAGE.md](AI_USAGE.md)。
