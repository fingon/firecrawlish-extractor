from typing import Literal

from fastapi.responses import JSONResponse
from pydantic import BaseModel

ERROR_SUCCESS = False


class ErrorEnvelope(BaseModel):
    success: Literal[False] = ERROR_SUCCESS
    error: str


def error_response(
    status_code: int,
    message: str,
    *,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    envelope = ErrorEnvelope(error=message)
    return JSONResponse(
        status_code=status_code,
        content=envelope.model_dump(),
        headers=headers,
    )
