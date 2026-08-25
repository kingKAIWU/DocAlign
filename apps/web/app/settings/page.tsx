"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { api, API_BASE } from "@/lib/api";
import type { Capabilities } from "@/lib/types";

export default function SettingsPage() {
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);

  useEffect(() => {
    api.capabilities().then(setCapabilities).catch(() => setCapabilities(null));
  }, []);

  return (
    <main className="settings-shell">
      <Link className="back-link" href="/">← 返回工作台</Link>
      <p className="eyebrow">LOCAL CONFIGURATION</p>
      <h1>设置与隐私边界</h1>
      <p className="settings-intro">密钥只从后端环境读取，不进入浏览器、SQLite 或审计日志。</p>

      <section className="settings-card">
        <div className="setting-row"><span>API 地址</span><code>{API_BASE}</code></div>
        <div className="setting-row"><span>本地处理</span><b>{capabilities?.local_only ? "已启用" : "检查中"}</b></div>
        <div className="setting-row"><span>兼容模型</span><b>{capabilities?.llm_configured ? "已配置" : "未配置"}</b></div>
        <div className="setting-row"><span>上传限制</span><b>{capabilities?.max_upload_mb ?? 20} MB</b></div>
      </section>

      <section className="settings-card prose-card">
        <h2>启用自然语言规则编译</h2>
        <p>在项目根目录的 <code>.env</code> 设置下列变量，然后重启 API。完整文档不会发送给模型；只发送格式要求和结构统计。</p>
        <pre>{`DOCALIGN_LLM_BASE_URL=https://your-endpoint.example/v1
DOCALIGN_LLM_API_KEY=...
DOCALIGN_LLM_MODEL=your-model
DOCALIGN_LLM_JSON_SCHEMA_MODE=auto`}</pre>
      </section>

      <section className="settings-card prose-card">
        <h2>数据保留</h2>
        <p>上传文件、分析结果、规则、任务和输出保存在本机 <code>DOCALIGN_DATA_DIR</code>。删除工作区文档时，关联数据会一并删除。</p>
      </section>
    </main>
  );
}

