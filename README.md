# DealGuard

## 项目介绍

DealGuard 是面向贸易团队的询盘与报价工作台。公司管理员使用邮箱注册并创建独立工作区，维护客户、商品和报价政策；系统分析询盘，由服务端规则校验权限、库存与折扣后生成报价草稿。

## 功能

- 解析中英文询盘，支持多商品报价。
- 检索版本化政策，并按政策适用范围过滤。
- 使用服务端 Guard 校验客户、库存、折扣和政策；不符合规则时拒绝或要求补充信息。
- 支持政策草稿、版本激活、经理审批、报价追踪与 CSV 导出。
- 提供运行记录、政策证据和 Trace，便于查看决策依据。
- 支持邮箱注册和登录；工作区、客户、商品、政策及报价按租户隔离。
- 管理员可邀请销售/经理加入工作区、分配客户、停用账号或生成一次性密码重置链接；邀请 48 小时有效，重置链接 1 小时有效。

## 技术栈

- 前端：Next.js、React、TypeScript、Tailwind CSS
- 后端：Python、FastAPI、Pydantic、LangGraph
- 数据与检索：SQLAlchemy、Alembic、SQLite（默认）/ PostgreSQL、Qwen3 Embedding（可选）
- 询盘模型：OpenAI-compatible API（可选，支持 DeepSeek）

## 启动方式

需要 Python、Node.js 和 npm。默认使用 SQLite。未配置模型时，系统使用本地规则解析询盘；配置 OpenAI-compatible API 后可启用模型解析。

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

如需接入模型，可将 `.env.example` 复制为项目根目录下的 `.env`，再配置 DeepSeek API。询盘解析使用模型，政策检索读取工作区 Registry，不需要配置 Embedding 模型。

首次访问工作台后选择“创建工作区”，填写公司名称、管理员姓名、邮箱和至少 12 位密码。注册会创建独立工作区并自动登录；管理员随后添加客户、商品并上传、配置和激活报价政策。管理员可以为销售或经理生成邀请链接，或为成员生成密码重置链接，并通过安全渠道发送。成员使用链接自行加入或设置新密码。
