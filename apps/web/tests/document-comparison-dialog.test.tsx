import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DocumentComparisonDialog } from "@/components/document-comparison-dialog";

const mocks = vi.hoisted(() => ({
  renderAsync: vi.fn(),
  fetch: vi.fn(),
}));

vi.mock("docx-preview", () => ({ renderAsync: mocks.renderAsync }));

const summary = {
  validation_passed: true,
  content_integrity_passed: true,
  format_operations: 18,
  changed_mutations: 12,
  change_categories: { paragraph_styles: 12 },
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
  structure_review_items: 0,
  delivery_review_items: 0,
  paragraphs_before: 4,
  paragraphs_after: 4,
  auto_layout_splits: 0,
};

describe("DocumentComparisonDialog", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal("fetch", mocks.fetch);
    Object.defineProperty(HTMLDialogElement.prototype, "showModal", {
      configurable: true,
      value() {
        this.setAttribute("open", "");
      },
    });
    Object.defineProperty(HTMLDialogElement.prototype, "close", {
      configurable: true,
      value() {
        this.removeAttribute("open");
        this.dispatchEvent(new Event("close"));
      },
    });
    mocks.fetch.mockResolvedValue({
      ok: true,
      blob: async () => new Blob(["docx"]),
    });
    mocks.renderAsync.mockImplementation(
      async (_blob: Blob, container: HTMLElement, _styles: undefined, options: { className: string }) => {
        const page = document.createElement("section");
        page.className = options.className;
        const link = document.createElement("a");
        link.href = "https://unsafe.example";
        link.textContent = "外部链接";
        page.append(link);
        container.append(page);
      },
    );
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("renders both local documents, synchronizes scrolling, and returns to a locator", async () => {
    const onClose = vi.fn();
    const onLocate = vi.fn();
    render(
      <DocumentComparisonDialog
        open
        sourcePath="/api/v1/documents/doc_1/source"
        outputPath="/api/v1/jobs/job_1/output"
        summary={summary}
        onClose={onClose}
        onLocate={onLocate}
      />,
    );

    expect(await screen.findByRole("dialog", { name: "格式前后对照" })).toHaveAttribute("open");
    await waitFor(() => expect(screen.getAllByText("预览已就绪")).toHaveLength(2));
    expect(mocks.fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/v1/documents/doc_1/source"),
      expect.objectContaining({ cache: "no-store" }),
    );
    expect(mocks.fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/v1/jobs/job_1/output"),
      expect.objectContaining({ cache: "no-store" }),
    );
    expect(mocks.renderAsync).toHaveBeenCalledWith(
      expect.any(Blob),
      expect.any(HTMLElement),
      undefined,
      expect.objectContaining({ renderAltChunks: false, renderChanges: false }),
    );
    expect(screen.getAllByText("外部链接")[0].closest("a")).not.toHaveAttribute("href");

    const source = screen.getByLabelText("源文件预览滚动区");
    const output = screen.getByLabelText("已验证输出预览滚动区");
    Object.defineProperties(source, {
      scrollHeight: { configurable: true, value: 1_000 },
      clientHeight: { configurable: true, value: 200 },
    });
    Object.defineProperties(output, {
      scrollHeight: { configurable: true, value: 600 },
      clientHeight: { configurable: true, value: 200 },
    });
    source.scrollTop = 400;
    fireEvent.scroll(source);
    expect(output.scrollTop).toBe(200);

    fireEvent.click(screen.getByRole("button", { name: "回到结构位置 p1" }));
    expect(onLocate).toHaveBeenCalledWith("p1");
    expect(onClose).toHaveBeenCalled();
  });

  it("shows a recoverable error when either browser preview cannot load", async () => {
    mocks.fetch.mockRejectedValueOnce(new Error("offline"));
    const onClose = vi.fn();
    render(
      <DocumentComparisonDialog
        open
        sourcePath="/source"
        outputPath="/output"
        summary={summary}
        onClose={onClose}
      />,
    );

    expect(await screen.findByText("浏览器预览失败，请下载 DOCX 复核")).toBeInTheDocument();
    mocks.fetch.mockResolvedValue({ ok: true, blob: async () => new Blob(["docx"]) });
    fireEvent.click(screen.getByRole("button", { name: "重新加载预览" }));
    await waitFor(() => expect(screen.getAllByText("预览已就绪")).toHaveLength(2));
  });
});
