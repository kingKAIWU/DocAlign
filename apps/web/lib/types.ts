import type { components } from "./generated-api";

export type SemanticRole = components["schemas"]["SemanticRole"];

export type ParagraphBlock = {
  kind: "paragraph";
  node_id: string;
  locator: string;
  index: number;
  text: string;
  detected_role: SemanticRole;
  role_confidence: number;
  role_source: string;
  role_evidence: string[];
  contains_drawing: boolean;
  is_empty: boolean;
};

export type TableBlock = {
  kind: "table";
  node_id: string;
  locator: string;
  index: number;
  rows: number;
  columns_estimate: number;
  cell_texts: string[][];
};

export type UnsupportedBlock = {
  kind: "unsupported";
  node_id: string;
  locator: string;
  index: number;
  qname: string;
  text_preview: string;
};

export type Analysis = {
  analysis_id: string;
  document_ir: {
    source_filename: string;
    blocks: Array<ParagraphBlock | TableBlock | UnsupportedBlock>;
    warnings: Array<{ code: string; message: string; node_id?: string }>;
  };
  summary: {
    paragraph_count: number;
    table_count: number;
    image_count: number;
    unknown_count: number;
    role_counts: Record<string, number>;
    analysis_mode: "deterministic" | "smart";
    document_kind: string | null;
    document_kind_confidence: number;
    model_reviewed_paragraphs: number;
    model_provider: string | null;
    model_name: string | null;
  };
};

export type DocumentRecord = {
  document_id: string;
  filename: string;
  sha256: string;
  size_bytes: number;
  status: "uploaded";
};

export type Capabilities = {
  docx: boolean;
  structured_spec: boolean;
  llm_configured: boolean;
  llm_protocol: string;
  smart_semantic_analysis: boolean;
  smart_analysis_sends_paragraph_text: boolean;
  auto_layout: boolean;
  default_cleanup_preset: boolean;
  audit_only: boolean;
  format_manifest: boolean;
  template_rule_candidate: boolean;
  rule_pack_library: boolean;
  batch_processing: boolean;
  max_batch_files: number;
  max_batch_total_mb: number;
  max_upload_mb: number;
  local_only: boolean;
};

export type Job = components["schemas"]["JobResponse"];

export type JobResultSummary = components["schemas"]["JobResultSummary"];

export type CleanupPreset = components["schemas"]["CleanupPresetCatalogItem"];

export type FormattingSpec = components["schemas"]["FormattingSpec"];

export type TemplateRuleCandidate = components["schemas"]["TemplateRuleCandidate"];

export type RulePackApprovalStatus = components["schemas"]["RulePackApprovalStatus"];

export type RulePackArtifact = components["schemas"]["RulePackArtifact"];

export type RulePackCatalogItem = components["schemas"]["RulePackCatalogItem"];

export type RulePackDetail = components["schemas"]["RulePackDetailResponse"];

export type BatchStatus = components["schemas"]["BatchStatus"];

export type BatchItemStatus = components["schemas"]["BatchItemStatus"];

export type BatchItem = components["schemas"]["BatchAuditItem"];

export type BatchAudit = components["schemas"]["BatchAudit"];

export type WorkspaceStorageReport = components["schemas"]["WorkspaceStorageReport"];

export type StorageBatchItem = components["schemas"]["StorageBatchItem"];

export type StorageDocumentItem = components["schemas"]["StorageDocumentItem"];

export type ComplianceViolation = {
  code: string;
  severity: "info" | "warning" | "error" | "fatal";
  message: string;
  node_id?: string | null;
  locator?: string | null;
  details: Record<string, unknown>;
};

export type ComplianceReport = {
  schema_version: "compliance-report.v1";
  document_id: string;
  analysis_id: string;
  spec_id: string;
  compliant: boolean;
  summary: {
    total_violations: number;
    returned_violations: number;
    affected_locators: number;
    by_severity: Record<string, number>;
    by_code: Record<string, number>;
    truncated: boolean;
  };
  violations: ComplianceViolation[];
  content_fingerprint: string | null;
};
