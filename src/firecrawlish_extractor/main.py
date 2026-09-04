from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException

from . import __version__
from .errors import (
    http_exception_handler,
    internal_exception_handler,
    request_validation_error_handler,
)
from .health import create_health_router
from .logging_config import configure_logging
from .middleware import BearerAuthenticationMiddleware, RequestIDMiddleware
from .settings import Settings, get_settings

APPLICATION_TITLE = "Firecrawlish Extractor"


def create_app(settings: Settings | None = None) -> FastAPI:
    application_settings = settings if settings is not None else get_settings()
    configure_logging(application_settings.log_level)

    application = FastAPI(
        title=APPLICATION_TITLE,
        version=__version__,
    )
    application.state.settings = application_settings
    application.add_exception_handler(
        RequestValidationError,
        request_validation_error_handler,
    )
    application.add_exception_handler(HTTPException, http_exception_handler)
    application.add_exception_handler(Exception, internal_exception_handler)
    application.include_router(create_health_router(application_settings))
    api_key = application_settings.firecrawlish_api_key
    application.add_middleware(
        BearerAuthenticationMiddleware,
        api_key=api_key.get_secret_value() if api_key is not None else None,
    )
    application.add_middleware(RequestIDMiddleware)
    return application


app = create_app()
