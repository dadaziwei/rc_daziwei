.PHONY: install test run db-up db-down migrate worker

install:
	python -m pip install -e ".[dev]"

test:
	python -m pytest

run:
	python -m uvicorn notification_service.main:app --reload

db-up:
	docker compose up -d db

db-down:
	docker compose down

migrate:
	alembic upgrade head

worker:
	python -m notification_service.cli worker --limit 10
