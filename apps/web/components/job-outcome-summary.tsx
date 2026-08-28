import type { Job } from "@/lib/types";

const changeCategoryLabels: Record<string, string> = {
  structure: "结构重排",
  page_layout: "页面版式",
  paragraph_styles: "标题与段落",
  text_font: "文字与字体",
  tables: "表格",
  header_footer: "页眉页脚",
  visual_cleanup: "颜色与底纹清理",
  other: "其他格式",
};

type JobOutcomeSummaryProps = {
  job: Job;
  onReview?: () => void;
};

export function JobOutcomeSummary({ job, onReview }: JobOutcomeSummaryProps) {
  const summary = job.result_summary;
  if (!summary) {
    return (
      <p>
        源文件未修改；{job.auto_layout_splits > 0
          ? `已安全重构 ${job.auto_layout_splits} 处连续正文。`
          : "未发现需要拆分的连续正文。"}
      </p>
    );
  }

  const categories = Object.entries(summary.change_categories)
    .filter(([, count]) => count > 0)
    .sort((left, right) => right[1] - left[1]);
  const paragraphChange =
    summary.auto_layout_splits > 0 &&
    summary.paragraphs_before !== null &&
    summary.paragraphs_after !== null
      ? `${summary.paragraphs_before} → ${summary.paragraphs_after} 段`
      : null;

  return (
    <section className="outcome-summary" aria-label="排版结果摘要">
      <div className="outcome-assurance">
        <span className={summary.validation_passed ? "passed" : "failed"}>
          {summary.validation_passed ? "格式验证通过" : "格式验证需检查"}
        </span>
        <span className={summary.content_integrity_passed ? "passed" : "failed"}>
          {summary.content_integrity_passed ? "原文与受保护结构通过" : "内容保护需检查"}
        </span>
      </div>
      <div className="outcome-metrics">
        <div>
          <strong>{summary.changed_mutations}</strong>
          <span>项实际格式调整</span>
          <small>{summary.format_operations} 个规则动作已执行</small>
        </div>
        <div className={summary.remaining_review_items > 0 ? "needs-review" : ""}>
          <strong>{summary.remaining_review_items}</strong>
          <span>项仍建议人工复核</span>
          <small>{summary.warning_count} 条执行提醒</small>
        </div>
      </div>
      {categories.length > 0 && (
        <div className="outcome-changes">
          <strong>改动分布</strong>
          <ul>
            {categories.map(([category, count]) => (
              <li key={category}>
                <span>{changeCategoryLabels[category] ?? changeCategoryLabels.other}</span>
                <b>{count}</b>
              </li>
            ))}
          </ul>
        </div>
      )}
      {summary.auto_layout_splits > 0 && (
        <p className="outcome-note">
          已将 {summary.auto_layout_splits} 处连续正文安全拆为真实段落
          {paragraphChange ? `（${paragraphChange}）` : ""}。
        </p>
      )}
      {summary.remaining_review_items > 0 && onReview && (
        <button type="button" className="outcome-review-button" onClick={onReview}>
          查看待确认段落
        </button>
      )}
    </section>
  );
}
