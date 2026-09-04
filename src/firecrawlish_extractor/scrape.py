from typing import Literal

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator

from .extraction import (
    ExtractionFailure,
    ExtractionResult,
    OutputFormat,
)
from .network import FetchedResponse, FetchError, validate_target_url

SCRAPE_PATH = "/v2/scrape"
DEFAULT_FORMAT: OutputFormat = "markdown"
SUPPORTED_HTML_MEDIA_TYPES = frozenset({"text/html", "application/xhtml+xml"})

logger = structlog.get_logger(__name__)


class ScrapeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: AnyHttpUrl
    formats: list[OutputFormat] = Field(
        default_factory=lambda: [DEFAULT_FORMAT],
    )

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        try:
            validate_target_url(str(value))
        except FetchError as exc:
            raise ValueError("The supplied URL is invalid") from exc
        return value

    @field_validator("formats")
    @classmethod
    def validate_formats(cls, value: list[OutputFormat]) -> list[OutputFormat]:
        if not value or len(value) != len(set(value)):
            raise ValueError("formats must be a non-empty, duplicate-free list")
        return value


class ScrapeData(BaseModel):
    markdown: str | None = None
    html: str | None = None
    metadata: dict[str, str | int]


class ScrapeResponse(BaseModel):
    success: Literal[True] = True
    data: ScrapeData


def _require_json_content_type(request: Request) -> None:
    content_type = request.headers.get("content-type")
    if content_type is None:
        raise HTTPException(
            status_code=400,
            detail="Request content type must be application/json",
        )
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise HTTPException(
            status_code=400,
            detail="Request content type must be application/json",
        )


def _fetch_error_to_http_exception(
    request: Request,
    exception: FetchError,
) -> HTTPException:
    logger.warning(
        "scrape_fetch_failed",
        category=exception.category,
        method=request.method,
        path=request.url.path,
        request_id=getattr(request.state, "request_id", "unknown"),
        status_code=exception.status_code,
    )
    return HTTPException(
        status_code=exception.status_code,
        detail=str(exception),
    )


def _validate_origin_response(
    request: Request,
    response: FetchedResponse,
) -> str:
    if response.status_code < 200 or response.status_code >= 300:
        logger.warning(
            "scrape_origin_unsuccessful",
            method=request.method,
            path=request.url.path,
            request_id=getattr(request.state, "request_id", "unknown"),
            status_code=response.status_code,
        )
        raise HTTPException(
            status_code=502,
            detail="The origin returned an unsuccessful response",
        )

    content_type = response.content_type
    if content_type is None:
        raise HTTPException(
            status_code=415,
            detail="The origin did not return supported HTML content",
        )
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type not in SUPPORTED_HTML_MEDIA_TYPES:
        raise HTTPException(
            status_code=415,
            detail="The origin did not return supported HTML content",
        )
    return content_type


def _decode_origin_body(response: FetchedResponse) -> str:
    try:
        return response.body.decode(response.encoding)
    except (LookupError, UnicodeError) as exc:
        raise HTTPException(
            status_code=502,
            detail="The origin returned an unusable response",
        ) from exc


def _scrape_response(
    response: FetchedResponse,
    extraction: ExtractionResult,
    content_type: str,
) -> ScrapeResponse:
    metadata: dict[str, str | int] = dict(extraction.metadata)
    metadata.update(
        {
            "sourceURL": response.url,
            "url": response.url,
            "statusCode": response.status_code,
            "contentType": content_type,
        }
    )
    data = ScrapeData(metadata=metadata, **extraction.outputs)
    return ScrapeResponse(data=data)


def create_scrape_router() -> APIRouter:
    router = APIRouter()

    @router.post(
        SCRAPE_PATH,
        response_model=ScrapeResponse,
        response_model_exclude_none=True,
    )
    async def scrape(
        request: Request,
        scrape_request: ScrapeRequest,
    ) -> ScrapeResponse:
        _require_json_content_type(request)
        fetcher = request.app.state.fetcher
        extraction_pool = request.app.state.extraction_pool
        try:
            origin_response = await fetcher.fetch(str(scrape_request.url))
        except FetchError as exc:
            raise _fetch_error_to_http_exception(request, exc) from exc

        content_type = _validate_origin_response(request, origin_response)
        html = _decode_origin_body(origin_response)
        try:
            extraction = await extraction_pool.extract(
                html,
                origin_response.url,
                tuple(scrape_request.formats),
            )
        except ExtractionFailure as exc:
            logger.warning(
                "scrape_extraction_empty",
                method=request.method,
                path=request.url.path,
                request_id=getattr(request.state, "request_id", "unknown"),
            )
            raise HTTPException(
                status_code=422,
                detail="Trafilatura found no extractable content",
            ) from exc

        return _scrape_response(origin_response, extraction, content_type)

    return router
