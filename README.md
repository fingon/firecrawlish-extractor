# Firecrawlish Extractor

Firecrawlish Extractor is a lightweight, self-hosted substitute for the small
part of Firecrawl that Hermes Agent uses for web search and content extraction.

It extracts readable Markdown or cleaned HTML from static web pages with
Trafilatura and can translate results from an optional SearXNG instance into
the Firecrawl v2 response format. The HTTP service is configured through
environment variables and has no required external state.

Built on Python 3.15, with reproducible dependencies and daily Dependabot
checks that give new releases a three-day cooldown.

See [DESIGN.md](DESIGN.md) for the API, security model, configuration, and
acceptance criteria, and [TODO.md](TODO.md) for the implementation checklist.
See [DEPLOYMENT.md](DEPLOYMENT.md) for verified container, Hermes, and
SearXNG setup examples.
