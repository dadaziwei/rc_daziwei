# AGENTS.md

This file defines the global collaboration rules for all future Codex tasks in this project.

## Project Context

This project is an interview assignment: design and implement an internal API notification system.

Business systems submit external HTTP notification requests to this service. The service is responsible for delivering those notifications to target vendor APIs as reliably as possible.

This is not a one-off demo. Treat it as a production-oriented MVP: implement the core reliable delivery path while keeping complexity controlled and leaving clear room for future production evolution.

## Technology Constraints

- Use Python 3.11+.
- Use FastAPI for HTTP APIs.
- Use PostgreSQL as the primary database.
- Use SQLAlchemy for data models.
- Use Alembic for database schema migrations.
- Use httpx for outbound HTTP requests.
- Use pytest for tests.
- Use Docker Compose for local PostgreSQL and service startup.
- Do not introduce Kafka, RabbitMQ, Redis, Celery, or Kubernetes in the first version.
- Do not build a frontend.
- Do not build a login or authentication system.
- Do not build a full production-grade admin console.

## Core Design Constraints

- Delivery semantics are at-least-once.
- Do not attempt to provide exactly-once delivery.
- The API must persist a notification before returning success to the caller.
- External HTTP delivery must be performed asynchronously by a worker.
- Every outbound HTTP request must set an explicit timeout.
- Failed deliveries must have a maximum retry count.
- Retries must never continue forever.
- Use a simple exponential backoff strategy for retries.
- Support `idempotency_key` to avoid creating duplicate notification jobs for the same business event.
- Track notification status, attempt count, last error, and next retry time.
- Preserve a record for every delivery attempt.
- Keep the code simple, readable, and easy to run locally.
- Avoid over-abstraction and complex framework code.

## Database And Queue Constraints

- PostgreSQL is the current system of record.
- The current MVP uses a PostgreSQL-backed job queue.
- Do not implement Kafka or RabbitMQ directly in the first version.
- README must explain how the architecture can evolve to Kafka, RabbitMQ, or SQS through the Outbox Pattern.
- Do not claim that Kafka replaces the database.
- Do not make a message queue a required component of the current MVP.

## Collaboration Rules

- Complete only one clear phase per task.
- Before modifying files, briefly explain the plan.
- After modifying files, run relevant tests.
- Show a concise git diff summary after changes.
- Create one git commit after each completed phase.
- Use Conventional Commits style for commit messages.
- Do not commit `.env` files, secrets, tokens, virtual environments, or cache files.
- If requirements are unclear, prefer reasonable MVP assumptions and document them in README.

## Implementation Guidance

- Favor straightforward, explicit code over premature abstractions.
- Keep the first version focused on the reliable notification delivery lifecycle:
  - accept request
  - persist notification
  - enqueue through PostgreSQL state
  - deliver asynchronously
  - record attempts
  - retry with bounded exponential backoff
  - expose enough status for debugging and tests
- Make local development reproducible with Docker Compose and clear README instructions.
- Keep tests focused on critical behavior: persistence before success, idempotency, retry scheduling, attempt recording, timeout handling, and terminal failure after maximum retries.
