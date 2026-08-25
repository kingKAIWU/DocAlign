"""DocAlign deterministic DOCX formatting engine."""

from docalign_core.docx.parser import parse_docx
from docalign_core.domain.formatting_spec import (
    FormattingSpec,
    default_academic_spec,
    default_cleanup_spec,
)

__all__ = ["FormattingSpec", "default_academic_spec", "default_cleanup_spec", "parse_docx"]
__version__ = "0.1.0"
