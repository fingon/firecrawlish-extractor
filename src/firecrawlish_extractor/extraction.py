import asyncio
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Final, Literal

import trafilatura

MAX_EXTRACTION_JOBS: Final[int] = 8

type OutputFormat = Literal["markdown", "html"]

_METADATA_FIELDS: Final[tuple[tuple[str, str], ...]] = (
    ("title", "title"),
    ("description", "description"),
    ("language", "language"),
    ("author", "author"),
    ("date", "publishedTime"),
    ("sitename", "siteName"),
    ("image", "ogImage"),
    ("license", "license"),
    ("pagetype", "type"),
)


class ExtractionFailure(Exception):
    category = "empty_extraction"


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    outputs: dict[OutputFormat, str]
    metadata: dict[str, str]


def _metadata_from(document: object) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for source_name, output_name in _METADATA_FIELDS:
        value = getattr(document, source_name, None)
        if isinstance(value, str):
            cleaned_value = value.strip()
            if cleaned_value:
                metadata[output_name] = cleaned_value
    return metadata


def _language_from(document: object, html: str) -> str | None:
    language = getattr(document, "language", None)
    if isinstance(language, str) and language.strip():
        return language.strip()

    tree = trafilatura.load_html(html)
    if tree is None:
        return None
    language_values = tree.xpath("/html/@lang")
    if not language_values:
        return None
    language = language_values[0]
    if not isinstance(language, str):
        return None
    language = language.strip()
    return language or None


def _extract_sync(
    html: str,
    final_url: str,
    formats: tuple[OutputFormat, ...],
) -> ExtractionResult:
    outputs: dict[OutputFormat, str] = {}
    for output_format in formats:
        output = trafilatura.extract(
            html,
            include_comments=False,
            include_links=True,
            include_tables=True,
            output_format=output_format,
            url=final_url,
        )
        if output is None or not output.strip():
            raise ExtractionFailure("Trafilatura found no extractable content")
        outputs[output_format] = output

    document = trafilatura.extract_metadata(html, default_url=final_url)
    metadata = _metadata_from(document)
    language = _language_from(document, html)
    if language is not None:
        metadata["language"] = language
    return ExtractionResult(
        metadata=metadata,
        outputs=outputs,
    )


class ExtractionPool:
    def __init__(self, max_workers: int = MAX_EXTRACTION_JOBS) -> None:
        if max_workers <= 0:
            raise ValueError("max_workers must be positive")
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="trafilatura",
        )
        self._slots = asyncio.Semaphore(max_workers)

    async def extract(
        self,
        html: str,
        final_url: str,
        formats: Sequence[OutputFormat],
    ) -> ExtractionResult:
        requested_formats = tuple(formats)
        async with self._slots:
            event_loop = asyncio.get_running_loop()
            return await event_loop.run_in_executor(
                self._executor,
                _extract_sync,
                html,
                final_url,
                requested_formats,
            )

    async def aclose(self) -> None:
        await asyncio.to_thread(
            self._executor.shutdown,
            wait=True,
            cancel_futures=True,
        )
