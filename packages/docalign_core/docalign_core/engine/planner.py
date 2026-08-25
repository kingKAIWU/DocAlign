from __future__ import annotations

import hashlib
from collections import Counter

from docalign_core.domain.audit import (
    FormattingOperation,
    FormattingPlan,
    OperationType,
    PlanWarning,
)
from docalign_core.domain.document_ir import DocumentIR, ParagraphIR, TableIR
from docalign_core.domain.enums import SemanticRole
from docalign_core.domain.formatting_spec import (
    FormattingSpec,
    RoleFormattingSpec,
    resolve_role_spec,
)

STYLE_NAMES: dict[SemanticRole, str] = {
    SemanticRole.COVER: "DA Cover",
    SemanticRole.TITLE: "DA Title",
    SemanticRole.SUBTITLE: "DA Subtitle",
    SemanticRole.AUTHOR_INFO: "DA Author Info",
    SemanticRole.ABSTRACT_HEADING: "DA Abstract Heading",
    SemanticRole.ABSTRACT_BODY: "DA Abstract",
    SemanticRole.KEYWORDS: "DA Keywords",
    SemanticRole.HEADING_1: "DA Heading 1",
    SemanticRole.HEADING_2: "DA Heading 2",
    SemanticRole.HEADING_3: "DA Heading 3",
    SemanticRole.HEADING_4: "DA Heading 4",
    SemanticRole.BODY: "DA Body",
    SemanticRole.BLOCKQUOTE: "DA Blockquote",
    SemanticRole.LIST_ITEM: "DA List Item",
    SemanticRole.FIGURE_CAPTION: "DA Figure Caption",
    SemanticRole.TABLE_CAPTION: "DA Table Caption",
    SemanticRole.BIBLIOGRAPHY_HEADING: "DA Bibliography Heading",
    SemanticRole.BIBLIOGRAPHY_ENTRY: "DA Bibliography",
    SemanticRole.APPENDIX_HEADING: "DA Appendix Heading",
    SemanticRole.APPENDIX_BODY: "DA Appendix",
    SemanticRole.HEADER: "DA Header",
    SemanticRole.FOOTER: "DA Footer",
}


def style_name_for(role: SemanticRole, custom: str | None = None) -> str:
    if custom:
        return custom if custom.startswith("DA ") else f"DA {custom}"
    return STYLE_NAMES.get(role, f"DA {role.value.replace('_', ' ').title()}")


def build_formatting_plan(document_ir: DocumentIR, spec: FormattingSpec) -> FormattingPlan:
    signature = f"{document_ir.source_sha256}:{spec.model_dump_json()}"
    plan_id = f"plan_{hashlib.sha256(signature.encode()).hexdigest()[:16]}"
    operations: list[FormattingOperation] = []
    warnings: list[PlanWarning] = []
    counter = 0

    def add(
        operation_type: OperationType,
        *,
        node_id: str | None = None,
        locator: str | None = None,
        role: SemanticRole | None = None,
        properties: dict[str, object] | None = None,
        reason: str,
    ) -> None:
        nonlocal counter
        counter += 1
        operations.append(
            FormattingOperation(
                operation_id=f"op-{counter:06d}",
                node_id=node_id,
                locator=locator,
                target_role=role,
                operation_type=operation_type,
                properties=properties or {},
                reason=reason,
            )
        )

    if spec.document is not None:
        for section in document_ir.sections:
            add(
                OperationType.SET_SECTION_LAYOUT,
                node_id=f"section-{section.index}",
                locator=section.locator,
                properties=spec.document.page.model_dump(mode="json", exclude_none=True),
                reason="Document page formatting specification.",
            )

    for role, role_spec in sorted(
        configured_role_specs(document_ir, spec).items(), key=lambda item: item[0].value
    ):
        add(
            OperationType.CREATE_OR_UPDATE_STYLE,
            role=role,
            properties={
                "style_name": style_name_for(role, role_spec.style_name),
                "spec": role_spec.model_dump(mode="json", exclude_none=True),
            },
            reason=f"Ensure the DocAlign-owned style for {role.value}.",
        )

    printable_width = _printable_width_twips(document_ir, spec)
    for block in document_ir.blocks:
        if isinstance(block, ParagraphIR):
            if block.is_empty:
                continue
            role = block.detected_role
            if role == SemanticRole.UNKNOWN:
                if spec.baseline is None and not spec.behavior.apply_to_unknown_roles:
                    warnings.append(
                        PlanWarning(
                            code="UNKNOWN_PARAGRAPH_ROLE",
                            node_id=block.node_id,
                            locator=block.locator,
                            message="Unknown paragraph role was preserved without role formatting.",
                        )
                    )
                    continue
                if spec.baseline is None:
                    role = spec.behavior.unknown_role_fallback
            target_spec = resolve_role_spec(spec, role)
            if target_spec is None:
                continue
            style_name = style_name_for(role, target_spec.style_name)
            add(
                OperationType.ASSIGN_PARAGRAPH_STYLE,
                node_id=block.node_id,
                locator=block.locator,
                role=role,
                properties={"style_name": style_name, "block_index": block.index},
                reason=f"Classified as {role.value} with confidence {block.role_confidence:.2f}.",
            )
            if target_spec.paragraph is not None:
                add(
                    OperationType.SET_PARAGRAPH_FORMAT,
                    node_id=block.node_id,
                    locator=block.locator,
                    role=role,
                    properties=target_spec.paragraph.model_dump(mode="json", exclude_none=True),
                    reason="Normalize paragraph properties covered by the role specification.",
                )
            if target_spec.font is not None:
                add(
                    OperationType.SET_RUN_FONT,
                    node_id=block.node_id,
                    locator=block.locator,
                    role=role,
                    properties=target_spec.font.model_dump(mode="json", exclude_none=True),
                    reason="Normalize script-specific fonts for the role.",
                )
            if (
                block.contains_drawing
                and not block.text.strip()
                and (spec.figures and spec.figures.center_image_only_paragraphs)
            ):
                add(
                    OperationType.ALIGN_IMAGE_PARAGRAPH,
                    node_id=block.node_id,
                    locator=block.locator,
                    properties={"alignment": "center"},
                    reason="Image-only paragraph centering is enabled.",
                )
        elif isinstance(block, TableIR) and spec.tables is not None:
            add(
                OperationType.SET_TABLE_FORMAT,
                node_id=block.node_id,
                locator=block.locator,
                properties={
                    "block_index": block.index,
                    **spec.tables.model_dump(mode="json", exclude_none=True),
                },
                reason="Apply table formatting without reconstructing the table.",
            )
            if (
                block.width_estimate_twips is not None
                and printable_width is not None
                and block.width_estimate_twips > printable_width * 1.02
            ):
                warnings.append(
                    PlanWarning(
                        code="TABLE_WIDTH_EXCEEDS_PAGE",
                        node_id=block.node_id,
                        locator=block.locator,
                        message="Estimated table width exceeds the printable page width.",
                    )
                )

    if spec.headers is not None:
        add(OperationType.FORMAT_HEADER, reason="Apply existing header formatting.")
    if spec.footers is not None:
        add(OperationType.FORMAT_FOOTER, reason="Apply existing footer formatting.")
    if spec.page_numbers is not None and spec.page_numbers.enabled:
        add(
            OperationType.INSERT_PAGE_NUMBER,
            properties=spec.page_numbers.model_dump(mode="json", exclude_none=True),
            reason="Insert a real Word PAGE field when absent.",
        )
    if spec.visual_cleanup is not None:
        add(
            OperationType.NORMALIZE_DOCUMENT_VISUALS,
            properties=spec.visual_cleanup.model_dump(mode="json", exclude_none=True),
            reason=("Normalize document-wide text color and removable Word background properties."),
        )

    locator_by_node = {block.node_id: block.locator for block in document_ir.blocks}
    warnings.extend(
        PlanWarning(
            code=item.code,
            message=item.message,
            node_id=item.node_id,
            locator=locator_by_node.get(item.node_id) if item.node_id else None,
        )
        for item in document_ir.warnings
    )
    return FormattingPlan(
        plan_id=plan_id,
        document_id=document_ir.document_id,
        operations=operations,
        warnings=_deduplicate_warnings(warnings),
    )


def role_counts(document_ir: DocumentIR) -> dict[str, int]:
    return dict(
        sorted(
            Counter(
                block.detected_role.value
                for block in document_ir.blocks
                if isinstance(block, ParagraphIR)
            ).items()
        )
    )


def configured_role_specs(
    document_ir: DocumentIR, spec: FormattingSpec
) -> dict[SemanticRole, RoleFormattingSpec]:
    roles = set(spec.roles)
    if SemanticRole.BODY in roles:
        roles.update(
            block.detected_role
            for block in document_ir.blocks
            if isinstance(block, ParagraphIR) and block.detected_role == SemanticRole.LIST_ITEM
        )
    if spec.baseline is not None:
        roles.update(
            block.detected_role
            for block in document_ir.blocks
            if isinstance(block, ParagraphIR) and not block.is_empty
        )
    return {
        role: role_spec
        for role in roles
        if (role_spec := resolve_role_spec(spec, role)) is not None
    }


def _printable_width_twips(document_ir: DocumentIR, spec: FormattingSpec) -> int | None:
    if not document_ir.sections:
        return None
    section = document_ir.sections[0]
    if spec.document is None:
        if (
            section.page_width_twips is None
            or section.margin_left_twips is None
            or section.margin_right_twips is None
        ):
            return None
        return section.page_width_twips - section.margin_left_twips - section.margin_right_twips
    page = spec.document.page
    if section.page_width_twips is None or section.page_height_twips is None:
        return None
    if page.size is None:
        short_side = min(section.page_width_twips, section.page_height_twips)
        long_side = max(section.page_width_twips, section.page_height_twips)
    elif page.size.value == "A4":
        short_side, long_side = round(210 * 56.6929133858), round(297 * 56.6929133858)
    else:
        short_side, long_side = 12_240, 15_840
    currently_landscape = section.orientation == "landscape"
    target_landscape = (
        currently_landscape if page.orientation is None else page.orientation.value == "landscape"
    )
    if (
        currently_landscape
        and page.preserve_existing_landscape_sections
        and not page.force_orientation_all_sections
        and page.orientation is not None
        and page.orientation.value == "portrait"
    ):
        target_landscape = True
    width_twips = long_side if target_landscape else short_side
    left_twips = (
        round(page.margin_left_mm * 56.6929133858)
        if page.margin_left_mm is not None
        else section.margin_left_twips
    )
    right_twips = (
        round(page.margin_right_mm * 56.6929133858)
        if page.margin_right_mm is not None
        else section.margin_right_twips
    )
    if left_twips is None or right_twips is None:
        return None
    return width_twips - left_twips - right_twips


def _deduplicate_warnings(warnings: list[PlanWarning]) -> list[PlanWarning]:
    seen: set[tuple[str, str | None]] = set()
    result: list[PlanWarning] = []
    for warning in warnings:
        key = (warning.code, warning.node_id)
        if key not in seen:
            seen.add(key)
            result.append(warning)
    return result
