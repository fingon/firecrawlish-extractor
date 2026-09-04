from fastapi import FastAPI

from . import __version__
from .logging_config import configure_logging
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
    return application


app = create_app()
