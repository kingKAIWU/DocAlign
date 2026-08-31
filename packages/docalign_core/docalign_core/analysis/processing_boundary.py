from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from docalign_core.domain.document_ir import (
    DocumentBoundaryItem,
    DocumentFeatureHandling,
    DocumentIR,
    DocumentProcessingBoundary,
    ParagraphIR,
    TableIR,
    UnsupportedBlockIR,
)

_MAX_LOCATORS = 20


@dataclass(frozen=True)
class _FeaturePolicy:
    handling: DocumentFeatureHandling
    review_required: bool


_POLICIES: dict[str, _FeaturePolicy] = {
    "field": _FeaturePolicy(DocumentFeatureHandling.PRESERVE_AND_VALIDATE, True),
    "header_footer_field": _FeaturePolicy(
        DocumentFeatureHandling.PRESERVE_AND_VALIDATE,
        False,
    ),
    "equation": _FeaturePolicy(DocumentFeatureHandling.PRESERVE_AND_VALIDATE, True),
    "drawing": _FeaturePolicy(DocumentFeatureHandling.PRESERVE_AND_VALIDATE, False),
    "hyperlink": _FeaturePolicy(DocumentFeatureHandling.PRESERVE_AND_VALIDATE, False),
    "bookmark": _FeaturePolicy(DocumentFeatureHandling.PRESERVE_AND_VALIDATE, False),
    "content_control": _FeaturePolicy(DocumentFeatureHandling.PRESERVE_AND_VALIDATE, True),
    "merged_table": _FeaturePolicy(DocumentFeatureHandling.FORMAT_AND_VALIDATE, True),
    "nested_table": _FeaturePolicy(DocumentFeatureHandling.FORMAT_AND_VALIDATE, True),
    "unknown_ooxml": _FeaturePolicy(DocumentFeatureHandling.PRESERVE_AND_VALIDATE, True),
    "text_box": _FeaturePolicy(DocumentFeatureHandling.PRESERVE_ONLY, True),
    "footnote": _FeaturePolicy(DocumentFeatureHandling.PRESERVE_ONLY, True),
    "endnote": _FeaturePolicy(DocumentFeatureHandling.PRESERVE_ONLY, True),
    "comment": _FeaturePolicy(DocumentFeatureHandling.PRESERVE_ONLY, True),
    "embedded_object": _FeaturePolicy(DocumentFeatureHandling.PRESERVE_ONLY, True),
    "macro": _FeaturePolicy(DocumentFeatureHandling.PRESERVE_ONLY, True),
    "external_link": _FeaturePolicy(DocumentFeatureHandling.PRESERVE_AND_VALIDATE, True),
    "multiple_sections": _FeaturePolicy(DocumentFeatureHandling.FORMAT_AND_VALIDATE, False),
}


def build_processing_boundary(document: DocumentIR) -> DocumentProcessingBoundary:
    counts = dict(document.metadata.feature_counts)
    locators: dict[str, list[str]] = {}

    paragraph_flags = {
        "field": "contains_field",
        "equation": "contains_equation",
        "drawing": "contains_drawing",
        "hyperlink": "contains_hyperlink",
        "bookmark": "contains_bookmark",
        "content_control": "contains_content_control",
    }
    for code, attribute in paragraph_flags.items():
        matches = [
            block.locator
            for block in document.blocks
            if isinstance(block, ParagraphIR) and getattr(block, attribute)
        ]
        if matches:
            counts[code] = max(counts.get(code, 0), len(matches))
            locators[code] = matches

    merged_tables = [
        block.locator
        for block in document.blocks
        if isinstance(block, TableIR) and block.merged_cells_present
    ]
    nested_tables = [
        block.locator
        for block in document.blocks
        if isinstance(block, TableIR) and block.nested_tables_present
    ]
    unsupported = [
        block.locator for block in document.blocks if isinstance(block, UnsupportedBlockIR)
    ]
    _add_located_feature(counts, locators, "merged_table", merged_tables)
    _add_located_feature(counts, locators, "nested_table", nested_tables)
    _add_located_feature(counts, locators, "unknown_ooxml", unsupported)

    part_paths = [part.path for part in document.package_parts]
    _add_part_feature(counts, "footnote", part_paths, lambda path: path == "word/footnotes.xml")
    _add_part_feature(counts, "endnote", part_paths, lambda path: path == "word/endnotes.xml")
    _add_part_feature(counts, "comment", part_paths, lambda path: path.startswith("word/comments"))
    _add_part_feature(
        counts,
        "embedded_object",
        part_paths,
        lambda path: path.startswith(("word/embeddings/", "word/activeX/")),
    )
    _add_part_feature(counts, "macro", part_paths, lambda path: path.endswith("vbaProject.bin"))
    external_relationships = [item for item in document.relationships if item.external]
    if external_relationships:
        counts["external_link"] = len(external_relationships)
    if len(document.sections) > 1:
        counts["multiple_sections"] = len(document.sections)
        locators["multiple_sections"] = [section.locator for section in document.sections]

    items: list[DocumentBoundaryItem] = []
    for code, policy in _POLICIES.items():
        count = counts.get(code, 0)
        if count <= 0:
            continue
        feature_locators = locators.get(code, [])
        items.append(
            DocumentBoundaryItem(
                code=code,
                count=count,
                handling=policy.handling,
                review_required=policy.review_required,
                locators=feature_locators[:_MAX_LOCATORS],
                locators_truncated=len(feature_locators) > _MAX_LOCATORS,
            )
        )

    review_count = sum(item.review_required for item in items)
    return DocumentProcessingBoundary(
        status="review_required" if review_count else "standard",
        detected_feature_count=len(items),
        review_feature_count=review_count,
        acknowledgment_required=bool(review_count),
        items=items,
    )


def _add_located_feature(
    counts: dict[str, int],
    locators: dict[str, list[str]],
    code: str,
    matches: list[str],
) -> None:
    if not matches:
        return
    counts[code] = len(matches)
    locators[code] = matches


def _add_part_feature(
    counts: dict[str, int],
    code: str,
    part_paths: list[str],
    predicate: Callable[[str], bool],
) -> None:
    matching = sum(1 for path in part_paths if predicate(path))
    if matching:
        counts[code] = matching
