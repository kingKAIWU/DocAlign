from __future__ import annotations

import re
from pathlib import Path

from docx import Document

MAX_TEXT_CHARACTERS = 1_000_000
MAX_TEXT_PARAGRAPHS = 10_000
_BULLET = re.compile(r"^\s*(?:[-*•])\s+(.+)$")
_NUMBERED = re.compile(r"^\s*\d+[.)、]\s+(.+)$")
_MARKDOWN_HEADING = re.compile(r"^\s*(#{1,4})\s+(.+)$")


class PlainTextImportError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def create_docx_from_text(text: str, output_path: Path) -> Path:
    """Create a loss-aware DOCX skeleton from user-supplied plain text.

    Each non-empty input line becomes one Word paragraph. Common Markdown headings and list
    markers are converted to real Word styles/numbering so the semantic analyzer receives useful
    structural evidence. Blank lines act as separators and are not turned into layout hacks.
    """

    normalized = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    if not normalized.strip():
        raise PlainTextImportError("TEXT_EMPTY", "Plain text input cannot be empty.")
    if len(normalized) > MAX_TEXT_CHARACTERS:
        raise PlainTextImportError(
            "TEXT_TOO_LARGE",
            f"Plain text input exceeds {MAX_TEXT_CHARACTERS:,} characters.",
        )
    lines = [line.strip() for line in normalized.split("\n") if line.strip()]
    if len(lines) > MAX_TEXT_PARAGRAPHS:
        raise PlainTextImportError(
            "TEXT_TOO_MANY_PARAGRAPHS",
            f"Plain text input exceeds {MAX_TEXT_PARAGRAPHS:,} paragraphs.",
        )

    document = Document()
    document.core_properties.title = "DocAlign plain-text import"
    document.core_properties.author = "DocAlign"
    for line in lines:
        heading = _MARKDOWN_HEADING.match(line)
        bullet = _BULLET.match(line)
        numbered = _NUMBERED.match(line)
        if heading:
            level = len(heading.group(1))
            style = "Title" if level == 1 else f"Heading {level - 1}"
            document.add_paragraph(heading.group(2), style=style)
        elif bullet:
            document.add_paragraph(bullet.group(1), style="List Bullet")
        elif numbered:
            document.add_paragraph(numbered.group(1), style="List Number")
        else:
            document.add_paragraph(line)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(output_path))
    return output_path
