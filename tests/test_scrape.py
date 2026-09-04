from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from firecrawlish_extractor.main import create_app
from firecrawlish_extractor.network import FetchedResponse, FetchError
from firecrawlish_extractor.settings import Settings

ARTICLE_HTML = Path("tests/testdata/article.html").read_text()
SCRAPE_URL = "https://example.com/articles/example"


def _response(
    *,
    body: str = ARTICLE_HTML,
    content_type: str | None = "text/html; charset=utf-8",
    status_code: int = 200,
    url: str = SCRAPE_URL,
) -> FetchedResponse:
    return FetchedResponse(
        body=body.encode("utf-8"),
        content_type=content_type,
        status_code=status_code,
        url=url,
    )


class StubFetcher:
    def __init__(
        self,
        response: FetchedResponse | None = None,
        error: FetchError | None = None,
    ) -> None:
        self.response = response
        self.error = error
        self.urls: list[str] = []

    async def fetch(self, url: str) -> FetchedResponse:
        self.urls.append(url)
        if self.error is not None:
            raise self.error
        if self.response is None:
            raise AssertionError("stub fetcher has no configured response")
        return self.response


def _client_with_fetcher(fetcher: StubFetcher) -> tuple[TestClient, object]:
    application = create_app(Settings())
    client = TestClient(application)
    client.__enter__()
    application.state.fetcher = fetcher
    return client, application


def _close_client(client: TestClient) -> None:
    client.__exit__(None, None, None)


def test_scrape_defaults_to_markdown_and_adds_final_response_metadata() -> None:
    fetcher = StubFetcher(_response())
    client, _application = _client_with_fetcher(fetcher)

    try:
        response = client.post(
            "/v2/scrape", json={"url": "https://input.example/"}
        )
    finally:
        _close_client(client)

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert set(payload["data"]) == {"markdown", "metadata"}
    assert "# Example article" in payload["data"]["markdown"]
    assert payload["data"]["metadata"] == {
        "title": "Example article",
        "description": "An example article description.",
        "language": "en",
        "siteName": "Example News",
        "sourceURL": SCRAPE_URL,
        "url": SCRAPE_URL,
        "statusCode": 200,
        "contentType": "text/html; charset=utf-8",
    }
    assert fetcher.urls == ["https://input.example/"]


def test_scrape_returns_only_requested_formats() -> None:
    fetcher = StubFetcher(_response())
    client, _application = _client_with_fetcher(fetcher)

    try:
        response = client.post(
            "/v2/scrape",
            json={"url": "https://input.example/", "formats": ["html"]},
        )
    finally:
        _close_client(client)

    assert response.status_code == 200
    assert set(response.json()["data"]) == {"html", "metadata"}
    assert "<article>" not in response.json()["data"]["html"]
    assert "<p>" in response.json()["data"]["html"]


@pytest.mark.parametrize(
    ("payload", "expected_error"),
    [
        ({"url": "https://example.com/", "unknown": True}, "Invalid request"),
        ({"url": "https://example.com/", "formats": []}, "Invalid request"),
        (
            {
                "url": "https://example.com/",
                "formats": ["markdown", "markdown"],
            },
            "Invalid request",
        ),
        (
            {"url": "https://example.com/", "formats": [{"type": "markdown"}]},
            "Invalid request",
        ),
        (
            {"url": "https://user:pass@example.com/"},
            "Invalid request",
        ),
        ({"formats": ["markdown"]}, "Invalid request"),
    ],
)
def test_scrape_rejects_invalid_request_shapes(
    payload: dict[str, object],
    expected_error: str,
) -> None:
    fetcher = StubFetcher(_response())
    client, _application = _client_with_fetcher(fetcher)

    try:
        response = client.post("/v2/scrape", json=payload)
    finally:
        _close_client(client)

    assert response.status_code == 400
    assert response.json() == {"success": False, "error": expected_error}
    assert fetcher.urls == []


def test_scrape_rejects_malformed_json_and_non_json_content_type() -> None:
    fetcher = StubFetcher(_response())
    client, _application = _client_with_fetcher(fetcher)

    try:
        malformed_response = client.post(
            "/v2/scrape",
            content="{not-json",
            headers={"Content-Type": "application/json"},
        )
        content_type_response = client.post(
            "/v2/scrape",
            content='{"url":"https://example.com/"}',
            headers={"Content-Type": "text/plain"},
        )
    finally:
        _close_client(client)

    for response in (malformed_response, content_type_response):
        assert response.status_code == 400
        assert response.json() == {"success": False, "error": "Invalid request"}
    assert fetcher.urls == []


@pytest.mark.parametrize(
    ("status_code", "content_type", "expected_status", "expected_error"),
    [
        (
            404,
            "text/html",
            502,
            "The origin returned an unsuccessful response",
        ),
        (
            200,
            "text/plain",
            415,
            "The origin did not return supported HTML content",
        ),
        (
            200,
            None,
            415,
            "The origin did not return supported HTML content",
        ),
    ],
)
def test_scrape_rejects_unusable_origin_responses(
    status_code: int,
    content_type: str | None,
    expected_status: int,
    expected_error: str,
) -> None:
    fetcher = StubFetcher(
        _response(status_code=status_code, content_type=content_type),
    )
    client, _application = _client_with_fetcher(fetcher)

    try:
        response = client.post("/v2/scrape", json={"url": SCRAPE_URL})
    finally:
        _close_client(client)

    assert response.status_code == expected_status
    assert response.json() == {"success": False, "error": expected_error}


def test_scrape_reports_empty_extraction_as_unprocessable() -> None:
    fetcher = StubFetcher(_response(body="<html><body></body></html>"))
    client, _application = _client_with_fetcher(fetcher)

    try:
        response = client.post("/v2/scrape", json={"url": SCRAPE_URL})
    finally:
        _close_client(client)

    assert response.status_code == 422
    assert response.json() == {
        "success": False,
        "error": "Trafilatura found no extractable content",
    }


@pytest.mark.parametrize(
    ("status_code", "category", "message"),
    [
        (400, "blocked_address", "The origin address is not allowed"),
        (
            413,
            "response_too_large",
            "The origin response exceeded the download limit",
        ),
        (502, "origin_failure", "The origin request failed"),
        (504, "timeout", "The origin request exceeded its deadline"),
    ],
)
def test_scrape_translates_fetch_failures(
    status_code: int,
    category: str,
    message: str,
) -> None:
    fetcher = StubFetcher(
        error=FetchError(message, status_code=status_code, category=category),
    )
    client, _application = _client_with_fetcher(fetcher)

    try:
        response = client.post("/v2/scrape", json={"url": SCRAPE_URL})
    finally:
        _close_client(client)

    assert response.status_code == status_code
    assert response.json() == {"success": False, "error": message}
