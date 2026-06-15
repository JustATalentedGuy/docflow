from __future__ import annotations

import re
from collections import Counter
from typing import Iterable


_PAGE_COUNTER_RE = re.compile(r"^\s*(?:page\s*)?\d+\s*/\s*\d+\s*$", re.IGNORECASE)
_ONLY_NUMBER_RE = re.compile(r"^\s*\d+\s*$")
_SPACE_RE = re.compile(r"[ \t]+")


def _normalize_line(line: str) -> str:
    line = line.replace("\u2022", "-")
    line = _SPACE_RE.sub(" ", line).strip()
    line = re.sub(r"\s+([,.;:!?])", r"\1", line)
    line = re.sub(r"([({\[])\s+", r"\1", line)
    line = re.sub(r"\s+([)}\]])", r"\1", line)
    return line


def _line_key(line: str) -> str:
    return re.sub(r"\W+", "", line).lower()


def _is_repeated_chrome(line: str, repeated_keys: set[str]) -> bool:
    key = _line_key(line)
    if not key:
        return True
    if _PAGE_COUNTER_RE.match(line) or _ONLY_NUMBER_RE.match(line):
        return True
    if key in repeated_keys and len(line) <= 90:
        return True
    return False


def clean_document_pages(pages: Iterable[str]) -> str:
    """
    Clean text extracted from slide-like PDFs before chunking/indexing.

    The main goal is to remove presentation chrome: repeated footer/header
    lines, author/course labels, page counters such as "4 / 6", and duplicate
    lines introduced by PDF extraction. Content bullets and theorem/proof text
    are preserved.
    """
    page_lines = []
    page_key_counts: Counter[str] = Counter()

    for page in pages:
        lines = [_normalize_line(line) for line in page.splitlines()]
        lines = [line for line in lines if line]
        page_lines.append(lines)
        page_key_counts.update({_line_key(line) for line in lines if _line_key(line)})

    page_count = max(len(page_lines), 1)
    repeated_keys = {
        key
        for key, count in page_key_counts.items()
        if count >= 2 and count >= max(2, page_count // 2)
    }

    cleaned_pages: list[str] = []
    previous_global_key = ""

    for lines in page_lines:
        cleaned_lines: list[str] = []
        previous_page_key = ""

        for line in lines:
            key = _line_key(line)
            if _is_repeated_chrome(line, repeated_keys):
                continue
            if key and (key == previous_page_key or key == previous_global_key):
                continue

            cleaned_lines.append(line)
            previous_page_key = key
            previous_global_key = key

        page_text = _join_slide_lines(cleaned_lines)
        if page_text:
            cleaned_pages.append(page_text)

    return "\n\n".join(cleaned_pages).strip()


def clean_document_text(text: str) -> str:
    """Fallback cleanup for already-combined text."""
    return clean_document_pages(text.split("\f"))


def _join_slide_lines(lines: list[str]) -> str:
    blocks: list[str] = []
    current: list[str] = []

    for line in lines:
        is_bullet = line.startswith("-")
        is_heading = (
            not is_bullet
            and len(line) <= 80
            and not line.endswith((".", ",", ";", ":"))
            and len(line.split()) <= 10
        )

        if is_bullet or is_heading:
            if current:
                blocks.append(" ".join(current))
                current = []
            blocks.append(line)
            continue

        current.append(line)

    if current:
        blocks.append(" ".join(current))

    return "\n".join(blocks).strip()
