import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest

from tests.support import fixture_server, free_port, wait_for_http

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


def _container_runtime() -> str | None:
    for candidate in ("docker", "podman"):
        if shutil.which(candidate) is not None:
            return candidate
    return None


def _run(runtime: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        (runtime, *arguments),
        capture_output=True,
        check=False,
        cwd=REPOSITORY_ROOT,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"{runtime} command failed with exit code {result.returncode}:\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return result


@pytest.mark.integration
def test_container_is_non_root_read_only_and_scrapes_fixture() -> None:
    runtime = _container_runtime()
    if runtime is None:
        pytest.skip("neither Docker nor Podman is installed")

    image_tag = f"firecrawlish-extractor:test-{uuid4().hex}"
    container_id: str | None = None
    image_built = False
    with fixture_server(bind_host="0.0.0.0") as origin_url:
        origin_port = urlsplit(origin_url).port
        if origin_port is None:
            raise AssertionError("fixture server did not expose a port")

        try:
            _run(runtime, "build", "--tag", image_tag, ".")
            image_built = True

            uid_result = _run(
                runtime,
                "run",
                "--rm",
                "--entrypoint",
                "python",
                image_tag,
                "-c",
                "import os; print(os.getuid())",
            )
            assert uid_result.stdout.strip() == "10001"

            host_port = free_port()
            gateway_hostname = (
                "host.docker.internal"
                if runtime == "docker"
                else "host.containers.internal"
            )
            gateway_options = (
                ("--add-host", "host.docker.internal:host-gateway")
                if runtime == "docker"
                else ()
            )
            run_result = _run(
                runtime,
                "run",
                "--detach",
                "--read-only",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=16m",
                *gateway_options,
                "--publish",
                f"127.0.0.1:{host_port}:3002",
                "--env",
                "ALLOW_PRIVATE_NETWORKS=true",
                "--env",
                "FIRECRAWLISH_API_KEY=container-secret",
                "--env",
                f"SEARXNG_URL=http://{gateway_hostname}:{origin_port}",
                image_tag,
            )
            container_id = run_result.stdout.strip()
            if not container_id:
                raise AssertionError("container runtime did not return an ID")

            service_url = f"http://127.0.0.1:{host_port}"
            health_response = wait_for_http(f"{service_url}/healthz")
            assert health_response.json() == {
                "status": "ok",
                "capabilities": {"scrape": True, "search": True},
            }

            headers = {
                "Authorization": "Bearer container-secret",
                "Content-Type": "application/json",
            }
            container_origin_url = (
                f"http://{gateway_hostname}:{origin_port}/article"
            )
            with httpx.Client(base_url=service_url, headers=headers) as client:
                scrape_response = client.post(
                    "/v2/scrape",
                    json={"url": container_origin_url},
                )
                search_response = client.post(
                    "/v2/search",
                    json={"limit": 1, "query": "fixture query"},
                )

            assert scrape_response.status_code == 200
            assert (
                "# Example article"
                in scrape_response.json()["data"]["markdown"]
            )
            assert search_response.status_code == 200
            assert search_response.json()["data"]["web"][0]["title"] == (
                "Fixture search result"
            )
        finally:
            if container_id is not None:
                _run(runtime, "rm", "--force", container_id)
            if image_built:
                _run(runtime, "rmi", "--force", image_tag)
