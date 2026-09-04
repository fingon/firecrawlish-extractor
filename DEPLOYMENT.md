# Deployment

Firecrawlish Extractor is a stateless HTTP service. The image listens on port
`3002`, runs as UID/GID `10001`, and does not need a volume. The examples below
keep the API on loopback; put a TLS-authenticated reverse proxy in front of it
when another host or service needs access.

## Build and run

Build the image from this repository with Docker or Podman. `PYTHON_BASE` is a
complete `FROM` reference, so a release build can replace the tag with a
registry digest.

```sh
docker build \
  --build-arg PYTHON_BASE=python:3.14.7-slim \
  --tag firecrawlish-extractor:local .
```

Start an authenticated instance with a read-only root filesystem. The `/tmp`
tmpfs is an intentionally empty scratch area for libraries that need a
temporary directory; the service itself has no persistent write path.

```sh
docker run --detach \
  --name firecrawlish-extractor \
  --publish 127.0.0.1:3002:3002 \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  --env FIRECRAWLISH_API_KEY=replace-with-a-long-random-secret \
  firecrawlish-extractor:local
```

Verify startup and the image user:

```sh
curl --fail http://127.0.0.1:3002/healthz
docker run --rm --entrypoint python firecrawlish-extractor:local \
  -c 'import os; print(os.getuid())'
```

The second command prints `10001`. To use Podman, substitute `podman` for
`docker`; the repository's local verification command is
`CONTAINER_RUNTIME=podman make smoke`.

Without `FIRECRAWLISH_API_KEY`, inbound authentication is disabled. Do not set
`ALLOW_PRIVATE_NETWORKS=true` unless callers are explicitly trusted to scrape
private network destinations.

## Hermes Agent

For a Hermes process running on the same host as the published container, set
the Firecrawl provider to this service and use the same secret:

```sh
export FIRECRAWL_API_URL=http://127.0.0.1:3002
export FIRECRAWL_API_KEY=replace-with-a-long-random-secret
```

Hermes uses `/v2/scrape` for `web_extract` and `/v2/search` for `web_search`.
Search is available only when `SEARXNG_URL` is configured on the service. If
Hermes runs in another container on the same container network, use
`http://firecrawlish-extractor:3002` as `FIRECRAWL_API_URL` instead.

## SearXNG

The service accepts an existing SearXNG origin through `SEARXNG_URL`. The
upstream must enable SearXNG's JSON result format; otherwise its `/search`
request returns HTTP 403. The service sends `q`, `format=json`, and
`pageno=1`.

The following uses the official SearXNG Compose template and joins the
Firecrawlish container to its default network:

```sh
mkdir -p ./searxng/core-config
cd ./searxng
curl -fsSL \
  -O https://raw.githubusercontent.com/searxng/searxng/master/container/docker-compose.yml \
  -O https://raw.githubusercontent.com/searxng/searxng/master/container/.env.example
cp .env.example .env
```

Add JSON to `core-config/settings.yml` under the existing `search:` section:

```yaml
search:
  formats:
    - html
    - json
```

Start SearXNG, then start Firecrawlish on the Compose network:

```sh
docker compose up --detach
docker run --detach \
  --name firecrawlish-extractor \
  --network searxng_default \
  --publish 127.0.0.1:3002:3002 \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  --env FIRECRAWLISH_API_KEY=replace-with-a-long-random-secret \
  --env SEARXNG_URL=http://searxng-core:8080 \
  firecrawlish-extractor:local
```

Check both sides before pointing Hermes at the service:

```sh
curl --fail \
  'http://127.0.0.1:8080/search?q=firecrawl&format=json'
curl --fail http://127.0.0.1:3002/healthz
```

The official SearXNG template also provides Valkey and configuration/data
mounts. Keep those mounts for SearXNG; Firecrawlish itself remains stateless.
Stop the example with:

```sh
docker rm --force firecrawlish-extractor
docker compose down
```

See the [SearXNG container installation guide](https://docs.searxng.org/admin/installation-docker)
and [SearXNG search API](https://docs.searxng.org/dev/search_api) for template
updates and engine configuration.
