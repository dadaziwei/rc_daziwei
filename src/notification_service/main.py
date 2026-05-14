from fastapi import FastAPI

from notification_service.api import router as api_router


def create_app() -> FastAPI:
    app = FastAPI(
        title="Internal API Notification Service",
        version="0.1.0",
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(api_router)

    return app


app = create_app()
