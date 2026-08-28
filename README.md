# DocAlign

DocAlign 是一个本地优先、确定性执行的 DOCX 自动排版与格式合规工具。它先识别文档结构，
再按版本化规则修改 Word 格式，最后重新打开输出文件验证内容、结构和格式。模型只用于可选的
语义复核与自然语言规则编译，不直接编辑 DOCX。

当前版本：`0.1.0`

## 核心能力

### 两种并列的整理模式

- **默认整理模式**：无需模型，按文档类型选择常规文档、紧凑信息、合同条款或宽表优先方案。
- 每个整理方案都携带版本、维护方、复核日期、能力覆盖和明确限制；当前内置方案均标记为
  “通用方案 · 非机构合规”。
- **自然语言编译模式**：把“正文宋体小四、标题黑体、清除所有背景”等要求编译为
  `FormattingSpec v1`，并展示已映射能力、假设、歧义和暂不支持项。
- 用户规则始终优先于预设规则；高级用户也可以直接编辑 JSON/YAML 规则。

### 自动结构排版

- 确定性识别封面、主副标题、作者、摘要、关键词、一至四级标题、正文、列表、图表题、
  参考文献和附录。
- 可选兼容模型逐段复核低置信度语义结果，用户可以在排版前手动修正角色。
- 可将单段内的手动换行转换为真实 Word 段落，并按完整句子边界安全拆分过长正文。
- 字段、图片、公式、超链接、书签、编号、内容控件和未知 OOXML 所在段落不会被自动拆分。
- 支持直接粘贴纯文本，生成带真实标题、正文和列表结构的 DOCX 草稿。

### 常用 Word 格式整理

- 页面大小、横竖方向、页边距、页眉页脚距离和页码。
- 中西文字体、字号、粗体、斜体、下划线、颜色和字符缩放。
- 对齐、行距、段前段后、首行缩进、左右缩进和分页属性。
- 表格字体字号、边框、宽度、自适应列宽、重复表头、行不可拆分和单元格垂直对齐。
- 图片独占段落居中、页眉页脚格式以及真实 Word `PAGE` 字段。
- 全文黑色文字、高亮、字符底纹、段落底纹、单元格底纹和页面背景清理。
- 源文件永不覆盖；格式化结果通过验证后才原子发布。

### 只读格式体检与格式画像

- **只做格式体检**：按照当前规则检查源文件，不修改文档、不创建格式化任务。
- **稳定位置编号**：问题和变更可定位到 `p3`、`s1` 或 `t1.r2.c3.p1.r1`，分别表示段落、
  分节和表格内的具体文字。
- **格式画像**：从 `styles.xml`、`document.xml`、`numbering.xml` 和页眉页脚中提取带
  R0001 编号、来源部件、证据、置信度和自动适用标记的 `format-manifest.v1`。
- 格式计划、变更记录、警告、合规问题和 Markdown 审计共享同一套位置编号。

### 落盘后的强制验证

- 重新打开输出 DOCX，验证正文、表格、节、图片和顶层结构数量。
- 比较正文、表格单元格、页眉页脚、字段、书签、图片、关系和未知 OOXML 的内容指纹。
- 额外保护批注、脚注、尾注、人员信息、`customXml` 和 glossary 部件。
- 逐段验证角色样式与字体，逐表验证字体字号、强调、颜色、边框、表头、分页和单元格对齐。
- 致命内容变化会阻止输出发布；可修复的格式偏差按照规则执行有限次数的确定性修复。
- 每次任务生成机器可读 `audit.json` 和便于人工复核的 `audit.md`。

### 本地任务与断连恢复

- FastAPI、SQLite、文件存储和 Next.js 工作台默认只监听 `127.0.0.1`。
- 后台格式化任务具有稳定任务 ID；页面刷新或短暂断连后恢复同一个任务，不会重复提交。
- 轮询请求带超时和取消机制，终态后立即停止，不创建重复连接。
- API 进程重启时会将中断任务标记为 `JOB_INTERRUPTED`，用户可明确重新提交。

### 面向用户的结果解释

- 完成页同时显示格式验证、原文与受保护结构校验、实际调整数量和剩余人工复核项。
- 格式变更按页面版式、标题与段落、文字与字体、表格、页眉页脚、视觉清理和结构重排分类。
- 任务结果通过版本化 OpenAPI 契约恢复；工作台可从结果摘要直接聚焦待确认段落。

## 内置整理方案

| 方案 | 适用场景 | 主要策略 |
| --- | --- | --- |
| 常规文档 | 通知、论文、普通报告 | A4 竖版、宋体/Times New Roman、正文小四、1.5 倍行距 |
| 紧凑信息 | 简历、会议纪要、操作手册 | 较小页边距、紧凑行距、左对齐层级 |
| 合同条款 | 合同和条款型法律文档 | 条款标题左对齐，保留连续条款阅读节奏 |
| 宽表优先 | 财务报表、多列表格 | 保留必要的横向分节，缩小页边距并自适应表格字号与列宽 |

所有整理方案都启用内容保护、黑色文字、突出与背景清理，以及保存后重开验证。

## 本地开发

### 环境要求

- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- Node.js 20.9 或更新版本
- pnpm 11

### 首次安装

```bash
cp .env.example .env
uv sync --frozen
pnpm install --frozen-lockfile
uv run alembic upgrade head
```

启动 API：

```bash
make api
```

另开终端启动网站：

```bash
make web
```

然后访问：

- 工作台：<http://127.0.0.1:3000>
- API 文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/api/v1/health>

macOS 的完整安装、启动和恢复说明见 [docs/MACOS.md](docs/MACOS.md)。服务不要绑定到公网地址。

## 工作台使用流程

面向普通用户的完整操作说明见 [DocAlign 网站使用说明](docs/WEB_USER_GUIDE.md)。

1. 上传 `.docx`，或者粘贴纯文本创建 Word 草稿。
2. 运行确定性结构分析；需要时再主动启用智能分析。
3. 检查并修正段落角色。
4. 选择默认整理方案，或使用自然语言编译格式要求。
5. 先运行“只做格式体检”，查看带位置编号的问题；也可以直接开始自动排版。
6. 阅读验证与改动摘要，处理剩余待确认段落，再下载 DOCX、JSON/Markdown 审计或格式画像。

## CLI

```bash
# 分析文档结构
uv run docalign analyze input.docx --out analysis.json

# 可选：使用兼容模型复核语义角色
uv run docalign analyze input.docx --smart --out smart-analysis.json

# 从 UTF-8 纯文本创建 DOCX 草稿
uv run docalign import-text input.txt --out draft.docx

# 确定性格式化；源文件与输出文件必须不同
uv run docalign format input.docx \
  --spec presets/generic-academic-cn.yaml \
  --out output.docx \
  --audit-dir output.docalign

# 检查现有 DOCX，并可写出 JSON 报告
uv run docalign validate output.docx \
  --spec presets/generic-academic-cn.yaml \
  --report validation.json

# 将自然语言要求编译成 FormattingSpec
uv run docalign spec compile \
  --instruction "正文宋体小四，一级标题黑体三号" \
  --out custom-spec.json
```

## 主要 API

| 方法与路径 | 用途 |
| --- | --- |
| `POST /api/v1/documents` | 上传 DOCX |
| `POST /api/v1/documents/from-text` | 从纯文本创建 DOCX |
| `POST /api/v1/documents/{id}/analyze` | 确定性或智能结构分析 |
| `PUT /api/v1/analyses/{id}/role-overrides` | 人工修正语义角色 |
| `POST /api/v1/specs/compile` | 编译自然语言格式规则 |
| `POST /api/v1/documents/{id}/compliance` | 只读格式体检 |
| `GET /api/v1/documents/{id}/format-manifest` | 导出格式画像 JSON |
| `POST /api/v1/jobs` | 创建格式化任务 |
| `GET /api/v1/presets` | 获取带来源、版本、覆盖与限制的规则包目录 |
| `GET /api/v1/jobs/{id}` | 查询任务状态、验证结论、改动分类和剩余复核项 |
| `GET /api/v1/jobs/{id}/output` | 下载验证通过的 DOCX |
| `GET /api/v1/jobs/{id}/audit.json` | 下载机器可读审计 |
| `GET /api/v1/jobs/{id}/audit.md` | 下载人工审计报告 |

完整接口定义位于 [schemas/openapi.v1.json](schemas/openapi.v1.json)，生成的独立数据模型包括：

- `formatting-spec.v1.schema.json`
- `document-ir.v1.schema.json`
- `audit-report.v1.schema.json`
- `compliance-report.v1.schema.json`
- `format-manifest.v1.schema.json`

## 质量门与回归

```bash
uv run ruff check .
uv run mypy
uv run pytest
uv run python scripts/export_schemas.py --check
uv run python -m scripts.export_openapi --check
uv run python -m scripts.benchmark_core
pnpm test
pnpm lint
pnpm build
pnpm e2e
```

Python 分支覆盖率低于 85% 会直接失败。综合测试覆盖中英混排、自动分段、图片、超链接、字段、
书签、公式、合并及嵌套表格、多节、横竖版、页眉页脚、视觉污染和未知顶层 OOXML。

八领域 DOCX 语料回归可单独运行：

```bash
uv run python tests/fixtures/domain-corpus/run_corpus.py \
  --source-dir tests/fixtures/domain-corpus/source \
  --output-dir .tmp/domain-corpus-output
```

语料包括政府通知、学术论文、经营报告、法律合同、会议纪要、个人简历、操作手册和财务宽表。

## 隐私与安全边界

- 上传文件、分析、规则、输出和审计默认存储在 `DOCALIGN_DATA_DIR`，不进入云存储。
- 不执行宏或嵌入对象，不直接覆盖源文件，不重建表格，不承诺浏览器预览与 Microsoft Word
  像素级一致。
- 自然语言编译仅发送格式要求和紧凑文档摘要；智能分析只在用户主动确认后发送段落文字与
  紧凑格式证据。
- DOCX 包、图片二进制、完整 OOXML、关系、书签和字段不会发送给模型。
- 未配置兼容模型时，默认整理、确定性分析、格式体检和完整格式化流程仍然可用。

更多信息见 [PRIVACY.md](PRIVACY.md)。开源项目对照、已吸收的工程思想和后续路线见
[docs/OPEN_SOURCE_BENCHMARK.md](docs/OPEN_SOURCE_BENCHMARK.md)。目标用户、市场定位、场景覆盖、
成熟度缺口和验收门槛见 [产品市场与成熟度审计](docs/PRODUCT_MARKET_AUDIT.md)。
