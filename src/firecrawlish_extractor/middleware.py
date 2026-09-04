from hmac import compare_digest
from uuid import uuid4

import structlog
from fastapi import Request
from starlette.middleware.base import (
    BaseHTTPMiddleware,
    RequestResponseEndpoint,
)
from starlette.responses import Response
from starlette.types import ASGIApp

from .responses import error_response

REQUEST_ID_HEADER = "X-Request-ID"
MAX_REQUEST_ID_LENGTH = 128
V2_PATH_PREFIX = "/v2"

logger = structlog.get_logger(__name__)


def _request_id_from(request: Request) -> str:
    request_id = request.headers.get(REQUEST_ID_HEADER)
    if (
        request_id
        and len(request_id) <= MAX_REQUEST_ID_LENGTH
        and all(character.isprintable() for character in request_id)
    ):
        return request_id
    return str(uuid4())


def _requires_authentication(request: Request) -> bool:
    path = request.url.path
    return path == V2_PATH_PREFIX or path.startswith(f"{V2_PATH_PREFIX}/")


def _bearer_token(authorization: str | None) -> str | None:
    if authorization is None:
        return None

    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not token:
        return None
    return token


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        request_id = _request_id_from(request)
        context_tokens = structlog.contextvars.bind_contextvars(
            request_id=request_id,
        )
        request.state.request_id = request_id
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.reset_contextvars(**context_tokens)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response


class BearerAuthenticationMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, api_key: str | None) -> None:
        super().__init__(app)
        self._api_key = api_key.encode("utf-8") if api_key else None

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        if self._api_key is None or not _requires_authentication(request):
            return await call_next(request)

        token = _bearer_token(request.headers.get("Authorization"))
        if token is None or not compare_digest(
            token.encode("utf-8"),
            self._api_key,
        ):
            logger.warning(
                "authentication_failed",
                method=request.method,
                path=request.url.path,
                request_id=getattr(request.state, "request_id", "unknown"),
            )
            return error_response(
                401,
                "Authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )

        return await call_next(request)
