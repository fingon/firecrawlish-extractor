from fastapi import FastAPI

from firecrawlish_extractor.main import create_app
from firecrawlish_extractor.settings import Settings


def test_create_app_uses_supplied_settings() -> None:
    settings = Settings(port=3003, allow_private_networks=True)

    application = create_app(settings)

    assert isinstance(application, FastAPI)
    assert application.title == "Firecrawlish Extractor"
    assert application.state.settings is settings
