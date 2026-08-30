import type {
  Analysis,
  BatchAudit,
  Capabilities,
  CleanupPreset,
  ComplianceReport,
  DocumentRecord,
  FormattingSpec,
  Job,
  RulePackApprovalStatus,
  RulePackArtifact,
  RulePackCatalogItem,
  RulePackDetail,
  SemanticRole,
  SupportDiagnosticReport,
  TemplateRuleCandidate,
  WorkspaceStorageReport,
} from "./types";

export const API_BASE =
  process.env.NEXT_PUBLIC_DOCALIGN_API_URL ??
  (process.env.NODE_ENV === "production" ? "/api/v1" : "http://127.0.0.1:8000/api/v1");

export class ApiError extends Error {
  constructor(
    public code: string,
    message: string,
    public status: number,
    public details: Record<string, unknown> = {},
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: init?.body instanceof FormData ? init.headers : { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    const error = payload?.error;
    throw new ApiError(
      error?.code ?? "REQUEST_FAILED",
      error?.message ?? `Request failed with HTTP ${response.status}`,
      response.status,
      error?.details ?? {},
    );
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  capabilities: (signal?: AbortSignal) =>
    request<Capabilities>("/capabilities", { signal }),
  preset: (signal?: AbortSignal) =>
    request<{ preset_id: string; spec: FormattingSpec }>("/presets/default-clean-cn", {
      signal,
    }),
  presets: (signal?: AbortSignal) =>
    request<{ presets: CleanupPreset[] }>("/presets", { signal }),
  rulePacks: (signal?: AbortSignal) =>
    request<{ rule_packs: RulePackCatalogItem[] }>("/rule-packs", { signal }),
  rulePack: (packId: string, signal?: AbortSignal) =>
    request<RulePackDetail>(`/rule-packs/${packId}`, { signal }),
  rulePackVersion: (packId: string, revision: number, signal?: AbortSignal) =>
    request<RulePackArtifact>(`/rule-packs/${packId}/versions/${revision}`, { signal }),
  createRulePack: (payload: {
    request_id: string;
    name: string;
    description: string;
    scope_label: string;
    spec: FormattingSpec;
    change_note: string;
    approval_status: RulePackApprovalStatus;
    approval_note: string | null;
  }) =>
    request<RulePackArtifact>("/rule-packs", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  createRulePackVersion: (
    packId: string,
    payload: {
      request_id: string;
      spec: FormattingSpec;
      change_note: string;
      approval_status: RulePackApprovalStatus;
      approval_note: string | null;
    },
  ) =>
    request<RulePackArtifact>(`/rule-packs/${packId}/versions`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  restoreRulePackVersion: (
    packId: string,
    revision: number,
    changeNote: string,
    requestId: string,
  ) =>
    request<RulePackArtifact>(`/rule-packs/${packId}/restore`, {
      method: "POST",
      body: JSON.stringify({ request_id: requestId, revision, change_note: changeNote }),
    }),
  document: (documentId: string, signal?: AbortSignal) =>
    request<DocumentRecord>(`/documents/${documentId}`, { signal }),
  analysis: (analysisId: string, signal?: AbortSignal) =>
    request<Analysis>(`/analyses/${analysisId}`, { signal }),
  upload: (file: File) => {
    const body = new FormData();
    body.append("file", file);
    return request<DocumentRecord>("/documents", { method: "POST", body });
  },
  templateCandidate: (file: File) => {
    const body = new FormData();
    body.append("file", file);
    return request<TemplateRuleCandidate>("/templates/candidate", { method: "POST", body });
  },
  createFromText: (text: string, filename: string) =>
    request<DocumentRecord>("/documents/from-text", {
      method: "POST",
      body: JSON.stringify({ text, filename }),
    }),
  analyze: (documentId: string, mode: "deterministic" | "smart") =>
    request<Analysis>(`/documents/${documentId}/analyze`, {
      method: "POST",
      body: JSON.stringify({ mode }),
    }),
  overrideRoles: (
    analysisId: string,
    overrides: Array<{ node_id: string; role: SemanticRole }>,
  ) =>
    request<Analysis>(`/analyses/${analysisId}/role-overrides`, {
      method: "PUT",
      body: JSON.stringify({ overrides }),
    }),
  createSpec: (documentId: string, spec: FormattingSpec) =>
    request<{ spec_id: string; spec: FormattingSpec }>("/specs", {
      method: "POST",
      body: JSON.stringify({ document_id: documentId, spec }),
    }),
  compileSpec: (
    documentId: string,
    analysisId: string,
    instruction: string,
    applyPreset: boolean,
  ) =>
    request<{
      spec_id: string;
      spec: FormattingSpec;
      applied_capabilities: string[];
      assumptions: string[];
      ambiguities: string[];
      unsupported_requests: string[];
    }>("/specs/compile", {
      method: "POST",
      body: JSON.stringify({
        document_id: documentId,
        analysis_id: analysisId,
        instruction,
        apply_preset: applyPreset,
      }),
    }),
  createJob: (documentId: string, analysisId: string, specId: string) =>
    request<Job>("/jobs", {
      method: "POST",
      body: JSON.stringify({ document_id: documentId, analysis_id: analysisId, spec_id: specId }),
    }),
  compliance: (documentId: string, analysisId: string, specId: string) =>
    request<ComplianceReport>(`/documents/${documentId}/compliance`, {
      method: "POST",
      body: JSON.stringify({ analysis_id: analysisId, spec_id: specId }),
    }),
  job: (jobId: string, signal?: AbortSignal) =>
    request<Job>(`/jobs/${jobId}`, { signal, cache: "no-store" }),
  createBatch: (payload: {
    requestId: string;
    name: string;
    rulePackId: string;
    rulePackRevision: number;
    files: File[];
  }) => {
    const body = new FormData();
    body.append("request_id", payload.requestId);
    body.append("name", payload.name);
    body.append("rule_pack_id", payload.rulePackId);
    body.append("rule_pack_revision", String(payload.rulePackRevision));
    for (const file of payload.files) body.append("files", file);
    return request<BatchAudit>("/batches", { method: "POST", body });
  },
  batch: (batchId: string, signal?: AbortSignal) =>
    request<BatchAudit>(`/batches/${batchId}`, {
      signal,
      cache: "no-store",
    }),
  workspaceStorage: (signal?: AbortSignal) =>
    request<WorkspaceStorageReport>("/workspace/storage", {
      signal,
      cache: "no-store",
    }),
  diagnostics: (signal?: AbortSignal) =>
    request<SupportDiagnosticReport>("/diagnostics", {
      signal,
      cache: "no-store",
    }),
  quitDesktop: () =>
    request<{ status: "shutting_down" }>("/system/quit", {
      method: "POST",
      headers: { "X-DocAlign-Action": "quit" },
      body: "{}",
    }),
  retryBatchItem: (batchId: string, itemId: string, requestId: string) =>
    request<BatchAudit>(`/batches/${batchId}/items/${itemId}/retry`, {
      method: "POST",
      body: JSON.stringify({ request_id: requestId }),
    }),
  cancelBatch: (batchId: string) =>
    request<BatchAudit>(`/batches/${batchId}/cancel`, { method: "POST" }),
  deleteBatch: (batchId: string) =>
    request<void>(`/batches/${batchId}`, { method: "DELETE" }),
  deleteDocument: (documentId: string) =>
    request<void>(`/documents/${documentId}`, { method: "DELETE" }),
};

export function apiUrl(path: string | null | undefined): string | undefined {
  if (!path) return undefined;
  if (path.startsWith("http://") || path.startsWith("https://")) return path;
  const origin = API_BASE.replace(/\/api\/v1\/?$/, "");
  return `${origin}${path}`;
}
