from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import AnyHttpUrl, Field, SecretStr, TypeAdapter, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

type LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

DEFAULT_PORT = 3002
_HTTP_URL_ADAPTER = TypeAdapter(AnyHttpUrl)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_ignore_empty=True,
        env_prefix="",
        extra="ignore",
    )

    searxng_url: str | None = None
    firecrawlish_api_key: SecretStr | None = None
    port: int = Field(default=DEFAULT_PORT, ge=1, le=65535)
    log_level: LogLevel = "INFO"
    allow_private_networks: bool = False

    @field_validator("searxng_url", mode="before")
    @classmethod
    def validate_searxng_url(cls, value: object) -> str | None:
        if value is None or value == "":
            return None

        validated_url = _HTTP_URL_ADAPTER.validate_python(value)
        parsed_url = urlsplit(str(validated_url))
        if parsed_url.username is not None or parsed_url.password is not None:
            raise ValueError("SEARXNG_URL must not contain user information")
        if parsed_url.query or parsed_url.fragment:
            raise ValueError("SEARXNG_URL must not contain a query or fragment")

        return urlunsplit(
            (
                parsed_url.scheme,
                parsed_url.netloc,
                parsed_url.path.rstrip("/"),
                "",
                "",
            )
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
