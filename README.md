# Internal API Notification System

This project is a production-oriented MVP for an internal API notification system.

Business systems submit external HTTP notification requests to this service. The service persists each notification first, then delivers it asynchronously to the target vendor API with bounded retries and attempt history.

## Database and Message Queue Trade-offs / 数据库与消息队列取舍

### PostgreSQL is the system of record

PostgreSQL is the source of truth for the notification delivery lifecycle. The service must be able to answer what happened to a notification even if a worker crashes, an external API is unavailable, or a future dispatch layer temporarily fails.

PostgreSQL stores:

- The current notification status.
- `attempt_count`.
- `next_retry_at`.
- `last_error`.
- The full `delivery_attempts` history.

Keeping this state in PostgreSQL supports manual queries, debugging, operational recovery, and future administrative tooling. It also makes the reliable delivery behavior observable and auditable instead of hiding critical state inside transient worker memory or a queue offset.

### MQ is a dispatch layer

Kafka, RabbitMQ, or SQS can be introduced later as a dispatch layer for task distribution, traffic smoothing, and worker scaling.

The message queue does not replace PostgreSQL. Even with a message queue, PostgreSQL should continue to store notification state, retry metadata, and delivery attempt history. The queue should help workers receive work more efficiently; it should not become the only place where business-critical delivery state exists.

### Why not SQLite

SQLite is useful for a local demo because it is lightweight and easy to start.

For this project, SQLite is not a good fit for the production-oriented MVP target. It is weaker for concurrent writes, multi-worker job claiming, and production operational trust. The notification system needs safe persistence, concurrent worker coordination, and a database model that can evolve toward real deployment conditions, so PostgreSQL is the better baseline.

### Why the current MVP uses a PostgreSQL-backed job queue

The first version uses PostgreSQL as both the system of record and the job queue backing store because the notification already needs to be persisted before the API returns success.

This approach provides:

- A unique constraint for `idempotency_key`.
- Transactions around notification creation and state changes.
- Direct status queries for debugging and operations.
- Retry scheduling through `next_retry_at`.
- Attempt history through `delivery_attempts`.
- A future path for multiple workers to claim jobs safely with `FOR UPDATE SKIP LOCKED`.
- Lower deployment complexity than introducing a separate message queue immediately.

This keeps the MVP focused on the core problem: reliable external HTTP delivery with clear state transitions and bounded retries.

### Why the first version does not introduce Kafka directly

Kafka is a strong platform for high-throughput event streams, multiple subscribers, and event replay. This project does not reject Kafka.

The first version does not introduce Kafka because the current core problem is reliable outbound HTTP delivery, not event stream processing. Kafka also does not replace the task state database: the service still needs PostgreSQL for notification status, retry metadata, idempotency, and delivery history.

Adding Kafka in the first version would introduce extra concerns before the MVP needs them:

- Database-to-message-queue consistency.
- Retry topics.
- Dead-letter topics.
- Consumer offset management.
- More complex local development and deployment.

For this MVP, skipping Kafka is an intentional complexity-control decision, not a claim that Kafka is unsuitable in general.

### When to introduce a message queue

A message queue becomes more attractive when:

- Database polling becomes a bottleneck.
- Notification volume grows significantly.
- More worker horizontal scaling is required.
- The system needs stronger traffic smoothing.
- The platform needs a unified event stream, event replay, or multiple independent consumers.

At that point, PostgreSQL should remain the system of record, and the queue should be added as a dispatch mechanism using an outbox-based design.

### Choosing RabbitMQ, SQS, or Kafka

The right queue depends on the dominant problem:

- For task queues, retries, and dead-letter queues, RabbitMQ or SQS is often the more natural fit.
- For high-throughput event streams, multiple consumer groups, and event replay, Kafka is usually a better fit.

The choice should be driven by production requirements, not by adding infrastructure before the MVP has proven the need.

### Evolution path

Current MVP:

```text
FastAPI -> PostgreSQL -> Worker polling -> External API
```

Future architecture with the Outbox Pattern:

```text
FastAPI -> PostgreSQL + outbox_events -> Outbox Dispatcher -> Kafka/RabbitMQ/SQS -> Worker Pool -> External API
```

In the future model, API requests still commit notification state and outbox records to PostgreSQL transactionally. A dispatcher then publishes outbox events to the selected queue. Workers consume from the queue, deliver to external APIs, and continue writing delivery status and attempt history back to PostgreSQL.
