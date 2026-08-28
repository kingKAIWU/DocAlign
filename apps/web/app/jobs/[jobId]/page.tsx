"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { JobOutcomeSummary } from "@/components/job-outcome-summary";
import { api, ApiError, apiUrl } from "@/lib/api";
import { jobStatusLabels } from "@/lib/messages";
import type { Job } from "@/lib/types";

export default function JobPage() {
  const params = useParams<{ jobId: string }>();
  const [job, setJob] = useState<Job | null>(null);
  const [connectionMessage, setConnectionMessage] = useState("");

  useEffect(() => {
    let active = true;
    let timeout: number | undefined;
    let controller: AbortController | undefined;
    let failureCount = 0;
    const refresh = async () => {
      controller?.abort();
      controller = new AbortController();
      const requestTimeout = window.setTimeout(() => controller?.abort(), 10_000);
      try {
        const next = await api.job(params.jobId, controller.signal);
        if (!active) return;
        failureCount = 0;
        setConnectionMessage("");
        setJob(next);
        if (!["completed", "failed"].includes(next.status)) {
          timeout = window.setTimeout(refresh, 800);
        }
      } catch (caught) {
        if (!active) return;
        if (caught instanceof ApiError && caught.status === 404) {
          setConnectionMessage("找不到这个本地任务，可能已被删除。");
          return;
        }
        failureCount += 1;
        setConnectionMessage("进度连接暂时中断，任务仍在本地运行，正在自动恢复…");
        const retryDelay = Math.min(5_000, 500 * 2 ** Math.min(failureCount - 1, 4));
        timeout = window.setTimeout(refresh, retryDelay);
      } finally {
        window.clearTimeout(requestTimeout);
      }
    };
    void refresh();
    return () => {
      active = false;
      if (timeout) window.clearTimeout(timeout);
      controller?.abort();
    };
  }, [params.jobId]);

  return (
    <main className="settings-shell job-page">
      <Link className="back-link" href="/">← 返回工作台</Link>
      <p className="eyebrow">PROCESSING JOB</p>
      <h1>任务状态</h1>
      <section className="settings-card">
        {connectionMessage && <div className="job-connection-note" role="status">{connectionMessage}</div>}
        <div className="setting-row"><span>任务 ID</span><code>{params.jobId}</code></div>
        <div className="setting-row"><span>状态</span><b>{job ? jobStatusLabels[job.status] : "读取中"}</b></div>
        <div className="setting-row"><span>进度</span><b>{job?.progress ?? 0}%</b></div>
        {job?.error_code && <div className="job-error"><strong>{job.error_code}</strong><p>{job.error_message}</p></div>}
        {job?.status === "completed" && (
          <>
            <div className="standalone-outcome"><JobOutcomeSummary job={job} /></div>
            <div className="download-row standalone">
              <a className="button primary" href={apiUrl(job.output_document_url)}>下载格式化 DOCX</a>
              <a className="button secondary" href={apiUrl(job.audit_json_url)}>下载审计 JSON</a>
              <a className="text-link" href={apiUrl(job.audit_markdown_url)}>下载审计 Markdown</a>
            </div>
          </>
        )}
      </section>
    </main>
  );
}
