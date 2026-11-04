# DealGuard

## 项目介绍

DealGuard 是面向贸易团队的询盘与报价工作台。公司管理员使用邮箱注册并创建独立工作区，维护客户、商品和报价政策；系统分析询盘，由服务端规则校验权限、库存与折扣后生成报价草稿。

## 功能

- 解析中英文询盘，支持多商品报价。
- 检索版本化政策，并按政策适用范围过滤。
- 使用服务端 Guard 校验客户、库存、折扣和政策；不符合规则时拒绝或要求补充信息。
- 支持政策草稿、版本激活、经理审批、报价追踪与 CSV 导出。
- 支持管理员用配置的模型从 Markdown/TXT 提取政策规则建议，也可用 CSV/Excel 批量导入政策；所有导入结果先进入待审核草稿，不会自动激活。
- 提供运行记录、政策证据和 Trace，便于查看决策依据。
- 支持邮箱注册和登录；工作区、客户、商品、政策及报价按租户隔离。
- 管理员可邀请销售/经理加入工作区、分配客户、停用账号或生成一次性密码重置链接；邀请 48 小时有效，重置链接 1 小时有效。
- 提供账户页面以修改姓名和密码；管理员可在账户页面邀请成员、查看待处理邀请、停用/启用账号及生成密码重置链接。
- 管理员可下载 CSV/Excel 商品模板，上传后预览逐行校验结果；确认时整批导入，已有 SKU 或存在错误时不会覆盖或部分写入。

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

打开 <http://localhost:3001>。前端默认连接 `http://localhost:8000`。

如需接入模型，可将 `.env.example` 复制为项目根目录下的 `.env`，再配置 DeepSeek API。询盘解析使用 OpenAI-compatible 模型。政策候选始终先由 Registry 按租户、客户等级、政策状态、生效时间和业务范围过滤；可选配置 `LOCAL_EMBEDDING_MODEL_PATH` 指向本机 Qwen3-Embedding-0.6B 权重目录，启用 BM25 + Dense 条款检索。多个合格政策时，只有 BM25 与 Dense 的首选政策一致才继续；未配置或检索分歧时安全弃权。

模型已配置时，`ACTION_PLANNER_ENABLED=true`（默认值）允许 Agent 在最多两轮、四次调用内选择客户、商品、库存和政策证据只读工具；设为 `false` 可运行固定工作流基线。工具结果仅供参考，报价写入仍须通过服务端 Guard。`ENABLE_DEMO_AUTH` 默认为 `false`，正式环境不接受 `X-Demo-Actor`。

配置本地 Embedding 示例：`LOCAL_EMBEDDING_MODEL_PATH=D:\gitclone\模型\Qwen3-Embedding-0.6B`。模型仅在检索需要时加载；启动和运行基准评测的方法见 [`docs/evaluation.md`](docs/evaluation.md)。

首次访问工作台后选择“创建工作区”，填写公司名称、管理员姓名、邮箱和至少 12 位密码。注册会创建独立工作区并自动登录；管理员随后添加客户、商品并上传、配置和激活报价政策。管理员可以为销售或经理生成邀请链接，或为成员生成密码重置链接，并通过安全渠道发送。成员使用链接自行加入或设置新密码。
