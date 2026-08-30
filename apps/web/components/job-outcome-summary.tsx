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

const reviewStatusLabels: Record<string, string> = {
  manual_review: "人工复核",
  unsupported: "暂不支持",
};

type JobOutcomeSummaryProps = {
  job: Job;
  onReview?: () => void;
  onLocate?: (locator: string) => void;
  onCompare?: () => void;
};

const changePropertyLabels: Record<string, string> = {
  "paragraph.structure": "正文安全分段",
  "paragraph.style": "段落样式",
  "paragraph.alignment": "段落对齐",
  "section.layout": "页面版式",
  "table.format": "表格布局",
  "header.format": "页眉格式",
  "footer.format": "页脚格式",
  "footer.page_number": "页码",
  "visual_cleanup.text_color_hex": "文字颜色",
  "visual_cleanup.remove_text_highlight": "文字高亮",
  "visual_cleanup.remove_character_shading": "字符底纹",
  "visual_cleanup.remove_paragraph_shading": "段落底纹",
  "visual_cleanup.remove_table_cell_shading": "表格底纹",
  "visual_cleanup.remove_page_background": "页面背景",
};

export function changePropertyLabel(propertyPath: string): string {
  if (propertyPath.startsWith("styles.")) return "角色样式";
  if (propertyPath.startsWith("runs.")) return "文字字体与强调";
  return changePropertyLabels[propertyPath] ?? "其他格式";
}

export function JobOutcomeSummary({ job, onReview, onLocate, onCompare }: JobOutcomeSummaryProps) {
  const summary = job.result_summary;
  if (!summary) {
    return (
      <>
        <p>
          源文件未修改；{job.auto_layout_splits > 0
            ? `已安全重构 ${job.auto_layout_splits} 处连续正文。`
            : "未发现需要拆分的连续正文。"}
        </p>
        {onCompare && (
          <div className="outcome-actions">
            <button type="button" className="outcome-compare-button" onClick={onCompare}>
              查看格式前后对照
            </button>
          </div>
        )}
      </>
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
  const changeDetails = summary.change_details ?? [];
  const executionEvidence = summary.execution_evidence;
  const appliedPreset = executionEvidence?.applied_preset;
  const reviewRequirements = appliedPreset?.review_requirements ?? [];
  const acceptanceManualChecks = appliedPreset?.acceptance_manual_checks ?? [];
  const structureReviewItems = summary.structure_review_items ?? summary.remaining_review_items;
  const deliveryReviewItems = summary.delivery_review_items ?? (
    reviewRequirements.length +
    acceptanceManualChecks.length +
    (appliedPreset && !appliedPreset.matches_catalog_spec ? 1 : 0)
  );
  const isReferencePack = appliedPreset?.claim_level === "reference";
  const validationLabel = summary.validation_passed
    ? isReferencePack
      ? "已声明自动条款验证通过"
      : "格式验证通过"
    : "格式验证需检查";

  return (
    <section className="outcome-summary" aria-label="排版结果摘要">
      <div className="outcome-assurance">
        <span className={summary.validation_passed ? "passed" : "failed"}>
          {validationLabel}
        </span>
        <span className={summary.content_integrity_passed ? "passed" : "failed"}>
          {summary.content_integrity_passed ? "原文与受保护结构通过" : "内容保护需检查"}
        </span>
        {appliedPreset && (
          <span className={appliedPreset.matches_catalog_spec ? "passed" : "failed"}>
            {appliedPreset.matches_catalog_spec ? "目录规则原样执行" : "目录规则已被修改"}
          </span>
        )}
      </div>
      <div className="outcome-metrics">
        <div>
          <strong>{summary.changed_mutations}</strong>
          <span>项实际格式调整</span>
          <small>{summary.format_operations} 个规则动作已执行</small>
        </div>
        <div className={structureReviewItems > 0 ? "needs-review" : ""}>
          <strong>{structureReviewItems}</strong>
          <span>个结构段落待确认</span>
          <small>{summary.warning_count} 条执行提醒</small>
        </div>
        {appliedPreset && (
          <div className={deliveryReviewItems > 0 ? "needs-review" : ""}>
            <strong>{deliveryReviewItems}</strong>
            <span>项交付前人工核对</span>
            <small>{reviewRequirements.length} 条规范边界</small>
          </div>
        )}
      </div>
      {appliedPreset && (
        <section
          className={`outcome-rule-evidence ${appliedPreset.matches_catalog_spec ? "matched" : "modified"}`}
          aria-label="规则交付边界"
        >
          <header>
            <div>
              <strong>{appliedPreset.preset_name}</strong>
              <span>v{appliedPreset.pack_version} · {appliedPreset.scope_label}</span>
            </div>
            <b>{appliedPreset.matches_catalog_spec ? "规则一致" : "规则已修改"}</b>
          </header>
          {executionEvidence && (
            <p className="outcome-rule-identity">
              引擎 {executionEvidence.engine_version} · 规则 SHA-256 {executionEvidence.spec_sha256}
            </p>
          )}
          {!appliedPreset.matches_catalog_spec && (
            <div className="outcome-rule-warning" role="alert">
              当前执行规则与目录原始版本不同；原参考包的自动覆盖和验收结论不能直接代表本次输出。
            </div>
          )}
          {(appliedPreset.source_references?.length ?? 0) > 0 && (
            <ul className="outcome-rule-sources" aria-label="规则公开来源">
              {appliedPreset.source_references?.map((source) => (
                <li key={`${source.title}-${source.url}`}>
                  <a href={source.url} target="_blank" rel="noreferrer">
                    {source.title}
                  </a>
                  {source.version && <span> · {source.version}</span>}
                </li>
              ))}
            </ul>
          )}
          {(reviewRequirements.length > 0 || acceptanceManualChecks.length > 0) && (
            <details className="outcome-rule-review" open>
              <summary>交付前核对清单（{deliveryReviewItems}）</summary>
              {reviewRequirements.length > 0 && (
                <ul>
                  {reviewRequirements.map((item) => (
                    <li key={`${item.requirement_id}-${item.requirement}`}>
                      <span className={`coverage-status ${item.status}`}>
                        {reviewStatusLabels[item.status] ?? item.status}
                      </span>
                      <div>
                        <strong>{item.requirement_id} · {item.requirement}</strong>
                        <p>{item.implementation_note}</p>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
              {acceptanceManualChecks.length > 0 && (
                <div className="outcome-acceptance-checks">
                  <strong>人工验收步骤</strong>
                  <ul>
                    {acceptanceManualChecks.map((item) => <li key={item}>{item}</li>)}
                  </ul>
                </div>
              )}
            </details>
          )}
          {(appliedPreset.limitations?.length ?? 0) > 0 && (
            <details className="outcome-rule-limitations">
              <summary>查看未覆盖与限制（{appliedPreset.limitations?.length ?? 0}）</summary>
              <ul>{appliedPreset.limitations?.map((item) => <li key={item}>{item}</li>)}</ul>
            </details>
          )}
        </section>
      )}
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
      {changeDetails.length > 0 && (
        <details className="outcome-details">
          <summary>
            查看具体改动（{changeDetails.length}{summary.change_details_truncated ? ` / ${summary.changed_mutations}` : ""}）
          </summary>
          <ol>
            {changeDetails.map((detail, index) => {
              const canLocate = Boolean(
                detail.locator && onLocate && /^(?:p|t|u)\d+(?:\.|$)/.test(detail.locator),
              );
              return (
                <li key={`${detail.locator ?? "document"}-${detail.property_path}-${index}`}>
                  <div className="outcome-detail-heading">
                    {canLocate ? (
                      <button
                        type="button"
                        onClick={() => onLocate?.(detail.locator!)}
                        aria-label={`定位到 ${detail.locator}`}
                      >
                        {detail.locator}
                      </button>
                    ) : (
                      <code>{detail.locator ?? "全文"}</code>
                    )}
                    <strong>{changePropertyLabel(detail.property_path)}</strong>
                  </div>
                  <div className="outcome-detail-values">
                    <span>{detail.before_value ?? "未设置"}</span>
                    <i aria-hidden="true">→</i>
                    <b>{detail.after_value ?? "已移除"}</b>
                  </div>
                </li>
              );
            })}
          </ol>
          {summary.change_details_truncated && (
            <p>为保持任务恢复轻量，仅展示前 32 项；完整改动保留在审计 JSON。</p>
          )}
        </details>
      )}
      {summary.auto_layout_splits > 0 && (
        <p className="outcome-note">
          已将 {summary.auto_layout_splits} 处连续正文安全拆为真实段落
          {paragraphChange ? `（${paragraphChange}）` : ""}。
        </p>
      )}
      {(onCompare || (structureReviewItems > 0 && onReview)) && (
        <div className="outcome-actions">
          {onCompare && (
            <button type="button" className="outcome-compare-button" onClick={onCompare}>
              查看格式前后对照
            </button>
          )}
          {structureReviewItems > 0 && onReview && (
            <button type="button" className="outcome-review-button" onClick={onReview}>
              查看待确认段落
            </button>
          )}
        </div>
      )}
    </section>
  );
}
