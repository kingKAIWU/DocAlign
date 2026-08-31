"use client";

import Link from "next/link";
import { ChangeEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api, ApiError, apiUrl } from "@/lib/api";
import { errorLabels } from "@/lib/messages";
import type {
  BatchAudit,
  BatchItem,
  BatchItemStatus,
  Capabilities,
  RulePackCatalogItem,
  RulePackDetail,
} from "@/lib/types";

const STORAGE_KEY = "docalign.batch.v1";
const TERMINAL = new Set(["completed", "completed_with_errors", "failed", "canceled"]);
const BATCH_CONNECTION_ERROR = "无法连接本地服务，页面会按退避间隔自动重试。";
const BATCH_RECOVERY_CONNECTION_ERROR =
  "上次批次暂时无法读取，记录已保留；本地服务恢复后会自动重试。";

type PendingRetry = { request_id: string; attempt_count: number };
type StoredBatch = { batch_id: string; pending_retries?: Record<string, PendingRetry> };
type PendingCreate = { fingerprint: string; request_id: string };

const itemStatusLabels: Record<BatchItemStatus, string> = {
  preparing: "准备文件",
  queued: "等待处理",
  analyzing: "分析结构",
  planning: "生成计划",
  formatting: "应用格式",
  validating: "验证结果",
  repairing: "自动修复",
  canceling: "正在安全停止",
  canceled: "已取消",
  completed: "已完成",
  failed: "处理失败",
};

function requestId(prefix: string): string {
  const suffix = globalThis.crypto?.randomUUID?.() ??
    `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}-${suffix}`;
}

function readStoredBatch(): StoredBatch | null {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY);
    if (!value) return null;
    const parsed = JSON.parse(value) as Partial<StoredBatch>;
    if (typeof parsed.batch_id !== "string" || !parsed.batch_id.trim()) {
      window.localStorage.removeItem(STORAGE_KEY);
      return null;
    }
    return {
      batch_id: parsed.batch_id,
      pending_retries:
        parsed.pending_retries && typeof parsed.pending_retries === "object"
          ? parsed.pending_retries
          : {},
    };
  } catch {
    window.localStorage.removeItem(STORAGE_KEY);
    return null;
  }
}

function storeBatch(batchId: string, retries: Record<string, PendingRetry>): void {
  window.localStorage.setItem(
    STORAGE_KEY,
    JSON.stringify({ batch_id: batchId, pending_retries: retries }),
  );
}

function readableError(caught: unknown): string {
  if (caught instanceof ApiError) return caught.message;
  if (caught instanceof Error) return caught.message;
  return "发生未知错误。";
}

function fileFingerprint(
  files: File[],
  name: string,
  packId: string,
  revision: number,
): string {
  return JSON.stringify({
    name,
    packId,
    revision,
    files: files.map((file) => [file.name, file.size, file.lastModified]),
  });
}

export function BatchWorkspace() {
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [packs, setPacks] = useState<RulePackCatalogItem[]>([]);
  const [packDetail, setPackDetail] = useState<RulePackDetail | null>(null);
  const [selectedPackId, setSelectedPackId] = useState("");
  const [selectedRevision, setSelectedRevision] = useState(1);
  const [batchName, setBatchName] = useState("本次批量排版");
  const [files, setFiles] = useState<File[]>([]);
  const [batch, setBatch] = useState<BatchAudit | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [serviceOnline, setServiceOnline] = useState<boolean | null>(null);
  const [recoveryNeedsRetry, setRecoveryNeedsRetry] = useState(false);
  const [connectionAttempt, setConnectionAttempt] = useState(0);
  const [draftAccepted, setDraftAccepted] = useState(false);
  const [processingBoundaryAccepted, setProcessingBoundaryAccepted] = useState(false);
  const pendingCreateRef = useRef<PendingCreate | null>(null);
  const pendingRetriesRef = useRef<Record<string, PendingRetry>>({});

  const selectedPack = packs.find((pack) => pack.pack_id === selectedPackId) ?? null;
  const selectedVersion = packDetail?.versions.find(
    (version) => version.revision === selectedRevision,
  );
  const isDraft = selectedVersion?.approval_status === "draft";
  const totalBytes = useMemo(
    () => files.reduce((total, file) => total + file.size, 0),
    [files],
  );
  const activeBatchId = batch && !TERMINAL.has(batch.status) ? batch.batch_id : null;
  const connectionRecovering = serviceOnline === false || recoveryNeedsRetry;

  const applyBatch = useCallback((current: BatchAudit) => {
    const pending = { ...pendingRetriesRef.current };
    for (const item of current.items) {
      const retry = pending[item.item_id];
      if (retry && (item.attempt_count > retry.attempt_count || item.status !== "failed")) {
        delete pending[item.item_id];
      }
    }
    pendingRetriesRef.current = pending;
    storeBatch(current.batch_id, pending);
    setBatch(current);
    setServiceOnline(true);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([api.capabilities(controller.signal), api.rulePacks(controller.signal)])
      .then(([nextCapabilities, catalog]) => {
        setCapabilities(nextCapabilities);
        setPacks(catalog.rule_packs);
        setSelectedPackId((current) => current || catalog.rule_packs[0]?.pack_id || "");
        setServiceOnline(true);
        setError((current) =>
          current === BATCH_CONNECTION_ERROR ? "" : current,
        );
      })
      .catch((caught) => {
        if (!controller.signal.aborted) {
          if (caught instanceof ApiError) {
            setServiceOnline(true);
            setError(`本地服务暂时无法准备批处理：${readableError(caught)}`);
          } else {
            setServiceOnline(false);
            setError(BATCH_CONNECTION_ERROR);
          }
        }
      });
    return () => controller.abort();
  }, [connectionAttempt]);

  useEffect(() => {
    if (!connectionRecovering || activeBatchId) return;
    const retryDelay = Math.min(30_000, 3_000 * 2 ** Math.min(connectionAttempt, 3));
    const timer = window.setTimeout(
      () => setConnectionAttempt((value) => value + 1),
      retryDelay,
    );
    return () => window.clearTimeout(timer);
  }, [activeBatchId, connectionAttempt, connectionRecovering]);

  useEffect(() => {
    if (!selectedPackId) return;
    const controller = new AbortController();
    api.rulePack(selectedPackId, controller.signal)
      .then((detail) => {
        setPackDetail(detail);
        setSelectedRevision(detail.current_revision);
        setDraftAccepted(false);
      })
      .catch((caught) => {
        if (!controller.signal.aborted) setError(readableError(caught));
      });
    return () => controller.abort();
  }, [selectedPackId]);

  useEffect(() => {
    const stored = readStoredBatch();
    if (!stored) return;
    pendingRetriesRef.current = stored.pending_retries ?? {};
    const controller = new AbortController();
    api.batch(stored.batch_id, controller.signal)
      .then((current) => {
        applyBatch(current);
        setRecoveryNeedsRetry(false);
        setError((currentError) =>
          currentError === BATCH_RECOVERY_CONNECTION_ERROR ? "" : currentError,
        );
        setMessage("已恢复上次批次，可继续查看进度或重试失败文件。 ");
      })
      .catch((caught) => {
        if (!controller.signal.aborted) {
          if (caught instanceof ApiError && caught.status === 404) {
            window.localStorage.removeItem(STORAGE_KEY);
            pendingRetriesRef.current = {};
            setBatch(null);
            setServiceOnline(true);
            setRecoveryNeedsRetry(false);
            setError("");
            setMessage("上次批次已不在当前本地存储，旧恢复记录已自动清理；可以新建批次。");
          } else if (caught instanceof ApiError) {
            setServiceOnline(true);
            setRecoveryNeedsRetry(false);
            setError(`上次批次无法恢复：${readableError(caught)}`);
          } else {
            setServiceOnline(false);
            setRecoveryNeedsRetry(true);
            setError(BATCH_RECOVERY_CONNECTION_ERROR);
          }
        }
      });
    return () => controller.abort();
  }, [applyBatch, connectionAttempt]);

  useEffect(() => {
    if (!activeBatchId) return;
    let cancelled = false;
    let timer: number | undefined;
    let failures = 0;
    const refresh = async () => {
      const controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), 10_000);
      try {
        const current = await api.batch(activeBatchId, controller.signal);
        if (cancelled) return;
        failures = 0;
        applyBatch(current);
        setError("");
        if (!TERMINAL.has(current.status)) timer = window.setTimeout(refresh, 1_000);
      } catch (caught) {
        if (cancelled) return;
        if (caught instanceof ApiError && caught.status === 404) {
          window.localStorage.removeItem(STORAGE_KEY);
          pendingRetriesRef.current = {};
          setBatch(null);
          setServiceOnline(true);
          setRecoveryNeedsRetry(false);
          setError("");
          setMessage("当前批次已不在本地存储；已停止无效重连，可以新建批次。");
          return;
        }
        failures += 1;
        if (caught instanceof ApiError) {
          setServiceOnline(true);
          setError(`批次进度暂时无法读取：${readableError(caught)}`);
        } else {
          setServiceOnline(false);
          setMessage("进度连接暂时中断，任务仍在本地后台运行，正在自动恢复…");
        }
        timer = window.setTimeout(refresh, Math.min(8_000, 500 * 2 ** failures));
      } finally {
        window.clearTimeout(timeout);
      }
    };
    timer = window.setTimeout(refresh, 300);
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [activeBatchId, applyBatch]);

  function selectFiles(event: ChangeEvent<HTMLInputElement>) {
    const selected = Array.from(event.target.files ?? []);
    const valid = selected.filter((file) => file.name.toLowerCase().endsWith(".docx"));
    const limit = capabilities?.max_batch_files ?? 20;
    setFiles((current) => [...current, ...valid].slice(0, limit));
    if (valid.length) setProcessingBoundaryAccepted(false);
    if (valid.length !== selected.length) setError("已忽略非 DOCX 文件。 ");
    if (files.length + valid.length > limit) setError(`每批最多 ${limit} 个文件。`);
    event.target.value = "";
  }

  async function createBatch() {
    if (!selectedPack || !files.length) return;
    if (!processingBoundaryAccepted) {
      setError("请先确认批量任务中的复杂内容需要逐份人工核对。");
      return;
    }
    const totalLimit = (capabilities?.max_batch_total_mb ?? 200) * 1024 * 1024;
    if (totalBytes > totalLimit) {
      setError(`本批文件总大小不能超过 ${capabilities?.max_batch_total_mb ?? 200} MB。`);
      return;
    }
    const normalizedName = batchName.trim();
    if (!normalizedName) {
      setError("请填写批次名称。 ");
      return;
    }
    const fingerprint = fileFingerprint(
      files,
      normalizedName,
      selectedPack.pack_id,
      selectedRevision,
    );
    if (pendingCreateRef.current?.fingerprint !== fingerprint) {
      pendingCreateRef.current = { fingerprint, request_id: requestId("batch") };
    }
    setBusy("create");
    setError("");
    setMessage("正在逐个验证文件并建立本地批次…");
    try {
      const current = await api.createBatch({
        requestId: pendingCreateRef.current.request_id,
        name: normalizedName,
        rulePackId: selectedPack.pack_id,
        rulePackRevision: selectedRevision,
        processingBoundaryAcknowledged: processingBoundaryAccepted,
        files,
      });
      pendingCreateRef.current = null;
      applyBatch(current);
      setMessage("批次已进入本地队列；刷新或关闭页面后仍可恢复。 ");
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? readableError(caught)
          : "连接中断，可直接再次点击开始；同一请求不会重复创建批次。",
      );
    } finally {
      setBusy(null);
    }
  }

  async function retryItem(item: BatchItem) {
    if (!batch) return;
    const existing = pendingRetriesRef.current[item.item_id];
    const retry = existing?.attempt_count === item.attempt_count
      ? existing
      : { request_id: requestId("retry"), attempt_count: item.attempt_count };
    pendingRetriesRef.current[item.item_id] = retry;
    storeBatch(batch.batch_id, pendingRetriesRef.current);
    setBusy(item.item_id);
    setError("");
    try {
      const current = await api.retryBatchItem(
        batch.batch_id,
        item.item_id,
        retry.request_id,
      );
      applyBatch(current);
      setMessage(`已重新提交“${item.filename}”；旧尝试记录已保留。`);
    } catch (caught) {
      if (caught instanceof ApiError) {
        delete pendingRetriesRef.current[item.item_id];
        storeBatch(batch.batch_id, pendingRetriesRef.current);
        setError(readableError(caught));
      } else {
        setError("重试响应中断，可再次点击；服务端会复用同一次重试请求。 ");
      }
    } finally {
      setBusy(null);
    }
  }

  async function refreshBatch() {
    if (!batch) return;
    setBusy("refresh");
    try {
      applyBatch(await api.batch(batch.batch_id));
      setError("");
    } catch (caught) {
      setError(readableError(caught));
    } finally {
      setBusy(null);
    }
  }

  async function cancelBatch() {
    if (!batch || TERMINAL.has(batch.status) || batch.status === "canceling") return;
    if (!window.confirm("确定取消这个批次吗？正在处理的文件会在当前安全阶段结束后停止，已完成文件会保留。")) return;
    setBusy("cancel");
    setError("");
    try {
      applyBatch(await api.cancelBatch(batch.batch_id));
      setMessage("已发出取消请求；正在处理的文件会安全停止并丢弃未完成输出。 ");
    } catch (caught) {
      setError(readableError(caught));
    } finally {
      setBusy(null);
    }
  }

  async function deleteBatch() {
    if (!batch || !TERMINAL.has(batch.status)) return;
    if (!window.confirm("确定永久删除这个本地批次吗？源文件、分析记录、作业、输出和审计文件都将被清理，此操作无法撤销。")) return;
    setBusy("delete");
    setError("");
    try {
      await api.deleteBatch(batch.batch_id);
      window.localStorage.removeItem(STORAGE_KEY);
      pendingRetriesRef.current = {};
      pendingCreateRef.current = null;
      setBatch(null);
      setFiles([]);
      setProcessingBoundaryAccepted(false);
      setMessage("本地批次及其文件已全部删除。 ");
    } catch (caught) {
      setError(readableError(caught));
    } finally {
      setBusy(null);
    }
  }

  function startNewBatch() {
    if (batch && !TERMINAL.has(batch.status)) return;
    window.localStorage.removeItem(STORAGE_KEY);
    pendingRetriesRef.current = {};
    pendingCreateRef.current = null;
    setBatch(null);
    setFiles([]);
    setMessage("");
    setError("");
  }

  return (
    <main className="app-shell batch-shell">
      <header className="topbar">
        <Link className="brand-lockup" href="/">
          <span className="brand-mark" aria-hidden="true">DA</span>
          <div><strong>DocAlign</strong><span>文档格式合规工作台</span></div>
        </Link>
        <div className="topbar-actions">
          <span className={`privacy-pill ${connectionRecovering ? "" : "ready"}`}>
            <i /> {connectionRecovering ? "正在重连" : "本地处理"}
          </span>
          <Link href="/">单文档</Link>
          <Link href="/settings">设置</Link>
        </div>
      </header>

      <section className="batch-hero">
        <div>
          <p className="eyebrow">DURABLE BATCH PROCESSING</p>
          <h1>一套规则，稳定处理一批文档</h1>
          <p>每个文件独立处理、独立失败；进度持久保存，断开后自动恢复。</p>
        </div>
        {batch && (
          <button
            className="button secondary"
            disabled={!TERMINAL.has(batch.status)}
            onClick={startNewBatch}
          >新建批次</button>
        )}
      </section>

      {message && <div className="notice success"><span>状态</span>{message}</div>}
      {error && <div className="notice error"><span>提示</span>{error}{connectionRecovering && (
        <button
          className="button secondary compact"
          onClick={() => setConnectionAttempt((value) => value + 1)}
        >立即重试</button>
      )}</div>}

      {!batch ? (
        <section className="batch-setup-grid">
          <article className="batch-card batch-files-card">
            <div className="batch-card-heading"><b>1</b><div><h2>选择文档</h2><p>文件之间互不影响</p></div></div>
            <label className="batch-dropzone">
              <strong>添加 DOCX 文件</strong>
              <span>可分多次选择；源文件不会被覆盖</span>
              <input
                aria-label="选择批量处理的 Word 文档"
                type="file"
                accept=".docx"
                multiple
                onChange={selectFiles}
                hidden
              />
            </label>
            <div className="batch-file-summary">
              <span>{files.length} / {capabilities?.max_batch_files ?? 20} 个</span>
              <span>{(totalBytes / 1024 / 1024).toFixed(1)} / {capabilities?.max_batch_total_mb ?? 200} MB</span>
            </div>
            <ol className="batch-source-list">
              {files.map((file, index) => (
                <li key={`${file.name}-${file.lastModified}-${index}`}>
                  <span><b>{index + 1}</b><span><strong>{file.name}</strong><small>{(file.size / 1024).toFixed(1)} KB</small></span></span>
                  <button aria-label={`移除 ${file.name}`} onClick={() => { setFiles((current) => current.filter((_, itemIndex) => itemIndex !== index)); setProcessingBoundaryAccepted(false); }}>移除</button>
                </li>
              ))}
              {!files.length && <li className="batch-list-empty">尚未添加文件</li>}
            </ol>
          </article>

          <article className="batch-card batch-rule-card">
            <div className="batch-card-heading"><b>2</b><div><h2>锁定规则版本</h2><p>整批使用同一不可变快照</p></div></div>
            <label>规则包
              <select aria-label="批处理规则包" value={selectedPackId} onChange={(event) => setSelectedPackId(event.target.value)}>
                {!packs.length && <option value="">请先在单文档工作台保存规则包</option>}
                {packs.map((pack) => <option key={pack.pack_id} value={pack.pack_id}>{pack.name}</option>)}
              </select>
            </label>
            <label>修订版本
              <select aria-label="批处理规则版本" value={selectedRevision} onChange={(event) => { setSelectedRevision(Number(event.target.value)); setDraftAccepted(false); }} disabled={!packDetail}>
                {packDetail?.versions.map((version) => <option key={version.revision} value={version.revision}>修订 {version.revision} · {version.approval_status === "locally_approved" ? "本地已确认" : "草稿"}</option>)}
              </select>
            </label>
            {selectedPack && <div className={`batch-rule-trust ${isDraft ? "draft" : "approved"}`}>
              <strong>{isDraft ? "草稿规则：需要明确确认" : "本地已确认规则"}</strong>
              <p>{selectedPack.scope_label}</p>
              <small>SHA-256 {selectedVersion?.spec_sha256.slice(0, 16)}…</small>
              {isDraft && <label className="batch-draft-confirm"><input type="checkbox" checked={draftAccepted} onChange={(event) => setDraftAccepted(event.target.checked)} />我了解该修订尚未完成本地确认，仍要用于本批次</label>}
            </div>}
            <label className="batch-boundary-confirm">
              <input
                type="checkbox"
                checked={processingBoundaryAccepted}
                onChange={(event) => setProcessingBoundaryAccepted(event.target.checked)}
              />
              <span>
                我了解批量任务不会在开始前逐份暂停；复杂内容将在结果中标记，需在 Word/WPS 中逐份核对
              </span>
            </label>
            <label>批次名称
              <input aria-label="批次名称" value={batchName} maxLength={160} onChange={(event) => setBatchName(event.target.value)} />
            </label>
            <button
              className="button primary batch-submit"
              disabled={busy === "create" || !files.length || !selectedPack || !processingBoundaryAccepted || (isDraft && !draftAccepted)}
              onClick={() => void createBatch()}
            >{busy === "create" ? "正在建立批次…" : `开始处理 ${files.length || ""} 个文档`}</button>
            <p className="batch-recovery-note">请求中断时直接重试即可；服务端用同一请求标识去重，不会重复建批。</p>
          </article>
        </section>
      ) : (
        <BatchProgress
          batch={batch}
          busy={busy}
          onRefresh={() => void refreshBatch()}
          onRetry={(item) => void retryItem(item)}
          onCancel={() => void cancelBatch()}
          onDelete={() => void deleteBatch()}
        />
      )}
    </main>
  );
}

function BatchProgress({
  batch,
  busy,
  onRefresh,
  onRetry,
  onCancel,
  onDelete,
}: {
  batch: BatchAudit;
  busy: string | null;
  onRefresh: () => void;
  onRetry: (item: BatchItem) => void;
  onCancel: () => void;
  onDelete: () => void;
}) {
  const terminal = TERMINAL.has(batch.status);
  return (
    <section className="batch-progress-shell">
      <div className="batch-overview">
        <div><p>批次</p><h2>{batch.name}</h2><span>{batch.rule_pack_name} · 修订 {batch.rule_pack_revision}</span><small>{batch.processing_boundary_acknowledged ? "已记录批量复杂内容核对确认" : "旧批次未记录复杂内容核对确认"}</small></div>
        <div className="batch-metrics">
          <span><b>{batch.summary.completed}</b>完成</span>
          <span><b>{batch.summary.failed}</b>失败</span>
          <span><b>{batch.summary.canceled}</b>取消</span>
          <span><b>{batch.summary.active}</b>处理中</span>
          <span><b>{batch.progress}%</b>总进度</span>
        </div>
        <div className="batch-actions">
          <button className="button secondary" disabled={busy === "refresh"} onClick={onRefresh}>刷新状态</button>
          <a className={`button primary ${batch.delivery_package_url ? "" : "disabled"}`} href={apiUrl(batch.delivery_package_url)}>下载完整交付包</a>
          <a className={`button secondary ${batch.output_zip_url ? "" : "disabled"}`} href={apiUrl(batch.output_zip_url)}>仅下载 DOCX ZIP</a>
          <a className="text-link" href={apiUrl(batch.audit_json_url)}>下载批次审计 JSON</a>
          {!terminal && <button className="button danger" disabled={busy === "cancel" || batch.status === "canceling"} onClick={onCancel}>{batch.status === "canceling" ? "正在安全停止…" : "取消批次"}</button>}
          {terminal && <button className="button danger" disabled={busy === "delete"} onClick={onDelete}>{busy === "delete" ? "正在删除…" : "删除本地批次"}</button>}
        </div>
        <p className="delivery-package-note">完整交付包包含每份输出的独立审计和 SHA-256 清单；批次仍在运行时需等待终态后生成。</p>
      </div>
      <div className="batch-total-progress"><i style={{ width: `${batch.progress}%` }} /></div>
      <div className="batch-results" role="table" aria-label="批处理文件进度">
        <div className="batch-result-header" role="row"><span>文件</span><span>状态</span><span>结果验证</span><span>操作</span></div>
        {batch.items.map((item) => (
          <div className={`batch-result-row ${item.status}`} role="row" key={item.item_id}>
            <div><b>{String(item.position).padStart(2, "0")}</b><span><strong>{item.filename}</strong><small>{item.attempt_count ? `已尝试 ${item.attempt_count} 次` : "上传校验未通过"}</small></span></div>
            <div><strong>{itemStatusLabels[item.status]}</strong><div className="batch-item-progress"><i style={{ width: `${item.progress}%` }} /></div><small>{item.error_code ? (errorLabels[item.error_code] ?? item.error_message) : item.error_message}</small></div>
            <div className="batch-validation">
              {item.status === "completed" ? <><span className={item.validation_passed ? "passed" : "failed"}>格式{item.validation_passed ? "通过" : "异常"}</span><span className={item.content_integrity_passed ? "passed" : "failed"}>内容{item.content_integrity_passed ? "安全" : "异常"}</span>{Boolean(item.source_review_features) && <span className="needs-review">复杂内容 {item.source_review_features} 类待核对</span>}<small>{item.changed_mutations ?? 0} 项实际变更</small></> : <small>{item.status === "failed" ? "未生成输出" : item.status === "canceled" ? "已取消，未生成输出" : item.status === "canceling" ? "正在安全停止" : "完成后显示验证结果"}</small>}
            </div>
            <div className="batch-item-actions">
              {item.output_document_url && <a className="text-link" href={apiUrl(item.output_document_url)}>下载 DOCX</a>}
              {item.audit_json_url && <a className="text-link" href={apiUrl(item.audit_json_url)}>审计</a>}
              {item.retryable && <button className="button secondary" disabled={busy === item.item_id} onClick={() => onRetry(item)}>{busy === item.item_id ? "提交中…" : "重试失败项"}</button>}
            </div>
          </div>
        ))}
      </div>
      <p className="batch-footer-note">批次 ID {batch.batch_id} · 进度、失败原因与重试历史均保存在本机。</p>
    </section>
  );
}
