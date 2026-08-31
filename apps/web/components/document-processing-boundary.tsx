import type { DocumentProcessingBoundary } from "@/lib/types";

const featureLabels: Record<string, string> = {
  field: "动态字段、目录或交叉引用",
  header_footer_field: "页眉页脚动态字段",
  equation: "公式",
  drawing: "图片或绘图对象",
  hyperlink: "超链接",
  bookmark: "书签",
  content_control: "内容控件",
  merged_table: "合并单元格表格",
  nested_table: "嵌套表格",
  unknown_ooxml: "未识别的顶层 Word 结构",
  text_box: "文本框",
  footnote: "脚注",
  endnote: "尾注",
  comment: "批注",
  embedded_object: "嵌入对象或 ActiveX",
  macro: "宏项目",
  external_link: "外部链接关系",
  multiple_sections: "多分节版式",
};

const handlingLabels: Record<string, string> = {
  format_and_validate: "参与格式化并验证",
  preserve_and_validate: "保留并验证完整性",
  preserve_only: "只保留，不做专门格式化",
};

type DocumentProcessingBoundaryProps = {
  boundary: DocumentProcessingBoundary;
  mode?: "preflight" | "result";
  acknowledged?: boolean;
  onAcknowledgedChange?: (acknowledged: boolean) => void;
};

export function DocumentProcessingBoundaryCard({
  boundary,
  mode = "preflight",
  acknowledged = false,
  onAcknowledgedChange,
}: DocumentProcessingBoundaryProps) {
  const items = boundary.items ?? [];
  const needsReview = boundary.review_feature_count > 0;
  const title = needsReview
    ? `${boundary.review_feature_count} 类复杂内容需人工核对`
    : boundary.detected_feature_count > 0
      ? `已识别 ${boundary.detected_feature_count} 类受保护内容`
      : "文档处理范围已预检";

  return (
    <section
      className={`processing-boundary ${needsReview ? "needs-review" : "ready"}`}
      aria-label={mode === "preflight" ? "文档处理范围预检" : "源文档处理边界"}
    >
      <header>
        <div>
          <strong>{title}</strong>
          <span>
            {needsReview
              ? "内容会按标注方式保留或验证，但自动排版不能替代 Word/WPS 中的最终检查。"
              : "未发现会阻止常规自动排版的复杂内容；输出仍会经过内容与结构验证。"}
          </span>
        </div>
        <b>{needsReview ? "需确认" : "可继续"}</b>
      </header>
      {items.length > 0 && (
        <details open={needsReview}>
          <summary>查看处理范围（{boundary.detected_feature_count} 类）</summary>
          <ul>
            {items.map((item) => {
              const locators = item.locators ?? [];
              return (
                <li
                  className={item.review_required ? "review" : "information"}
                  key={item.code}
                >
                  <div>
                    <strong>{featureLabels[item.code] ?? item.code}</strong>
                    <span>
                      {item.count} 处 · {handlingLabels[item.handling] ?? item.handling}
                    </span>
                  </div>
                  <b>{item.review_required ? "人工核对" : "已保护"}</b>
                  {locators.length > 0 && (
                    <small>
                      位置：{locators.join("、")}
                      {item.locators_truncated ? "…" : ""}
                    </small>
                  )}
                </li>
              );
            })}
          </ul>
        </details>
      )}
      {mode === "preflight" && boundary.acknowledgment_required && (
        <label className="processing-boundary-acknowledgment">
          <input
            type="checkbox"
            checked={acknowledged}
            onChange={(event) => onAcknowledgedChange?.(event.target.checked)}
          />
          <span>我已了解这些复杂内容需要在 Word/WPS 中逐项核对</span>
        </label>
      )}
    </section>
  );
}
