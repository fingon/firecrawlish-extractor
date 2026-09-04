import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from firecrawlish_extractor.main import create_app
from firecrawlish_extractor.search import (
    SearchError,
    SearXNGClient,
    WebResult,
)
from firecrawlish_extractor.settings import Settings

type Handler = Callable[[httpx.Request], Coroutine[Any, Any, httpx.Response]]


class StubSearcher:
    def __init__(
        self,
        results: list[WebResult] | None = None,
        error: SearchError | None = None,
    ) -> None:
        self.results = results if results is not None else []
        self.error = error
        self.calls: list[tuple[str, int]] = []

    async def search(self, query: str, limit: int) -> list[WebResult]:
        self.calls.append((query, limit))
        if self.error is not None:
            raise self.error
        return self.results


def _client_with_searcher(searcher: StubSearcher) -> TestClient:
    application = create_app(Settings(searxng_url="http://search.local"))
    client = TestClient(application)
    client.__enter__()
    application.state.searcher = searcher
    return client


def _close_client(client: TestClient) -> None:
    client.__exit__(None, None, None)


@pytest.mark.anyio
async def test_searxng_client_requests_first_json_page_and_sorts_results() -> (
    None
):
    seen_requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "low",
                        "url": "https://example.com/low",
                        "content": "low content",
                        "score": 1,
                    },
                    {
                        "title": "high",
                        "url": "https://example.com/high",
                        "content": "high content",
                        "score": 3,
                    },
                    {
                        "title": "tie first",
                        "url": "https://example.com/tie-first",
                        "content": "first",
                        "score": 2,
                    },
                    {
                        "title": "tie second",
                        "url": "https://example.com/tie-second",
                        "content": "second",
                        "score": 2,
                    },
                    {
                        "title": "no score first",
                        "url": "https://example.com/no-score-first",
                        "content": "first",
                    },
                    {
                        "title": "no score second",
                        "url": "https://example.com/no-score-second",
                        "content": "second",
                    },
                    {
                        "title": "invalid result",
                        "url": "javascript:alert(1)",
                        "content": "must be omitted",
                        "score": 10,
                    },
                ],
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as client:
        searcher = SearXNGClient(client, "http://search.local/")
        results = await searcher.search("test query", limit=5)

    assert seen_requests[0].url.path == "/search"
    assert seen_requests[0].url.params["q"] == "test query"
    assert seen_requests[0].url.params["format"] == "json"
    assert seen_requests[0].url.params["pageno"] == "1"
    assert [result.title for result in results] == [
        "high",
        "tie first",
        "tie second",
        "low",
        "no score first",
    ]
    assert [result.position for result in results] == [1, 2, 3, 4, 5]


@pytest.mark.anyio
async def test_searxng_client_maps_missing_text_fields_to_empty_strings() -> (
    None
):
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"results": [{"url": "https://example.com/result"}]},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as client:
        results = await SearXNGClient(client, "http://search.local").search(
            "query",
            limit=10,
        )

    assert results == [
        WebResult(
            title="",
            url="https://example.com/result",
            description="",
            position=1,
        )
    ]


@pytest.mark.anyio
async def test_searxng_client_truncates_after_sorting() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "one",
                        "url": "https://example.com/1",
                        "score": 1,
                    },
                    {
                        "title": "two",
                        "url": "https://example.com/2",
                        "score": 2,
                    },
                    {
                        "title": "invalid",
                        "url": "not a URL",
                        "score": 10,
                    },
                ],
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as client:
        results = await SearXNGClient(client, "http://search.local").search(
            "query",
            limit=1,
        )

    assert [result.title for result in results] == ["two"]


@pytest.mark.parametrize(
    "payload",
    [
        {"query": ""},
        {"query": "   "},
        {"query": "a" * 501},
        {"query": "query", "limit": 0},
        {"query": "query", "limit": 101},
        {"query": "query", "limit": "10"},
        {"query": "query", "unknown": True},
    ],
)
def test_search_rejects_invalid_requests(payload: dict[str, object]) -> None:
    searcher = StubSearcher()
    client = _client_with_searcher(searcher)

    try:
        response = client.post("/v2/search", json=payload)
    finally:
        _close_client(client)

    assert response.status_code == 400
    assert response.json() == {"success": False, "error": "Invalid request"}
    assert searcher.calls == []


def test_search_returns_unavailable_when_searxng_is_not_configured() -> None:
    application = create_app(Settings())
    with TestClient(application) as client:
        response = client.post("/v2/search", json={"query": "query"})

    assert response.status_code == 503
    assert response.json() == {
        "success": False,
        "error": "Search is not configured",
    }


def test_search_translates_results_and_passes_limit() -> None:
    results = [
        WebResult(
            title="Result",
            url="https://example.com/result",
            description="Description",
            position=1,
        )
    ]
    searcher = StubSearcher(results=results)
    client = _client_with_searcher(searcher)

    try:
        response = client.post(
            "/v2/search",
            json={"query": "query", "limit": 7},
        )
    finally:
        _close_client(client)

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "data": {
            "web": [
                {
                    "title": "Result",
                    "url": "https://example.com/result",
                    "description": "Description",
                    "position": 1,
                }
            ]
        },
    }
    assert searcher.calls == [("query", 7)]


@pytest.mark.parametrize(
    ("status_code", "category", "message"),
    [
        (502, "upstream_failure", "SearXNG request failed"),
        (504, "timeout", "SearXNG request exceeded its deadline"),
    ],
)
def test_search_translates_upstream_failures(
    status_code: int,
    category: str,
    message: str,
) -> None:
    searcher = StubSearcher(
        error=SearchError(message, status_code=status_code, category=category),
    )
    client = _client_with_searcher(searcher)

    try:
        response = client.post("/v2/search", json={"query": "query"})
    finally:
        _close_client(client)

    assert response.status_code == status_code
    assert response.json() == {"success": False, "error": message}


@pytest.mark.anyio
async def test_searxng_client_rejects_http_failure() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(
            SearchError, match="unsuccessful response"
        ) as raised:
            await SearXNGClient(client, "http://search.local").search(
                "query",
                limit=10,
            )

    assert raised.value.status_code == 502
    assert raised.value.category == "upstream_failure"


@pytest.mark.anyio
async def test_searxng_client_rejects_invalid_json_and_shape() -> None:
    async def invalid_json_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    async def invalid_shape_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": {}})

    for handler in (invalid_json_handler, invalid_shape_handler):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
        ) as client:
            with pytest.raises(SearchError, match="invalid") as raised:
                await SearXNGClient(client, "http://search.local").search(
                    "query",
                    limit=10,
                )
        assert raised.value.status_code == 502
        assert raised.value.category == "invalid_response"


@pytest.mark.anyio
async def test_searxng_client_enforces_total_deadline() -> None:
    async def slow_handler(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.05)
        return httpx.Response(200, json={"results": []})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(slow_handler),
    ) as client:
        with pytest.raises(SearchError, match="deadline") as raised:
            await SearXNGClient(
                client,
                "http://search.local",
                deadline_sec=0.01,
            ).search("query", limit=10)

    assert raised.value.status_code == 504
    assert raised.value.category == "timeout"


@pytest.mark.anyio
async def test_searxng_client_maps_transport_errors() -> None:
    async def failing_handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection failed")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(failing_handler),
    ) as client:
        with pytest.raises(SearchError, match="request failed") as raised:
            await SearXNGClient(client, "http://search.local").search(
                "query",
                limit=10,
            )

    assert raised.value.status_code == 502
    assert raised.value.category == "upstream_failure"
