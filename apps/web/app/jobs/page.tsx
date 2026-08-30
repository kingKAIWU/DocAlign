import { Suspense } from "react";

import { JobPageClient } from "./job-page-client";

export default function JobPage() {
  return (
    <Suspense fallback={<main className="settings-shell job-page"><p>正在读取任务…</p></main>}>
      <JobPageClient />
    </Suspense>
  );
}
