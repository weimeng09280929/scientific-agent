"""Public, read-only configuration guidance; this is not a health probe."""

from fastapi import APIRouter, Response

from core.llm.readiness import runtime_status


def build_runtime_status_router(configuration):
    router = APIRouter(prefix="/api", tags=["runtime-status"])

    @router.get("/runtime-status")
    def status(response: Response):
        response.headers["Cache-Control"] = "no-store"
        config = configuration() if callable(configuration) else configuration
        return runtime_status(config)

    return router
