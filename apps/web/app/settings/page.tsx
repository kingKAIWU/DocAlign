"use client";

import Link from "next/link";
import { type MouseEvent, type ReactNode, useEffect, useState } from "react";

import { api, API_BASE, ApiError } from "@/lib/api";
import { errorLabels } from "@/lib/messages";
import type {
  Capabilities,
  DeliveryPackageVerification,
  StorageBatchItem,
  StorageDocumentItem,
  SupportDiagnosticReport,
  WorkspaceStorageReport,
} from "@/lib/types";

const categoryLabels: Record<WorkspaceStorageReport["categories"][number]["category"], string> = {
  source_documents: "源 DOCX",
  analyses: "结构分析",
  job_audits: "作业与审计",
  outputs: "排版输出",
  batch_packages: "批次压缩包",
  database: "本地数据库",
  other: "其他本地数据",
};

const batchStatusLabels: Record<StorageBatchItem["status"], string> = {
  preparing: "准备中",
  processing: "处理中",
  canceling: "取消中",
  canceled: "已取消",
  completed: "已完成",
  completed_with_errors: "部分完成",
  failed: "失败",
};

const diagnosticOverallLabels: Record<SupportDiagnosticReport["overall"], string> = {
  ready: "诊断正常",
  attention: "建议关注",
  action_required: "需要处理",
};

export default function SettingsPage() {
  const packagedMode = API_BASE.startsWith("/");
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [storage, setStorage] = useState<WorkspaceStorageReport | null>(null);
  const [diagnostic, setDiagnostic] = useState<SupportDiagnosticReport | null>(null);
  const [deliveryFile, setDeliveryFile] = useState<File | null>(null);
  const [deliveryVerification, setDeliveryVerification] =
    useState<DeliveryPackageVerification | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    api.capabilities(controller.signal)
      .then(setCapabilities)
      .catch(() => {
        if (!controller.signal.aborted) setCapabilities(null);
      });
    api.workspaceStorage(controller.signal)
      .then(setStorage)
      .catch((caught) => {
        if (!controller.signal.aborted) setError(readableError(caught));
      });
    return () => controller.abort();
  }, []);

  async function refreshStorage() {
    setBusy("refresh");
    setError("");
    try {
      setStorage(await api.workspaceStorage());
    } catch (caught) {
      setError(readableError(caught));
    } finally {
      setBusy(null);
    }
  }

  async function runDiagnostics() {
    setBusy("diagnostics");
    setError("");
    setMessage("");
    try {
      setDiagnostic(await api.diagnostics());
    } catch (caught) {
      setError(readableError(caught));
    } finally {
      setBusy(null);
    }
  }

  async function verifyDelivery() {
    if (!deliveryFile) return;
    setBusy("delivery-verify");
    setError("");
    setMessage("");
    setDeliveryVerification(null);
    try {
      setDeliveryVerification(await api.verifyDelivery(deliveryFile));
    } catch (caught) {
      setError(readableError(caught));
    } finally {
      setBusy(null);
    }
  }

  async function quitDesktop() {
    if (!window.confirm("安全退出 DocAlign？正在处理的任务会先完成安全收尾，然后应用停止运行。源文件和已完成结果都会保留。")) return;
    setBusy("quit");
    setError("");
    setMessage("");
    try {
      await api.quitDesktop();
      setMessage("DocAlign 正在安全退出。任务收尾和本地数据关闭完成后，你可以关闭这个浏览器页面。");
    } catch (caught) {
      setError(readableError(caught));
      setBusy(null);
    }
  }

  async function deleteBatch(item: StorageBatchItem) {
    if (!window.confirm(`永久删除批次“${item.name}”及其 ${item.item_count} 份源文件、分析、作业、输出和审计？共享规则包不会被删除。`)) return;
    setBusy(`batch:${item.batch_id}`);
    setError("");
    try {
      await api.deleteBatch(item.batch_id);
      clearMatchingLocalRecord("docalign.batch.v1", "batch_id", item.batch_id);
      setStorage(await api.workspaceStorage());
      setMessage(`已删除批次“${item.name}”，释放约 ${formatBytes(item.bytes)}。`);
    } catch (caught) {
      setError(readableError(caught));
    } finally {
      setBusy(null);
    }
  }

  async function deleteDocument(item: StorageDocumentItem) {
    if (!item.deletable) return;
    if (!window.confirm(`永久删除独立文档“${item.filename}”及其分析、作业、输出和审计？此操作无法撤销。`)) return;
    setBusy(`document:${item.document_id}`);
    setError("");
    try {
      await api.deleteDocument(item.document_id);
      clearMatchingLocalRecord("docalign.workspace.v1", "document_id", item.document_id);
      setStorage(await api.workspaceStorage());
      setMessage(`已删除独立文档“${item.filename}”，释放约 ${formatBytes(item.bytes)}。`);
    } catch (caught) {
      setError(readableError(caught));
    } finally {
      setBusy(null);
    }
  }

  function confirmWorkspaceBackup(event: MouseEvent<HTMLAnchorElement>) {
    const blocked = !storage
      || storage.records.active_jobs > 0
      || storage.records.active_batches > 0;
    if (blocked) {
      event.preventDefault();
      return;
    }
    if (!window.confirm(
      "下载完整工作区备份？备份包含源 DOCX、原文件名、规则、任务记录和输出，未加密且没有数字签名。请保存在受保护的位置。",
    )) {
      event.preventDefault();
      return;
    }
    setError("");
    setMessage("正在生成一致的完整备份。大型工作区在浏览器显示下载前可能需要一些时间，请保持 DocAlign 运行。");
  }

  return (
    <main className="settings-shell">
      <div className="settings-nav"><Link className="back-link" href="/">← 返回工作台</Link><Link href="/batches">批量处理</Link></div>
      <p className="eyebrow">LOCAL CONFIGURATION</p>
      <h1>设置与隐私边界</h1>
      <p className="settings-intro">密钥只从后端环境读取，不进入浏览器、SQLite 或审计日志。</p>

      {message && <div className="notice success"><span>状态</span>{message}</div>}
      {error && <div className="notice error"><span>提示</span>{error}</div>}

      <section className="settings-card">
        <div className="setting-row"><span>API 地址</span><code>{API_BASE}</code></div>
        <div className="setting-row"><span>本地处理</span><b>{capabilities?.local_only ? "已启用" : "检查中"}</b></div>
        <div className="setting-row"><span>兼容模型</span><b>{capabilities?.llm_configured ? "已配置" : "未配置"}</b></div>
        <div className="setting-row"><span>上传限制</span><b>{capabilities?.max_upload_mb ?? 20} MB</b></div>
        <div className="setting-row"><span>单批文件数</span><b>{capabilities?.max_batch_files ?? 20} 个</b></div>
        <div className="setting-row"><span>单批总大小</span><b>{capabilities?.max_batch_total_mb ?? 200} MB</b></div>
      </section>

      {capabilities?.desktop_app && <section className="settings-card app-lifecycle-card">
        <div>
          <p className="eyebrow">APPLICATION LIFECYCLE</p>
          <h2>安全退出 DocAlign</h2>
          <p>关闭浏览器页面不会停止本地任务。完成使用后请从这里退出；正在处理的任务会先安全收尾，源文件、规则和已完成结果不会被删除。</p>
        </div>
        <button className="button danger" disabled={Boolean(busy)} onClick={() => void quitDesktop()}>
          {busy === "quit" ? "正在安全退出…" : "安全退出应用"}
        </button>
      </section>}

      <section className="settings-card storage-card">
        <div className="storage-heading">
          <div><p className="eyebrow">LOCAL STORAGE</p><h2>本地存储中心</h2><p>只统计 DocAlign 数据目录，不读取文档正文。默认不自动删除，由你逐项确认。</p></div>
          <button className="button secondary" disabled={Boolean(busy)} onClick={() => void refreshStorage()}>{busy === "refresh" ? "正在刷新…" : "刷新占用"}</button>
        </div>
        {!storage ? <div className="storage-loading">正在统计本地数据…</div> : <>
          <div className="storage-overview">
            <span><small>DocAlign 占用</small><strong>{formatBytes(storage.docalign_bytes)}</strong></span>
            <span><small>可逐项清理</small><strong>{formatBytes(storage.reclaimable_bytes)}</strong></span>
            <span><small>磁盘可用</small><strong>{formatBytes(storage.disk_free_bytes)}</strong></span>
            <span className={storage.pressure}><small>磁盘状态</small><strong>{storage.pressure === "normal" ? "空间正常" : storage.pressure === "warning" ? "空间偏低" : "空间紧张"}</strong></span>
          </div>
          {storage.pressure !== "normal" && <div className={`storage-pressure ${storage.pressure}`}>{storage.pressure === "critical" ? "磁盘空间已接近不足。建议先下载需要留存的成果，再清理下方终态批次或独立文档。" : "磁盘可用空间偏低，建议检查下方占用较大的数据。"}</div>}
          <div className="storage-categories">
            {storage.categories.map((category) => <div key={category.category}>
              <span><b>{categoryLabels[category.category]}</b><small>{category.file_count} 个文件</small></span>
              <i><em style={{ width: `${storage.docalign_bytes && category.bytes ? Math.max(2, category.bytes / storage.docalign_bytes * 100) : 0}%` }} /></i>
              <strong>{formatBytes(category.bytes)}</strong>
            </div>)}
          </div>
          <div className="storage-records">
            <span>{storage.records.documents} 份文档</span><span>{storage.records.batches} 个批次</span><span>{storage.records.jobs} 个作业</span><span>{storage.records.active_jobs} 个活动任务</span><span>{storage.records.rule_packs} 个规则包</span>
          </div>
        </>}
      </section>

      <section className="settings-card workspace-backup-card">
        <div className="storage-heading">
          <div>
            <p className="eyebrow">RECOVERY BACKUP</p>
            <h2>完整工作区备份</h2>
            <p>一次下载源文档、分析、规则、任务审计、输出、批次产物和一致的数据库快照；环境变量、密钥、运行锁及 SQLite 临时文件不会收录。</p>
          </div>
          <a
            className={`button secondary ${!storage || storage.records.active_jobs > 0 || storage.records.active_batches > 0 ? "disabled" : ""}`}
            href={`${API_BASE}/workspace/backup`}
            download
            aria-disabled={!storage || storage.records.active_jobs > 0 || storage.records.active_batches > 0}
            onClick={confirmWorkspaceBackup}
          >下载完整备份</a>
        </div>
        <div className="workspace-backup-boundary">
          <strong>敏感且未加密</strong>
          <p>SHA-256 可以发现包内文件损坏或不一致，但不能证明备份由谁创建。请按原始文档的保密等级保存。</p>
        </div>
        {storage && (storage.records.active_jobs > 0 || storage.records.active_batches > 0)
          ? <p className="workspace-backup-wait">当前有 {storage.records.active_jobs} 个活动任务、{storage.records.active_batches} 个活动批次。全部结束后刷新占用，再下载备份。</p>
          : <p className="workspace-backup-restore">恢复时先安全退出 DocAlign，再执行 <code>docalign restore-workspace-backup 备份.zip --data-dir 新目录</code>；系统只允许恢复到尚不存在的目录，不会覆盖当前工作区。</p>}
      </section>

      <section className="settings-card delivery-verifier-card">
        <div className="storage-heading">
          <div>
            <p className="eyebrow">DELIVERY VERIFICATION</p>
            <h2>校验 DocAlign 交付包</h2>
            <p>在本机检查 BagIt 文件清单、全部 SHA-256、输出与任务审计是否一致；文件不会保存或上传到外部。</p>
          </div>
        </div>
        <div className="delivery-verifier-form">
          <label htmlFor="delivery-package-file">选择交付包 ZIP</label>
          <input
            id="delivery-package-file"
            type="file"
            accept=".zip,application/zip"
            disabled={Boolean(busy)}
            onChange={(event) => {
              setDeliveryFile(event.target.files?.[0] ?? null);
              setDeliveryVerification(null);
              setError("");
            }}
          />
          <button
            className="button secondary"
            disabled={!deliveryFile || Boolean(busy)}
            onClick={() => void verifyDelivery()}
          >
            {busy === "delivery-verify" ? "正在逐项校验…" : "开始校验"}
          </button>
          <small>最大 {capabilities?.max_delivery_package_mb ?? 220} MB；校验过程不解压到工作区。</small>
        </div>
        {deliveryVerification && <div className="delivery-verification-result" role="status">
          <div>
            <span>校验通过</span>
            <strong>{deliveryVerification.package_kind === "job" ? "单文档交付包" : "批量交付包"}</strong>
            <code>{deliveryVerification.package_id}</code>
          </div>
          <div className="delivery-verification-metrics">
            <span><b>{deliveryVerification.items.length}</b>份输出</span>
            <span><b>{deliveryVerification.payload_file_count}</b>个载荷文件</span>
            <span><b>{formatBytes(deliveryVerification.payload_bytes)}</b>载荷大小</span>
          </div>
          <ul>
            {deliveryVerification.items.map((item) => <li key={item.job_id}>
              <span>{item.position}. {item.source_filename}</span>
              <small>格式{item.validation_passed ? "通过" : "异常"} · 内容{item.content_integrity_passed ? "安全" : "异常"} · SHA-256 {item.output_sha256.slice(0, 16)}…</small>
            </li>)}
          </ul>
          <p>完整性已验证，但发布者身份未验证：当前交付包没有数字签名。</p>
        </div>}
      </section>

      <section className="settings-card diagnostic-card">
        <div className="storage-heading">
          <div><p className="eyebrow">LOCAL DIAGNOSTICS</p><h2>本机诊断与支持报告</h2><p>检查数据库、版本、数据目录、磁盘和本地产物；不会自动上传任何信息。</p></div>
          <button className="button secondary" disabled={Boolean(busy)} onClick={() => void runDiagnostics()}>{busy === "diagnostics" ? "正在诊断…" : diagnostic ? "重新诊断" : "运行诊断"}</button>
        </div>
        {!diagnostic ? <div className="diagnostic-empty">遇到启动、保存或任务异常时先运行诊断。应用无法打开时可在项目目录执行 <code>uv run python -m scripts.diagnose --out docalign-support-diagnostic.json</code>。</div> : <>
          <div className={`diagnostic-summary ${diagnostic.overall}`}>
            <span><small>综合结果</small><strong>{diagnosticOverallLabels[diagnostic.overall]}</strong></span>
            <span><small>DocAlign</small><strong>v{diagnostic.runtime.application_version}</strong></span>
            <span><small>运行环境</small><strong>{diagnostic.runtime.operating_system} · {diagnostic.runtime.architecture}</strong></span>
            <a className="button secondary" href={`${API_BASE}/diagnostics/export`}>下载安全诊断 JSON</a>
          </div>
          <div className="diagnostic-checks">
            {diagnostic.checks.map((check) => <div className={`diagnostic-check ${check.status}`} key={check.check_id}>
              <span>{check.status === "pass" ? "通过" : check.status === "warning" ? "关注" : "失败"}</span>
              <div><strong>{check.title}</strong><p>{check.detail}</p>{check.remediation && <small>处理建议：{check.remediation}</small>}</div>
            </div>)}
          </div>
          {diagnostic.recent_error_codes.length > 0 && <div className="diagnostic-errors"><b>近 30 天错误代码</b>{diagnostic.recent_error_codes.map((item) => <code key={item.code}>{item.code} × {item.count}</code>)}</div>}
          <p className="diagnostic-privacy">报告仅含运行版本、系统类型、配置状态、汇总数量、检查结果和错误代码；明确排除正文、文件名、记录 ID、完整路径、数据库连接串、模型端点、密钥和原始日志。请先自行检查 JSON，再决定是否发送给支持人员。</p>
        </>}
      </section>

      {storage && <section className="storage-cleanup-grid">
        <StorageList
          title="终态批次"
          description="已完成、部分完成、失败或已取消的批次。删除后共享规则包仍保留。"
          empty="暂无可清理的终态批次"
          truncated={storage.terminal_batches_truncated}
        >
          {storage.terminal_batches.map((item) => <div className="storage-item" key={item.batch_id}>
            <div><strong>{item.name}</strong><small>{batchStatusLabels[item.status]} · {item.item_count} 份文档 · 更新于 {formatDate(item.updated_at)}</small><small>{item.completed} 完成 / {item.failed} 失败 / {item.canceled} 取消</small></div>
            <span><b>{formatBytes(item.bytes)}</b><button className="button danger" disabled={Boolean(busy)} onClick={() => void deleteBatch(item)}>{busy === `batch:${item.batch_id}` ? "删除中…" : "删除"}</button></span>
          </div>)}
        </StorageList>

        <StorageList
          title="独立文档"
          description="未归入批次的单文档工作区。活动作业结束前禁止删除。"
          empty="暂无独立文档"
          truncated={storage.unbatched_documents_truncated}
        >
          {storage.unbatched_documents.map((item) => <div className="storage-item" key={item.document_id}>
            <div><strong>{item.filename}</strong><small>{item.analysis_count} 次分析 · {item.job_count} 个作业 · 创建于 {formatDate(item.created_at)}</small>{item.active_job_count > 0 && <small className="active-note">仍有 {item.active_job_count} 个活动作业</small>}</div>
            <span><b>{formatBytes(item.bytes)}</b><button className="button danger" disabled={!item.deletable || Boolean(busy)} onClick={() => void deleteDocument(item)}>{item.deletable ? (busy === `document:${item.document_id}` ? "删除中…" : "删除") : "处理中"}</button></span>
          </div>)}
        </StorageList>
      </section>}

      <section className="settings-card prose-card">
        <h2>启用自然语言规则编译</h2>
        <p>
          {packagedMode
            ? "桌面分发版只从启动进程的系统环境变量读取兼容模型配置，不读取当前目录中的 .env。重启 DocAlign 后生效。"
            : <>在项目根目录的 <code>.env</code> 设置下列变量，然后重启 API。</>}
          完整文档不会发送给模型；只发送格式要求和结构统计。
        </p>
        <pre>{`DOCALIGN_LLM_BASE_URL=https://your-endpoint.example/v1
DOCALIGN_LLM_API_KEY=...
DOCALIGN_LLM_MODEL=your-model
DOCALIGN_LLM_JSON_SCHEMA_MODE=auto`}</pre>
      </section>

      <section className="settings-card prose-card">
        <h2>数据保留</h2>
        <p>上传文件、分析结果、临时规则、独立规则包、任务、批次、重试历史和输出保存在本机 <code>DOCALIGN_DATA_DIR</code>。DocAlign 不预设统一自动过期时间，因为行政归档、论文复核和合同审计的留存要求不同。清理前请先创建并校验完整工作区备份，或单独下载需要长期保存的交付包，再从存储中心明确删除。</p>
      </section>
    </main>
  );
}

function StorageList({
  title,
  description,
  empty,
  truncated,
  children,
}: {
  title: string;
  description: string;
  empty: string;
  truncated: boolean;
  children: ReactNode;
}) {
  const hasItems = Array.isArray(children) ? children.length > 0 : Boolean(children);
  return <section className="settings-card storage-list-card">
    <div><h2>{title}</h2><p>{description}</p></div>
    <div className="storage-list">{hasItems ? children : <span className="storage-empty">{empty}</span>}</div>
    {truncated && <p className="storage-truncated">仅显示占用最大的 50 项。</p>}
  </section>;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = units[0];
  for (let index = 1; index < units.length && value >= 1024; index += 1) {
    value /= 1024;
    unit = units[index];
  }
  return `${new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 1 }).format(value)} ${unit}`;
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(value));
}

function readableError(caught: unknown): string {
  if (caught instanceof ApiError) return errorLabels[caught.code] ?? caught.message;
  if (caught instanceof Error) return caught.message;
  return "读取本地服务信息失败。";
}

function clearMatchingLocalRecord(key: string, field: string, id: string): void {
  try {
    const value = window.localStorage.getItem(key);
    if (!value) return;
    const parsed = JSON.parse(value) as Record<string, unknown>;
    if (parsed[field] === id) window.localStorage.removeItem(key);
  } catch {
    // A malformed recovery record should not block an already-confirmed deletion.
  }
}
