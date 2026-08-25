# DocAlign 格式化审计

- 任务：`corpus-01-government-notice`
- 源文件：`01-government-notice.docx`
- 输出文件：`01-government-notice-默认整理.docx`
- 验证状态：**通过**
- 段落 / 表格 / 图片：14 / 1 / 0
- 自动排版分段：1 处（13 → 14 段）
- 格式操作：68
- 实际变更：56
- 规则来源：preset

## 角色统计

- author_info: 1
- body: 4
- heading_1: 2
- heading_2: 1
- list_item: 3
- title: 1
- unknown: 2

## 警告与验证问题

- `TABLE_WIDTH_EXCEEDS_PAGE`：Estimated table width exceeds the printable page width.
- `LOW_CONFIDENCE_ROLE`：Paragraph role confidence is 0.58.
- `LOW_CONFIDENCE_ROLE`：Paragraph role confidence is 0.58.
- `NUMBERING_LAYOUT_PRESERVED`：Existing list numbering, paragraph style, alignment, and indents were preserved; compatible font and spacing rules were applied directly.
- `NUMBERING_LAYOUT_PRESERVED`：Existing list numbering, paragraph style, alignment, and indents were preserved; compatible font and spacing rules were applied directly.
- `NUMBERING_LAYOUT_PRESERVED`：Existing list numbering, paragraph style, alignment, and indents were preserved; compatible font and spacing rules were applied directly.

## 编译假设

- All sections are normalized to A4 portrait with 20 mm margins.
- Chinese text uses SimSun, Latin text uses Times New Roman, and visible text is black.
- Highlights and Word character, paragraph, cell, and page backgrounds are removed.
- Titles remain distinguishable through conventional size and weight hierarchy.
