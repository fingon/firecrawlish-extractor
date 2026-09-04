# Firecrawlish Extractor

Firecrawlish Extractor is a proposed lightweight, self-hosted substitute for
the small part of Firecrawl that Hermes Agent uses for web search and content
extraction.

It will extract readable Markdown or cleaned HTML from static web pages with
Trafilatura and can translate results from an optional SearXNG instance into
the Firecrawl v2 response format. The intended deployment is a single
stateless container configured through environment variables, with no required
volumes.

The service is currently designed but not implemented. See [DESIGN.md](DESIGN.md)
for the API, security model, configuration, and acceptance criteria, and
[TODO.md](TODO.md) for implementation work.

