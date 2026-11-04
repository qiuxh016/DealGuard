# DealGuard

DealGuard 是面向贸易团队的询盘与报价工作台。它用 DeepSeek 解析中英文询盘，通过政策 Registry 检索当前适用依据，再由服务端 Guard 确定是否允许创建报价草稿。

**在线体验：**[https://47-238-29-6.sslip.io](https://47-238-29-6.sslip.io)（当前 ECS 演示环境）

## 功能

- 公司管理员注册并创建独立工作区；管理员、销售和经理按角色协作，数据按工作区隔离。
- 管理客户、商品和图片；支持 CSV/Excel 批量导入与导入前校验。
- 导入、审核和激活版本化政策；检索只使用当前有效且适用的政策，并保留原文引用。
- Agent 解析询盘、读取业务数据和政策；服务端 Guard 校验客户权限、库存、折扣及审批要求。只有允许的询盘才会生成报价草稿。
- 支持经理审批、报价议价与修订、正式版本发布、PDF/CSV 导出和限时客户确认链接。
- 提供运行 Trace、政策审计、检索评测和政策影响回放；界面支持中英文。

## 技术栈

- 前端：Next.js、React、TypeScript、Tailwind CSS
- 后端：Python、FastAPI、Pydantic、LangGraph、SQLAlchemy、Alembic
- 数据库：开发环境 SQLite；生产环境 PostgreSQL
- 模型与检索：DeepSeek OpenAI-compatible API 负责询盘解析；政策依据由 Registry 按租户、状态、生效时间、客户等级和适用范围筛选。当前部署不启用 Embedding。
- 部署：Docker Compose、Caddy HTTPS

## 启动方式

需要 Python 3.12、Node.js 22 和 npm。在项目根目录复制环境变量模板，并在 `.env` 中填写 DeepSeek API 密钥：

```powershell
Copy-Item .env.example .env
```

```dotenv
LLM_API_KEY=你的DeepSeek_API密钥
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-flash
```

在两个 PowerShell 窗口分别启动后端和前端：

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

```powershell
cd frontend
npm install
npm run dev
```

打开 <http://localhost:3001>，注册公司管理员并创建工作区。后端 API 文档位于 <http://localhost:8000/docs>。

生产环境使用 `compose.production.yaml`：复制 `.env.production.example` 为 `.env.production`，配置域名、PostgreSQL 密码和 DeepSeek 变量后运行：

```bash
docker compose -f compose.production.yaml --env-file .env.production up -d --build
```
