import asyncio
import threading
import time
from pathlib import Path

import pytest

from firecrawlish_extractor import extraction
from firecrawlish_extractor.extraction import (
    ExtractionFailure,
    ExtractionPool,
)

ARTICLE_HTML = Path("tests/testdata/article.html").read_text()
ARTICLE_URL = "https://example.com/articles/example"


@pytest.mark.anyio
async def test_extraction_returns_requested_formats_and_metadata() -> None:
    pool = ExtractionPool()

    try:
        result = await pool.extract(
            ARTICLE_HTML,
            ARTICLE_URL,
            ("markdown", "html"),
        )
    finally:
        await pool.aclose()

    assert set(result.outputs) == {"markdown", "html"}
    assert "# Example article" in result.outputs["markdown"]
    assert "link to related reading" in result.outputs["markdown"]
    assert "HyperText Markup Language" in result.outputs["markdown"]
    assert (
        "Navigation that should not be extracted"
        not in result.outputs["markdown"]
    )
    assert "This comment must not be included" not in result.outputs["markdown"]
    assert '<a href="https://example.com/related">' in result.outputs["html"]
    assert "<nav>" not in result.outputs["html"]
    assert result.metadata["title"] == "Example article"
    assert result.metadata["description"] == "An example article description."
    assert result.metadata["language"] == "en"
    assert result.metadata["siteName"] == "Example News"


@pytest.mark.anyio
async def test_extraction_only_runs_requested_format() -> None:
    pool = ExtractionPool()

    try:
        result = await pool.extract(ARTICLE_HTML, ARTICLE_URL, ("html",))
    finally:
        await pool.aclose()

    assert set(result.outputs) == {"html"}
    assert result.outputs["html"]


@pytest.mark.anyio
async def test_empty_extraction_is_an_explicit_failure() -> None:
    pool = ExtractionPool()

    try:
        with pytest.raises(ExtractionFailure, match="no extractable content"):
            await pool.extract(
                "<html><body></body></html>",
                ARTICLE_URL,
                ("markdown",),
            )
    finally:
        await pool.aclose()


@pytest.mark.anyio
async def test_extraction_pool_bounds_worker_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active_workers = 0
    maximum_active_workers = 0
    counter_lock = threading.Lock()

    def fake_extract(
        html: str,
        final_url: str,
        formats: tuple[extraction.OutputFormat, ...],
    ) -> extraction.ExtractionResult:
        nonlocal active_workers, maximum_active_workers
        with counter_lock:
            active_workers += 1
            maximum_active_workers = max(maximum_active_workers, active_workers)
        time.sleep(0.02)
        with counter_lock:
            active_workers -= 1
        return extraction.ExtractionResult(
            metadata={"url": final_url, "input": html},
            outputs={output_format: "content" for output_format in formats},
        )

    monkeypatch.setattr(extraction, "_extract_sync", fake_extract)
    pool = ExtractionPool(max_workers=2)

    try:
        results = await asyncio.gather(
            *(
                pool.extract("html", ARTICLE_URL, ("markdown",))
                for _ in range(8)
            ),
        )
    finally:
        await pool.aclose()

    assert len(results) == 8
    assert maximum_active_workers == 2
