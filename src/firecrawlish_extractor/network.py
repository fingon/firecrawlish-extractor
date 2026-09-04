import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Final
from urllib.parse import urljoin, urlsplit

import httpx
from pydantic import AnyHttpUrl, TypeAdapter, ValidationError

SCRAPE_DEADLINE_SEC: Final[float] = 45.0
MAX_REDIRECTS: Final[int] = 5
MAX_DECODED_BODY_BYTES: Final[int] = 10 * 1024 * 1024
DEFAULT_HTTP_PORT: Final[int] = 80
DEFAULT_HTTPS_PORT: Final[int] = 443
ALLOWED_SCHEMES: Final[frozenset[str]] = frozenset({"http", "https"})

_HTTP_URL_ADAPTER = TypeAdapter(AnyHttpUrl)

type IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
type AddressResolver = Callable[[str, int], Awaitable[Sequence[IPAddress]]]


class FetchError(Exception):
    def __init__(
        self, message: str, *, status_code: int, category: str
    ) -> None:
        super().__init__(message)
        self.category = category
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class TargetURL:
    url: httpx.URL
    hostname: str
    port: int
    host_header: str
    sni_hostname: str


@dataclass(frozen=True, slots=True)
class FetchedResponse:
    url: str
    status_code: int
    content_type: str | None
    body: bytes


@dataclass(frozen=True, slots=True)
class _Redirect:
    location: str


def validate_target_url(raw_url: str) -> TargetURL:
    try:
        split_url = urlsplit(raw_url)
        hostname = split_url.hostname
        split_port = split_url.port
    except (TypeError, ValueError) as exc:
        raise FetchError(
            "The supplied URL is invalid",
            status_code=400,
            category="invalid_url",
        ) from exc

    if (
        not isinstance(raw_url, str)
        or not raw_url
        or not all(character.isprintable() for character in raw_url)
        or split_url.scheme.lower() not in ALLOWED_SCHEMES
        or not split_url.netloc
        or not hostname
        or "#" in raw_url
        or split_url.username is not None
        or split_url.password is not None
    ):
        raise FetchError(
            "The supplied URL is invalid",
            status_code=400,
            category="invalid_url",
        )

    try:
        validated_url = _HTTP_URL_ADAPTER.validate_python(raw_url)
    except ValidationError as exc:
        raise FetchError(
            "The supplied URL is invalid",
            status_code=400,
            category="invalid_url",
        ) from exc

    url = httpx.URL(str(validated_url))
    if not url.raw_host:
        raise FetchError(
            "The supplied URL is invalid",
            status_code=400,
            category="invalid_url",
        )

    hostname = url.raw_host.decode("ascii")
    port = (
        split_port
        if split_port is not None
        else DEFAULT_HTTPS_PORT
        if url.scheme == "https"
        else DEFAULT_HTTP_PORT
    )
    host_header = hostname
    if ":" in hostname:
        host_header = f"[{hostname}]"
    if url.port is not None:
        host_header = f"{host_header}:{url.port}"

    return TargetURL(
        url=url,
        hostname=hostname,
        port=port,
        host_header=host_header,
        sni_hostname=hostname,
    )


async def resolve_host(hostname: str, port: int) -> tuple[IPAddress, ...]:
    loop = asyncio.get_running_loop()
    address_infos = await loop.getaddrinfo(
        hostname,
        port,
        family=socket.AF_UNSPEC,
        type=socket.SOCK_STREAM,
        proto=socket.IPPROTO_TCP,
    )
    addresses: list[IPAddress] = []
    for address_info in address_infos:
        socket_address = address_info[4]
        address = ipaddress.ip_address(socket_address[0])
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise FetchError(
            "The origin hostname did not resolve",
            status_code=502,
            category="dns_failure",
        )
    return tuple(addresses)


def is_allowed_address(
    address: IPAddress,
    *,
    allow_private_networks: bool,
) -> bool:
    mapped_address = (
        address.ipv4_mapped
        if isinstance(address, ipaddress.IPv6Address)
        else None
    )
    checked_address = mapped_address if mapped_address is not None else address
    if (
        checked_address.is_multicast
        or checked_address.is_reserved
        or checked_address.is_unspecified
    ):
        return False
    if checked_address.is_global:
        return True
    return allow_private_networks and (
        checked_address.is_private
        or checked_address.is_loopback
        or checked_address.is_link_local
    )


def create_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        follow_redirects=False,
        trust_env=False,
    )


class SafeHTTPFetcher:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        allow_private_networks: bool,
        resolver: AddressResolver = resolve_host,
        max_redirects: int = MAX_REDIRECTS,
        max_body_bytes: int = MAX_DECODED_BODY_BYTES,
        deadline_sec: float = SCRAPE_DEADLINE_SEC,
    ) -> None:
        if max_redirects < 0:
            raise ValueError("max_redirects must not be negative")
        if max_body_bytes <= 0:
            raise ValueError("max_body_bytes must be positive")
        if deadline_sec <= 0:
            raise ValueError("deadline_sec must be positive")

        self._allow_private_networks = allow_private_networks
        self._client = client
        self._deadline_sec = deadline_sec
        self._httpx_timeout = httpx.Timeout(deadline_sec)
        self._max_body_bytes = max_body_bytes
        self._max_redirects = max_redirects
        self._resolver = resolver

    async def fetch(self, raw_url: str) -> FetchedResponse:
        try:
            async with asyncio.timeout(self._deadline_sec):
                return await self._fetch_with_redirects(raw_url)
        except FetchError:
            raise
        except TimeoutError as exc:
            raise FetchError(
                "The origin request exceeded its deadline",
                status_code=504,
                category="timeout",
            ) from exc
        except httpx.TimeoutException as exc:
            raise FetchError(
                "The origin request exceeded its deadline",
                status_code=504,
                category="timeout",
            ) from exc
        except httpx.HTTPError as exc:
            raise FetchError(
                "The origin request failed",
                status_code=502,
                category="origin_failure",
            ) from exc

    async def _fetch_with_redirects(self, raw_url: str) -> FetchedResponse:
        target = validate_target_url(raw_url)
        redirect_count = 0

        while True:
            result = await self._fetch_once(target)
            if not isinstance(result, _Redirect):
                return result
            if redirect_count >= self._max_redirects:
                raise FetchError(
                    "The origin exceeded the redirect limit",
                    status_code=502,
                    category="redirect_limit",
                )

            redirected_url = urljoin(str(target.url), result.location)
            target = validate_target_url(redirected_url)
            redirect_count += 1

    async def _fetch_once(
        self,
        target: TargetURL,
    ) -> FetchedResponse | _Redirect:
        addresses = await self._resolve_addresses(target)
        address = addresses[0]
        connection_url = target.url.copy_with(host=str(address))
        request_extensions = {
            "sni_hostname": target.sni_hostname,
        }
        request_headers = {
            "Connection": "close",
            "Host": target.host_header,
        }

        async with self._client.stream(
            "GET",
            connection_url,
            extensions=request_extensions,
            follow_redirects=False,
            headers=request_headers,
            timeout=self._httpx_timeout,
        ) as response:
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise FetchError(
                        "The origin returned an unusable redirect",
                        status_code=502,
                        category="invalid_redirect",
                    )
                return _Redirect(location=location)

            body = await self._read_body(response)
            return FetchedResponse(
                body=body,
                content_type=response.headers.get("content-type"),
                status_code=response.status_code,
                url=str(target.url),
            )

    async def _resolve_addresses(
        self, target: TargetURL
    ) -> tuple[IPAddress, ...]:
        try:
            addresses = tuple(
                await self._resolver(target.hostname, target.port)
            )
        except FetchError:
            raise
        except OSError as exc:
            raise FetchError(
                "The origin hostname could not be resolved",
                status_code=502,
                category="dns_failure",
            ) from exc

        if not addresses:
            raise FetchError(
                "The origin hostname did not resolve",
                status_code=502,
                category="dns_failure",
            )
        if any(
            not is_allowed_address(
                address,
                allow_private_networks=self._allow_private_networks,
            )
            for address in addresses
        ):
            raise FetchError(
                "The origin address is not allowed",
                status_code=400,
                category="blocked_address",
            )
        return addresses

    async def _read_body(self, response: httpx.Response) -> bytes:
        content_lengths = response.headers.get_list("content-length")
        if len(content_lengths) > 1:
            raise FetchError(
                "The origin returned an invalid content length",
                status_code=502,
                category="invalid_response",
            )
        if content_lengths:
            try:
                content_length = int(content_lengths[0])
            except ValueError as exc:
                raise FetchError(
                    "The origin returned an invalid content length",
                    status_code=502,
                    category="invalid_response",
                ) from exc
            if content_length < 0:
                raise FetchError(
                    "The origin returned an invalid content length",
                    status_code=502,
                    category="invalid_response",
                )
            if content_length > self._max_body_bytes:
                raise FetchError(
                    "The origin response exceeded the download limit",
                    status_code=413,
                    category="response_too_large",
                )

        body_parts: list[bytes] = []
        body_size_bytes = 0
        async for chunk in response.aiter_bytes():
            body_size_bytes += len(chunk)
            if body_size_bytes > self._max_body_bytes:
                raise FetchError(
                    "The origin response exceeded the download limit",
                    status_code=413,
                    category="response_too_large",
                )
            body_parts.append(chunk)
        return b"".join(body_parts)
