"use client";

import { useEffect, useRef, useState } from "react";

import { changePropertyLabel } from "@/components/job-outcome-summary";
import { apiUrl } from "@/lib/api";
import { renderSafeDocxPreview } from "@/lib/docx-preview";
import type { JobResultSummary } from "@/lib/types";

type PreviewState = "idle" | "loading" | "ready" | "error";

type DocumentComparisonDialogProps = {
  open: boolean;
  sourcePath: string;
  outputPath: string;
  summary: JobResultSummary | null;
  onClose: () => void;
  onLocate?: (locator: string) => void;
};

const previewStateLabels: Record<PreviewState, string> = {
  idle: "等待打开",
  loading: "正在生成本地预览…",
  ready: "预览已就绪",
  error: "浏览器预览失败，请下载 DOCX 复核",
};

export function DocumentComparisonDialog({
  open,
  sourcePath,
  outputPath,
  summary,
  onClose,
  onLocate,
}: DocumentComparisonDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const sourceRef = useRef<HTMLDivElement>(null);
  const outputRef = useRef<HTMLDivElement>(null);
  const synchronizingRef = useRef(false);
  const [sourceState, setSourceState] = useState<PreviewState>("idle");
  const [outputState, setOutputState] = useState<PreviewState>("idle");
  const [syncScroll, setSyncScroll] = useState(true);
  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "");
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const sourceTarget = sourceRef.current;
    const outputTarget = outputRef.current;
    const sourceUrl = apiUrl(sourcePath);
    const outputUrl = apiUrl(outputPath);
    if (!sourceTarget || !outputTarget || !sourceUrl || !outputUrl) return;

    let cancelled = false;
    const controller = new AbortController();
    sourceTarget.replaceChildren();
    outputTarget.replaceChildren();
    setSourceState("loading");
    setOutputState("loading");

    async function fetchPreview(url: string): Promise<Blob> {
      const response = await fetch(url, { signal: controller.signal, cache: "no-store" });
      if (!response.ok) throw new Error(`Preview failed: ${response.status}`);
      return response.blob();
    }

    async function renderPreview(
      result: PromiseSettledResult<Blob>,
      target: HTMLElement,
      className: string,
      updateState: (state: PreviewState) => void,
    ) {
      if (cancelled) return;
      if (result.status === "rejected") {
        if (!(result.reason instanceof DOMException && result.reason.name === "AbortError")) {
          updateState("error");
        }
        return;
      }
      try {
        await renderSafeDocxPreview(result.value, target, className);
        if (!cancelled) updateState("ready");
      } catch (caught) {
        if (!cancelled && !(caught instanceof DOMException && caught.name === "AbortError")) {
          updateState("error");
        }
      }
    }

    void (async () => {
      const [sourceResult, outputResult] = await Promise.allSettled([
        fetchPreview(sourceUrl),
        fetchPreview(outputUrl),
      ]);
      // docx-preview is invoked sequentially so its style-container writes cannot race.
      await renderPreview(sourceResult, sourceTarget, "docx-before", setSourceState);
      await renderPreview(outputResult, outputTarget, "docx-after", setOutputState);
    })();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [open, outputPath, reloadToken, sourcePath]);

  useEffect(() => {
    if (!open) return;
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleEscape);
    return () => window.removeEventListener("keydown", handleEscape);
  }, [onClose, open]);

  function synchronizeScroll(source: HTMLElement, target: HTMLElement) {
    if (!syncScroll || synchronizingRef.current) return;
    const sourceRange = source.scrollHeight - source.clientHeight;
    const targetRange = target.scrollHeight - target.clientHeight;
    if (sourceRange <= 0 || targetRange <= 0) return;
    synchronizingRef.current = true;
    target.scrollTop = (source.scrollTop / sourceRange) * targetRange;
    window.requestAnimationFrame(() => {
      synchronizingRef.current = false;
    });
  }

  const details = summary?.change_details ?? [];
  const previewFailed = sourceState === "error" || outputState === "error";

  if (!open) return null;

  return (
    <dialog
      ref={dialogRef}
      className="comparison-dialog"
      aria-labelledby="comparison-title"
      aria-describedby="comparison-boundary"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClose={() => {
        if (open) onClose();
      }}
    >
      <div className="comparison-shell">
        <header className="comparison-header">
          <div>
            <p className="eyebrow">REVIEW BEFORE DOWNLOAD</p>
            <h2 id="comparison-title">格式前后对照</h2>
          </div>
          <div className="comparison-header-actions">
            <label>
              <input
                type="checkbox"
                checked={syncScroll}
                onChange={(event) => setSyncScroll(event.target.checked)}
              />
              同步滚动
            </label>
            <button type="button" className="button secondary compact" onClick={onClose} autoFocus>
              关闭对照
            </button>
          </div>
        </header>

        <section className="comparison-assurance" aria-label="对照验证摘要">
          {summary ? (
            <>
              <span className={summary.validation_passed ? "passed" : "failed"}>
                {summary.validation_passed ? "格式验证通过" : "格式验证需检查"}
              </span>
              <span className={summary.content_integrity_passed ? "passed" : "failed"}>
                {summary.content_integrity_passed ? "原文与受保护结构通过" : "内容保护需检查"}
              </span>
            </>
          ) : (
            <span className="unavailable">旧任务未保存轻量验证摘要</span>
          )}
          <b>{summary?.changed_mutations ?? 0} 项格式调整</b>
          <p id="comparison-boundary">
            两侧均为本机浏览器近似渲染，用于人工核对版式；分页、字体替代、形状和文本框可能与 Word/WPS 不同。
            机器结论仍以 OOXML 重开验证和审计报告为准。
          </p>
        </section>

        <div className="comparison-grid">
          <ComparisonPane
            title="源文件"
            subtitle="保持不变"
            state={sourceState}
            previewRef={sourceRef}
            onScroll={() => {
              if (sourceRef.current && outputRef.current) {
                synchronizeScroll(sourceRef.current, outputRef.current);
              }
            }}
          />
          <ComparisonPane
            title="已验证输出"
            subtitle="待下载复核"
            state={outputState}
            previewRef={outputRef}
            onScroll={() => {
              if (outputRef.current && sourceRef.current) {
                synchronizeScroll(outputRef.current, sourceRef.current);
              }
            }}
          />
        </div>

        <footer className="comparison-footer">
          <div>
            <strong>定位级改动</strong>
            <span>
              {details.length} 项轻量摘要
              {summary?.change_details_truncated ? ` / 共 ${summary.changed_mutations} 项` : ""}
            </span>
          </div>
          {details.length > 0 ? (
            <ol>
              {details.slice(0, 12).map((detail, index) => {
                const canLocate = Boolean(
                  onLocate && detail.locator && /^(?:p|t|u)\d+(?:\.|$)/.test(detail.locator),
                );
                return (
                  <li key={`${detail.locator ?? "document"}-${detail.property_path}-${index}`}>
                    {canLocate ? (
                      <button
                        type="button"
                        onClick={() => {
                          onLocate?.(detail.locator!);
                          onClose();
                        }}
                        aria-label={`回到结构位置 ${detail.locator}`}
                      >
                        {detail.locator}
                      </button>
                    ) : (
                      <code>{detail.locator ?? "全文"}</code>
                    )}
                    <strong>{changePropertyLabel(detail.property_path)}</strong>
                    <span>{detail.before_value ?? "未设置"} → {detail.after_value ?? "已移除"}</span>
                  </li>
                );
              })}
            </ol>
          ) : (
            <p>当前任务没有可展示的定位级改动；仍可并排核对整体版式。</p>
          )}
          {previewFailed && (
            <button
              type="button"
              className="button secondary compact"
              onClick={() => setReloadToken((value) => value + 1)}
            >
              重新加载预览
            </button>
          )}
        </footer>
      </div>
    </dialog>
  );
}

function ComparisonPane({
  title,
  subtitle,
  state,
  previewRef,
  onScroll,
}: {
  title: string;
  subtitle: string;
  state: PreviewState;
  previewRef: React.RefObject<HTMLDivElement | null>;
  onScroll: () => void;
}) {
  return (
    <section className="comparison-pane" aria-label={`${title}浏览器预览`}>
      <header>
        <div><strong>{title}</strong><span>{subtitle}</span></div>
        <small className={state}>{previewStateLabels[state]}</small>
      </header>
      <div
        className="comparison-scroll"
        ref={previewRef}
        onScroll={onScroll}
        aria-label={`${title}预览滚动区`}
      />
    </section>
  );
}
