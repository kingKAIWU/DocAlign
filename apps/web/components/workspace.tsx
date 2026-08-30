"use client";

import { ChangeEvent, DragEvent, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";

import { DocumentComparisonDialog } from "@/components/document-comparison-dialog";
import { JobOutcomeSummary } from "@/components/job-outcome-summary";
import { RulePackLibrary } from "@/components/rule-pack-library";
import { api, ApiError, API_BASE, apiUrl } from "@/lib/api";
import { renderSafeDocxPreview } from "@/lib/docx-preview";
import { errorLabels, jobStatusLabels, roleLabels, roles } from "@/lib/messages";
import type {
  Analysis,
  Capabilities,
  CleanupPreset,
  ComplianceReport,
  DocumentRecord,
  FormattingSpec,
  Job,
  ParagraphBlock,
  RulePackArtifact,
  SemanticRole,
  TemplateRuleCandidate,
} from "@/lib/types";

const WORKSPACE_STORAGE_KEY = "docalign.workspace.v1";
const SERVICE_CONNECTION_ERROR = "无法连接本地排版服务。请确认 API 已启动，然后点击重试连接。";
const SERVICE_RETRY_DELAY_MS = 3_000;
const capabilityLabels: Record<string, string> = {
  page_layout: "页面布局",
  document_typography: "全文字体与段落",
  role_typography: "按标题/正文等角色排版",
  table_formatting: "表格格式",
  figure_formatting: "图片段落格式",
  header_footer_formatting: "页眉页脚",
  page_numbers: "页码",
  document_text_color: "全部可见文字颜色",
  document_background_cleanup: "高亮、底纹与页面背景清理",
  auto_layout: "标题层级识别与正文自动分段",
};
const coverageStatusLabels: Record<string, string> = {
  automated: "自动执行",
  manual_review: "人工复核",
  unsupported: "暂不支持",
};

type CompilationReport = {
  applied_capabilities: string[];
  assumptions: string[];
  ambiguities: string[];
  unsupported_requests: string[];
};

type StoredWorkspace = {
  document_id: string;
  analysis_id?: string;
  job_id?: string;
};

function readableError(caught: unknown): string {
  if (caught instanceof SyntaxError) return "高级规则 JSON 无法解析，请检查逗号、引号和括号。";
  const code = caught instanceof ApiError ? caught.code : "";
  const detail = caught instanceof Error ? caught.message : "发生未知错误。";
  return errorLabels[code] ?? detail;
}

function readStoredWorkspace(): StoredWorkspace | null {
  try {
    const value = window.localStorage.getItem(WORKSPACE_STORAGE_KEY);
    return value ? (JSON.parse(value) as StoredWorkspace) : null;
  } catch {
    return null;
  }
}

function storeWorkspace(value: StoredWorkspace): void {
  window.localStorage.setItem(WORKSPACE_STORAGE_KEY, JSON.stringify(value));
}

export function Workspace() {
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [document, setDocument] = useState<DocumentRecord | null>(null);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [overrides, setOverrides] = useState<Record<string, SemanticRole>>({});
  const [instruction, setInstruction] = useState("");
  const [ruleMode, setRuleMode] = useState<
    "default" | "natural-language" | "template" | "library"
  >("default");
  const [applyPreset, setApplyPreset] = useState(true);
  const [autoLayout, setAutoLayout] = useState(true);
  const [specText, setSpecText] = useState("");
  const [defaultSpec, setDefaultSpec] = useState<FormattingSpec | null>(null);
  const [presets, setPresets] = useState<CleanupPreset[]>([]);
  const [selectedPresetId, setSelectedPresetId] = useState("default-clean-cn");
  const [referenceCoverageAcknowledged, setReferenceCoverageAcknowledged] = useState(false);
  const [serviceOnline, setServiceOnline] = useState<boolean | null>(null);
  const [connectionAttempt, setConnectionAttempt] = useState(0);
  const [compilationReport, setCompilationReport] = useState<CompilationReport | null>(null);
  const [templateCandidate, setTemplateCandidate] = useState<TemplateRuleCandidate | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [compliance, setCompliance] = useState<ComplianceReport | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string>("");
  const [error, setError] = useState<string>("");
  const [dragging, setDragging] = useState(false);
  const [showOnlyUncertain, setShowOnlyUncertain] = useState(false);
  const [showTextImport, setShowTextImport] = useState(false);
  const [highlightedLocator, setHighlightedLocator] = useState<string | null>(null);
  const [comparisonOpen, setComparisonOpen] = useState(false);
  const [plainText, setPlainText] = useState("");
  const [plainFilename, setPlainFilename] = useState("未命名文档.docx");
  const previewRef = useRef<HTMLDivElement>(null);
  const structurePanelRef = useRef<HTMLElement>(null);
  const pollAbortRef = useRef<AbortController | null>(null);
  const previewPath =
    job?.status === "completed" && job.output_document_url
      ? job.output_document_url
      : document
        ? `/api/v1/documents/${document.document_id}/source`
        : null;
  const jobActive = Boolean(job && !["completed", "failed"].includes(job.status));
  const activeJobId = jobActive ? job?.job_id ?? null : null;

  useEffect(() => {
    const controller = new AbortController();
    Promise.allSettled([api.capabilities(controller.signal), api.presets(controller.signal)])
      .then(([capabilityResult, presetResult]) => {
        if (capabilityResult.status === "fulfilled") {
          setCapabilities(capabilityResult.value);
          setServiceOnline(true);
          setError((current) => current === SERVICE_CONNECTION_ERROR ? "" : current);
        } else {
          setServiceOnline(false);
        }
        if (presetResult.status === "fulfilled") {
          const catalog = presetResult.value.presets;
          const standard = catalog.find((item) => item.preset_id === "default-clean-cn");
          setPresets(catalog);
          if (standard) {
            setDefaultSpec(standard.spec);
            setSpecText((current) => current || JSON.stringify(standard.spec, null, 2));
            setAutoLayout(standard.spec.auto_layout?.enabled ?? true);
          }
        }
        if (capabilityResult.status === "rejected" && presetResult.status === "rejected") {
          setError(SERVICE_CONNECTION_ERROR);
        }
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, [connectionAttempt]);

  useEffect(() => {
    if (serviceOnline !== false) return;
    const timer = window.setTimeout(
      () => setConnectionAttempt((value) => value + 1),
      SERVICE_RETRY_DELAY_MS,
    );
    return () => window.clearTimeout(timer);
  }, [connectionAttempt, serviceOnline]);

  useEffect(() => {
    if (!activeJobId) return;
    const pollingJobId = activeJobId;
    let cancelled = false;
    let timer: number | undefined;
    let failureCount = 0;

    async function refreshJob() {
      if (cancelled) return;
      const controller = new AbortController();
      pollAbortRef.current?.abort();
      pollAbortRef.current = controller;
      const requestTimeout = window.setTimeout(() => controller.abort(), 10_000);
      try {
        const current = await api.job(pollingJobId, controller.signal);
        if (cancelled) return;
        failureCount = 0;
        setJob(current);
        if (current.status === "completed") {
          setMessage(
            current.auto_layout_splits > 0
              ? `自动排版完成：重构 ${current.auto_layout_splits} 处连续正文；格式、重开和内容指纹验证均已通过。`
              : "自动排版与格式验证完成；当前文档无需额外拆分正文。",
          );
          setError("");
          return;
        }
        if (current.status === "failed") {
          setError(
            readableError(
              new ApiError(
                current.error_code ?? "JOB_FAILED",
                current.error_message ?? "自动排版任务失败。",
                500,
              ),
            ),
          );
          return;
        }
        timer = window.setTimeout(refreshJob, 1000);
      } catch {
        if (cancelled) return;
        failureCount += 1;
        setMessage("进度连接暂时不可用，任务仍在本地后台运行，正在自动恢复…");
        const retryDelay = Math.min(5000, 500 * 2 ** Math.min(failureCount, 4));
        timer = window.setTimeout(refreshJob, retryDelay);
      } finally {
        window.clearTimeout(requestTimeout);
      }
    }

    timer = window.setTimeout(refreshJob, 250);
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
      pollAbortRef.current?.abort();
      pollAbortRef.current = null;
    };
  }, [activeJobId]);

  useEffect(() => {
    const stored = readStoredWorkspace();
    if (!stored) return;
    let active = true;
    const controller = new AbortController();
    Promise.allSettled([
      api.document(stored.document_id, controller.signal),
      stored.analysis_id
        ? api.analysis(stored.analysis_id, controller.signal)
        : Promise.resolve(null),
      stored.job_id ? api.job(stored.job_id, controller.signal) : Promise.resolve(null),
    ])
      .then(([documentResult, analysisResult, jobResult]) => {
        if (!active) return;
        if (documentResult.status === "fulfilled") {
          setDocument(documentResult.value);
          if (analysisResult.status === "fulfilled") setAnalysis(analysisResult.value);
          if (jobResult.status === "fulfilled") setJob(jobResult.value);
          setMessage("已恢复上次本地工作区。 ");
        } else if (!controller.signal.aborted) {
          setError("上次工作区暂时无法连接，记录已保留；服务恢复后刷新即可继续。 ");
        }
      })
      .catch(() => undefined);
    return () => {
      active = false;
      controller.abort();
    };
  }, []);

  useEffect(() => {
    const target = previewRef.current;
    if (!document || !target || !previewPath) return;
    let cancelled = false;
    const controller = new AbortController();
    target.replaceChildren();
    const previewUrl = apiUrl(previewPath);
    if (!previewUrl) return;
    fetch(previewUrl, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error(`Preview failed: ${response.status}`);
        return response.blob();
      })
      .then(async (blob) => {
        if (!cancelled && previewRef.current) {
          await renderSafeDocxPreview(blob, previewRef.current, "docx-preview-page");
        }
      })
      .catch(() => {
        if (!cancelled && target) {
          target.textContent = "浏览器预览暂不可用，请以下载文档和审计结果为准。";
        }
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [document, previewPath]);

  const paragraphs = useMemo(
    () =>
      analysis?.document_ir.blocks.filter(
        (block): block is ParagraphBlock => block.kind === "paragraph",
      ) ?? [],
    [analysis],
  );
  const visibleBlocks = useMemo(
    () =>
      analysis?.document_ir.blocks.filter(
        (block) =>
          !showOnlyUncertain ||
          (block.kind === "paragraph" &&
            block.detected_role === "unknown" &&
            (!block.is_empty || block.contains_drawing)),
      ) ?? [],
    [analysis, showOnlyUncertain],
  );
  const unknownCount = analysis?.summary.unknown_count ?? 0;
  const selectedPreset = presets.find((preset) => preset.preset_id === selectedPresetId);
  const presetGroups = [
    {
      label: "通用整理方案",
      description: "按文档类型自动推荐",
      items: presets.filter((preset) => preset.metadata.claim_level === "generic"),
    },
    {
      label: "官方规范参考包",
      description: "需按覆盖矩阵人工确认",
      items: presets.filter((preset) => preset.metadata.claim_level !== "generic"),
    },
  ].filter((group) => group.items.length > 0);
  const referencePackNeedsAcknowledgment = Boolean(
    ruleMode === "default" &&
    selectedPreset?.metadata.claim_level === "reference" &&
    !referenceCoverageAcknowledged,
  );
  const templateRoleMappings = templateCandidate?.role_mappings ?? [];
  const templateAmbiguities = templateCandidate?.ambiguities ?? [];
  const templateUnsupported = templateCandidate?.unsupported_features ?? [];

  async function upload(file: File) {
    setBusy("upload");
    clearNotices();
    try {
      const uploaded = await api.upload(file);
      acceptDocument(uploaded, "文档已安全上传，可以开始结构分析。未修改源文件。 ");
    } catch (caught) {
      setError(readableError(caught));
    } finally {
      setBusy(null);
    }
  }

  async function createFromText() {
    if (!plainText.trim()) return;
    setBusy("text-import");
    clearNotices();
    try {
      const created = await api.createFromText(plainText, plainFilename);
      acceptDocument(created, "纯文本已转换为 Word 段落，可以开始智能结构分析。 ");
      setShowTextImport(false);
    } catch (caught) {
      setError(readableError(caught));
    } finally {
      setBusy(null);
    }
  }

  function acceptDocument(nextDocument: DocumentRecord, notice: string) {
    setDocument(nextDocument);
    setAnalysis(null);
    setShowOnlyUncertain(false);
    setOverrides({});
    setJob(null);
    setCompilationReport(null);
    setCompliance(null);
    setHighlightedLocator(null);
    storeWorkspace({ document_id: nextDocument.document_id });
    setMessage(notice);
  }

  async function runAnalysis(mode: "deterministic" | "smart") {
    if (!document) return;
    if (
      mode === "smart" &&
      !window.confirm(
        "智能分析会把段落文字和格式摘要发送到你配置的兼容模型端点；不会发送 DOCX 文件、图片或密钥。是否继续？",
      )
    ) return;
    setBusy(mode === "smart" ? "analyze-smart" : "analyze");
    clearNotices();
    try {
      const result = await api.analyze(document.document_id, mode);
      setAnalysis(result);
      setShowOnlyUncertain(false);
      setOverrides({});
      setCompliance(null);
      setHighlightedLocator(null);
      storeWorkspace({
        document_id: document.document_id,
        analysis_id: result.analysis_id,
      });
      const smartDetail = result.summary.analysis_mode === "smart"
        ? `智能模型复核 ${result.summary.model_reviewed_paragraphs} 段，文档类型 ${result.summary.document_kind ?? "other"}；`
        : "确定性分析；";
      setMessage(`分析完成：${smartDetail}${result.summary.paragraph_count} 个段落，${result.summary.unknown_count} 个待确认。`);
      const recommended = presets.find((preset) =>
        preset.metadata.claim_level === "generic" && (result.document_ir.blocks.some(
          (block) => block.kind === "table" && block.columns_estimate >= 8,
        )
          ? preset.preset_id === "wide-table-clean-cn"
          : preset.recommended_kinds.includes(result.summary.document_kind ?? "other")),
      );
      if (ruleMode === "default" && recommended) selectCleanupPreset(recommended, false);
    } catch (caught) {
      setError(readableError(caught));
    } finally {
      setBusy(null);
    }
  }

  async function changeRole(nodeId: string, role: SemanticRole) {
    if (!analysis) return;
    const nextOverrides = { ...overrides, [nodeId]: role };
    setOverrides(nextOverrides);
    clearNotices();
    try {
      const result = await api.overrideRoles(
        analysis.analysis_id,
        Object.entries(nextOverrides).map(([node_id, selectedRole]) => ({
          node_id,
          role: selectedRole,
        })),
      );
      setAnalysis(result);
      setCompliance(null);
      setMessage("角色修正已保存。 ");
    } catch (caught) {
      setError(readableError(caught));
    }
  }

  async function compileInstruction() {
    if (!document || !analysis || !instruction.trim()) return;
    setBusy("compile");
    clearNotices();
    try {
      const result = await api.compileSpec(
        document.document_id,
        analysis.analysis_id,
        instruction,
        applyPreset,
      );
      setSpecText(JSON.stringify(result.spec, null, 2));
      setRuleMode("natural-language");
      setCompilationReport(result);
      setCompliance(null);
      const modeText = applyPreset
        ? "已保留通用学术设计体系，并用你的要求覆盖对应角色"
        : "只处理明确要求，未提及的格式保持原样";
      setMessage(`规则已编译：${modeText}；记录 ${result.assumptions.length} 项解释假设。请检查结构化结果。`);
    } catch (caught) {
      setError(readableError(caught));
    } finally {
      setBusy(null);
    }
  }

  async function extractTemplateCandidate(file: File) {
    setBusy("template");
    clearNotices();
    try {
      const candidate = await api.templateCandidate(file);
      setTemplateCandidate(candidate);
      setRuleMode("template");
      setMessage(
        `候选规则已提取：映射 ${candidate.summary.mapped_role_count} 个文档角色，` +
        `纳入 ${candidate.summary.applied_requirement_count} 项可靠属性；尚未应用。`,
      );
    } catch (caught) {
      setError(readableError(caught));
    } finally {
      setBusy(null);
    }
  }

  function applyTemplateCandidate() {
    if (!templateCandidate?.safe_to_apply) return;
    setSpecText(JSON.stringify(templateCandidate.spec, null, 2));
    setAutoLayout(templateCandidate.spec.auto_layout?.enabled ?? false);
    setCompilationReport(null);
    setCompliance(null);
    clearNotices();
    setMessage(
      `已采用“${templateCandidate.source_filename}”生成的候选规则。` +
      "你仍可在高级规则中检查或修改，然后再体检或排版。",
    );
  }

  function applyRulePack(artifact: RulePackArtifact) {
    setSpecText(JSON.stringify(artifact.spec, null, 2));
    setAutoLayout(artifact.spec.auto_layout?.enabled ?? false);
    setRuleMode("library");
    setCompilationReport(null);
    setMessage(
      `已载入“${artifact.name}”修订 ${artifact.revision}；当前只更新规则，尚未修改源文档。`,
    );
    setError("");
  }

  function locateChange(locator: string) {
    const topLevelLocator = locator.split(".")[0];
    if (!/^(?:p|t|u)\d+$/.test(topLevelLocator)) return;
    setShowOnlyUncertain(false);
    setHighlightedLocator(topLevelLocator);
    window.requestAnimationFrame(() => {
      window.document.getElementById(`structure-${topLevelLocator}`)?.scrollIntoView?.({
        behavior: "smooth",
        block: "center",
      });
    });
  }

  function selectCleanupPreset(preset: CleanupPreset, announce = true) {
    setRuleMode("default");
    setSelectedPresetId(preset.preset_id);
    setReferenceCoverageAcknowledged(false);
    setSpecText(JSON.stringify(preset.spec, null, 2));
    setAutoLayout(preset.spec.auto_layout?.enabled ?? true);
    setCompilationReport(null);
    setCompliance(null);
    if (announce) {
      clearNotices();
      setMessage(
        preset.metadata.claim_level === "generic"
          ? `已载入通用整理方案“${preset.name}”：${preset.description}`
          : `已载入规范参考包“${preset.name}”；请先核对自动、人工和未支持条款。`,
      );
    }
  }

  function useDefaultCleanupMode() {
    const selected = presets.find((preset) => preset.preset_id === selectedPresetId);
    if (selected) selectCleanupPreset(selected);
    else if (defaultSpec) {
      setRuleMode("default");
      setSpecText(JSON.stringify(defaultSpec, null, 2));
      setCompliance(null);
    }
  }

  async function saveCurrentSpec(): Promise<string> {
    if (!document) throw new Error("请先上传文档。");
    const parsed = JSON.parse(specText) as FormattingSpec;
    parsed.auto_layout = {
      enabled: autoLayout,
      split_body_paragraphs: parsed.auto_layout?.split_body_paragraphs ?? true,
      split_on_manual_breaks: parsed.auto_layout?.split_on_manual_breaks ?? true,
      target_body_chars: parsed.auto_layout?.target_body_chars ?? 280,
      max_body_chars: parsed.auto_layout?.max_body_chars ?? 480,
    };
    const result = await api.createSpec(document.document_id, parsed);
    return result.spec_id;
  }

  async function startFormatting() {
    if (!document || jobActive) return;
    if (referencePackNeedsAcknowledgment) {
      setError("请先查看规范参考包的覆盖矩阵，并确认理解未覆盖条款。");
      return;
    }
    setBusy("format");
    clearNotices();
    try {
      let currentAnalysis = analysis;
      if (!currentAnalysis) {
        setMessage("正在自动分析文档结构…");
        currentAnalysis = await api.analyze(document.document_id, "deterministic");
        setAnalysis(currentAnalysis);
        setOverrides({});
      }
      const specId = await saveCurrentSpec();
      const created = await api.createJob(document.document_id, currentAnalysis.analysis_id, specId);
      setJob(created);
      storeWorkspace({
        document_id: document.document_id,
        analysis_id: currentAnalysis.analysis_id,
        job_id: created.job_id,
      });
      setMessage("自动排版任务已进入本地队列，可安全刷新页面，进度会自动恢复。 ");
    } catch (caught) {
      setError(readableError(caught));
    } finally {
      setBusy(null);
    }
  }

  async function runComplianceAudit() {
    if (!document) return;
    setBusy("audit");
    clearNotices();
    try {
      let currentAnalysis = analysis;
      if (!currentAnalysis) {
        setMessage("正在自动分析文档结构，然后执行只读格式体检…");
        currentAnalysis = await api.analyze(document.document_id, "deterministic");
        setAnalysis(currentAnalysis);
        setOverrides({});
      }
      const specId = await saveCurrentSpec();
      const report = await api.compliance(
        document.document_id,
        currentAnalysis.analysis_id,
        specId,
      );
      setCompliance(report);
      storeWorkspace({
        document_id: document.document_id,
        analysis_id: currentAnalysis.analysis_id,
      });
      setMessage(
        report.compliant
          ? "格式体检通过：源文档已经符合当前规则，未修改文件。"
          : `格式体检完成：发现 ${report.summary.total_violations} 项偏差，涉及 ${report.summary.affected_locators} 个位置；源文件未修改。`,
      );
    } catch (caught) {
      setError(readableError(caught));
    } finally {
      setBusy(null);
    }
  }

  async function deleteWorkspace() {
    if (!document) return;
    if (!window.confirm("删除此文档及其分析、规则、任务和输出？此操作不可撤销。")) return;
    setBusy("delete");
    clearNotices();
    try {
      await api.deleteDocument(document.document_id);
      window.localStorage.removeItem(WORKSPACE_STORAGE_KEY);
      setDocument(null);
      setAnalysis(null);
      setOverrides({});
      setJob(null);
      setCompliance(null);
      setPlainText("");
      setMessage("本地文档及其关联产物已删除。 ");
    } catch (caught) {
      setError(readableError(caught));
    } finally {
      setBusy(null);
    }
  }

  function handleFileInput(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (file) void upload(file);
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    const file = event.dataTransfer.files?.[0];
    if (file) void upload(file);
  }

  function clearNotices() {
    setError("");
    setMessage("");
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand-lockup">
          <span className="brand-mark" aria-hidden="true">DA</span>
          <div>
            <strong>DocAlign</strong>
            <span>文档格式合规工作台</span>
          </div>
        </div>
        <div className="topbar-actions">
          <span className={`privacy-pill ${capabilities?.local_only ? "ready" : ""}`}>
            <i /> 本地处理
          </span>
          <Link href="/batches">批量处理</Link>
          <Link href="/settings">设置</Link>
        </div>
      </header>

      <section className="hero-row">
        <div>
          <p className="eyebrow">DOCX FORMAT COMPILER</p>
          <h1>先理解文档，再智能排版</h1>
          <p>识别标题、正文和内容层级，以整体基线与角色规则生成可验证的 Word。</p>
        </div>
        <div className="source-stack">
          <div
            className={`upload-card ${dragging ? "dragging" : ""}`}
            onDragOver={(event) => {
              event.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={handleDrop}
          >
            <div className="upload-icon">DOCX</div>
            <div>
              <strong>{document?.filename ?? "拖入一个 Word 文档"}</strong>
              <span>
                {document
                  ? `${(document.size_bytes / 1024).toFixed(1)} KB · 源文件保持不变`
                  : `支持 DOCX 或粘贴纯文本，DOCX 最大 ${capabilities?.max_upload_mb ?? 20} MB`}
              </span>
            </div>
            <div className="upload-actions">
              <label className="button secondary">
                {busy === "upload" ? "上传中…" : "选择文件"}
                <input
                  type="file"
                  accept=".docx"
                  aria-label="上传待处理 Word 文档"
                  onChange={handleFileInput}
                  hidden
                />
              </label>
              {!document && (
                <button className="remove-document text-import-toggle" onClick={() => setShowTextImport((value) => !value)}>
                  {showTextImport ? "收起纯文本" : "粘贴纯文本"}
                </button>
              )}
              {document && (
                <button className="remove-document" disabled={Boolean(busy) || jobActive} onClick={deleteWorkspace}>
                  {jobActive ? "任务完成后可删除" : "删除本地文档"}
                </button>
              )}
            </div>
          </div>
          {showTextImport && !document && (
            <div className="text-import-card">
              <input
                aria-label="纯文本文件名"
                value={plainFilename}
                onChange={(event) => setPlainFilename(event.target.value)}
              />
              <textarea
                aria-label="粘贴纯文本"
                value={plainText}
                onChange={(event) => setPlainText(event.target.value)}
                placeholder={"每行作为一个段落；可选：# 文档标题、## 一级标题、- 项目符号。"}
              />
              <button className="button primary" disabled={!plainText.trim() || Boolean(busy)} onClick={createFromText}>
                {busy === "text-import" ? "生成中…" : "生成 Word 草稿"}
              </button>
            </div>
          )}
        </div>
      </section>

      {(message || error) && (
        <div className={`notice ${error ? "error" : "success"}`} role="status">
          <span>{error ? "!" : "✓"}</span>{error || message}
          {serviceOnline === false && (
            <button className="button secondary compact" onClick={() => setConnectionAttempt((value) => value + 1)}>
              重试连接
            </button>
          )}
        </div>
      )}

      <section className="workflow-toolbar">
        <div className="workflow-steps">
          <Step number="1" label="上传" active={Boolean(document)} />
          <Step number="2" label="分析" active={Boolean(analysis)} />
          <Step number="3" label="规则" active={Boolean(specText)} />
          <Step number="4" label="验证输出" active={job?.status === "completed"} />
        </div>
        <div className="toolbar-actions">
          <button className="button secondary" disabled={!document || Boolean(busy)} onClick={() => void runAnalysis("deterministic")}>
            {busy === "analyze" ? "分析中…" : "分析结构"}
          </button>
          <button
            className="button ai-button compact"
            disabled={!document || !capabilities?.smart_semantic_analysis || Boolean(busy)}
            onClick={() => void runAnalysis("smart")}
          >
            {busy === "analyze-smart" ? "理解中…" : "智能分析"}
          </button>
          <button
            className="button secondary"
            disabled={!document || !specText || Boolean(busy) || jobActive}
            onClick={runComplianceAudit}
          >
            {busy === "audit" ? "体检中…" : "只做格式体检"}
          </button>
          <a
            className={`button secondary ${!document ? "disabled" : ""}`}
            href={document ? apiUrl(`/api/v1/documents/${document.document_id}/format-manifest`) : undefined}
            aria-disabled={!document}
            download={document ? `${document.filename.replace(/\.docx$/i, "")}_format-manifest.json` : undefined}
          >
            导出格式画像
          </a>
          <button
            className="button primary"
            disabled={
              !document || !specText || Boolean(busy) || jobActive ||
              referencePackNeedsAcknowledgment
            }
            onClick={startFormatting}
          >
            {busy === "format"
              ? "创建任务中…"
              : jobActive && job
                ? `自动排版中 ${job.progress}%`
                : autoLayout ? "自动排版并验证" : "格式化并验证"}
          </button>
        </div>
      </section>

      <section className="workspace-grid">
        <aside className="panel structure-panel" ref={structurePanelRef}>
          <PanelHeading
            title="文档结构"
            meta={analysis
              ? `${analysis.summary.analysis_mode === "smart" ? "智能" : "规则"} · ${paragraphs.length} 段${unknownCount ? ` · ${unknownCount} 待确认` : ""}`
              : "等待分析"}
          />
          {!analysis ? (
            <EmptyState icon="01" title="尚未分析" text="上传文档后运行结构分析，识别标题、正文、图表题和参考文献。" />
          ) : (
            <>
              <div className={`structure-review-bar ${unknownCount ? "needs-review" : "ready"}`}>
                <div>
                  <strong>{unknownCount ? `${unknownCount} 个段落待确认` : "结构识别已就绪"}</strong>
                  <span>
                    {unknownCount
                      ? "建议在排版前确认这些段落；未确认内容将按正文基线处理。"
                      : "标题、正文和特殊内容已完成结构识别。"}
                  </span>
                </div>
                {(unknownCount > 0 || showOnlyUncertain) && (
                  <button
                    type="button"
                    aria-pressed={showOnlyUncertain}
                    onClick={() => setShowOnlyUncertain((current) => !current)}
                  >
                    {showOnlyUncertain ? "查看全部段落" : "仅看待确认"}
                  </button>
                )}
              </div>
              <div className="structure-list">
                {visibleBlocks.length === 0 && showOnlyUncertain ? (
                  <div className="structure-list-empty">待确认段落已全部处理。</div>
                ) : visibleBlocks.map((block) => {
                  if (block.kind === "table") {
                    return <div id={`structure-${block.locator}`} className={`structure-table ${highlightedLocator === block.locator ? "change-focus" : ""}`} key={block.node_id}>{block.locator} · 表格 · {block.rows} × {block.columns_estimate}</div>;
                  }
                  if (block.kind === "unsupported") {
                    return <div id={`structure-${block.locator}`} className={`structure-warning ${highlightedLocator === block.locator ? "change-focus" : ""}`} key={block.node_id}>{block.locator} · 未识别结构 · 已保留</div>;
                  }
                  return (
                    <article
                      id={`structure-${block.locator}`}
                      className={`structure-item ${block.detected_role === "unknown" ? "uncertain" : ""} ${highlightedLocator === block.locator ? "change-focus" : ""}`}
                      key={block.node_id}
                    >
                      <div className="structure-copy">
                        <span>{block.text || (block.contains_drawing ? "[图片]" : "[空段落]")}</span>
                        <small title={block.role_evidence.join(" · ")}>
                          {block.locator} · {Math.round(block.role_confidence * 100)}% · {block.role_source}
                        </small>
                      </div>
                      <select
                        aria-label={`修改段落角色：${block.text}`}
                        value={block.detected_role}
                        onChange={(event) => void changeRole(block.node_id, event.target.value as SemanticRole)}
                      >
                        {roles.map((role) => <option value={role} key={role}>{roleLabels[role]}</option>)}
                      </select>
                    </article>
                  );
                })}
              </div>
            </>
          )}
        </aside>

        <section className="panel preview-panel">
          <PanelHeading
            title="文档预览"
            meta={job?.status === "completed" ? "格式化结果 · 最佳努力预览" : "源文件 · 最佳努力预览"}
          />
          <div className="preview-canvas" ref={previewRef}>
            {!document && <EmptyState icon="A4" title="预览区域" text="上传后将在这里显示浏览器预览。最终合规结果以 OOXML 验证和审计报告为准。" />}
          </div>
        </section>

        <aside className="panel rules-panel">
          <PanelHeading title="格式规则" meta="FormattingSpec v1" />
          {compliance && (
            <div className={`compliance-card ${compliance.compliant ? "passed" : "failed"}`}>
              <div>
                <strong>{compliance.compliant ? "格式体检通过" : "格式体检发现偏差"}</strong>
                <span>{compliance.summary.total_violations} 项</span>
              </div>
              {!compliance.compliant && (
                <ol>
                  {compliance.violations.slice(0, 12).map((violation, index) => (
                    <li key={`${violation.code}-${violation.locator ?? index}`}>
                      <code>{violation.locator ?? "document"}</code>
                      <span>{errorLabels[violation.code] ?? violation.message}</span>
                    </li>
                  ))}
                </ol>
              )}
              {compliance.summary.total_violations > 12 && (
                <p>仅展示前 12 项；完整结果可通过 API 获取。</p>
              )}
            </div>
          )}
          {job && (
            <div className={`job-card ${job.status}`}>
              <div>
                <strong>{job.status === "completed" ? "输出已生成" : jobStatusLabels[job.status]}</strong>
                <span>{job.progress}%</span>
              </div>
              <div className="progress"><i style={{ width: `${job.progress}%` }} /></div>
              {job.status === "completed" && (
                <>
                  <JobOutcomeSummary
                    job={job}
                    onLocate={analysis ? locateChange : undefined}
                    onCompare={() => setComparisonOpen(true)}
                    onReview={analysis && unknownCount > 0
                      ? () => {
                          setShowOnlyUncertain(true);
                          window.requestAnimationFrame(() => {
                            structurePanelRef.current?.scrollIntoView({
                              behavior: "smooth",
                              block: "start",
                            });
                          });
                        }
                      : undefined}
                  />
                  <div className="download-row">
                    <a className="button primary" href={apiUrl(job.output_document_url)} download>下载格式化 DOCX</a>
                    <a className="text-link" href={apiUrl(job.audit_json_url)}>查看审计</a>
                  </div>
                </>
              )}
            </div>
          )}
          <div className="rule-section">
            <div className="rule-mode-tabs" role="tablist" aria-label="规则生成模式">
              <button
                type="button"
                role="tab"
                aria-selected={ruleMode === "default"}
                className={ruleMode === "default" ? "active" : ""}
                disabled={!defaultSpec || Boolean(busy)}
                onClick={useDefaultCleanupMode}
              >
                默认整理模式
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={ruleMode === "natural-language"}
                className={ruleMode === "natural-language" ? "active" : ""}
                onClick={() => setRuleMode("natural-language")}
              >
                自然语言编译
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={ruleMode === "template"}
                className={ruleMode === "template" ? "active" : ""}
                onClick={() => setRuleMode("template")}
              >
                参考样例提取
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={ruleMode === "library"}
                className={ruleMode === "library" ? "active" : ""}
                onClick={() => setRuleMode("library")}
              >
                我的规则包
              </button>
            </div>
            {ruleMode === "default" ? (
              <div className="default-mode-card" role="tabpanel">
                <strong>场景整理与规范参考</strong>
                <p>
                  通用方案按文档类型推荐；官方规范参考包只执行覆盖矩阵中的条款，
                  不会自动宣称完整合规。
                </p>
                <div className="preset-groups">
                  {presetGroups.map((group) => (
                    <section className="preset-group" key={group.label}>
                      <header>
                        <strong>{group.label}</strong>
                        <span>{group.description}</span>
                      </header>
                      <div className="preset-grid" role="radiogroup" aria-label={group.label}>
                        {group.items.map((preset) => {
                          const recommended = preset.metadata.claim_level === "generic" && (
                            analysis
                              ? analysis.document_ir.blocks.some(
                                  (block) => block.kind === "table" && block.columns_estimate >= 8,
                                )
                                ? preset.preset_id === "wide-table-clean-cn"
                                : preset.recommended_kinds.includes(
                                    analysis.summary.document_kind ?? "other",
                                  )
                              : preset.preset_id === "default-clean-cn"
                          );
                          return (
                            <button
                              key={preset.preset_id}
                              type="button"
                              role="radio"
                              aria-checked={selectedPresetId === preset.preset_id}
                              className={`preset-option ${selectedPresetId === preset.preset_id ? "active" : ""}`}
                              onClick={() => selectCleanupPreset(preset)}
                            >
                              <strong>
                                {preset.name}
                                {recommended
                                  ? " · 推荐"
                                  : preset.metadata.claim_level === "reference"
                                    ? " · 部分覆盖"
                                    : ""}
                              </strong>
                              <small>{preset.description}</small>
                            </button>
                          );
                        })}
                      </div>
                    </section>
                  ))}
                </div>
                {selectedPreset && (
                  <div
                    className={`preset-trust-card ${selectedPreset.metadata.claim_level === "reference" ? "reference" : ""}`}
                    role="note"
                    aria-label="规则来源与适用边界"
                  >
                    <div>
                      <strong>
                        {selectedPreset.metadata.claim_level === "generic"
                          ? "通用方案 · 非机构合规"
                          : selectedPreset.metadata.claim_level === "verified"
                            ? "已验证规则包"
                            : "参考规则包 · 部分覆盖"}
                      </strong>
                      <span>v{selectedPreset.metadata.pack_version}</span>
                    </div>
                    <p>
                      {selectedPreset.metadata.scope_label} · {selectedPreset.metadata.maintained_by} 维护 · {selectedPreset.metadata.last_reviewed_on} 复核
                    </p>
                    <details open={selectedPreset.metadata.claim_level === "reference"}>
                      <summary>
                        查看逐条覆盖与限制（
                        {(selectedPreset.metadata.coverage_items?.length ?? 0) +
                          selectedPreset.metadata.limitations.length}）
                      </summary>
                      <p>
                        覆盖：{selectedPreset.metadata.covered_capabilities
                          .map((item) => capabilityLabels[item] ?? item)
                          .join("、")}
                      </p>
                      {selectedPreset.metadata.source_references.length > 0 && (
                        <ul className="preset-sources">
                          {selectedPreset.metadata.source_references.map((source) => (
                            <li key={source.url}>
                              <a href={source.url} target="_blank" rel="noreferrer">{source.title}</a>
                              {source.version ? ` · ${source.version}` : ""}
                            </li>
                          ))}
                        </ul>
                      )}
                      {(selectedPreset.metadata.coverage_items?.length ?? 0) > 0 && (
                        <ul className="preset-coverage-list" aria-label="规范条款覆盖矩阵">
                          {selectedPreset.metadata.coverage_items?.map((item) => (
                            <li key={`${item.requirement_id}-${item.requirement}`}>
                              <span className={`coverage-status ${item.status}`}>
                                {coverageStatusLabels[item.status] ?? item.status}
                              </span>
                              <strong>{item.requirement_id} · {item.requirement}</strong>
                              <p>{item.implementation_note}</p>
                            </li>
                          ))}
                        </ul>
                      )}
                      {selectedPreset.metadata.acceptance_evidence && (
                        <div className="preset-acceptance">
                          <strong>自动验收证据</strong>
                          <span>
                            {selectedPreset.metadata.acceptance_evidence.fixture_id} ·
                            {selectedPreset.metadata.acceptance_evidence.last_passed_on} ·
                            {selectedPreset.metadata.acceptance_evidence.automated_checks.length} 项
                          </span>
                          <ul>
                            {selectedPreset.metadata.acceptance_evidence.automated_checks.map(
                              (check) => <li key={check}>{check}</li>,
                            )}
                          </ul>
                          {(selectedPreset.metadata.acceptance_evidence.manual_checks?.length ?? 0) > 0 && (
                            <>
                              <strong className="preset-manual-checks-title">人工验收清单</strong>
                              <ul>
                                {selectedPreset.metadata.acceptance_evidence.manual_checks?.map(
                                  (check) => <li key={check}>{check}</li>,
                                )}
                              </ul>
                            </>
                          )}
                        </div>
                      )}
                      <strong className="preset-limitations-title">未覆盖与限制</strong>
                      <ul>
                        {selectedPreset.metadata.limitations.map((limitation) => (
                          <li key={limitation}>{limitation}</li>
                        ))}
                      </ul>
                    </details>
                    {selectedPreset.metadata.claim_level === "reference" && (
                      <label className="preset-coverage-acknowledgment">
                        <input
                          type="checkbox"
                          checked={referenceCoverageAcknowledged}
                          onChange={(event) => {
                            setReferenceCoverageAcknowledged(event.target.checked);
                            setError("");
                          }}
                        />
                        <span>
                          我已查看自动、人工和暂不支持条款，理解这不是发布机构认证或完整合规结论。
                        </span>
                      </label>
                    )}
                  </div>
                )}
                <button
                  type="button"
                  className="button secondary"
                  disabled={!defaultSpec || Boolean(busy)}
                  onClick={useDefaultCleanupMode}
                >
                  重新载入默认规则
                </button>
              </div>
            ) : ruleMode === "natural-language" ? (
              <div role="tabpanel">
                <label htmlFor="instruction">自然语言要求</label>
                <textarea
                  id="instruction"
                  className="instruction-input"
                  value={instruction}
                  onChange={(event) => setInstruction(event.target.value)}
                  placeholder="例如：全文中文宋体、英文 Times New Roman；正文小四、首行缩进两字符；一级标题黑体三号居中……"
                />
                <label className="format-mode-option">
                  <input
                    type="checkbox"
                    checked={applyPreset}
                    onChange={(event) => setApplyPreset(event.target.checked)}
                  />
                  <span>
                    <strong>智能排版设计体系</strong>
                    <small>自动设置页面、主标题、层级标题、正文、列表和题注；你的要求优先覆盖</small>
                  </span>
                </label>
                <button
                  className="button ai-button"
                  disabled={!analysis || !instruction.trim() || !capabilities?.llm_configured || Boolean(busy)}
                  onClick={compileInstruction}
                >
                  {busy === "compile" ? "编译中…" : "编译为结构化规则"}
                </button>
                {!capabilities?.llm_configured && <p className="helper">兼容模型未配置；默认整理模式仍可直接使用。</p>}
              </div>
            ) : ruleMode === "template" ? (
              <div className="template-mode-card" role="tabpanel">
                <strong>从已确认合格的 Word 样例提取</strong>
                <p>
                  选择一份排版已经确认正确的 DOCX。系统只生成待确认候选，不会覆盖当前文档，也不会自动复制不确定结构。
                </p>
                <label className={`template-upload ${busy === "template" ? "disabled" : ""}`}>
                  <span>{busy === "template" ? "正在安全提取…" : "选择合格样例 DOCX"}</span>
                  <input
                    type="file"
                    accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    aria-label="上传合格 Word 样例"
                    disabled={Boolean(busy)}
                    onChange={(event) => {
                      const file = event.target.files?.[0];
                      if (file) void extractTemplateCandidate(file);
                      event.target.value = "";
                    }}
                  />
                </label>
                <small className="template-privacy-note">
                  本地临时解析 · 返回候选后立即清理参考文件 · 不替换当前待处理文档
                </small>
                {templateCandidate && (
                  <div className="template-candidate" aria-label="样例候选规则摘要">
                    <div className="template-candidate-heading">
                      <div>
                        <strong>{templateCandidate.source_filename}</strong>
                        <small>SHA-256 {templateCandidate.source_sha256.slice(0, 12)}…</small>
                      </div>
                      <span>{templateCandidate.summary.coverage_percent}% 可靠属性已映射</span>
                    </div>
                    <p>
                      {templateCandidate.summary.mapped_role_count} 个角色 · {templateCandidate.summary.applied_requirement_count} 项已纳入
                      · {templateCandidate.summary.auto_applicable_requirement_count} 项可比较属性
                    </p>
                    {templateRoleMappings.length > 0 && (
                      <ul className="template-role-mappings">
                        {templateRoleMappings.map((mapping) => (
                          <li key={`${mapping.role}-${mapping.source_style_name}`}>
                            <strong>{roleLabels[mapping.role]}</strong>
                            <span>← {mapping.source_style_name}</span>
                            <small>{mapping.included_properties?.length ?? 0} 项 · {Math.round(mapping.confidence * 100)}%</small>
                          </li>
                        ))}
                      </ul>
                    )}
                    {(templateAmbiguities.length > 0 || templateUnsupported.length > 0) && (
                      <details>
                        <summary>
                          查看待确认与未自动复制内容（{templateAmbiguities.length + templateUnsupported.length}）
                        </summary>
                        <ul>
                          {[...templateAmbiguities, ...templateUnsupported].map((item) => (
                            <li key={item}>{item}</li>
                          ))}
                        </ul>
                      </details>
                    )}
                    <button
                      type="button"
                      className="button primary"
                      disabled={!templateCandidate.safe_to_apply || Boolean(busy)}
                      onClick={applyTemplateCandidate}
                    >
                      确认采用候选规则
                    </button>
                    {!templateCandidate.safe_to_apply && (
                      <p className="compile-report-warning">没有提取到可安全应用的属性，请换一份样式更明确的参考文档。</p>
                    )}
                  </div>
                )}
              </div>
            ) : (
              <RulePackLibrary
                specText={specText}
                disabled={Boolean(busy)}
                onApply={applyRulePack}
              />
            )}
            <label className="format-mode-option">
              <input
                type="checkbox"
                checked={autoLayout}
                onChange={(event) => {
                  setAutoLayout(event.target.checked);
                  setReferenceCoverageAcknowledged(false);
                  setCompliance(null);
                }}
              />
              <span>
                <strong>自动结构排版</strong>
                <small>识别各级标题，将连续正文按换行和完整句子安全分段；受保护 Word 结构不拆分</small>
              </span>
            </label>
            {compilationReport && (
              <div className="compile-report" aria-label="规则能力覆盖报告">
                <strong>规则能力覆盖报告</strong>
                <p>
                  已映射：{compilationReport.applied_capabilities.length
                    ? compilationReport.applied_capabilities
                      .map((item) => capabilityLabels[item] ?? item)
                      .join("、")
                    : "未识别到可执行能力"}
                </p>
                {compilationReport.assumptions.length > 0 && (
                  <details>
                    <summary>解释与安全边界（{compilationReport.assumptions.length}）</summary>
                    <ul>{compilationReport.assumptions.map((item) => <li key={item}>{item}</li>)}</ul>
                  </details>
                )}
                {compilationReport.ambiguities.length > 0 && (
                  <div className="compile-report-warning">
                    需要确认：{compilationReport.ambiguities.join("；")}
                  </div>
                )}
                {compilationReport.unsupported_requests.length > 0 && (
                  <div className="compile-report-error">
                    当前无法执行：{compilationReport.unsupported_requests.join("；")}
                  </div>
                )}
              </div>
            )}
          </div>
          <div className="rule-section grow">
            <details className="advanced-rules">
              <summary>
                高级规则 JSON · {ruleMode === "default"
                  ? "整理方案"
                  : ruleMode === "natural-language"
                    ? "自然语言"
                    : ruleMode === "template"
                      ? "参考样例"
                      : "规则包"}
              </summary>
              <p className="helper">仅在需要精细控制时编辑；格式错误会在提交前给出明确提示。</p>
              <textarea
                id="spec-json"
                aria-label="结构化规则"
                className="spec-editor"
                spellCheck={false}
                value={specText}
                onChange={(event) => {
                  setSpecText(event.target.value);
                  setReferenceCoverageAcknowledged(false);
                  setCompliance(null);
                }}
              />
            </details>
          </div>
        </aside>
      </section>

      {document && job?.status === "completed" && job.output_document_url && (
        <DocumentComparisonDialog
          open={comparisonOpen}
          sourcePath={`/api/v1/documents/${document.document_id}/source`}
          outputPath={job.output_document_url}
          summary={job.result_summary}
          onClose={() => setComparisonOpen(false)}
          onLocate={analysis ? locateChange : undefined}
        />
      )}

      <footer className="app-footer">
        <span>API: {API_BASE}</span>
        <span>内容保护 · 确定性执行 · 落盘重开验证</span>
      </footer>
    </main>
  );
}

function Step({ number, label, active }: { number: string; label: string; active: boolean }) {
  return <div className={`workflow-step ${active ? "active" : ""}`}><b>{active ? "✓" : number}</b><span>{label}</span></div>;
}

function PanelHeading({ title, meta }: { title: string; meta: string }) {
  return <div className="panel-heading"><h2>{title}</h2><span>{meta}</span></div>;
}

function EmptyState({ icon, title, text }: { icon: string; title: string; text: string }) {
  return <div className="empty-state"><b>{icon}</b><strong>{title}</strong><p>{text}</p></div>;
}
