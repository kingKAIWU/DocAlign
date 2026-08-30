import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { JobPageClient } from "@/app/jobs/job-page-client";

const mocks = vi.hoisted(() => ({ job: vi.fn() }));

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams("jobId=job_reconnect"),
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
        change_details: [
          {
            locator: "p1",
            node_id: "node_1",
            category: "paragraph_styles",
            property_path: "paragraph.style",
            before_value: "Normal",
            after_value: "DA Body",
          },
        ],
        change_details_truncated: false,
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

    render(<JobPageClient />);

    expect(await screen.findByText("进度连接暂时中断，任务仍在本地运行，正在自动恢复…"))
      .toBeInTheDocument();
    await waitFor(() => expect(mocks.job).toHaveBeenCalledTimes(2), { timeout: 2_000 });
    expect(await screen.findByText("已完成")).toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.getByText("格式验证通过")).toBeInTheDocument();
    expect(screen.getByText("查看具体改动（1）")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "查看格式前后对照" })).toBeInTheDocument();
  });

  it("restores rule provenance and separates delivery checks from structure review", async () => {
    mocks.job.mockResolvedValue({
      job_id: "job_reference",
      document_id: "doc_1",
      analysis_id: "analysis_1",
      spec_id: "spec_1",
      status: "completed",
      progress: 100,
      auto_layout_splits: 0,
      result_summary: {
        validation_passed: true,
        content_integrity_passed: true,
        format_operations: 62,
        changed_mutations: 50,
        change_categories: { paragraph_styles: 30, text_font: 20 },
        change_details: [],
        change_details_truncated: false,
        warning_count: 0,
        validation_issue_count: 0,
        remaining_review_items: 0,
        structure_review_items: 0,
        delivery_review_items: 4,
        paragraphs_before: 19,
        paragraphs_after: 19,
        auto_layout_splits: 0,
        execution_evidence: {
          engine_version: "0.1.0",
          spec_sha256: "c".repeat(64),
          applied_preset: {
            preset_id: "nankai-thesis-2026-reference-cn",
            preset_name: "南开大学论文 2026 参考",
            pack_version: "1.0.0",
            claim_level: "reference",
            scope_label: "南开大学公开规范可执行子集",
            maintained_by: "DocAlign",
            last_reviewed_on: "2026-08-30",
            source_references: [
              {
                title: "南开大学研究生院",
                url: "https://graduate.nankai.edu.cn/example",
                version: "2026版",
              },
            ],
            catalog_spec_sha256: "d".repeat(64),
            matches_catalog_spec: false,
            automated_requirements: [],
            review_requirements: [
              {
                requirement_id: "4.5",
                requirement: "摘要标题语言字体",
                status: "manual_review",
                implementation_note: "在 Word 中核对。",
              },
              {
                requirement_id: "3.4",
                requirement: "分节页码",
                status: "unsupported",
                implementation_note: "在 Word 中设置。",
              },
            ],
            acceptance_fixture_id: "institutional-reference-smoke-v1",
            acceptance_last_passed_on: "2026-08-30",
            acceptance_automated_checks: ["页面设置一致"],
            acceptance_manual_checks: ["核对封面"],
            limitations: ["未经发布机构审核或背书。"],
          },
        },
      },
      output_document_url: "/output",
      audit_json_url: "/audit.json",
      audit_markdown_url: "/audit.md",
      error_code: null,
      error_message: null,
      created_at: "2026-08-30T00:00:00Z",
      updated_at: "2026-08-30T00:00:01Z",
    });

    render(<JobPageClient />);

    expect(await screen.findByText("已声明自动条款验证通过")).toBeInTheDocument();
    expect(screen.getByText("个结构段落待确认")).toBeInTheDocument();
    expect(screen.getByText("项交付前人工核对")).toBeInTheDocument();
    expect(screen.getByText("南开大学论文 2026 参考")).toBeInTheDocument();
    expect(screen.getByText("目录规则已被修改")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("覆盖和验收结论不能直接代表本次输出");
    expect(screen.getByText("人工复核")).toBeInTheDocument();
    expect(screen.getByText("暂不支持")).toBeInTheDocument();
    expect(screen.getByText("核对封面")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "南开大学研究生院" })).toHaveAttribute(
      "href",
      "https://graduate.nankai.edu.cn/example",
    );
  });
});
