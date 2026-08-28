"use client";

import { ChangeEvent, DragEvent, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";

import { api, ApiError, API_BASE, apiUrl } from "@/lib/api";
import { errorLabels, roleLabels, roles } from "@/lib/messages";
import type {
  Analysis,
  Capabilities,
  ComplianceReport,
  DocumentRecord,
  FormattingSpec,
  Job,
  ParagraphBlock,
  SemanticRole,
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

type CleanupPreset = {
  preset_id: string;
  name: string;
  description: string;
  recommended_kinds: string[];
  spec: FormattingSpec;
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
  const [ruleMode, setRuleMode] = useState<"default" | "natural-language">("default");
  const [applyPreset, setApplyPreset] = useState(true);
  const [autoLayout, setAutoLayout] = useState(true);
  const [specText, setSpecText] = useState("");
  const [defaultSpec, setDefaultSpec] = useState<FormattingSpec | null>(null);
  const [presets, setPresets] = useState<CleanupPreset[]>([]);
  const [selectedPresetId, setSelectedPresetId] = useState("default-clean-cn");
  const [serviceOnline, setServiceOnline] = useState<boolean | null>(null);
  const [connectionAttempt, setConnectionAttempt] = useState(0);
  const [compilationReport, setCompilationReport] = useState<CompilationReport | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [compliance, setCompliance] = useState<ComplianceReport | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string>("");
  const [error, setError] = useState<string>("");
  const [dragging, setDragging] = useState(false);
  const [showOnlyUncertain, setShowOnlyUncertain] = useState(false);
  const [showTextImport, setShowTextImport] = useState(false);
  const [plainText, setPlainText] = useState("");
  const [plainFilename, setPlainFilename] = useState("未命名文档.docx");
  const previewRef = useRef<HTMLDivElement>(null);
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
        const { renderAsync } = await import("docx-preview");
        if (!cancelled && previewRef.current) {
          await renderAsync(blob, previewRef.current, undefined, {
            className: "docx-preview-page",
            inWrapper: true,
            ignoreWidth: false,
            ignoreHeight: false,
          });
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
      storeWorkspace({
        document_id: document.document_id,
        analysis_id: result.analysis_id,
      });
      const smartDetail = result.summary.analysis_mode === "smart"
        ? `智能模型复核 ${result.summary.model_reviewed_paragraphs} 段，文档类型 ${result.summary.document_kind ?? "other"}；`
        : "确定性分析；";
      setMessage(`分析完成：${smartDetail}${result.summary.paragraph_count} 个段落，${result.summary.unknown_count} 个待确认。`);
      const recommended = presets.find((preset) =>
        result.document_ir.blocks.some(
          (block) => block.kind === "table" && block.columns_estimate >= 8,
        )
          ? preset.preset_id === "wide-table-clean-cn"
          : preset.recommended_kinds.includes(result.summary.document_kind ?? "other"),
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

  function selectCleanupPreset(preset: CleanupPreset, announce = true) {
    setRuleMode("default");
    setSelectedPresetId(preset.preset_id);
    setSpecText(JSON.stringify(preset.spec, null, 2));
    setAutoLayout(preset.spec.auto_layout?.enabled ?? true);
    setCompilationReport(null);
    setCompliance(null);
    if (announce) {
      clearNotices();
      setMessage(`已载入默认整理模式“${preset.name}”：${preset.description}`);
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
                <input type="file" accept=".docx" onChange={handleFileInput} hidden />
              </label>
              {!document && (
                <button className="remove-document text-import-toggle" onClick={() => setShowTextImport((value) => !value)}>
                  {showTextImport ? "收起纯文本" : "粘贴纯文本"}
                </button>
              )}
              {document && (
                <button className="remove-document" disabled={Boolean(busy)} onClick={deleteWorkspace}>
                  删除本地文档
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
            disabled={!document || !specText || Boolean(busy) || jobActive}
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
        <aside className="panel structure-panel">
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
                    return <div className="structure-table" key={block.node_id}>{block.locator} · 表格 · {block.rows} × {block.columns_estimate}</div>;
                  }
                  if (block.kind === "unsupported") {
                    return <div className="structure-warning" key={block.node_id}>{block.locator} · 未识别结构 · 已保留</div>;
                  }
                  return (
                    <article className={`structure-item ${block.detected_role === "unknown" ? "uncertain" : ""}`} key={block.node_id}>
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
                <strong>{job.status === "completed" ? "输出已生成" : `任务 ${job.status}`}</strong>
                <span>{job.progress}%</span>
              </div>
              <div className="progress"><i style={{ width: `${job.progress}%` }} /></div>
              {job.status === "completed" && (
                <>
                  <p>
                    源文件未修改；{job.auto_layout_splits > 0
                      ? `已安全重构 ${job.auto_layout_splits} 处连续正文。`
                      : "未发现需要拆分的连续正文。"}
                  </p>
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
            </div>
            {ruleMode === "default" ? (
              <div className="default-mode-card" role="tabpanel">
                <strong>常规、干净、可重复</strong>
                <p>先按文档类型推荐方案，你仍可随时切换；所有方案都统一字体颜色、清除突出与底纹，并保留原文。</p>
                <div className="preset-grid" role="radiogroup" aria-label="整理方案">
                  {presets.map((preset) => {
                    const recommended = analysis
                      ? analysis.document_ir.blocks.some(
                          (block) => block.kind === "table" && block.columns_estimate >= 8,
                        )
                        ? preset.preset_id === "wide-table-clean-cn"
                        : preset.recommended_kinds.includes(analysis.summary.document_kind ?? "other")
                      : preset.preset_id === "default-clean-cn";
                    return (
                      <button
                        key={preset.preset_id}
                        type="button"
                        role="radio"
                        aria-checked={selectedPresetId === preset.preset_id}
                        className={`preset-option ${selectedPresetId === preset.preset_id ? "active" : ""}`}
                        onClick={() => selectCleanupPreset(preset)}
                      >
                        <strong>{preset.name}{recommended ? " · 推荐" : ""}</strong>
                        <small>{preset.description}</small>
                      </button>
                    );
                  })}
                </div>
                <button
                  type="button"
                  className="button secondary"
                  disabled={!defaultSpec || Boolean(busy)}
                  onClick={useDefaultCleanupMode}
                >
                  重新载入默认规则
                </button>
              </div>
            ) : (
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
            )}
            <label className="format-mode-option">
              <input
                type="checkbox"
                checked={autoLayout}
                onChange={(event) => {
                  setAutoLayout(event.target.checked);
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
              <summary>高级规则 JSON · {ruleMode === "default" ? "整理方案" : "自然语言"}</summary>
              <p className="helper">仅在需要精细控制时编辑；格式错误会在提交前给出明确提示。</p>
              <textarea
                id="spec-json"
                aria-label="结构化规则"
                className="spec-editor"
                spellCheck={false}
                value={specText}
                onChange={(event) => {
                  setSpecText(event.target.value);
                  setCompliance(null);
                }}
              />
            </details>
          </div>
        </aside>
      </section>

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
