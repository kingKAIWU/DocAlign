import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import JobPage from "@/app/jobs/[jobId]/page";

const mocks = vi.hoisted(() => ({ job: vi.fn() }));

vi.mock("next/navigation", () => ({
  useParams: () => ({ jobId: "job_reconnect" }),
}));

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    constructor(
      public code: string,
      message: string,
      public status: number,
    ) {
      super(message);
    }
  },
  apiUrl: (path: string | null) => path ?? undefined,
  api: { job: mocks.job },
}));

describe("JobPage", () => {
  afterEach(() => cleanup());

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("recovers after a transient progress connection failure", async () => {
    mocks.job.mockRejectedValueOnce(new Error("offline")).mockResolvedValue({
      job_id: "job_reconnect",
      document_id: "doc_1",
      analysis_id: "analysis_1",
      spec_id: "spec_1",
      status: "completed",
      progress: 100,
      auto_layout_splits: 0,
      result_summary: {
        validation_passed: true,
        content_integrity_passed: true,
        format_operations: 12,
        changed_mutations: 8,
        change_categories: { paragraph_styles: 8 },
        warning_count: 0,
        validation_issue_count: 0,
        remaining_review_items: 0,
        paragraphs_before: 4,
        paragraphs_after: 4,
        auto_layout_splits: 0,
      },
      output_document_url: "/output",
      audit_json_url: "/audit.json",
      audit_markdown_url: "/audit.md",
      error_code: null,
      error_message: null,
      created_at: "2026-08-28T00:00:00Z",
      updated_at: "2026-08-28T00:00:01Z",
    });

    render(<JobPage />);

    expect(await screen.findByText("进度连接暂时中断，任务仍在本地运行，正在自动恢复…"))
      .toBeInTheDocument();
    await waitFor(() => expect(mocks.job).toHaveBeenCalledTimes(2), { timeout: 2_000 });
    expect(await screen.findByText("已完成")).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.getByText("格式验证通过")).toBeInTheDocument();
  });
});
