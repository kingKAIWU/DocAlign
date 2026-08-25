from __future__ import annotations

import argparse
import json
from pathlib import Path

from docalign_core.domain.audit import AuditReport
from docalign_core.domain.compliance import ComplianceReport
from docalign_core.domain.document_ir import DocumentIR
from docalign_core.domain.formatting_spec import FormattingSpec
from docalign_core.domain.manifest import FormatManifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    output = Path("schemas")
    output.mkdir(exist_ok=True)
    models = {
        "formatting-spec.v1.schema.json": FormattingSpec,
        "document-ir.v1.schema.json": DocumentIR,
        "audit-report.v1.schema.json": AuditReport,
        "compliance-report.v1.schema.json": ComplianceReport,
        "format-manifest.v1.schema.json": FormatManifest,
    }
    for filename, model in models.items():
        target = output / filename
        content = json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2) + "\n"
        if args.check:
            if not target.exists() or target.read_text(encoding="utf-8") != content:
                raise SystemExit(f"Schema drift detected for {filename}; run `make schemas`.")
        else:
            target.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
