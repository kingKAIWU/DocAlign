# DocAlign 桌面分发与安装

DocAlign 的桌面分发版把 Python 解释器、后端依赖、数据库迁移和静态网站放在同一个本地应用中。
普通用户解压后启动 `DocAlign` 即可，不需要安装 Python、Node.js、uv 或 pnpm。应用只监听
`127.0.0.1`，就绪后自动在默认浏览器打开工作台。

当前仓库已经具备可重复的 macOS/Windows one-folder 构建和产物自检。产物尚未使用 Apple
Developer ID 或 Windows Authenticode 证书签名，也没有自动更新器，因此目前属于可测试分发版，
不应标记为面向公众的正式签名发行版。

## 普通用户安装

### macOS

1. 从 GitHub Actions 的 `distribution` 工作流下载与机器架构对应的 macOS ZIP。
2. 解压后把 `DocAlign.app` 拖到“应用程序”。
3. 双击启动；应用会自动打开浏览器。再次启动只会打开已运行的同一个工作台，不会创建第二套服务。
4. 未签名测试版首次运行可能被 Gatekeeper 阻止。内部测试者可在 Finder 中按住 Control 点击应用，
   选择“打开”并确认；公开发行前应完成签名与公证，不能要求普通用户长期绕过系统保护。

### Windows

1. 从 GitHub Actions 下载 Windows ZIP，并完整解压到一个固定目录。
2. 双击目录内的 `DocAlign.exe`；不要只把 EXE 单独移出，因为 `_internal` 包含运行依赖。
3. 应用会自动打开浏览器。重复双击会复用当前本地实例。
4. 未签名测试版可能显示 SmartScreen 提示。公开发行前应完成 Authenticode 签名并提供安装器。

默认工作数据不会写入应用安装目录：

- macOS：`~/Library/Application Support/DocAlign`
- Windows：`%LOCALAPPDATA%\DocAlign`
- Linux 开发验证：`$XDG_DATA_HOME/DocAlign`，未设置时为 `~/.local/share/DocAlign`

替换或删除应用不会自动删除上述数据目录。升级时关闭旧版本并替换应用即可，启动时会自动执行向后兼容
的数据库迁移。卸载后如果确认不再需要源文件、规则包、结果与审计，可以再手工删除数据目录。

## 构建者流程

PyInstaller 产物与构建系统和 CPU 架构相关，不是跨平台编译结果。因此必须在 macOS 上构建 macOS
产物，在 Windows 上构建 Windows 产物。仓库的 `distribution` 工作流会分别运行这两个构建。

本机可按以下流程验证当前平台：

```bash
uv sync --frozen
pnpm install --frozen-lockfile
pnpm build
uv run python -m scripts.build_distribution
```

macOS 自检：

```bash
dist/distribution/DocAlign.app/Contents/MacOS/DocAlign --self-test
```

Windows 自检：

```powershell
dist\distribution\DocAlign\DocAlign.exe --self-test
```

自检会在临时目录验证静态网站资源和完整数据库迁移，不读取或修改用户工作区。构建脚本默认先清理明确的
`dist/distribution` 与 `build/pyinstaller` 目录，并拒绝把项目根目录或更宽的路径作为清理目标。
CI 还会实际启动产物，访问健康检查、主页和设置页，再启动第二次确认它复用现有实例，避免出现
“文件能生成但应用打不开”的假阳性。

实现依据：Next.js 的[静态导出指南](https://nextjs.org/docs/app/guides/static-exports)说明
`output: 'export'` 会生成可由任意 Web 服务器托管的 `out`；PyInstaller 的
[官方手册](https://pyinstaller.org/en/stable/)说明产物包含解释器和依赖、用户无需另装 Python，
同时明确它不是跨平台编译器；[使用说明](https://pyinstaller.org/en/stable/usage.html)将 one-folder
列为默认模式；[spec 文件说明](https://pyinstaller.org/en/stable/spec-files.html)定义了把网站与迁移
作为 `datas` 放入产物的方式。

## 正式公开发行前的剩余门槛

- Apple Developer ID 签名、公证与 stapling 验证。
- Windows Authenticode 签名、安装/卸载入口和 SmartScreen 信誉积累。
- 构建产物 SHA-256、SBOM、依赖漏洞扫描和不可变 GitHub Release 附件。
- 带回滚能力的升级策略；在此之前只支持关闭旧版后整包替换。
- 至少在一台真实 Intel/Apple Silicon Mac 和 Windows 11 机器完成安装、升级、卸载及大文档验收。
