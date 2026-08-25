# DocAlign 格式化审计

- 任务：`corpus-07-training-manual`
- 源文件：`07-training-manual.docx`
- 输出文件：`07-training-manual-紧凑信息.docx`
- 验证状态：**通过**
- 段落 / 表格 / 图片：16 / 1 / 1
- 自动排版分段：0 处（16 → 16 段）
- 格式操作：74
- 实际变更：55
- 规则来源：preset

## 角色统计

- body: 2
- figure_caption: 1
- heading_1: 3
- heading_2: 1
- list_item: 7
- title: 1
- unknown: 1

## 警告与验证问题

- `TABLE_WIDTH_EXCEEDS_PAGE`：Estimated table width exceeds the printable page width.
- `LOW_CONFIDENCE_ROLE`：Paragraph role confidence is 0.58.
- `NUMBERING_LAYOUT_PRESERVED`：Existing list numbering, paragraph style, alignment, and indents were preserved; compatible font and spacing rules were applied directly.
- `NUMBERING_LAYOUT_PRESERVED`：Existing list numbering, paragraph style, alignment, and indents were preserved; compatible font and spacing rules were applied directly.
- `NUMBERING_LAYOUT_PRESERVED`：Existing list numbering, paragraph style, alignment, and indents were preserved; compatible font and spacing rules were applied directly.
- `NUMBERING_LAYOUT_PRESERVED`：Existing list numbering, paragraph style, alignment, and indents were preserved; compatible font and spacing rules were applied directly.
- `NUMBERING_LAYOUT_PRESERVED`：Existing list numbering, paragraph style, alignment, and indents were preserved; compatible font and spacing rules were applied directly.
- `NUMBERING_LAYOUT_PRESERVED`：Existing list numbering, paragraph style, alignment, and indents were preserved; compatible font and spacing rules were applied directly.
- `NUMBERING_LAYOUT_PRESERVED`：Existing list numbering, paragraph style, alignment, and indents were preserved; compatible font and spacing rules were applied directly.
- `PROTECTED_RUN_SKIPPED`：A protected run was preserved without direct font normalization.

## 编译假设

- Compact left-aligned hierarchy is used for resumes, minutes, and manuals.
- All visual cleanup and content-integrity safeguards remain enabled.
