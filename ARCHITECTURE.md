# DocAlign v0.1 Architecture

## 核心原则

DocAlign 是“语义理解 + 格式编译 + 确定性执行”的本地文档系统，不是文本润色器。系统先把
DOCX 或纯文本草稿分析成带角色的 `DocumentIR`，再把预设、自然语言与结构化编辑统一为
`FormattingSpec v1`；确定性引擎据此修改 DOCX，随后从磁盘重开、验证和审计。

硬性不变量：

1. 模型不接收 DOCX 包、图片、关系或 OOXML，不产生 OOXML，也不执行文件操作。仅在用户
   明确选择“智能分析”后，语义模型会收到逐段文字与紧凑格式证据。
2. 源文档永不覆盖；输出通过校验后原子发布。
3. 文本、表格内容、图片、关系、节与受保护对象默认保持不变；只有显式启用自动排版时，
   才允许在内容指纹守卫下调整普通正文的段落边界。
4. 只修改规则明确覆盖的属性，未知 OOXML 保留并警告。
5. 相同输入、规则和版本产生等价格式；第二次执行没有新增实质变更。
6. 模型草稿必须经过本地作用域裁剪。保守模式只应用用户明确要求；“智能排版设计体系”
   模式先加载本地通用学术预设，再让明确要求覆盖相应角色，模型不得自行扩大作用域。

## 模块边界

```text
Requirement Interpreter -> Requirement Intents -> Local Capability Resolver
Local Capability Resolver -> Scoped Spec + Coverage Report -> Preset Merge -> FormattingSpec
DOCX / plain text -> Safety Validator -> DocumentIR -> Deterministic Classifier
                                            -> optional Semantic Reviewer
DocumentIR + FormattingSpec -> FormattingPlan -> Formatter
Formatter -> temp.docx -> Reopen Validator -> Repair -> final.docx + audit
```

启用 `auto_layout` 时，在计划生成前增加一个本地确定性阶段：只对普通正文中的手动换行和
过长连续文本生成分段计划，复制原有运行格式，重新解析并识别标题层级。转换前后会比较逻辑
全文、表格、页眉页脚、字段、书签、图片、关系和二进制包部件；任一受保护分量变化都会停止
发布。工作副本随后进入原有格式化与重开验证链路。

- `docalign_core.domain`：版本化数据契约。
- `docalign_core.docx`：ZIP/OOXML 解析与确定性写入。
- `docalign_core.analysis`：确定性角色分类、可选语义复核与有优先级的结果合并。
- `docalign_core.engine`：计划、样式、格式应用和变更记录。
- `docalign_core.validation`：包、内容、结构与规则验证。
- `docalign_core.llm`：可替换的自然语言规则解释器与逐段语义复核器。
- `apps.api`：SQLite、本地存储、任务状态与 REST；路由不包含格式业务逻辑。
- `apps.web`：中文工作台与最佳努力 DOCX 预览。

## 稳定契约

- `document-ir.v1`
- `formatting-spec.v1`
- `formatting-plan.v1`
- `validation-report.v1`
- `audit-report.v1`
- `/api/v1`

破坏性 schema 变更必须增加版本，不得原地改变既有含义。

## v0.1 非目标

`.doc`、PDF/OCR、文本润色、修订合并、评论解析、公式改写、任意多级编号重建、公共 SaaS、多租户与像素级 Word 浏览器渲染。

## 智能排版工作流

1. 解析段落、运行、样式、编号、上下文和格式证据；纯文本的每个非空行先变成真实 Word
   段落，Markdown 标题与列表标记变成 Word 结构。
2. 确定性规则锁定 Word 标题样式、明确编号层级、摘要/关键词、图表题和真实列表等高置信
   角色。
3. 用户选择“智能分析”时，兼容模型复核语义含混段落，判断文档类型和标题、正文、作者、
   列表等角色；高置信结构证据和人工修正优先于模型。
4. 用户可在工作台逐段修正角色。排版任务始终复用这份持久化 IR，不会重新猜测。
5. `baseline` 表达全文共同属性，`roles` 表达主标题、各级标题、正文、列表、题注等覆盖。
   默认智能模式合并通用设计预设与用户要求；保守模式只执行明确规则。
6. Python 引擎生成类型化计划并写入专用 Word 样式，保留内联强调、编号和受保护对象；输出
   经内容指纹、OOXML、重开和幂等验证后发布。
7. 自动结构排版启用时，先将安全的连续正文拆成真实段落并重新运行本地分类；用户修正过且
   未被拆分的段落继续沿用人工角色，新增段落按结构证据重新识别。

## 任务连接生命周期

浏览器只维护一个当前任务轮询器，不使用 WebSocket。轮询请求可取消，Fast Refresh、组件
卸载或任务切换会先中止旧请求；刷新页面后从本地工作区恢复同一个任务 ID。短暂网络失败采用
有上限的指数退避并继续读取服务端持久状态，不会重新创建任务或形成并发重连。

## 能力感知的规则编译

自然语言不是执行脚本，模型也不直接决定 Word 修改。编译链必须把每一项要求分成三类：
`applied`（已映射为受支持的类型化操作）、`ambiguous`（需要用户确认含义）和
`unsupported`（当前引擎不能安全实现）。工作台在执行前展示覆盖报告，不能把“模型理解了”
误报为“引擎已经支持”。

本地能力解析器是最终权限边界：它将“全文/正文/一级标题”等作用域和字体、字号、缩进、
行距、颜色、底纹等属性映射到 `FormattingSpec`，丢弃模型自行扩大的范围。每个新增能力必须
同时增加四部分：schema 字段、类型化计划操作、确定性 OOXML 执行器和独立验证器。只有四者
齐全才算支持。

“所有颜色改为黑色且不需要背景”是首个文档级视觉清理能力：所有可见 Word 文字写入直接
黑色并移除主题色冲突；清除文本高亮、字符/段落/单元格底纹和页面背景。图片、形状填充、
边框和线条不属于“文字背景”，默认保留。处理覆盖主文档、样式、页眉页脚以及
`stylesWithEffects.xml` 等 `python-docx` 不会完整回写的包部件，并在保存后独立扫描全部
相关 OOXML；任何残留都会阻止发布。
