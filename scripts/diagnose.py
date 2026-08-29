from __future__ import annotations

import argparse
import sys
from pathlib import Path

from docalign_core.config import Settings
from docalign_core.domain.diagnostics import DiagnosticOverall

from apps.api.diagnostics import standalone_diagnostic_service


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a privacy-safe DocAlign support diagnostic report."
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="Write JSON to this file instead of standard output.",
    )
    args = parser.parse_args()

    report = standalone_diagnostic_service(Settings()).report()
    payload = report.model_dump_json(indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload, encoding="utf-8")
        print(f"Diagnostic report written to {args.out}", file=sys.stderr)
    else:
        print(payload, end="")
    if report.overall == DiagnosticOverall.ACTION_REQUIRED:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
