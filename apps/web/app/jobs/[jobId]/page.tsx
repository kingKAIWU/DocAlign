"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { api, apiUrl } from "@/lib/api";
import type { Job } from "@/lib/types";

export default function JobPage() {
  const params = useParams<{ jobId: string }>();
  const [job, setJob] = useState<Job | null>(null);

  useEffect(() => {
    let active = true;
    let timeout: number | undefined;
    const refresh = async () => {
      const next = await api.job(params.jobId);
      if (!active) return;
      setJob(next);
      if (!["completed", "failed"].includes(next.status)) {
        timeout = window.setTimeout(refresh, 800);
      }
    };
    void refresh();
    return () => {
      active = false;
      if (timeout) window.clearTimeout(timeout);
    };
  }, [params.jobId]);

  return (
    <main className="settings-shell job-page">
      <Link className="back-link" href="/">← 返回工作台</Link>
      <p className="eyebrow">PROCESSING JOB</p>
      <h1>任务状态</h1>
      <section className="settings-card">
        <div className="setting-row"><span>任务 ID</span><code>{params.jobId}</code></div>
        <div className="setting-row"><span>状态</span><b>{job?.status ?? "读取中"}</b></div>
        <div className="setting-row"><span>进度</span><b>{job?.progress ?? 0}%</b></div>
        {job?.error_code && <div className="job-error"><strong>{job.error_code}</strong><p>{job.error_message}</p></div>}
        {job?.status === "completed" && (
          <div className="download-row standalone">
            <a className="button primary" href={apiUrl(job.output_document_url)}>下载格式化 DOCX</a>
            <a className="button secondary" href={apiUrl(job.audit_json_url)}>下载审计 JSON</a>
            <a className="text-link" href={apiUrl(job.audit_markdown_url)}>下载审计 Markdown</a>
          </div>
        )}
      </section>
    </main>
  );
}

