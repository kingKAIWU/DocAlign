import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BatchWorkspace } from "@/components/batch-workspace";
import type { BatchAudit } from "@/lib/types";

const mocks = vi.hoisted(() => ({
  capabilities: vi.fn(),
  rulePacks: vi.fn(),
  rulePack: vi.fn(),
  batch: vi.fn(),
  createBatch: vi.fn(),
  retryBatchItem: vi.fn(),
  cancelBatch: vi.fn(),
  deleteBatch: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    constructor(public code: string, message: string, public status: number) {
      super(message);
    }
  },
  apiUrl: (path: string | null) => path ?? undefined,
  api: mocks,
}));

const completedBatch: BatchAudit = {
  schema_version: "batch-audit.v2",
  batch_id: "batch_saved",
  request_id: "saved-request",
  name: "月度材料",
  status: "completed",
  progress: 100,
  rule_pack_id: "pack_1",
  rule_pack_revision: 2,
  rule_pack_name: "办公室月报",
  rule_pack_spec_sha256: "a".repeat(64),
  processing_boundary_acknowledged: true,
  summary: { total: 1, completed: 1, failed: 0, canceled: 0, active: 0 },
  items: [
    {
      item_id: "item_1",
      position: 1,
      filename: "月报.docx",
      status: "completed",
      progress: 100,
      source_sha256: "b".repeat(64),
      document_id: "doc_1",
      analysis_id: "analysis_1",
      job_id: "job_1",
      attempt_count: 1,
      retryable: false,
      error_code: null,
      error_message: null,
      validation_passed: true,
      content_integrity_passed: true,
      changed_mutations: 12,
      source_review_features: 2,
      output_document_url: "/api/v1/jobs/job_1/output",
      audit_json_url: "/api/v1/jobs/job_1/audit.json",
    },
  ],
  output_zip_url: "/api/v1/batches/batch_saved/outputs.zip",
  delivery_package_url: "/api/v1/batches/batch_saved/delivery-package.zip",
  audit_json_url: "/api/v1/batches/batch_saved/audit.json",
  created_at: "2026-08-29T00:00:00Z",
  updated_at: "2026-08-29T00:01:00Z",
};

const failedBatch: BatchAudit = {
  ...completedBatch,
  status: "failed",
  summary: { total: 1, completed: 0, failed: 1, canceled: 0, active: 0 },
  output_zip_url: null,
  delivery_package_url: null,
  items: [
    {
      ...completedBatch.items[0],
      status: "failed",
      progress: 100,
      retryable: true,
      error_code: "JOB_INTERRUPTED",
      error_message: "本地服务重启导致任务中断。",
      validation_passed: null,
      content_integrity_passed: null,
      changed_mutations: null,
      output_document_url: null,
      audit_json_url: null,
    },
  ],
};

const activeBatch: BatchAudit = {
  ...completedBatch,
  status: "processing",
  progress: 45,
  summary: { total: 1, completed: 0, failed: 0, canceled: 0, active: 1 },
  output_zip_url: null,
  delivery_package_url: null,
  items: [
    {
      ...completedBatch.items[0],
      status: "formatting",
      progress: 45,
      retryable: false,
      validation_passed: null,
      content_integrity_passed: null,
      changed_mutations: null,
      output_document_url: null,
      audit_json_url: null,
    },
  ],
};

const canceledBatch: BatchAudit = {
  ...activeBatch,
  status: "canceled",
  progress: 100,
  summary: { total: 1, completed: 0, failed: 0, canceled: 1, active: 0 },
  items: [{ ...activeBatch.items[0], status: "canceled", progress: 100 }],
};

describe("BatchWorkspace", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    mocks.capabilities.mockResolvedValue({
      local_only: true,
      batch_processing: true,
      max_batch_files: 20,
      max_batch_total_mb: 200,
      max_upload_mb: 20,
    });
    mocks.rulePacks.mockResolvedValue({ rule_packs: [] });
  });

  afterEach(() => cleanup());

  it("restores the durable batch after a page reconnect", async () => {
    window.localStorage.setItem(
      "docalign.batch.v1",
      JSON.stringify({ batch_id: "batch_saved", pending_retries: {} }),
    );
    mocks.batch.mockResolvedValue(completedBatch);

    render(<BatchWorkspace />);

    expect(await screen.findByText("月度材料")).toBeInTheDocument();
    expect(screen.getByText("月报.docx")).toBeInTheDocument();
    expect(screen.getByText("复杂内容 2 类待核对")).toBeInTheDocument();
    expect(screen.getByText("已记录批量复杂内容核对确认")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "下载完整交付包" })).toHaveAttribute(
      "href",
      completedBatch.delivery_package_url,
    );
    expect(screen.getByRole("link", { name: "仅下载 DOCX ZIP" })).toHaveAttribute(
      "href",
      completedBatch.output_zip_url,
    );
    expect(mocks.batch).toHaveBeenCalledWith("batch_saved", expect.any(AbortSignal));
  });

  it("reuses one retry request when the response is lost", async () => {
    window.localStorage.setItem(
      "docalign.batch.v1",
      JSON.stringify({ batch_id: "batch_saved", pending_retries: {} }),
    );
    mocks.batch.mockResolvedValue(failedBatch);
    mocks.retryBatchItem
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce({
        ...failedBatch,
        status: "processing",
        summary: { total: 1, completed: 0, failed: 0, canceled: 0, active: 1 },
        items: [{ ...failedBatch.items[0], status: "queued", retryable: false }],
      });

    render(<BatchWorkspace />);
    const retry = await screen.findByRole("button", { name: "重试失败项" });
    fireEvent.click(retry);
    expect(await screen.findByText(/重试响应中断/)).toBeInTheDocument();
    const firstRequestId = mocks.retryBatchItem.mock.calls[0][2];

    fireEvent.click(screen.getByRole("button", { name: "重试失败项" }));
    await waitFor(() => expect(mocks.retryBatchItem).toHaveBeenCalledTimes(2));
    expect(mocks.retryBatchItem.mock.calls[1][2]).toBe(firstRequestId);
  });

  it("reuses one create request when the batch response is lost", async () => {
    mocks.rulePacks.mockResolvedValue({
      rule_packs: [
        {
          pack_id: "pack_1",
          name: "办公室月报",
          description: "",
          scope_label: "综合办公室月报",
          current_revision: 2,
          current_approval_status: "locally_approved",
          current_spec_sha256: "a".repeat(64),
          created_at: "2026-08-29T00:00:00Z",
          updated_at: "2026-08-29T00:00:00Z",
        },
      ],
    });
    mocks.rulePack.mockResolvedValue({
      pack_id: "pack_1",
      name: "办公室月报",
      description: "",
      scope_label: "综合办公室月报",
      current_revision: 2,
      created_at: "2026-08-29T00:00:00Z",
      updated_at: "2026-08-29T00:00:00Z",
      versions: [
        {
          revision: 2,
          approval_status: "locally_approved",
          approval_note: "已核对",
          change_note: "确认版本",
          restored_from_revision: null,
          spec_sha256: "a".repeat(64),
          source_type: "structured",
          created_at: "2026-08-29T00:00:00Z",
        },
      ],
    });
    mocks.createBatch
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce(completedBatch);

    render(<BatchWorkspace />);
    const input = await screen.findByLabelText("选择批量处理的 Word 文档");
    fireEvent.change(input, {
      target: {
        files: [
          new File(["docx"], "月报.docx", {
            type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
          }),
        ],
      },
    });
    const submit = await screen.findByRole("button", { name: "开始处理 1 个文档" });
    expect(submit).toBeDisabled();
    fireEvent.click(
      screen.getByRole("checkbox", {
        name: /我了解批量任务不会在开始前逐份暂停/,
      }),
    );
    expect(submit).toBeEnabled();
    fireEvent.click(submit);
    expect(await screen.findByText(/同一请求不会重复创建批次/)).toBeInTheDocument();
    const firstRequestId = mocks.createBatch.mock.calls[0][0].requestId;
    expect(mocks.createBatch.mock.calls[0][0].processingBoundaryAcknowledged).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "开始处理 1 个文档" }));
    expect(await screen.findByText("月度材料")).toBeInTheDocument();
    expect(mocks.createBatch.mock.calls[1][0].requestId).toBe(firstRequestId);
  });

  it("confirms cancellation and terminal local-data deletion", async () => {
    window.localStorage.setItem(
      "docalign.batch.v1",
      JSON.stringify({ batch_id: "batch_saved", pending_retries: {} }),
    );
    vi.spyOn(window, "confirm").mockReturnValue(true);
    mocks.batch.mockResolvedValue(activeBatch);
    mocks.cancelBatch.mockResolvedValue(canceledBatch);
    mocks.deleteBatch.mockResolvedValue(undefined);

    render(<BatchWorkspace />);

    fireEvent.click(await screen.findByRole("button", { name: "取消批次" }));
    await waitFor(() => expect(mocks.cancelBatch).toHaveBeenCalledWith("batch_saved"));
    expect(await screen.findByText("已取消，未生成输出")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "删除本地批次" }));
    await waitFor(() => expect(mocks.deleteBatch).toHaveBeenCalledWith("batch_saved"));
    expect(window.localStorage.getItem("docalign.batch.v1")).toBeNull();
    expect(await screen.findByText(/本地批次及其文件已全部删除/)).toBeInTheDocument();
    expect(window.confirm).toHaveBeenCalledTimes(2);
  });
});
