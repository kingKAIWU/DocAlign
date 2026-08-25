from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from docx.oxml.ns import qn
from lxml import etree

TEXT_TAG = qn("w:t")
TAB_TAG = qn("w:tab")
BREAK_TAGS = {qn("w:br"), qn("w:cr")}
INSTRUCTION_TAG = qn("w:instrText")


def local_name(element: Any) -> str:
    return str(etree.QName(element).localname)


def logical_text(element: Any) -> str:
    chunks: list[str] = []
    for node in element.iter():
        if node.tag == TEXT_TAG:
            chunks.append(node.text or "")
        elif node.tag == TAB_TAG:
            chunks.append("\t")
        elif node.tag in BREAK_TAGS:
            chunks.append("\n")
    return "".join(chunks)


def field_instructions(elements: Iterable[Any]) -> list[str]:
    values: list[str] = []
    for element in elements:
        for node in element.iter(INSTRUCTION_TAG):
            value = (node.text or "").strip()
            if value:
                values.append(value)
    return values


def contains_any(element: Any, tags: set[str]) -> bool:
    return any(node.tag in tags for node in element.iter())
