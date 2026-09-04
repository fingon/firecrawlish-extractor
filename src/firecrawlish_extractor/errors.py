import structlog
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from .responses import error_response

logger = structlog.get_logger(__name__)


async def request_validation_error_handler(
    request: Request,
    _exception: Exception,
) -> JSONResponse:
    logger.warning(
        "request_validation_failed",
        method=request.method,
        path=request.url.path,
        request_id=getattr(request.state, "request_id", "unknown"),
    )
    return error_response(400, "Invalid request")


async def http_exception_handler(
    request: Request,
    exception: Exception,
) -> JSONResponse:
    if not isinstance(exception, HTTPException):
        return error_response(500, "Internal server error")

    message = (
        exception.detail
        if isinstance(exception.detail, str)
        else "Request failed"
    )
    logger.warning(
        "http_request_failed",
        method=request.method,
        path=request.url.path,
        request_id=getattr(request.state, "request_id", "unknown"),
        status_code=exception.status_code,
    )
    return error_response(exception.status_code, message)


async def internal_exception_handler(
    request: Request,
    _exception: Exception,
) -> JSONResponse:
    logger.exception(
        "unhandled_request_error",
        method=request.method,
        path=request.url.path,
        request_id=getattr(request.state, "request_id", "unknown"),
    )
    return error_response(500, "Internal server error")
