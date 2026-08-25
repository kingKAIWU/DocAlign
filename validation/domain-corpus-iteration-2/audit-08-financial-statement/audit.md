# DocAlign 格式化审计

- 任务：`corpus-08-financial-statement`
- 源文件：`08-financial-statement.docx`
- 输出文件：`08-financial-statement-宽表优先.docx`
- 验证状态：**通过**
- 段落 / 表格 / 图片：6 / 1 / 0
- 自动排版分段：0 处（6 → 6 段）
- 格式操作：39
- 实际变更：44
- 规则来源：preset

## 角色统计

- body: 1
- heading_1: 2
- unknown: 3

## 警告与验证问题

- `TABLE_WIDTH_EXCEEDS_PAGE`：Estimated table width exceeds the printable page width.
- `LOW_CONFIDENCE_ROLE`：Paragraph role confidence is 0.58.

## 编译假设

- Existing landscape sections are preserved so wide tables stay readable.
- All visual cleanup and content-integrity safeguards remain enabled.
