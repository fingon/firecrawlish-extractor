from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from .settings import Settings

HEALTH_PATH = "/healthz"


class HealthCapabilities(BaseModel):
    scrape: Literal[True] = True
    search: bool


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    capabilities: HealthCapabilities


def create_health_router(settings: Settings) -> APIRouter:
    router = APIRouter()

    @router.get(HEALTH_PATH, response_model=HealthResponse)
    async def healthz() -> HealthResponse:
        return HealthResponse(
            capabilities=HealthCapabilities(
                search=settings.searxng_url is not None,
            ),
        )

    return router
