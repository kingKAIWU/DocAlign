from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from docalign_core.analysis.classifier import analyze_document
from docalign_core.analysis.semantic import SemanticAnalyzerError, merge_semantic_analysis
from docalign_core.config import Settings
from docalign_core.delivery import DeliveryPackageError, verify_delivery_package
from docalign_core.docx.parser import parse_docx
from docalign_core.docx.text_import import create_docx_from_text
from docalign_core.domain.audit import ProcessingBoundaryAcknowledgmentMethod
from docalign_core.domain.formatting_spec import load_formatting_spec
from docalign_core.llm.base import DocumentSummary
from docalign_core.llm.openai_compatible import OpenAICompatibleChatInterpreter
from docalign_core.llm.semantic import OpenAICompatibleSemanticAnalyzer
from docalign_core.services.processing import ProcessingFailure, process_document
from docalign_core.validation.validator import DocumentValidator

app = typer.Typer(help="DocAlign deterministic DOCX formatting engine.", no_args_is_help=True)
spec_app = typer.Typer(help="Compile and inspect FormattingSpec documents.", no_args_is_help=True)
app.add_typer(spec_app, name="spec")


@app.command()
def analyze(
    source: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    out: Annotated[Path, typer.Option("--out", "-o")],
    smart: Annotated[bool, typer.Option("--smart")] = False,
) -> None:
    document_ir = parse_docx(source)
    result = analyze_document(document_ir)
    if smart:
        settings = Settings()
        try:
            analyzer = OpenAICompatibleSemanticAnalyzer(
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key,
                model=settings.llm_model,
                timeout_seconds=settings.llm_timeout_seconds,
                json_schema_mode=settings.llm_json_schema_mode,
            )
            draft = asyncio.run(analyzer.analyze(document_ir, result))
        except SemanticAnalyzerError as exc:
            typer.echo(f"{exc.code}: {exc.message}", err=True)
            raise typer.Exit(1) from exc
        result = merge_semantic_analysis(
            result, draft, provider=analyzer.provider, model=analyzer.model
        )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    typer.echo(f"Analysis written to {out}")


@app.command("import-text")
def import_text(
    source: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    out: Annotated[Path, typer.Option("--out", "-o")],
) -> None:
    if source.resolve() == out.resolve():
        raise typer.BadParameter("The output path must differ from the source text file.")
    create_docx_from_text(source.read_text(encoding="utf-8"), out)
    typer.echo(f"Plain text DOCX skeleton: {out}")


@app.command("verify-delivery")
def verify_delivery(
    package: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    report: Annotated[Path | None, typer.Option("--report")] = None,
) -> None:
    if report is not None and report.resolve() == package.resolve():
        raise typer.BadParameter("The verification report must not overwrite the package.")
    try:
        verification = verify_delivery_package(package)
    except DeliveryPackageError as exc:
        typer.echo(f"{exc.code}: {exc.message}", err=True)
        raise typer.Exit(2) from exc
    if report is not None:
        report.write_text(verification.model_dump_json(indent=2) + "\n", encoding="utf-8")
        typer.echo(f"Verification report: {report}")
    typer.echo(
        "Delivery package verified: "
        f"{verification.package_kind.value} {verification.package_id} · "
        f"{len(verification.items)} item(s) · unsigned"
    )


@app.command("format")
def format_document(
    source: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    spec: Annotated[Path, typer.Option("--spec", exists=True, dir_okay=False)],
    out: Annotated[Path, typer.Option("--out", "-o")],
    audit_dir: Annotated[Path | None, typer.Option("--audit-dir")] = None,
    acknowledge_processing_boundary: Annotated[
        bool,
        typer.Option(
            "--acknowledge-processing-boundary",
            help="Confirm that complex source content will be reviewed in Word/WPS.",
        ),
    ] = False,
) -> None:
    if source.resolve() == out.resolve():
        raise typer.BadParameter("The output path must not overwrite the source DOCX.")
    formatting_spec = load_formatting_spec(spec)
    analysis = analyze_document(parse_docx(source))
    boundary = analysis.summary.processing_boundary
    if boundary.acknowledgment_required and not acknowledge_processing_boundary:
        typer.echo(
            "PROCESSING_BOUNDARY_ACKNOWLEDGMENT_REQUIRED: "
            f"{boundary.review_feature_count} complex content types require review; "
            "rerun with --acknowledge-processing-boundary after inspecting the analysis.",
            err=True,
        )
        raise typer.Exit(2)
    artifacts = audit_dir or out.parent / f"{out.stem}.docalign"
    try:
        result = process_document(
            source,
            analysis.document_ir,
            formatting_spec,
            out,
            job_id=f"job_{uuid.uuid4().hex}",
            artifact_dir=artifacts,
            processing_boundary_acknowledgment_method=(
                ProcessingBoundaryAcknowledgmentMethod.EXPLICIT_CLI
                if boundary.acknowledgment_required
                else ProcessingBoundaryAcknowledgmentMethod.NOT_REQUIRED
            ),
            processing_boundary_acknowledged_at=(
                datetime.now(UTC) if boundary.acknowledgment_required else None
            ),
        )
    except ProcessingFailure as exc:
        typer.echo(f"{exc.code}: {exc.message}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Formatted DOCX: {result.output_path}")
    typer.echo(f"Audit: {result.audit_json_path}")


@app.command()
def validate(
    source: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    spec: Annotated[Path, typer.Option("--spec", exists=True, dir_okay=False)],
    report: Annotated[Path | None, typer.Option("--report")] = None,
) -> None:
    formatting_spec = load_formatting_spec(spec)
    analysis = analyze_document(parse_docx(source))
    result = DocumentValidator().validate(source, formatting_spec, analysis.document_ir)
    payload = json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2)
    if report:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(payload, encoding="utf-8")
    typer.echo(payload)
    if not result.valid:
        raise typer.Exit(1)


@spec_app.command("compile")
def compile_spec(
    instruction: Annotated[str, typer.Option("--instruction", "-i")],
    out: Annotated[Path, typer.Option("--out", "-o")],
) -> None:
    settings = Settings()
    interpreter = OpenAICompatibleChatInterpreter(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
        json_schema_mode=settings.llm_json_schema_mode,
    )
    result = asyncio.run(
        interpreter.compile_requirements(
            instruction, DocumentSummary(paragraph_count=0, table_count=0, image_count=0)
        )
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(result.spec.model_dump_json(indent=2), encoding="utf-8")
    typer.echo(f"FormattingSpec written to {out}")


if __name__ == "__main__":
    app()
