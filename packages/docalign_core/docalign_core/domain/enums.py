from enum import StrEnum


class SemanticRole(StrEnum):
    COVER = "cover"
    TITLE = "title"
    SUBTITLE = "subtitle"
    AUTHOR_INFO = "author_info"
    ABSTRACT_HEADING = "abstract_heading"
    ABSTRACT_BODY = "abstract_body"
    KEYWORDS = "keywords"
    HEADING_1 = "heading_1"
    HEADING_2 = "heading_2"
    HEADING_3 = "heading_3"
    HEADING_4 = "heading_4"
    BODY = "body"
    BLOCKQUOTE = "blockquote"
    LIST_ITEM = "list_item"
    FIGURE_CAPTION = "figure_caption"
    TABLE_CAPTION = "table_caption"
    BIBLIOGRAPHY_HEADING = "bibliography_heading"
    BIBLIOGRAPHY_ENTRY = "bibliography_entry"
    APPENDIX_HEADING = "appendix_heading"
    APPENDIX_BODY = "appendix_body"
    HEADER = "header"
    FOOTER = "footer"
    UNKNOWN = "unknown"


class RoleSource(StrEnum):
    EXISTING_STYLE = "existing_style"
    DETERMINISTIC = "deterministic"
    LLM = "llm"
    USER_OVERRIDE = "user_override"
    FALLBACK = "fallback"


class AnalysisMode(StrEnum):
    DETERMINISTIC = "deterministic"
    SMART = "smart"


class DocumentKind(StrEnum):
    ACADEMIC_PAPER = "academic_paper"
    REPORT = "report"
    GOVERNMENT_DOCUMENT = "government_document"
    FINANCIAL_REPORT = "financial_report"
    CONTRACT = "contract"
    MEETING_MINUTES = "meeting_minutes"
    PROPOSAL = "proposal"
    MANUAL = "manual"
    ESSAY = "essay"
    RESUME = "resume"
    LETTER = "letter"
    OTHER = "other"


class Severity(StrEnum):
    FATAL = "fatal"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class JobStatus(StrEnum):
    QUEUED = "queued"
    ANALYZING = "analyzing"
    PLANNING = "planning"
    FORMATTING = "formatting"
    VALIDATING = "validating"
    REPAIRING = "repairing"
    COMPLETED = "completed"
    FAILED = "failed"
