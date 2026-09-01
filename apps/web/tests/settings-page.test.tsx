import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import SettingsPage from "@/app/settings/page";
import type {
  DeliveryPackageVerification,
  SupportDiagnosticReport,
  WorkspaceStorageReport,
} from "@/lib/types";

const mocks = vi.hoisted(() => ({
  capabilities: vi.fn(),
  workspaceStorage: vi.fn(),
  diagnostics: vi.fn(),
  verifyDelivery: vi.fn(),
  quitDesktop: vi.fn(),
  deleteBatch: vi.fn(),
  deleteDocument: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  API_BASE: "http://127.0.0.1:8000/api/v1",
  ApiError: class ApiError extends Error {
    constructor(public code: string, message: string, public status: number) {
      super(message);
    }
  },
  api: mocks,
}));

const report: WorkspaceStorageReport = {
  schema_version: "workspace-storage.v1",
  generated_at: "2026-08-29T08:00:00Z",
  docalign_bytes: 12 * 1024 * 1024,
  reclaimable_bytes: 9 * 1024 * 1024,
  disk_total_bytes: 500 * 1024 * 1024 * 1024,
  disk_free_bytes: 200 * 1024 * 1024 * 1024,
  minimum_free_reserve_bytes: 1024 * 1024 * 1024,
  write_headroom_bytes: 199 * 1024 * 1024 * 1024,
  estimated_backup_working_bytes: 88 * 1024 * 1024,
  can_create_backup: true,
  pressure: "normal",
  categories: [
    { category: "source_documents", bytes: 4 * 1024 * 1024, file_count: 2 },
    { category: "analyses", bytes: 1024 * 1024, file_count: 2 },
    { category: "job_audits", bytes: 1024 * 1024, file_count: 4 },
    { category: "outputs", bytes: 4 * 1024 * 1024, file_count: 2 },
    { category: "batch_packages", bytes: 1024 * 1024, file_count: 1 },
    { category: "database", bytes: 1024 * 1024, file_count: 1 },
    { category: "other", bytes: 0, file_count: 0 },
  ],
  records: {
    documents: 3,
    analyses: 3,
    jobs: 3,
    active_jobs: 0,
    batches: 1,
    active_batches: 0,
    rule_packs: 2,
  },
  terminal_batches: [
    {
      batch_id: "batch_1",
      name: "归档材料",
      status: "completed_with_errors",
      updated_at: "2026-08-28T08:00:00Z",
      bytes: 6 * 1024 * 1024,
      item_count: 2,
      completed: 1,
      failed: 1,
      canceled: 0,
    },
  ],
  terminal_batches_truncated: false,
  unbatched_documents: [
    {
      document_id: "doc_1",
      filename: "独立报告.docx",
      created_at: "2026-08-27T08:00:00Z",
      bytes: 3 * 1024 * 1024,
      analysis_count: 1,
      job_count: 1,
      active_job_count: 0,
      deletable: true,
    },
    {
      document_id: "doc_active",
      filename: "处理中材料.docx",
      created_at: "2026-08-29T08:00:00Z",
      bytes: 2 * 1024 * 1024,
      analysis_count: 1,
      job_count: 1,
      active_job_count: 1,
      deletable: false,
    },
  ],
  unbatched_documents_truncated: false,
};

const diagnosticReport: SupportDiagnosticReport = {
  schema_version: "support-diagnostic.v1",
  generated_at: "2026-08-29T08:00:00Z",
  overall: "attention",
  runtime: {
    application_version: "0.1.0",
    python_version: "3.12.14",
    operating_system: "Darwin",
    operating_system_release: "25.4.0",
    architecture: "arm64",
  },
  configuration: {
    local_only: true,
    database_backend: "sqlite",
    llm_configured: false,
    job_concurrency: 1,
    max_upload_mb: 20,
    max_batch_files: 20,
    max_batch_total_mb: 200,
    min_free_mb: 1024,
  },
  data_summary: {
    docalign_bytes: 12 * 1024 * 1024,
    disk_total_bytes: 500 * 1024 * 1024 * 1024,
    disk_free_bytes: 200 * 1024 * 1024 * 1024,
    storage_pressure: "normal",
    documents: 3,
    analyses: 3,
    jobs: 3,
    active_jobs: 0,
    failed_jobs: 1,
    batches: 1,
    rule_packs: 2,
  },
  checks: [
    {
      check_id: "database_connection",
      status: "pass",
      title: "本地数据库",
      detail: "连接正常，基础查询已通过。",
      remediation: null,
    },
    {
      check_id: "artifact_references",
      status: "warning",
      title: "本地产物引用",
      detail: "发现 1 个数据库记录指向的本地产物缺失。未收集文件名或路径。",
      remediation: "保留本诊断 JSON 并寻求支持。",
    },
  ],
  recent_error_codes: [{ code: "JOB_INTERRUPTED", count: 1 }],
  excluded_data: [
    "document_content",
    "filenames",
    "record_identifiers",
    "local_paths",
    "database_connection_string",
    "model_endpoint",
    "credentials",
    "raw_logs",
  ],
};

const deliveryVerification: DeliveryPackageVerification = {
  schema_version: "delivery-package-verification.v1",
  valid: true,
  package_kind: "job",
  package_id: "job_delivery",
  created_at: "2026-08-31T08:00:00Z",
  application_version: "0.1.0",
  checksum_algorithm: "sha256",
  signature_status: "not_signed",
  payload_file_count: 3,
  payload_bytes: 3 * 1024 * 1024,
  items: [
    {
      position: 1,
      job_id: "job_delivery",
      source_filename: "合同终稿.docx",
      output_sha256: "a".repeat(64),
      validation_passed: true,
      content_integrity_passed: true,
      structure_review_items: 0,
      delivery_review_items: 2,
      source_review_features: 1,
    },
  ],
  warnings: ["unsigned"],
};

describe("SettingsPage storage center", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    mocks.capabilities.mockResolvedValue({
      local_only: true,
      desktop_app: false,
      llm_configured: false,
      max_upload_mb: 20,
      max_batch_files: 20,
      max_batch_total_mb: 200,
      min_free_mb: 1024,
      max_delivery_package_mb: 220,
      verifiable_workspace_backup: true,
      safe_workspace_restore: true,
    });
    mocks.deleteBatch.mockResolvedValue(undefined);
    mocks.deleteDocument.mockResolvedValue(undefined);
    mocks.workspaceStorage.mockResolvedValue(report);
    mocks.diagnostics.mockResolvedValue(diagnosticReport);
    mocks.verifyDelivery.mockResolvedValue(deliveryVerification);
    mocks.quitDesktop.mockResolvedValue({ status: "shutting_down" });
  });

  afterEach(() => cleanup());

  it("shows categorized usage and clears matching recovery records after confirmed deletion", async () => {
    const afterBatch = {
      ...report,
      terminal_batches: [],
      records: { ...report.records, documents: 1, jobs: 1, batches: 0 },
    };
    const afterDocument = {
      ...afterBatch,
      unbatched_documents: [report.unbatched_documents[1]],
      records: { ...afterBatch.records, documents: 0, jobs: 0 },
    };
    mocks.workspaceStorage
      .mockResolvedValueOnce(report)
      .mockResolvedValueOnce(afterBatch)
      .mockResolvedValueOnce(afterDocument);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    window.localStorage.setItem("docalign.batch.v1", JSON.stringify({ batch_id: "batch_1" }));
    window.localStorage.setItem("docalign.workspace.v1", JSON.stringify({ document_id: "doc_1" }));

    render(<SettingsPage />);

    expect(await screen.findByText("12 MB")).toBeInTheDocument();
    expect(screen.getByText("源 DOCX")).toBeInTheDocument();
    expect(screen.getByText("2 个规则包")).toBeInTheDocument();
    const activeItem = screen.getByText("处理中材料.docx").closest<HTMLElement>(".storage-item");
    expect(activeItem).not.toBeNull();
    expect(within(activeItem!).getByRole("button", { name: "处理中" })).toBeDisabled();

    const batchItem = screen.getByText("归档材料").closest<HTMLElement>(".storage-item");
    fireEvent.click(within(batchItem!).getByRole("button", { name: "删除" }));
    await waitFor(() => expect(mocks.deleteBatch).toHaveBeenCalledWith("batch_1"));
    expect(window.localStorage.getItem("docalign.batch.v1")).toBeNull();

    const documentItem = screen.getByText("独立报告.docx").closest<HTMLElement>(".storage-item");
    fireEvent.click(within(documentItem!).getByRole("button", { name: "删除" }));
    await waitFor(() => expect(mocks.deleteDocument).toHaveBeenCalledWith("doc_1"));
    expect(window.localStorage.getItem("docalign.workspace.v1")).toBeNull();
    expect(window.confirm).toHaveBeenCalledTimes(2);
  });

  it("runs a privacy-safe diagnostic and exposes an explicit local download", async () => {
    render(<SettingsPage />);

    fireEvent.click(screen.getByRole("button", { name: "运行诊断" }));

    expect(await screen.findByText("建议关注")).toBeInTheDocument();
    const diagnosticCard = screen
      .getByRole("heading", { name: "本机诊断与支持报告" })
      .closest<HTMLElement>(".diagnostic-card");
    expect(diagnosticCard).not.toBeNull();
    expect(within(diagnosticCard!).getByText("本地数据库")).toBeInTheDocument();
    expect(within(diagnosticCard!).getByText("本地产物引用")).toBeInTheDocument();
    expect(within(diagnosticCard!).getByText("JOB_INTERRUPTED × 1")).toBeInTheDocument();
    expect(within(diagnosticCard!).getByText(/明确排除正文、文件名、记录 ID/)).toBeInTheDocument();
    expect(within(diagnosticCard!).getByRole("link", { name: "下载安全诊断 JSON" })).toHaveAttribute(
      "href",
      "http://127.0.0.1:8000/api/v1/diagnostics/export",
    );
    expect(mocks.diagnostics).toHaveBeenCalledTimes(1);
  });

  it("offers a guarded full workspace backup and explains safe restore", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<SettingsPage />);

    const backup = await screen.findByRole("link", { name: "下载完整备份" });
    expect(backup).toHaveAttribute(
      "href",
      "http://127.0.0.1:8000/api/v1/workspace/backup",
    );
    expect(backup).toHaveAttribute("aria-disabled", "false");
    expect(screen.getByText("敏感且未加密")).toBeInTheDocument();
    expect(screen.getByText(/只允许恢复到尚不存在的目录/)).toBeInTheDocument();
    expect(fireEvent.click(backup)).toBe(false);
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining("源 DOCX"));
  });

  it("shows safe write headroom and blocks backup when estimated space is insufficient", async () => {
    mocks.workspaceStorage.mockResolvedValueOnce({
      ...report,
      disk_free_bytes: 1024 * 1024 * 1024,
      write_headroom_bytes: 0,
      can_create_backup: false,
    });
    render(<SettingsPage />);

    expect(await screen.findByText("安全可写余量")).toBeInTheDocument();
    expect(screen.getByText(/保留 1 GB 安全余量/)).toBeInTheDocument();
    expect(screen.getByText(/新上传或排版会在开始前停止/)).toBeInTheDocument();
    expect(screen.getByText(/预计需要约 88 MB 临时空间/)).toBeInTheDocument();
    const backup = screen.getByRole("link", { name: "下载完整备份" });
    expect(backup).toHaveAttribute("aria-disabled", "true");
    expect(fireEvent.click(backup)).toBe(false);
  });

  it("verifies a delivery package locally and explains the unsigned boundary", async () => {
    render(<SettingsPage />);

    const file = new File(["package"], "job-delivery.zip", { type: "application/zip" });
    fireEvent.change(screen.getByLabelText("选择交付包 ZIP"), {
      target: { files: [file] },
    });
    fireEvent.click(screen.getByRole("button", { name: "开始校验" }));

    expect(await screen.findByText("校验通过")).toBeInTheDocument();
    expect(screen.getByText(/合同终稿\.docx/)).toBeInTheDocument();
    expect(screen.getByText(/发布者身份未验证/)).toBeInTheDocument();
    expect(mocks.verifyDelivery).toHaveBeenCalledWith(file);
  });

  it("shows an explicit desktop-only safe exit action", async () => {
    mocks.capabilities.mockResolvedValueOnce({
      local_only: true,
      desktop_app: true,
      llm_configured: false,
      max_upload_mb: 20,
      max_batch_files: 20,
      max_batch_total_mb: 200,
      min_free_mb: 1024,
      max_delivery_package_mb: 220,
      verifiable_workspace_backup: true,
      safe_workspace_restore: true,
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<SettingsPage />);

    fireEvent.click(await screen.findByRole("button", { name: "安全退出应用" }));
    await waitFor(() => expect(mocks.quitDesktop).toHaveBeenCalledTimes(1));
    expect(screen.getByText(/DocAlign 正在安全退出/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "正在安全退出…" })).toBeDisabled();
  });
});
