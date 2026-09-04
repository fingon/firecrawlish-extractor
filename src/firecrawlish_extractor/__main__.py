import uvicorn

from .settings import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "firecrawlish_extractor.main:app",
        host="0.0.0.0",
        log_config=None,
        port=settings.port,
    )


if __name__ == "__main__":
    main()
