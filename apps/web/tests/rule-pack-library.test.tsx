import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RulePackLibrary } from "@/components/rule-pack-library";

const mocks = vi.hoisted(() => ({
  rulePacks: vi.fn(),
  rulePack: vi.fn(),
  rulePackVersion: vi.fn(),
  createRulePack: vi.fn(),
  createRulePackVersion: vi.fn(),
  restoreRulePackVersion: vi.fn(),
  previewRulePackImport: vi.fn(),
  importRulePack: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    constructor(public code: string, message: string, public status: number) {
      super(message);
    }
  },
  apiUrl: (path: string) => path,
  api: mocks,
}));

const spec = {
  schema_version: "formatting-spec.v1" as const,
  roles: {},
  auto_layout: { enabled: false },
  behavior: {},
  source: { type: "structured" as const, assumptions: [] },
};

const firstArtifact = {
  schema_version: "rule-pack.v1" as const,
  pack_id: "pack_1",
  request_id: "request-create-1",
  name: "办公室月报",
  description: "内部月报规则",
  scope_label: "2026 年综合办公室月报",
  revision: 1,
  approval_status: "draft" as const,
  approval_note: null,
  change_note: "创建初始修订",
  restored_from_revision: null,
  spec_sha256: "a".repeat(64),
  created_at: "2026-08-29T00:00:00Z",
  spec,
};

describe("RulePackLibrary", () => {
  afterEach(() => cleanup());

  beforeEach(() => {
    vi.clearAllMocks();
    mocks.rulePacks.mockResolvedValue({ rule_packs: [] });
  });

  it("reuses one write request after a lost response", async () => {
    mocks.rulePacks
      .mockResolvedValueOnce({ rule_packs: [] })
      .mockResolvedValue({
        rule_packs: [
          {
            pack_id: "pack_1",
            name: "办公室月报",
            description: "内部月报规则",
            scope_label: "2026 年综合办公室月报",
            current_revision: 1,
            current_approval_status: "draft",
            current_spec_sha256: "a".repeat(64),
            created_at: "2026-08-29T00:00:00Z",
            updated_at: "2026-08-29T00:00:00Z",
          },
        ],
      });
    mocks.rulePack.mockResolvedValue({
      pack_id: "pack_1",
      name: "办公室月报",
      description: "内部月报规则",
      scope_label: "2026 年综合办公室月报",
      current_revision: 1,
      created_at: "2026-08-29T00:00:00Z",
      updated_at: "2026-08-29T00:00:00Z",
      versions: [
        {
          revision: 1,
          approval_status: "draft",
          approval_note: null,
          change_note: "创建初始修订",
          restored_from_revision: null,
          spec_sha256: "a".repeat(64),
          source_type: "structured",
          created_at: "2026-08-29T00:00:00Z",
        },
      ],
    });
    mocks.createRulePack
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce(firstArtifact);

    render(<RulePackLibrary specText={JSON.stringify(spec)} disabled={false} onApply={vi.fn()} />);
    expect(await screen.findByText("尚未保存规则包")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("规则包名称"), {
      target: { value: "办公室月报" },
    });
    fireEvent.change(screen.getByLabelText("具体适用范围"), {
      target: { value: "2026 年综合办公室月报" },
    });
    fireEvent.click(screen.getByRole("button", { name: "另存为新规则包" }));

    expect(await screen.findByText(/连接中断，可直接重试/)).toBeInTheDocument();
    const firstRequestId = mocks.createRulePack.mock.calls[0][0].request_id;

    fireEvent.click(screen.getByRole("button", { name: "另存为新规则包" }));
    expect(await screen.findByText("已保存“办公室月报”修订 1。")).toBeInTheDocument();
    const retriedRequestId = mocks.createRulePack.mock.calls[1][0].request_id;

    expect(firstRequestId).toBeTruthy();
    expect(retriedRequestId).toBe(firstRequestId);
  });

  it("loads an old revision and restores it without overwriting history", async () => {
    const restoredArtifact = {
      ...firstArtifact,
      request_id: "request-restore-1",
      revision: 3,
      restored_from_revision: 1,
      change_note: "从修订 1 恢复；需要重新确认适用范围",
    };
    mocks.rulePacks.mockResolvedValue({
      rule_packs: [
        {
          pack_id: "pack_1",
          name: "办公室月报",
          description: "内部月报规则",
          scope_label: "2026 年综合办公室月报",
          current_revision: 2,
          current_approval_status: "locally_approved",
          current_spec_sha256: "b".repeat(64),
          created_at: "2026-08-29T00:00:00Z",
          updated_at: "2026-08-29T01:00:00Z",
        },
      ],
    });
    mocks.rulePack.mockResolvedValue({
      pack_id: "pack_1",
      name: "办公室月报",
      description: "内部月报规则",
      scope_label: "2026 年综合办公室月报",
      current_revision: 2,
      created_at: "2026-08-29T00:00:00Z",
      updated_at: "2026-08-29T01:00:00Z",
      versions: [
        {
          revision: 2,
          approval_status: "locally_approved",
          approval_note: "张三已核对",
          change_note: "更新正文",
          restored_from_revision: null,
          spec_sha256: "b".repeat(64),
          source_type: "structured",
          created_at: "2026-08-29T01:00:00Z",
        },
        {
          revision: 1,
          approval_status: "draft",
          approval_note: null,
          change_note: "创建初始修订",
          restored_from_revision: null,
          spec_sha256: "a".repeat(64),
          source_type: "structured",
          created_at: "2026-08-29T00:00:00Z",
        },
      ],
    });
    mocks.rulePackVersion.mockResolvedValue(firstArtifact);
    mocks.restoreRulePackVersion.mockResolvedValue(restoredArtifact);
    const onApply = vi.fn();
    vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<RulePackLibrary specText={JSON.stringify(spec)} disabled={false} onApply={onApply} />);
    expect(await screen.findByText("办公室月报", { selector: ".rule-pack-history strong" }))
      .toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("修订版本"), { target: { value: "1" } });
    fireEvent.click(screen.getByRole("button", { name: "载入所选修订" }));
    await waitFor(() => expect(onApply).toHaveBeenCalledWith(firstArtifact));
    expect(screen.getByRole("link", { name: "导出 JSON" })).toHaveAttribute(
      "href",
      "/api/v1/rule-packs/pack_1/versions/1/export",
    );

    fireEvent.click(screen.getByRole("button", { name: "恢复为新修订" }));
    await waitFor(() => expect(mocks.restoreRulePackVersion).toHaveBeenCalled());
    expect(mocks.restoreRulePackVersion.mock.calls[0][0]).toBe("pack_1");
    expect(mocks.restoreRulePackVersion.mock.calls[0][1]).toBe(1);
    expect(onApply).toHaveBeenLastCalledWith(restoredArtifact);
    expect(await screen.findByText(/已从修订 1 恢复为修订 3/)).toBeInTheDocument();
  });

  it("previews an unsigned portable artifact and imports it as a local draft", async () => {
    const source = {
      artifact_sha256: "c".repeat(64),
      pack_id: "pack_external",
      request_id: "external-request",
      name: "院系论文格式",
      scope_label: "2026 年院系论文",
      revision: 4,
      approval_status: "locally_approved" as const,
      approval_note: "来源电脑已核对",
      change_note: "调整目录格式",
      spec_sha256: "a".repeat(64),
      created_at: "2026-08-30T00:00:00Z",
    };
    mocks.previewRulePackImport.mockResolvedValue({
      integrity_verified: true,
      signature_status: "unsigned",
      source,
      suggested_name: "院系论文格式（导入）",
      source_name_conflict: true,
      already_present: false,
      existing_pack_id: null,
      existing_revision: null,
      target_approval_status: "draft",
      warnings: [],
    });
    const importedArtifact = {
      ...firstArtifact,
      pack_id: "pack_imported",
      request_id: "import-request",
      name: "院系论文格式（导入）",
      change_note: "跨机导入；需在本机重新核对",
      import_source: source,
    };
    mocks.importRulePack.mockResolvedValue({
      artifact: importedArtifact,
      already_present: false,
    });
    const onApply = vi.fn();

    render(<RulePackLibrary specText={JSON.stringify(spec)} disabled={false} onApply={onApply} />);
    expect(await screen.findByText("尚未保存规则包")).toBeInTheDocument();
    fireEvent.click(screen.getByText("从另一台电脑导入规则包"));
    const file = new File([JSON.stringify(firstArtifact)], "external.rule-pack.json", {
      type: "application/json",
    });
    fireEvent.change(screen.getByLabelText("规则包 JSON 文件"), {
      target: { files: [file] },
    });
    fireEvent.click(screen.getByRole("button", { name: "检查文件" }));

    expect(await screen.findByLabelText("规则包导入检查结果")).toBeInTheDocument();
    expect(screen.getByText("结构与摘要通过")).toBeInTheDocument();
    expect(screen.getByText("未验证数字签名")).toBeInTheDocument();
    expect(screen.getByDisplayValue("院系论文格式（导入）")).toBeInTheDocument();
    expect(screen.getByText(/不会随导入继承/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "确认导入为草稿" }));
    await waitFor(() => expect(mocks.importRulePack).toHaveBeenCalledTimes(1));
    expect(mocks.importRulePack.mock.calls[0][0]).toBe(file);
    expect(mocks.importRulePack.mock.calls[0][1]).toBe("院系论文格式（导入）");
    expect(mocks.importRulePack.mock.calls[0][2]).toBeTruthy();
    expect(await screen.findByText(/状态已重置为草稿/)).toBeInTheDocument();
    expect(onApply).not.toHaveBeenCalled();
  });
});
