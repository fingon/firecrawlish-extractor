import json
import socket
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI
from pydantic import SecretStr

from firecrawlish_extractor.main import create_app
from firecrawlish_extractor.settings import Settings

ARTICLE_HTML = (Path(__file__).parent / "testdata/article.html").read_bytes()
SEARCH_RESPONSE = {
    "results": [
        {
            "content": "A fixture search result.",
            "score": 1.0,
            "title": "Fixture search result",
            "url": "https://example.com/search-result",
        }
    ]
}


class FixtureRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        request_path = self.path.split("?", 1)[0]
        if request_path == "/article":
            body = ARTICLE_HTML
            content_type = "text/html; charset=utf-8"
        elif request_path == "/search":
            body = json.dumps(SEARCH_RESPONSE).encode("utf-8")
            content_type = "application/json"
        else:
            self.send_error(404)
            return

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def fixture_server(bind_host: str = "127.0.0.1") -> Generator[str]:
    server = ThreadingHTTPServer(
        (bind_host, 0),
        FixtureRequestHandler,
    )
    thread = threading.Thread(
        target=server.serve_forever,
        name="fixture-http-server",
        daemon=True,
    )
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        if thread.is_alive():
            raise RuntimeError("fixture HTTP server did not stop")


def wait_for_http(url: str, *, timeout_sec: float = 10.0) -> httpx.Response:
    deadline = time.monotonic() + timeout_sec
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with httpx.Client(timeout=0.5) as client:
                response = client.get(url)
            if response.status_code == 200:
                return response
            last_error = RuntimeError(
                f"{url} returned HTTP {response.status_code}"
            )
        except httpx.HTTPError as exc:
            last_error = exc
        time.sleep(0.05)

    raise RuntimeError(f"Timed out waiting for {url}: {last_error}")


@contextmanager
def running_app(
    *,
    api_key: str | None = None,
    searxng_url: str | None = None,
) -> Generator[str]:
    port = free_port()
    settings = Settings(
        allow_private_networks=True,
        firecrawlish_api_key=(
            SecretStr(api_key) if api_key is not None else None
        ),
        port=port,
        searxng_url=searxng_url,
    )
    application: FastAPI = create_app(settings)
    server = uvicorn.Server(
        uvicorn.Config(
            application,
            access_log=False,
            host="127.0.0.1",
            lifespan="on",
            log_config=None,
            port=port,
        )
    )
    thread = threading.Thread(
        target=server.run,
        name="test-uvicorn-server",
        daemon=True,
    )
    thread.start()
    service_url = f"http://127.0.0.1:{port}"
    try:
        wait_for_http(f"{service_url}/healthz")
        yield service_url
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        if thread.is_alive():
            raise RuntimeError("test Uvicorn server did not stop")
