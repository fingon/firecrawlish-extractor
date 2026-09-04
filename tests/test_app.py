from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from firecrawlish_extractor.main import create_app
from firecrawlish_extractor.settings import Settings


def test_create_app_uses_supplied_settings() -> None:
    settings = Settings(port=3003, allow_private_networks=True)

    application = create_app(settings)

    assert isinstance(application, FastAPI)
    assert application.title == "Firecrawlish Extractor"
    assert application.state.settings is settings


def test_healthz_reports_configured_capabilities() -> None:
    application = create_app(Settings(searxng_url="https://search.example/"))

    response = TestClient(application).get("/healthz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "capabilities": {"scrape": True, "search": True},
    }


def test_healthz_is_not_authenticated() -> None:
    application = create_app(
        Settings(firecrawlish_api_key=SecretStr("test-secret")),
    )

    response = TestClient(application).get("/healthz")

    assert response.status_code == 200


@pytest.mark.parametrize(
    ("authorization", "expected_status"),
    [
        (None, 401),
        ("Basic test-secret", 401),
        ("Bearer wrong-secret", 401),
        ("Bearer test-secret", 404),
    ],
)
def test_v2_authentication(
    authorization: str | None,
    expected_status: int,
) -> None:
    application = create_app(
        Settings(firecrawlish_api_key=SecretStr("test-secret")),
    )
    headers = {} if authorization is None else {"Authorization": authorization}

    response = TestClient(application).get(
        "/v2/not-implemented", headers=headers
    )

    assert response.status_code == expected_status
    assert response.json()["success"] is False
    if expected_status == 401:
        assert response.headers["WWW-Authenticate"] == "Bearer"
    else:
        assert "WWW-Authenticate" not in response.headers


def test_authentication_is_disabled_without_a_configured_key() -> None:
    application = create_app(Settings())

    response = TestClient(application).get(
        "/v2/not-implemented",
        headers={"Authorization": "Bearer arbitrary"},
    )

    assert response.status_code == 404
    assert response.json() == {
        "success": False,
        "error": "Not Found",
    }


def test_request_id_is_preserved() -> None:
    application = create_app(Settings())
    request_id = "request-from-client"

    response = TestClient(application).get(
        "/healthz",
        headers={"X-Request-ID": request_id},
    )

    assert response.headers["X-Request-ID"] == request_id


def test_request_id_is_generated_when_missing() -> None:
    application = create_app(Settings())

    response = TestClient(application).get("/healthz")

    UUID(response.headers["X-Request-ID"])


def test_validation_errors_use_the_common_envelope() -> None:
    application = create_app(Settings())

    @application.get("/test-validation")
    async def test_validation(required: int) -> int:
        return required

    response = TestClient(application).get("/test-validation")

    assert response.status_code == 400
    assert response.json() == {
        "success": False,
        "error": "Invalid request",
    }


def test_internal_errors_use_the_common_envelope() -> None:
    application = create_app(Settings())

    @application.get("/test-error")
    async def test_error() -> None:
        raise RuntimeError("private failure detail")

    response = TestClient(
        application,
        raise_server_exceptions=False,
    ).get("/test-error")

    assert response.status_code == 500
    assert response.json() == {
        "success": False,
        "error": "Internal server error",
    }
    assert "private failure detail" not in response.text
