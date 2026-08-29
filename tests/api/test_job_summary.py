from __future__ import annotations

from docalign_core.domain.audit import (
    AuditReport,
    AuditSummary,
    MutationRecord,
    ValidationReport,
)

from apps.api.change_summary import build_job_result_summary


def test_job_result_summary_prioritizes_and_bounds_locator_details() -> None:
    mutations = [
        MutationRecord(
            operation_id="section",
            node_id="section-0",
            locator="s1",
            property_path="section.layout",
            before={
                "width": 12240,
                "height": 15840,
                "orientation": "PORTRAIT",
                "margins": [1440, 1440, 1800, 1800],
            },
            after={
                "width": 11906,
                "height": 16838,
                "orientation": "PORTRAIT",
                "margins": [1134, 1134, 1134, 1134],
            },
            status="changed",
        ),
        MutationRecord(
            operation_id="font",
            node_id="node-1",
            locator="p1.r1",
            property_path="runs.0.font",
            before={"ascii": "Arial", "eastAsia": None, "size_pt": 10},
            after={"ascii": "Times New Roman", "eastAsia": "宋体", "size_pt": 12},
            status="changed",
        ),
        *[
            MutationRecord(
                operation_id=f"paragraph-{index}",
                node_id=f"node-{index}",
                locator=f"p{index}",
                property_path="paragraph.style",
                before="Normal",
                after="DA Body",
                status="changed",
            )
            for index in range(2, 40)
        ],
        MutationRecord(
            operation_id="global-style",
            property_path="styles.DA Body",
            before=None,
            after="configured",
            status="changed",
        ),
    ]
    mutations.append(mutations[2].model_copy(deep=True))
    audit = AuditReport(
        job_id="job_summary",
        source_file="source.docx",
        output_file="formatted.docx",
        source_sha256="a" * 64,
        output_sha256="b" * 64,
        summary=AuditSummary(
            paragraphs=39,
            tables=0,
            images=0,
            classified_blocks=39,
            unknown_blocks=0,
            format_operations=len(mutations),
            changed_mutations=len(mutations),
            validation_failures=0,
        ),
        mutations=mutations,
        validation=ValidationReport(valid=True),
    )

    summary = build_job_result_summary(audit)

    assert len(summary.change_details) == 32
    assert summary.change_details_truncated
    assert summary.change_details[0].locator == "s1"
    assert "210 × 297 mm" in (summary.change_details[0].after_value or "")
    assert summary.change_details[1].locator == "p1.r1"
    assert "中文字体=宋体" in (summary.change_details[1].after_value or "")
    assert all(detail.locator is not None for detail in summary.change_details)
    assert summary.change_categories == {
        "page_layout": 1,
        "paragraph_styles": 40,
        "text_font": 1,
    }
