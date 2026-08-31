from __future__ import annotations

import json
from collections import Counter

from docalign_core.domain.audit import CONTENT_INTEGRITY_CODES, AuditReport, MutationRecord

from apps.api.schemas import JobChangeDetail, JobResultSummary

CHANGE_DETAIL_LIMIT = 32


def build_job_result_summary(audit: AuditReport) -> JobResultSummary:
    changed = [mutation for mutation in audit.mutations if mutation.status == "changed"]
    categories = Counter(change_category(mutation.property_path) for mutation in changed)
    unique_changes = _unique_changes(changed)
    change_details = [
        JobChangeDetail(
            locator=change_locator(mutation.locator, mutation.node_id),
            node_id=mutation.node_id,
            category=change_category(mutation.property_path),
            property_path=mutation.property_path,
            before_value=display_change_value(
                mutation.property_path, mutation.before, mutation.after
            ),
            after_value=display_change_value(
                mutation.property_path, mutation.after, mutation.before
            ),
        )
        for mutation in unique_changes[:CHANGE_DETAIL_LIMIT]
    ]
    applied_preset = (
        audit.execution_evidence.applied_preset if audit.execution_evidence else None
    )
    delivery_review_items = (
        len(applied_preset.review_requirements)
        + len(applied_preset.acceptance_manual_checks)
        + (0 if applied_preset.matches_catalog_spec else 1)
        if applied_preset
        else 0
    )
    source_boundary = audit.source_processing_boundary
    return JobResultSummary(
        validation_passed=audit.validation.valid,
        content_integrity_passed=not any(
            issue.code in CONTENT_INTEGRITY_CODES for issue in audit.validation.issues
        ),
        format_operations=audit.summary.format_operations,
        changed_mutations=audit.summary.changed_mutations,
        change_categories=dict(sorted(categories.items())),
        change_details=change_details,
        change_details_truncated=len(unique_changes) > CHANGE_DETAIL_LIMIT,
        warning_count=len(audit.warnings),
        validation_issue_count=len(audit.validation.issues),
        remaining_review_items=audit.summary.unknown_blocks,
        structure_review_items=audit.summary.unknown_blocks,
        delivery_review_items=delivery_review_items,
        source_review_features=(source_boundary.review_feature_count if source_boundary else 0),
        paragraphs_before=audit.summary.paragraphs_before,
        paragraphs_after=audit.summary.paragraphs_after,
        auto_layout_splits=audit.summary.auto_layout_splits,
        execution_evidence=audit.execution_evidence,
        source_processing_boundary=source_boundary,
    )


def _unique_changes(changed: list[MutationRecord]) -> list[MutationRecord]:
    located = [item for item in changed if change_locator(item.locator, item.node_id)]
    global_changes = [
        item for item in changed if not change_locator(item.locator, item.node_id)
    ]
    result: list[MutationRecord] = []
    seen: set[tuple[str | None, str, str, str]] = set()
    for mutation in [*located, *global_changes]:
        key = (
            change_locator(mutation.locator, mutation.node_id),
            mutation.property_path,
            stable_change_value(mutation.before),
            stable_change_value(mutation.after),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(mutation)
    return result


def change_locator(locator: str | None, node_id: str | None) -> str | None:
    if locator:
        return locator
    if node_id and node_id.startswith(("header-", "footer-")):
        part, _, index_text = node_id.partition("-")
        if index_text.isdigit():
            return f"s{int(index_text) + 1}.{part}"
    return None


def stable_change_value(value: object | None) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(value)


def display_change_value(
    property_path: str, value: object | None, counterpart: object | None
) -> str | None:
    if value is None:
        return None
    if property_path == "section.layout" and isinstance(value, dict):
        rendered = _display_section_layout(value)
    elif property_path.startswith("runs.") and isinstance(value, dict):
        rendered = _display_font_state(value, counterpart)
    elif property_path.startswith("visual_cleanup."):
        rendered = _display_visual_cleanup(property_path, value)
    elif isinstance(value, dict):
        other = counterpart if isinstance(counterpart, dict) else {}
        changed_items = [
            (str(key), item)
            for key, item in value.items()
            if key not in other or other[key] != item
        ]
        if not changed_items:
            changed_items = [(str(key), item) for key, item in value.items()]
        rendered = " · ".join(
            f"{key}={_display_scalar(item)}" for key, item in changed_items
        )
    elif isinstance(value, list):
        rendered = " / ".join(_display_scalar(item) for item in value)
    else:
        rendered = _display_scalar(value)
    compact = " ".join(rendered.split())
    return f"{compact[:177]}…" if len(compact) > 180 else compact


def _display_section_layout(value: dict[object, object]) -> str:
    width = _numeric(value.get("width"))
    height = _numeric(value.get("height"))
    orientation = str(value.get("orientation") or "").lower()
    is_landscape = "landscape" in orientation or (
        width is not None and height is not None and width > height
    )
    parts = ["横向" if is_landscape else "纵向"]
    if width is not None and height is not None:
        parts.append(f"{_twips_to_mm(width):g} × {_twips_to_mm(height):g} mm")
    margins = value.get("margins")
    if isinstance(margins, list) and len(margins) == 4:
        rendered_margins = [
            _twips_to_mm(item) if (item := _numeric(raw)) is not None else None
            for raw in margins
        ]
        if all(item is not None for item in rendered_margins):
            top, bottom, left, right = rendered_margins
            parts.append(f"页边距 上{top:g} 下{bottom:g} 左{left:g} 右{right:g} mm")
    return " · ".join(parts)


def _display_font_state(value: dict[object, object], counterpart: object) -> str:
    other = counterpart if isinstance(counterpart, dict) else {}
    labels = {
        "ascii": "西文字体",
        "hAnsi": "高 ANSI 字体",
        "eastAsia": "中文字体",
        "cs": "复杂文字字体",
        "size_pt": "字号",
        "bold": "粗体",
        "italic": "斜体",
        "underline": "下划线",
    }
    items = [
        (str(key), item)
        for key, item in value.items()
        if key not in other or other[key] != item
    ]
    if not items:
        items = [(str(key), item) for key, item in value.items()]
    return " · ".join(f"{labels.get(key, key)}={_display_scalar(item)}" for key, item in items)


def _display_visual_cleanup(property_path: str, value: object) -> str:
    if isinstance(value, dict) and isinstance(value.get("noncompliant_nodes"), int):
        return f"发现 {value['noncompliant_nodes']} 处"
    if property_path.startswith("visual_cleanup.remove_") and value is True:
        return "已清除"
    if property_path == "visual_cleanup.text_color_hex" and isinstance(value, str):
        return f"#{value.removeprefix('#').upper()}"
    return _display_scalar(value)


def _numeric(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    return float(value) if isinstance(value, int | float) else None


def _twips_to_mm(value: float) -> float:
    return round(value / 1440 * 25.4, 1)


def _display_scalar(value: object) -> str:
    if value is None:
        return "未设置"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, list):
        return "[" + ", ".join(_display_scalar(item) for item in value) + "]"
    if isinstance(value, dict):
        return stable_change_value(value)
    return str(value)


def change_category(property_path: str) -> str:
    if property_path == "paragraph.structure":
        return "structure"
    if property_path.startswith("section."):
        return "page_layout"
    if property_path.startswith(("styles.", "paragraph.")):
        return "paragraph_styles"
    if property_path.startswith("runs."):
        return "text_font"
    if property_path.startswith(("table.", "cell.")):
        return "tables"
    if property_path.startswith(("header.", "footer.")):
        return "header_footer"
    if property_path.startswith("visual_cleanup."):
        return "visual_cleanup"
    return "other"
