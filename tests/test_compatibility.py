from typing import Any, cast

import httpx
import pytest

from tests.support import fixture_server, running_app

FORMATS = cast(
    "list[Any]",
    ["markdown", "html"],
)


def _assert_scrape_payload(response: httpx.Response) -> None:
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert set(payload["data"]) == {"markdown", "html", "metadata"}
    assert "# Example article" in payload["data"]["markdown"]
    assert "<h1>Example article</h1>" in payload["data"]["html"]
    assert payload["data"]["metadata"]["statusCode"] == 200


@pytest.mark.integration
@pytest.mark.parametrize(
    "api_key",
    [None, "compatibility-secret"],
    ids=["without-authentication", "with-authentication"],
)
def test_firecrawl_python_sdk_smoke(api_key: str | None) -> None:
    from firecrawl import Firecrawl
    from firecrawl.v2.types import SearchResultWeb

    with (
        fixture_server() as origin_url,
        running_app(
            api_key=api_key,
            searxng_url=origin_url,
        ) as service_url,
    ):
        if api_key is None:
            client = Firecrawl(
                api_url=service_url,
                max_retries=0,
                timeout=5,
            )
        else:
            client = Firecrawl(
                api_key=api_key,
                api_url=service_url,
                max_retries=0,
                timeout=5,
            )
        document = client.scrape(
            f"{origin_url}/article",
            formats=FORMATS,
        )
        assert document.markdown is not None
        assert "# Example article" in document.markdown
        assert document.html is not None
        assert "<h1>Example article</h1>" in document.html
        assert document.metadata is not None
        assert document.metadata.source_url == f"{origin_url}/article"
        assert document.metadata.status_code == 200

        search_data = client.search("fixture query", limit=1)
        web_results = search_data.web
        assert web_results is not None
        assert len(web_results) == 1
        web_result = web_results[0]
        assert isinstance(web_result, SearchResultWeb)
        assert web_result.title == "Fixture search result"
        assert web_result.position == 1


@pytest.mark.integration
@pytest.mark.parametrize(
    "api_key",
    [None, "hermes-secret"],
    ids=["without-authentication", "with-authentication"],
)
def test_hermes_payloads_work_with_and_without_authentication(
    api_key: str | None,
) -> None:
    with (
        fixture_server() as origin_url,
        running_app(
            api_key=api_key,
            searxng_url=origin_url,
        ) as service_url,
    ):
        headers = {"Content-Type": "application/json"}
        if api_key is not None:
            headers["Authorization"] = f"Bearer {api_key}"

        with httpx.Client(base_url=service_url, headers=headers) as client:
            scrape_response = client.post(
                "/v2/scrape",
                json={
                    "formats": ["markdown", "html"],
                    "url": f"{origin_url}/article",
                },
            )
            search_response = client.post(
                "/v2/search",
                json={"limit": 1, "query": "fixture query"},
            )

        _assert_scrape_payload(scrape_response)
        assert search_response.status_code == 200
        assert search_response.json()["data"]["web"][0]["position"] == 1
