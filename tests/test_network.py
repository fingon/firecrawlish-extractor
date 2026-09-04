import asyncio
import gzip
import ipaddress
from collections.abc import Callable, Coroutine, Sequence
from typing import Any

import httpx
import pytest

from firecrawlish_extractor.network import (
    MAX_DECODED_BODY_BYTES,
    SCRAPE_DEADLINE_SEC,
    AddressResolver,
    FetchedResponse,
    FetchError,
    IPAddress,
    SafeHTTPFetcher,
    create_http_client,
    validate_target_url,
)

type Handler = Callable[[httpx.Request], Coroutine[Any, Any, httpx.Response]]


def _address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    return ipaddress.ip_address(value)


async def _fetch_with_handler(
    handler: Handler,
    *,
    address_values: Sequence[str] = ("93.184.216.34",),
    allow_private_networks: bool = False,
    resolver: AddressResolver | None = None,
    max_body_bytes: int = MAX_DECODED_BODY_BYTES,
    deadline_sec: float = SCRAPE_DEADLINE_SEC,
) -> FetchedResponse:
    transport = httpx.MockTransport(handler)
    address_values_as_ip = tuple(_address(value) for value in address_values)

    async def resolve(_hostname: str, _port: int) -> Sequence[IPAddress]:
        return address_values_as_ip

    active_resolver = resolve if resolver is None else resolver
    async with httpx.AsyncClient(transport=transport) as client:
        fetcher = SafeHTTPFetcher(
            client,
            allow_private_networks=allow_private_networks,
            resolver=active_resolver,
            max_body_bytes=max_body_bytes,
            deadline_sec=deadline_sec,
        )
        result = await fetcher.fetch("https://example.com/article?part=1")
    return result


@pytest.mark.anyio
async def test_fetch_pins_connection_to_validated_address() -> None:
    seen_requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        return httpx.Response(
            200,
            headers={
                "content-length": "11",
                "content-type": "text/html; charset=utf-8",
            },
            content=b"hello world",
        )

    result = await _fetch_with_handler(handler)

    assert result.body == b"hello world"
    assert result.status_code == 200
    assert result.content_type == "text/html; charset=utf-8"
    assert result.url == "https://example.com/article?part=1"
    assert len(seen_requests) == 1
    request = seen_requests[0]
    assert str(request.url) == "https://93.184.216.34/article?part=1"
    assert request.headers["host"] == "example.com"
    assert request.extensions["sni_hostname"] == "example.com"
    assert request.headers["connection"] == "close"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "address_value",
    [
        "0.0.0.0",
        "10.0.0.1",
        "127.0.0.1",
        "169.254.1.1",
        "192.0.2.1",
        "224.0.0.1",
        "::",
        "::1",
        "::ffff:127.0.0.1",
        "2001:db8::1",
        "ff02::1",
        "fe80::1",
        "fd00::1",
    ],
)
async def test_fetch_rejects_unsafe_addresses(address_value: str) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("unsafe address reached the HTTP client")

    with pytest.raises(FetchError, match="not allowed") as raised:
        await _fetch_with_handler(handler, address_values=(address_value,))

    assert raised.value.category == "blocked_address"
    assert raised.value.status_code == 400


@pytest.mark.anyio
@pytest.mark.parametrize("address_value", ["10.0.0.1", "127.0.0.1", "fe80::1"])
async def test_private_network_opt_in_allows_private_targets(
    address_value: str,
) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"ok")

    result = await _fetch_with_handler(
        handler,
        address_values=(address_value,),
        allow_private_networks=True,
    )

    assert result.body == b"ok"


@pytest.mark.anyio
async def test_mixed_dns_answers_are_rejected() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("mixed DNS answers reached the HTTP client")

    with pytest.raises(FetchError, match="not allowed"):
        await _fetch_with_handler(
            handler,
            address_values=("93.184.216.34", "10.0.0.1"),
        )


@pytest.mark.parametrize(
    "raw_url",
    [
        "",
        "ftp://example.com/article",
        "https:///article",
        "https://user:pass@example.com/article",
        "https://example.com/article#fragment",
        "https://example.com:bad/article",
        "https://example.com:65536/article",
        "https://exa mple.com/article",
    ],
)
def test_validate_target_url_rejects_unsafe_url_syntax(raw_url: str) -> None:
    with pytest.raises(FetchError, match="supplied URL is invalid") as raised:
        validate_target_url(raw_url)

    assert raised.value.category == "invalid_url"
    assert raised.value.status_code == 400


@pytest.mark.anyio
async def test_redirect_targets_are_revalidated() -> None:
    request_count = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(
            302,
            headers={"location": "http://private.example/next"},
        )

    async def resolver(hostname: str, _port: int) -> Sequence[IPAddress]:
        if hostname == "private.example":
            return (_address("10.0.0.1"),)
        return (_address("93.184.216.34"),)

    with pytest.raises(FetchError, match="not allowed") as raised:
        await _fetch_with_handler(handler, resolver=resolver)

    assert raised.value.category == "blocked_address"
    assert request_count == 1


@pytest.mark.anyio
async def test_redirect_limit_is_enforced() -> None:
    request_count = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(
            302,
            headers={"location": "https://example.com/next"},
        )

    with pytest.raises(FetchError, match="redirect limit") as raised:
        await _fetch_with_handler(handler)

    assert raised.value.category == "redirect_limit"
    assert request_count == 6


@pytest.mark.anyio
async def test_content_length_limit_is_enforced_before_reading() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-length": "5"},
            content=b"12345",
        )

    with pytest.raises(FetchError, match="download limit") as raised:
        await _fetch_with_handler(handler, max_body_bytes=4)

    assert raised.value.category == "response_too_large"


@pytest.mark.anyio
async def test_streamed_decoded_body_limit_is_enforced() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"12345")

    with pytest.raises(FetchError, match="download limit") as raised:
        await _fetch_with_handler(handler, max_body_bytes=4)

    assert raised.value.category == "response_too_large"


@pytest.mark.anyio
async def test_decoded_body_limit_applies_after_content_decoding() -> None:
    compressed_body = gzip.compress(b"a" * 1000)

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "content-encoding": "gzip",
                "content-length": str(len(compressed_body)),
            },
            content=compressed_body,
        )

    with pytest.raises(FetchError, match="download limit") as raised:
        await _fetch_with_handler(handler, max_body_bytes=100)

    assert raised.value.category == "response_too_large"


@pytest.mark.anyio
async def test_total_deadline_includes_dns_resolution() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"ok")

    async def slow_resolver(
        _hostname: str,
        _port: int,
    ) -> Sequence[IPAddress]:
        await asyncio.sleep(0.05)
        return (_address("93.184.216.34"),)

    with pytest.raises(FetchError, match="deadline") as raised:
        await _fetch_with_handler(
            handler,
            resolver=slow_resolver,
            deadline_sec=0.01,
        )

    assert raised.value.category == "timeout"
    assert raised.value.status_code == 504


@pytest.mark.anyio
async def test_dns_failures_are_reported_without_reaching_http_client() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("DNS failure reached the HTTP client")

    async def failing_resolver(
        _hostname: str,
        _port: int,
    ) -> Sequence[ipaddress.IPv4Address]:
        raise OSError("resolver failure")

    with pytest.raises(FetchError, match="could not be resolved") as raised:
        await _fetch_with_handler(handler, resolver=failing_resolver)

    assert raised.value.category == "dns_failure"
    assert raised.value.status_code == 502


@pytest.mark.anyio
async def test_http_client_disables_environment_proxy_discovery() -> None:
    client = create_http_client()

    try:
        assert client._trust_env is False
    finally:
        await client.aclose()
