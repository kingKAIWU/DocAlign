# DocAlign 格式化审计

- 任务：`corpus-03-business-report`
- 源文件：`03-business-report.docx`
- 输出文件：`03-business-report-宽表优先.docx`
- 验证状态：**通过**
- 段落 / 表格 / 图片：12 / 1 / 0
- 自动排版分段：0 处（12 → 12 段）
- 格式操作：57
- 实际变更：53
- 规则来源：preset

## 角色统计

- heading_1: 4
- list_item: 3
- title: 1
- unknown: 4

## 警告与验证问题

- `TABLE_WIDTH_EXCEEDS_PAGE`：Estimated table width exceeds the printable page width.
- `LOW_CONFIDENCE_ROLE`：Paragraph role confidence is 0.58.
- `LOW_CONFIDENCE_ROLE`：Paragraph role confidence is 0.58.
- `NUMBERING_LAYOUT_PRESERVED`：Existing list numbering, paragraph style, alignment, and indents were preserved; compatible font and spacing rules were applied directly.
- `NUMBERING_LAYOUT_PRESERVED`：Existing list numbering, paragraph style, alignment, and indents were preserved; compatible font and spacing rules were applied directly.
- `NUMBERING_LAYOUT_PRESERVED`：Existing list numbering, paragraph style, alignment, and indents were preserved; compatible font and spacing rules were applied directly.

## 编译假设

- Existing landscape sections are preserved so wide tables stay readable.
- All visual cleanup and content-integrity safeguards remain enabled.
