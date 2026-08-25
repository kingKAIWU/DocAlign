import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Workspace } from "@/components/workspace";

const mocks = vi.hoisted(() => ({
  capabilities: vi.fn(),
  preset: vi.fn(),
  presets: vi.fn(),
  document: vi.fn(),
  analysis: vi.fn(),
  job: vi.fn(),
  createFromText: vi.fn(),
  createSpec: vi.fn(),
  compileSpec: vi.fn(),
  compliance: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  API_BASE: "http://127.0.0.1:8000/api/v1",
  ApiError: class ApiError extends Error {},
  apiUrl: (path: string) => path,
  api: {
    capabilities: mocks.capabilities,
    preset: mocks.preset,
    presets: mocks.presets,
    document: mocks.document,
    analysis: mocks.analysis,
    upload: vi.fn(),
    createFromText: mocks.createFromText,
    analyze: vi.fn(),
    overrideRoles: vi.fn(),
    createSpec: mocks.createSpec,
    compileSpec: mocks.compileSpec,
    compliance: mocks.compliance,
    createJob: vi.fn(),
    job: mocks.job,
    deleteDocument: vi.fn(),
  },
}));

describe("Workspace", () => {
  afterEach(() => cleanup());

  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    mocks.capabilities.mockResolvedValue({
      docx: true,
      structured_spec: true,
      llm_configured: false,
      llm_protocol: "openai-compatible-chat-completions",
      smart_semantic_analysis: false,
      smart_analysis_sends_paragraph_text: true,
      auto_layout: true,
      default_cleanup_preset: true,
      audit_only: true,
      format_manifest: true,
      max_upload_mb: 20,
      local_only: true,
    });
    mocks.preset.mockResolvedValue({
      preset_id: "default-clean-cn",
      spec: {
        schema_version: "formatting-spec.v1",
        roles: {},
        visual_cleanup: {
          text_color_hex: "000000",
          remove_text_highlight: true,
          remove_character_shading: true,
          remove_paragraph_shading: true,
          remove_table_cell_shading: true,
          remove_page_background: true,
        },
      },
    });
    mocks.presets.mockResolvedValue({
      presets: [
        {
          preset_id: "default-clean-cn",
          name: "常规文档",
          description: "A4 竖版、正文小四、1.5 倍行距。",
          recommended_kinds: ["other"],
          spec: {
            schema_version: "formatting-spec.v1",
            roles: {},
            visual_cleanup: {
              text_color_hex: "000000",
              remove_text_highlight: true,
              remove_character_shading: true,
              remove_paragraph_shading: true,
              remove_table_cell_shading: true,
              remove_page_background: true,
            },
          },
        },
      ],
    });
  });

  it("renders the Chinese-first local workspace", async () => {
    render(<Workspace />);
    expect(screen.getByRole("heading", { name: "先理解文档，再智能排版" })).toBeInTheDocument();
    expect(await screen.findByText("常规、干净、可重复")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "自然语言编译" }));
    expect(screen.getByText("兼容模型未配置；默认整理模式仍可直接使用。")).toBeInTheDocument();
    expect(screen.getByText("本地处理")).toBeInTheDocument();
  });

  it("clears a stale connection error after a successful retry", async () => {
    mocks.capabilities
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValue({
        docx: true,
        structured_spec: true,
        llm_configured: false,
        llm_protocol: "openai-compatible-chat-completions",
        smart_semantic_analysis: false,
        smart_analysis_sends_paragraph_text: true,
        auto_layout: true,
        default_cleanup_preset: true,
        audit_only: true,
        format_manifest: true,
        max_upload_mb: 20,
        local_only: true,
      });
    mocks.presets.mockRejectedValueOnce(new Error("offline"));

    render(<Workspace />);
    const error = await screen.findByText(
      "无法连接本地排版服务。请确认 API 已启动，然后点击重试连接。",
    );
    expect(error).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "重试连接" }));

    await waitFor(() => expect(mocks.capabilities).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(error).not.toBeInTheDocument());
    expect(screen.getByText("本地处理")).toBeInTheDocument();
  });

  it("restores the deterministic cleanup rules from the default mode tab", async () => {
    render(<Workspace />);
    const editor = await screen.findByLabelText("结构化规则");
    expect((editor as HTMLTextAreaElement).value).toContain('"remove_text_highlight": true');

    fireEvent.click(screen.getByRole("tab", { name: "自然语言编译" }));
    fireEvent.change(editor, { target: { value: "{}" } });
    fireEvent.click(screen.getByRole("tab", { name: "默认整理模式" }));

    expect((editor as HTMLTextAreaElement).value).toContain('"remove_page_background": true');
    expect(screen.getByRole("status")).toHaveTextContent("已载入默认整理模式");
  });

  it("restores the last local workspace after refresh", async () => {
    window.localStorage.setItem(
      "docalign.workspace.v1",
      JSON.stringify({ document_id: "doc_1", analysis_id: "analysis_1", job_id: "job_1" }),
    );
    mocks.document.mockResolvedValue({
      document_id: "doc_1",
      filename: "restored.docx",
      sha256: "abc",
      size_bytes: 1024,
      status: "uploaded",
    });
    mocks.analysis.mockResolvedValue({
      analysis_id: "analysis_1",
      document_ir: { source_filename: "restored.docx", blocks: [], warnings: [] },
      summary: {
        paragraph_count: 0,
        table_count: 0,
        image_count: 0,
        unknown_count: 0,
        role_counts: {},
        analysis_mode: "deterministic",
        document_kind: null,
        document_kind_confidence: 0,
        model_reviewed_paragraphs: 0,
        model_provider: null,
        model_name: null,
      },
    });
    mocks.job.mockResolvedValue({
      job_id: "job_1",
      document_id: "doc_1",
      analysis_id: "analysis_1",
      spec_id: "spec_1",
      status: "completed",
      progress: 100,
      output_document_url: "/api/v1/jobs/job_1/output",
      audit_json_url: "/api/v1/jobs/job_1/audit.json",
      audit_markdown_url: "/api/v1/jobs/job_1/audit.md",
      error_code: null,
      error_message: null,
    });

    render(<Workspace />);
    expect(await screen.findByText("restored.docx")).toBeInTheDocument();
    expect(screen.getByText("已恢复上次本地工作区。")).toBeInTheDocument();
    expect(screen.getByText("输出已生成")).toBeInTheDocument();
  });

  it("resumes one job poll after refresh and reaches the terminal state", async () => {
    window.localStorage.setItem(
      "docalign.workspace.v1",
      JSON.stringify({ document_id: "doc_2", analysis_id: "analysis_2", job_id: "job_2" }),
    );
    mocks.document.mockResolvedValue({
      document_id: "doc_2",
      filename: "running.docx",
      sha256: "def",
      size_bytes: 1024,
      status: "uploaded",
    });
    mocks.analysis.mockResolvedValue({
      analysis_id: "analysis_2",
      document_ir: { source_filename: "running.docx", blocks: [], warnings: [] },
      summary: {
        paragraph_count: 0,
        table_count: 0,
        image_count: 0,
        unknown_count: 0,
        role_counts: {},
        analysis_mode: "deterministic",
        document_kind: null,
        document_kind_confidence: 0,
        model_reviewed_paragraphs: 0,
        model_provider: null,
        model_name: null,
      },
    });
    mocks.job
      .mockResolvedValueOnce({
        job_id: "job_2",
        document_id: "doc_2",
        analysis_id: "analysis_2",
        spec_id: "spec_2",
        status: "formatting",
        progress: 45,
        auto_layout_splits: 0,
        output_document_url: null,
        audit_json_url: null,
        audit_markdown_url: null,
        error_code: null,
        error_message: null,
      })
      .mockResolvedValueOnce({
        job_id: "job_2",
        document_id: "doc_2",
        analysis_id: "analysis_2",
        spec_id: "spec_2",
        status: "completed",
        progress: 100,
        auto_layout_splits: 2,
        output_document_url: "/api/v1/jobs/job_2/output",
        audit_json_url: "/api/v1/jobs/job_2/audit.json",
        audit_markdown_url: "/api/v1/jobs/job_2/audit.md",
        error_code: null,
        error_message: null,
      });

    render(<Workspace />);

    expect(await screen.findByText("running.docx")).toBeInTheDocument();
    expect(
      await screen.findByText(/自动排版完成：重构 2 处连续正文/),
    ).toBeInTheDocument();
    expect(mocks.job).toHaveBeenCalledTimes(2);
  });

  it("creates a DOCX skeleton from pasted plain text", async () => {
    mocks.createFromText.mockResolvedValue({
      document_id: "doc_text",
      filename: "未命名文档.docx",
      sha256: "text-sha",
      size_bytes: 2048,
      status: "uploaded",
    });
    render(<Workspace />);
    fireEvent.click(screen.getByRole("button", { name: "粘贴纯文本" }));
    fireEvent.change(screen.getByLabelText("粘贴纯文本"), {
      target: { value: "# 标题\n正文内容" },
    });
    fireEvent.click(screen.getByRole("button", { name: "生成 Word 草稿" }));

    await waitFor(() => expect(mocks.createFromText).toHaveBeenCalled());
    expect(await screen.findByText("未命名文档.docx")).toBeInTheDocument();
  });

  it("shows the locally resolved capability coverage after compilation", async () => {
    window.localStorage.setItem(
      "docalign.workspace.v1",
      JSON.stringify({ document_id: "doc_1", analysis_id: "analysis_1" }),
    );
    mocks.capabilities.mockResolvedValue({
      docx: true,
      structured_spec: true,
      llm_configured: true,
      llm_protocol: "openai-compatible-chat-completions",
      smart_semantic_analysis: true,
      smart_analysis_sends_paragraph_text: true,
      auto_layout: true,
      default_cleanup_preset: true,
      audit_only: true,
      format_manifest: true,
      max_upload_mb: 20,
      local_only: true,
    });
    mocks.document.mockResolvedValue({
      document_id: "doc_1",
      filename: "coverage.docx",
      sha256: "abc",
      size_bytes: 1024,
      status: "uploaded",
    });
    mocks.analysis.mockResolvedValue({
      analysis_id: "analysis_1",
      document_ir: { source_filename: "coverage.docx", blocks: [], warnings: [] },
      summary: {
        paragraph_count: 0,
        table_count: 0,
        image_count: 0,
        unknown_count: 0,
        role_counts: {},
        analysis_mode: "deterministic",
        document_kind: null,
        document_kind_confidence: 0,
        model_reviewed_paragraphs: 0,
        model_provider: null,
        model_name: null,
      },
    });
    mocks.compileSpec.mockResolvedValue({
      spec_id: "spec_1",
      spec: { schema_version: "formatting-spec.v1", roles: {} },
      applied_capabilities: ["document_text_color", "document_background_cleanup"],
      assumptions: ["图片、形状、边框和线条保持原样。"],
      ambiguities: [],
      unsupported_requests: [],
    });

    render(<Workspace />);
    await screen.findByText("coverage.docx");
    fireEvent.click(screen.getByRole("tab", { name: "自然语言编译" }));
    fireEvent.change(screen.getByLabelText("自然语言要求"), {
      target: { value: "所有颜色改为黑色且不需要背景" },
    });
    fireEvent.click(screen.getByRole("button", { name: "编译为结构化规则" }));

    expect(await screen.findByLabelText("规则能力覆盖报告")).toHaveTextContent(
      "全部可见文字颜色、高亮、底纹与页面背景清理",
    );
    expect(screen.getByText("解释与安全边界（1）")).toBeInTheDocument();
  });

  it("runs a read-only compliance audit and shows stable document locators", async () => {
    window.localStorage.setItem(
      "docalign.workspace.v1",
      JSON.stringify({ document_id: "doc_audit", analysis_id: "analysis_audit" }),
    );
    mocks.document.mockResolvedValue({
      document_id: "doc_audit",
      filename: "audit.docx",
      sha256: "audit-sha",
      size_bytes: 2048,
      status: "uploaded",
    });
    mocks.analysis.mockResolvedValue({
      analysis_id: "analysis_audit",
      document_ir: {
        source_filename: "audit.docx",
        warnings: [],
        blocks: [
          {
            kind: "paragraph",
            node_id: "node_1",
            locator: "p1",
            index: 0,
            text: "第一章 范围",
            detected_role: "heading_1",
            role_confidence: 0.98,
            role_source: "marker",
            role_evidence: ["numbered-heading"],
            contains_drawing: false,
            is_empty: false,
          },
          {
            kind: "table",
            node_id: "table_1",
            locator: "t1",
            index: 1,
            rows: 2,
            columns_estimate: 3,
            cell_texts: [],
          },
        ],
      },
      summary: {
        paragraph_count: 1,
        table_count: 1,
        image_count: 0,
        unknown_count: 0,
        role_counts: { heading_1: 1 },
        analysis_mode: "deterministic",
        document_kind: "other",
        document_kind_confidence: 0.6,
        model_reviewed_paragraphs: 0,
        model_provider: null,
        model_name: null,
      },
    });
    mocks.createSpec.mockResolvedValue({
      spec_id: "spec_audit",
      spec: { schema_version: "formatting-spec.v1", roles: {} },
    });
    mocks.compliance.mockResolvedValue({
      schema_version: "compliance-report.v1",
      document_id: "doc_audit",
      analysis_id: "analysis_audit",
      spec_id: "spec_audit",
      compliant: false,
      summary: {
        total_violations: 1,
        returned_violations: 1,
        affected_locators: 1,
        by_severity: { error: 1 },
        by_code: { TABLE_FONT_VALIDATION_FAILED: 1 },
        truncated: false,
      },
      violations: [
        {
          code: "TABLE_FONT_VALIDATION_FAILED",
          severity: "error",
          message: "Table font mismatch.",
          node_id: "table_1",
          locator: "t1.r2.c3.p1.r1",
          details: {},
        },
      ],
      content_fingerprint: "fingerprint",
    });

    render(<Workspace />);
    expect(await screen.findByText("audit.docx")).toBeInTheDocument();
    expect(screen.getByText(/t1 · 表格/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "只做格式体检" }));

    await waitFor(() =>
      expect(mocks.compliance).toHaveBeenCalledWith(
        "doc_audit",
        "analysis_audit",
        "spec_audit",
      ),
    );
    expect(await screen.findByText("格式体检发现偏差")).toBeInTheDocument();
    expect(screen.getByText("t1.r2.c3.p1.r1")).toBeInTheDocument();
  });
});
