# Firecrawlish Extractor Design

## Status and goals

Firecrawlish Extractor is a small, stateless web service that implements the
Firecrawl API subset used by Hermes Agent for web search and extraction. It is
not intended to be a general Firecrawl replacement.

The service has two capabilities:

- Fetch an HTML document and use Trafilatura to return its main content as
  Markdown, cleaned HTML, or both.
- Optionally forward search queries to an operator-provided SearXNG instance
  and translate its results into the Firecrawl response shape.

The compatibility target is the Firecrawl provider in Hermes Agent revision
[`6327930`](https://github.com/NousResearch/hermes-agent/blob/63279301bcbdc185c1b07b98a9312eb0c862f26d/plugins/web/firecrawl/provider.py).
Only the `/v2` endpoints and fields described below are part of the contract.

The default deployment is one container with no required mounts or external
state. Search is optional: an instance without SearXNG remains healthy and can
serve extraction requests.

## Non-goals

The initial implementation does not provide:

- Firecrawl v1 compatibility or endpoints other than `/v2/scrape` and
  `/v2/search`.
- Crawling, batch jobs, asynchronous jobs, webhooks, queues, or durable caches.
- JavaScript execution, browser actions, screenshots, or rendering of
  client-side applications.
- PDF, image, audio, video, or arbitrary binary-document extraction.
- LLM-based cleaning, summarization, structured extraction, or search-result
  scraping.
- Firecrawl request options not explicitly listed in this document.
- A built-in search engine or fallback search provider.

Unsupported behavior must fail explicitly. The service must not silently
accept an option and then return output as if that option had been applied.

## Architecture

The implementation uses Python with FastAPI for the HTTP API, Pydantic for
request and configuration validation, HTTPX for outbound requests, and
Trafilatura for content and metadata extraction. Uvicorn serves the ASGI
application.

At process startup, configuration is parsed and validated once. The process
creates a shared HTTPX client with environment proxy discovery disabled. API
handlers perform asynchronous network I/O, while CPU-bound Trafilatura work
runs in a bounded worker pool so it cannot block the event loop or grow without
limit.

### Scrape flow

1. Authenticate the incoming request when service authentication is enabled.
2. Validate the request and the supplied URL.
3. Resolve the target and reject unsafe network destinations.
4. Fetch the response with streaming size enforcement. Revalidate and resolve
   every redirect target before following it.
5. Require a successful response with an HTML media type.
6. Run Trafilatura for every requested output format and extract metadata.
7. Return a Firecrawl-compatible response using the final response URL.

The HTTP client, rather than Trafilatura's downloader, owns fetching. This
allows the service to enforce redirect, timeout, response-size, TLS, and SSRF
policies before passing bytes to Trafilatura.

Trafilatura extraction uses the normal fallback cascade, excludes comments,
includes tables and links, and passes the final URL so relative links and URL
metadata can be resolved. Markdown uses `output_format="markdown"`; cleaned
HTML uses `output_format="html"`. The `html` field is extracted main-content
HTML, not the page's original source.

### Search flow

1. Authenticate and validate the request.
2. If SearXNG is not configured, return the unavailable response defined
   below.
3. Send `GET {SEARXNG_URL}/search` with `q`, `format=json`, and `pageno=1`.
4. Validate the upstream status and JSON response.
5. Stably sort results by numeric `score` in descending order. Results without
   a score retain upstream order relative to other equal-scoring results.
6. Truncate the first page to the requested limit and translate it to the
   Firecrawl web-result shape.

The service does not fetch additional SearXNG result pages. Consequently, a
successful response can contain fewer than `limit` entries. The configured
SearXNG instance must enable JSON responses; SearXNG returns HTTP 403 when the
requested format is disabled.

## HTTP API

All responses use `application/json`. The Firecrawl endpoints accept only
`application/json` requests. Request bodies have a fixed schema and reject
unknown fields.

### Authentication

When `FIRECRAWLISH_API_KEY` is non-empty, both `/v2` endpoints require:

```http
Authorization: Bearer <token>
```

The token is compared in constant time. A missing or invalid token produces
HTTP 401. When the variable is empty or absent, application authentication is
disabled and any incoming `Authorization` header is ignored. `/healthz` never
requires authentication.

To use authenticated mode with Hermes, set Hermes' `FIRECRAWL_API_KEY` to the
same secret and its `FIRECRAWL_API_URL` to this service's origin.

### `POST /v2/scrape`

Request:

```json
{
  "url": "https://example.com/article",
  "formats": ["markdown", "html"]
}
```

`url` is required and must be an absolute HTTP or HTTPS URL without embedded
credentials. `formats` is optional and defaults to `["markdown"]`. It must be
a non-empty, duplicate-free list containing only the string values `markdown`
and `html`. Firecrawl's object-form format requests are not supported. The
Python SDK's optional `origin` diagnostic field is accepted and discarded;
other unknown fields are rejected. The SDK's default values for
`onlyMainContent`, `mobile`, `skipTlsVerification`, `removeBase64Images`,
`fastMode`, `blockAds`, `maxAge`, and `storeInCache` are also accepted and
discarded; non-default values for these unsupported options are rejected.

Successful response:

```json
{
  "success": true,
  "data": {
    "markdown": "# Example\n\nArticle text.",
    "html": "<article><head>Example</head><p>Article text.</p></article>",
    "metadata": {
      "title": "Example",
      "description": "An example article",
      "language": "en",
      "sourceURL": "https://example.com/article",
      "url": "https://example.com/article",
      "statusCode": 200,
      "contentType": "text/html"
    }
  }
}
```

Only requested output fields are included. Metadata values that Trafilatura or
the origin does not provide are omitted, except `sourceURL`, `url`,
`statusCode`, and `contentType`, which come from the final HTTP response.

An empty Trafilatura result is an extraction failure; it is not reported as a
successful response with empty content.

### `POST /v2/search`

Request:

```json
{
  "query": "Trafilatura main content extraction",
  "limit": 5
}
```

`query` is required, must contain non-whitespace text, and may contain at most
500 characters. `limit` is optional, defaults to 10, and must be between 1 and
100 inclusive. Only ordinary web results are supported. The Python SDK's
optional `origin` diagnostic field is accepted and discarded; other unknown
fields are rejected.

Successful response:

```json
{
  "success": true,
  "data": {
    "web": [
      {
        "title": "Trafilatura documentation",
        "url": "https://trafilatura.readthedocs.io/",
        "description": "Documentation for web content extraction.",
        "position": 1
      }
    ]
  }
}
```

SearXNG fields map as follows:

| SearXNG field | Firecrawl field |
| --- | --- |
| `title` | `title` |
| `url` | `url` |
| `content` | `description` |
| Sorted result index + 1 | `position` |

Missing text fields become empty strings. Entries without a usable HTTP(S) URL
are omitted.

### Health check

`GET /healthz` returns HTTP 200 after configuration and application startup are
complete. It does not probe the public internet or SearXNG, because readiness
must not flap with optional upstream availability.

```json
{
  "status": "ok",
  "capabilities": {
    "scrape": true,
    "search": false
  }
}
```

`search` reflects whether `SEARXNG_URL` is configured, not whether the upstream
is currently reachable.

### Error behavior

Every application-generated error uses a Firecrawl-compatible envelope:

```json
{
  "success": false,
  "error": "Human-readable error without secrets"
}
```

The status codes are:

| Status | Meaning |
| --- | --- |
| 400 | Invalid JSON semantics, unknown options, or unsupported formats |
| 401 | Missing or invalid configured bearer token |
| 413 | Origin response exceeded the download limit |
| 415 | Origin did not return supported HTML content |
| 422 | Valid request but Trafilatura found no extractable content |
| 502 | Origin or SearXNG failed, returned an unusable response, or could not be reached |
| 503 | Search requested without `SEARXNG_URL` configured |
| 504 | Origin or SearXNG exceeded its deadline |

Framework validation errors must be converted to this envelope rather than
leaking FastAPI's native validation response. Internal exceptions are logged
with context and return a generic HTTP 500 envelope. Response bodies, secrets,
and exception details that can contain credentials are not returned to callers.

## Network security

An extraction API is an SSRF boundary. Before every origin request, including
redirects, the service must:

- Permit only `http` and `https` schemes and standard URL host syntax.
- Reject URL user information and fragments.
- Resolve the hostname and reject the request if any returned address is
  loopback, private, link-local, multicast, reserved, unspecified, or otherwise
  non-global.
- Reject IPv4-mapped IPv6 addresses when their mapped address is disallowed.
- Connect only after validation, retain the original hostname for HTTP Host and
  TLS SNI, and repeat validation for every redirect.
- Reject redirects after the configured maximum rather than returning partial
  content.

`ALLOW_PRIVATE_NETWORKS=true` permits private, loopback, and link-local targets
for explicitly trusted deployments, but it does not relax scheme, redirect,
size, timeout, or TLS verification rules. The configured SearXNG URL is an
operator-selected upstream and is not subject to the caller-supplied URL
policy, so a SearXNG container can reside on a private container network.

The HTTP client uses `trust_env=false`; ambient `HTTP_PROXY`, `HTTPS_PROXY`, and
`NO_PROXY` variables therefore cannot change routing or bypass destination
checks. Operators should also apply container-network egress policy as defense
in depth when deploying the service for untrusted callers.

## Resource limits

The initial limits are constants rather than configuration knobs:

| Limit | Value |
| --- | --- |
| Scrape deadline | 45 seconds |
| SearXNG deadline | 15 seconds |
| Redirects | 5 |
| Decoded response body | 10 MiB |
| Concurrent extraction jobs per process | 8 |

The download limit is enforced both against a trustworthy `Content-Length` and
while streaming the decoded body. A response is rejected as soon as it crosses
the limit. The 45-second scrape deadline leaves time for the current Hermes
provider to receive an error before its 60-second operation timeout.

The service runs one application process per container. Horizontal scaling is
performed with additional container replicas. Configuration hooks for these
limits should be added only after operational evidence shows that they are
needed.

## Configuration

All configuration is environment-based; no configuration file or mount is
required.

| Variable | Default | Purpose |
| --- | --- | --- |
| `SEARXNG_URL` | Unset | Absolute HTTP(S) origin for optional SearXNG search |
| `FIRECRAWLISH_API_KEY` | Unset | Enables inbound bearer authentication when non-empty |
| `PORT` | `3002` | TCP port on all container interfaces |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL` |
| `ALLOW_PRIVATE_NETWORKS` | `false` | Permit extraction from trusted private network targets |

The application fails fast at startup for an invalid port, log level, boolean,
or configured SearXNG URL. `SEARXNG_URL` is normalized by removing trailing
slashes and must not contain user information, a query, or a fragment.

Configuration values are never reloaded in process. A deployment changes them
by replacing the container.

## Observability

Logs are structured JSON on standard output. Each request receives or preserves
an `X-Request-ID`, and the same value is returned in the response. Request logs
include the request ID, operation, response status, elapsed milliseconds,
target hostname where applicable, result count or downloaded bytes, and a
stable error category.

Logs must not contain authorization headers, response bodies, SearXNG query
text, URL user information, or complete URLs with query strings. Expected
caller errors use warning level, successful requests use info, diagnostic
details use debug, and unexpected internal failures use error with a traceback.

No metrics endpoint or tracing backend is required initially.

## Container and lifecycle

The image uses a current supported, versioned slim Python base. The Dockerfile
accepts the complete base reference as a build argument so release automation
can resolve the versioned tag to a digest and build against that exact image. A
multi-stage build installs locked production dependencies without leaving a
compiler or package manager cache in the runtime image. It runs as a dedicated
non-root user, disables bytecode generation, and starts one Uvicorn process.

The image exposes port 3002 and defines a health check against `/healthz`. It
handles SIGTERM by refusing new requests, allowing a short graceful shutdown
window for active requests, closing the HTTP client, and exiting.

The service writes logs only to standard output and needs no writable working
directory. It must run with a read-only root filesystem. No volume is needed:
there is no database, durable cache, generated configuration, or other state
whose survival across requests or container replacements would improve the
defined behavior.

Concrete Docker and Podman commands, including Hermes and SearXNG wiring, are
maintained in [DEPLOYMENT.md](DEPLOYMENT.md).

## Dependency and development policy

Runtime and development dependencies live in `pyproject.toml` and a committed
lock file. Direct dependencies are limited to the HTTP framework/server,
validation/settings, HTTPX, Trafilatura, and structured logging support.

The repository provides Makefile targets for at least `lint`, `test`, `build`,
`image`, and `smoke`. Linting includes formatting checks, static analysis, and
strict type checking. Pre-commit hooks use prek, and `prek run --all-files`
must pass before changes are considered complete.

## GitHub Actions

The repository has two workflows.

### Continuous integration

The CI workflow runs for pull requests and pushes to the default branch. It
installs the declared Python version and dependencies from the committed lock
file in frozen mode, then runs the Makefile `lint`, `test`, `integration`, and
`build` targets. It also builds and smoke-tests a local container image without
pushing it. GitHub dependency caching may cache downloaded packages, but it
must not allow resolution outside the lock file.

The workflow uses least-privilege read-only repository permissions, cancels an
older run for the same branch or pull request, and does not receive publishing
credentials.

### Publish latest image

The image workflow runs once daily, can be dispatched manually, and also runs
after relevant changes reach the default branch. Relevant repository inputs
include the Dockerfile, Python project and lock files, application source, and
container entrypoint files.

The workflow publishes to GitHub Container Registry and applies only the
`latest` tag. It must not create commit-SHA, branch, semantic-version, date, or
floating Python-version tags.

Before building, the workflow resolves the configured Python base tag to its
current registry digest. It computes a build-input fingerprint from that
digest, the Python lock-file checksum, and the checksums of all other relevant
container inputs. The current `latest` image stores the fingerprint in the
`org.firecrawlish.build-inputs` OCI label. If the label matches, the scheduled
run exits successfully without building or pushing. If the image is absent or
the fingerprint differs, the workflow builds with the resolved base digest,
runs the container smoke test, and atomically replaces `latest`.

Python dependencies remain reproducible: a dependency change means a reviewed
change to the committed lock file, not an unrecorded dependency upgrade during
the image build. Daily dependency-update tooling may propose lock-file changes,
but publishing occurs only from the default branch. A changed base digest is
picked up directly by the daily image workflow.

The publish job uses `contents: read` and `packages: write`, authenticates with
the repository's GitHub token, prevents overlapping publishers with a single
concurrency group, and never exposes registry credentials to pull requests.
The detection job has only `packages: read`; the publishing job is created only
after a changed fingerprint is detected. The workflow is triggered by relevant
default-branch pushes, a daily schedule, or an explicit manual dispatch.
BuildKit registry caching and provenance are allowed, but neither may introduce
additional image tags.

## Verification strategy

### Unit and contract tests

- Exercise the exact `search(query, limit)` and `scrape(url, formats)` payloads
  used by the pinned Hermes provider.
- Verify defaults, bounds, unknown-field rejection, unsupported formats, and
  conversion of validation failures to the common error envelope.
- Cover authentication disabled, missing token, wrong token, and correct token.
- Test SearXNG score ordering, stable ties, truncation, missing fields, invalid
  URLs, malformed JSON, HTTP failures, timeouts, and absent configuration.
- Use local HTML fixtures and golden files for Markdown, cleaned HTML, title,
  description, language, final URL, media type, and empty extraction.
- Cover source failures, redirect limits, size enforcement with and without
  `Content-Length`, and supported versus unsupported content types.
- Test loopback, private, link-local, reserved, IPv4-mapped IPv6, mixed DNS
  answers, redirect-to-private, and private-network opt-in cases without making
  requests to real internal services.
- Confirm logs and error responses do not contain configured tokens or URL
  credentials.

Tests use local mock transports and servers. They must not depend on public web
or SearXNG availability.

### Integration tests

- Build and start the container without mounts, verify `/healthz`, and scrape a
  fixture origin through the published port.
- Start a fixture SearXNG-compatible server and verify translated search
  results.
- Point the Firecrawl Python SDK at the service and exercise the same calls that
  Hermes makes, both without authentication and with matching client/server
  keys.
- Run the image with a read-only root filesystem and as its declared non-root
  user.
- Verify CI executes lint, test, build, and a non-publishing image build with a
  frozen lock file.
- Verify the daily publisher is a no-op for an unchanged input fingerprint and
  replaces only `latest` when either the resolved base digest or lock file
  changes.

### Acceptance criteria

The implementation is complete when:

- Current Hermes Agent can use the service for `web_extract` by setting
  `FIRECRAWL_API_URL`, and for `web_search` when SearXNG is configured.
- Markdown and HTML extraction return useful main content and the final source
  URL for representative static HTML pages.
- Every failure is visible through an appropriate status, error response, and
  structured log entry.
- SSRF and resource-limit tests pass with private access disabled by default.
- The container starts with environment variables alone, needs no mount, and
  passes lint, tests, build, image, smoke, and prek checks.
- GitHub Actions validates every proposed change and keeps the sole `latest`
  image refreshed when its base image or committed Python dependencies change.

## References

- [Hermes Agent Firecrawl provider](https://github.com/NousResearch/hermes-agent/blob/63279301bcbdc185c1b07b98a9312eb0c862f26d/plugins/web/firecrawl/provider.py)
- [Firecrawl v2 scrape API](https://docs.firecrawl.dev/api-reference/endpoint/scrape)
- [Firecrawl v2 search API](https://docs.firecrawl.dev/api-reference/endpoint/search)
- [SearXNG search API](https://docs.searxng.org/dev/search_api.html)
- [Trafilatura core functions](https://trafilatura.readthedocs.io/en/latest/corefunctions.html)
