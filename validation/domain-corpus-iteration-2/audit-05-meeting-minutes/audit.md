# DocAlign 格式化审计

- 任务：`corpus-05-meeting-minutes`
- 源文件：`05-meeting-minutes.docx`
- 输出文件：`05-meeting-minutes-紧凑信息.docx`
- 验证状态：**通过**
- 段落 / 表格 / 图片：13 / 1 / 0
- 自动排版分段：0 处（13 → 13 段）
- 格式操作：64
- 实际变更：52
- 规则来源：preset

## 角色统计

- body: 4
- heading_1: 3
- list_item: 4
- title: 1
- unknown: 1

## 警告与验证问题

- `TABLE_WIDTH_EXCEEDS_PAGE`：Estimated table width exceeds the printable page width.
- `LOW_CONFIDENCE_ROLE`：Paragraph role confidence is 0.58.
- `NUMBERING_LAYOUT_PRESERVED`：Existing list numbering, paragraph style, alignment, and indents were preserved; compatible font and spacing rules were applied directly.
- `NUMBERING_LAYOUT_PRESERVED`：Existing list numbering, paragraph style, alignment, and indents were preserved; compatible font and spacing rules were applied directly.
- `NUMBERING_LAYOUT_PRESERVED`：Existing list numbering, paragraph style, alignment, and indents were preserved; compatible font and spacing rules were applied directly.
- `NUMBERING_LAYOUT_PRESERVED`：Existing list numbering, paragraph style, alignment, and indents were preserved; compatible font and spacing rules were applied directly.

## 编译假设

- Compact left-aligned hierarchy is used for resumes, minutes, and manuals.
- All visual cleanup and content-integrity safeguards remain enabled.
