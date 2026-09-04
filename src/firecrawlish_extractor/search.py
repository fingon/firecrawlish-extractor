import asyncio
import math
from dataclasses import dataclass
from numbers import Real
from typing import Literal

import httpx
import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .scrape import require_json_content_type

SEARCH_PATH = "/v2/search"
DEFAULT_SEARCH_LIMIT = 10
MIN_SEARCH_LIMIT = 1
MAX_SEARCH_LIMIT = 100
SEARXNG_DEADLINE_SEC = 15.0

logger = structlog.get_logger(__name__)


class SearchError(Exception):
    def __init__(
        self, message: str, *, status_code: int, category: str
    ) -> None:
        super().__init__(message)
        self.category = category
        self.status_code = status_code


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    query: str
    limit: int = Field(
        default=DEFAULT_SEARCH_LIMIT,
        ge=MIN_SEARCH_LIMIT,
        le=MAX_SEARCH_LIMIT,
    )

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        if not value.strip() or len(value) > 500:
            raise ValueError(
                "query must contain 1 to 500 non-whitespace characters"
            )
        return value


class WebResult(BaseModel):
    title: str
    url: str
    description: str
    position: int


class SearchData(BaseModel):
    web: list[WebResult]


class SearchResponse(BaseModel):
    success: Literal[True] = True
    data: SearchData


@dataclass(frozen=True, slots=True)
class _SearchCandidate:
    title: str
    url: str
    description: str
    score: float


def _text_value(value: object) -> str:
    return value if isinstance(value, str) else ""


def _usable_result_url(value: object) -> str | None:
    if (
        not isinstance(value, str)
        or not value
        or not all(character.isprintable() for character in value)
    ):
        return None
    try:
        parsed_url = httpx.URL(value)
    except TypeError, ValueError:
        return None
    if (
        parsed_url.scheme not in {"http", "https"}
        or not parsed_url.host
        or parsed_url.userinfo
    ):
        return None
    return str(parsed_url)


def _score_value(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        return 0.0
    score = float(value)
    return score if math.isfinite(score) else 0.0


def _candidate_from(value: object) -> _SearchCandidate | None:
    if not isinstance(value, dict):
        raise SearchError(
            "SearXNG returned an invalid result",
            status_code=502,
            category="invalid_response",
        )
    result_url = _usable_result_url(value.get("url"))
    if result_url is None:
        return None
    return _SearchCandidate(
        description=_text_value(value.get("content")),
        score=_score_value(value.get("score")),
        title=_text_value(value.get("title")),
        url=result_url,
    )


def _translate_results(payload: object, limit: int) -> list[WebResult]:
    if not isinstance(payload, dict) or not isinstance(
        payload.get("results"), list
    ):
        raise SearchError(
            "SearXNG returned an invalid response",
            status_code=502,
            category="invalid_response",
        )

    candidates = [
        candidate
        for item in payload["results"]
        if (candidate := _candidate_from(item)) is not None
    ]
    candidates.sort(
        key=lambda candidate: candidate.score,
        reverse=True,
    )
    return [
        WebResult(
            description=candidate.description,
            position=position,
            title=candidate.title,
            url=candidate.url,
        )
        for position, candidate in enumerate(candidates[:limit], start=1)
    ]


class SearXNGClient:
    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        *,
        deadline_sec: float = SEARXNG_DEADLINE_SEC,
    ) -> None:
        if deadline_sec <= 0:
            raise ValueError("deadline_sec must be positive")
        self._client = client
        self._deadline_sec = deadline_sec
        self._search_url = f"{base_url.rstrip('/')}/search"
        self._timeout = httpx.Timeout(deadline_sec)

    async def search(self, query: str, limit: int) -> list[WebResult]:
        try:
            async with asyncio.timeout(self._deadline_sec):
                response = await self._client.get(
                    self._search_url,
                    params={"format": "json", "pageno": 1, "q": query},
                    timeout=self._timeout,
                )
        except TimeoutError as exc:
            raise SearchError(
                "SearXNG request exceeded its deadline",
                status_code=504,
                category="timeout",
            ) from exc
        except httpx.TimeoutException as exc:
            raise SearchError(
                "SearXNG request exceeded its deadline",
                status_code=504,
                category="timeout",
            ) from exc
        except httpx.HTTPError as exc:
            raise SearchError(
                "SearXNG request failed",
                status_code=502,
                category="upstream_failure",
            ) from exc

        if response.status_code < 200 or response.status_code >= 300:
            raise SearchError(
                "SearXNG returned an unsuccessful response",
                status_code=502,
                category="upstream_failure",
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise SearchError(
                "SearXNG returned invalid JSON",
                status_code=502,
                category="invalid_response",
            ) from exc
        return _translate_results(payload, limit)


def create_search_router() -> APIRouter:
    router = APIRouter()

    @router.post(SEARCH_PATH, response_model=SearchResponse)
    async def search(
        request: Request,
        search_request: SearchRequest,
    ) -> SearchResponse:
        require_json_content_type(request)
        searcher = request.app.state.searcher
        if searcher is None:
            raise HTTPException(
                status_code=503, detail="Search is not configured"
            )
        try:
            results = await searcher.search(
                search_request.query,
                search_request.limit,
            )
        except SearchError as exc:
            logger.warning(
                "search_upstream_failed",
                category=exc.category,
                method=request.method,
                path=request.url.path,
                request_id=getattr(request.state, "request_id", "unknown"),
                status_code=exc.status_code,
            )
            raise HTTPException(
                status_code=exc.status_code,
                detail=str(exc),
            ) from exc
        return SearchResponse(data=SearchData(web=results))

    return router
