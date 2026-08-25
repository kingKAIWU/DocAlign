# DocAlign 格式化审计

- 任务：`corpus-02-academic-paper`
- 源文件：`02-academic-paper.docx`
- 输出文件：`02-academic-paper-默认整理.docx`
- 验证状态：**通过**
- 段落 / 表格 / 图片：17 / 1 / 1
- 自动排版分段：1 处（16 → 17 段）
- 格式操作：78
- 实际变更：65
- 规则来源：preset

## 角色统计

- abstract_body: 1
- abstract_heading: 1
- author_info: 1
- bibliography_entry: 1
- bibliography_heading: 1
- body: 3
- figure_caption: 1
- heading_1: 2
- heading_2: 1
- keywords: 1
- table_caption: 1
- title: 1
- unknown: 2

## 警告与验证问题

- `TABLE_WIDTH_EXCEEDS_PAGE`：Estimated table width exceeds the printable page width.
- `LOW_CONFIDENCE_ROLE`：Paragraph role confidence is 0.58.
- `LOW_CONFIDENCE_ROLE`：Paragraph role confidence is 0.58.
- `PROTECTED_RUN_SKIPPED`：A protected run was preserved without direct font normalization.

## 编译假设

- All sections are normalized to A4 portrait with 20 mm margins.
- Chinese text uses SimSun, Latin text uses Times New Roman, and visible text is black.
- Highlights and Word character, paragraph, cell, and page backgrounds are removed.
- Titles remain distinguishable through conventional size and weight hierarchy.
