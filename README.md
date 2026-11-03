# DealGuard

## 项目介绍

DealGuard 是一个面向跨境贸易的询盘与报价工作台。系统从客户询盘中提取商品、数量和折扣信息，结合业务数据与适用政策进行校验，并在获准后生成报价草稿。

## 功能

- 解析中英文询盘，支持多商品报价。
- 检索版本化政策，并按政策适用范围过滤。
- 使用服务端 Guard 校验客户、库存、折扣和政策；不符合规则时拒绝或要求补充信息。
- 支持政策草稿、版本激活、经理审批、报价追踪与 CSV 导出。
- 提供运行记录、政策证据和 Trace，便于查看决策依据。

## 技术栈

- 前端：Next.js、React、TypeScript、Tailwind CSS
- 后端：Python、FastAPI、Pydantic、LangGraph
- 数据与检索：SQLAlchemy、Alembic、SQLite（默认）/ PostgreSQL、Qwen3 Embedding（可选）
- 询盘模型：OpenAI-compatible API（可选，支持 DeepSeek）

## 启动方式

需要 Python、Node.js 和 npm。默认使用 SQLite；未配置模型时可运行确定性 Demo 模式。

### 1. 启动后端

在项目根目录打开 PowerShell：

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

API：<http://127.0.0.1:8000>；健康检查：<http://127.0.0.1:8000/health>；接口文档：<http://127.0.0.1:8000/docs>。

### 2. 启动前端

在另一个 PowerShell 窗口，从项目根目录运行：

```powershell
cd frontend
npm install
npm run dev
```

打开 <http://localhost:3000>。前端默认连接 `http://localhost:8000`。

如需接入模型，可将 `.env.example` 复制为项目根目录下的 `.env`，再配置 DeepSeek API；Embedding 模型为可选配置。
