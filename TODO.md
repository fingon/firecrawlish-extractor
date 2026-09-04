# TODO

The service described in [DESIGN.md](DESIGN.md) is not yet implemented.

- [x] Establish code-quality checks before any implementation: target the latest
  stable Python release; add the latest stable prek, Ruff, and ty releases to
  the locked development dependencies; configure prek through
  `.pre-commit-config.yaml` to run Ruff linting and formatting plus ty type
  checking; and install the commit hook with `prek install`. Use each tool's
  default configuration, with Ruff's formatter and linter line width set to 80
  as the sole override. Make both `prek run --all-files` and the equivalent
  Makefile lint target mandatory in CI so every subsequent commit follows the
  same checks.
- Scaffold the locked Python project, FastAPI application, typed settings,
  structured logging, and remaining Makefile targets.
- Implement the optional bearer-authentication middleware, request IDs, common
  Firecrawl error envelope, and `/healthz` endpoint.
- Implement safe streaming HTTP fetching with URL validation, DNS/address
  checks, redirect revalidation, fixed deadlines, and decoded-body limits.
- Implement Trafilatura Markdown, cleaned HTML, and metadata extraction behind
  bounded worker concurrency.
- Implement `/v2/scrape` with the exact request, response, and error behavior in
  the design.
- Implement the optional SearXNG adapter and `/v2/search` result translation.
- Add unit, golden, security, contract, and container integration tests,
  including a Firecrawl SDK/Hermes-compatible smoke test.
- Build the non-root, read-only-compatible container image and document concrete
  Hermes and SearXNG deployment examples once the commands can be verified.
- Add a least-privilege GitHub Actions CI workflow that runs the Makefile lint,
  test, build, and non-publishing container-build targets for pull requests and
  default-branch pushes using the frozen Python lock file.
- Add a daily and manually dispatchable GitHub Actions publisher that detects
  base-image digest or committed dependency-lock changes, smoke-tests changed
  builds, and publishes only the `latest` tag to GitHub Container Registry.
