# macOS 本地运行

## 要求

- macOS 13 或更新版本。
- Python 3.12 与 [uv](https://docs.astral.sh/uv/)。
- Node.js 20.9 或更新版本与 pnpm 11。

## 首次安装

```bash
cd /path/to/DocAlign
cp .env.example .env
uv sync --frozen
pnpm install --frozen-lockfile
```

API 启动时会对当前 `DOCALIGN_DATABASE_URL` 自动执行向后兼容的数据库迁移。管理员仍可在备份后使用
`uv run alembic upgrade head` 提前执行升级，但普通本地启动不再依赖手工迁移步骤。

如需自然语言规则编译，在 `.env` 设置兼容端点、密钥和模型。不要把真实密钥提交到仓库。

## 启动

终端一：

```bash
uv run uvicorn apps.api.main:create_app --factory --host 127.0.0.1 --port 8000
```

终端二：

```bash
pnpm dev
```

打开 `http://127.0.0.1:3000`。停止两个进程不会损失已经落盘的工作区。

## 恢复

- 服务启动时会把普通未完成任务标记为 `failed / JOB_INTERRUPTED`，可从批次重试；已经收到取消
  请求的任务会直接收敛为 `canceled`，不会误报失败。
- 服务会先完成数据库迁移再接受请求，避免版本更新后把缺失字段误报为网络断连。
- 如果端口占用，先停止旧的本地进程，不要改成公共网络地址。
- 如果 schema 漂移，运行 `make schemas` 后重新执行质量门。
- 本地数据默认保留；可使用“删除本地文档”或终态批次中的“删除本地批次”精确清理关联产物。
  “我的规则包”独立保留，可以逐修订导出备份。

## 诊断

网站能够打开时，进入“设置 → 本机诊断与支持报告”，点击“运行诊断”。检查范围包括数据库连接与
版本、数据目录权限、磁盘空间和本地产物引用。结果不会自动上传；如需寻求支持，请先下载并自行
检查 JSON。

如果网站或 API 无法启动，在项目目录运行：

```bash
uv run python -m scripts.diagnose --out docalign-support-diagnostic.json
```

命令退出码为 `0` 表示没有必须处理的问题，`1` 表示至少一项检查失败。即使返回 `1`，仍会在可写时
生成报告。离线诊断不会创建缺失的数据库或数据目录，也不会执行数据库迁移。

诊断报告不包含正文、文件名、文档/任务 ID、完整路径、数据库连接串、模型端点、密钥或原始日志。
它会包含 DocAlign/Python/操作系统版本、配置是否完整、汇总记录数、磁盘汇总、检查结果和近 30 天
安全错误代码。不要用原始 `.env`、数据库或启动日志替代这份安全报告发送给第三方。
