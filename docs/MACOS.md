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
uv run alembic upgrade head
```

如需自然语言规则编译，在 `.env` 设置兼容端点、密钥和模型。不要把真实密钥提交到仓库。

## 启动

终端一：

```bash
uv run uvicorn apps.api.main:app --host 127.0.0.1 --port 8000
```

终端二：

```bash
pnpm dev
```

打开 `http://127.0.0.1:3000`。停止两个进程不会损失已经落盘的工作区。

## 恢复

- 服务启动时会把上次未完成任务标记为 `failed / JOB_INTERRUPTED`，可从工作台重新提交。
- 如果端口占用，先停止旧的本地进程，不要改成公共网络地址。
- 如果 schema 漂移，运行 `make schemas` 后重新执行质量门。
- 本地数据默认保留；在工作台使用“删除本地文档”可级联清理关联产物。
