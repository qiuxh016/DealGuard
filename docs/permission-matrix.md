# DealGuard 演示 API 权限矩阵

此矩阵描述当前单租户演示角色，不构成生产 RBAC 或身份安全认证。API 均通过服务端会话解析身份；开发环境的 `X-Demo-Actor` 仅为演示入口。

| 能力 | Sales | Manager | Admin | 附加范围检查 |
|---|---:|---:|---:|---|
| 提交询盘 / 报价 | 是 | 否 | 否 | Guard 再校验可信客户、商品、政策和库存 |
| 查看/修改/导出报价 | 自有 | 否 | 本租户 | Sales 同时匹配 owner 与 customer；其他租户按 404 隐藏 |
| 查看运行列表 | 自有 | 本租户 | 本租户 | 详情只允许自有记录或 Admin；Manager 的列表权限不扩展为读取他人 Trace |
| 查看政策 / 对比版本 | 是 | 是 | 是 | 只能读取当前租户记录 |
| 激活预置政策 / 管理政策草稿 | 否 | 否 | 是 | 激活、草稿均限制在 actor.tenant_id；拒绝管理操作写审计 |
| 查看/处理审批 | 否 | 是 | 否 | 审批请求按租户过滤；批准时重验申请人、政策、库存 |
| 查看指标 / Evaluation | 否 | 是 | 是 | 仅本租户运行指标；Evaluation 是公开的固定合成数据集 |
| 查看政策管理审计 | 否 | 否 | 是 | 只查询本租户审计事件 |
| 重置演示数据 | 否 | 否 | 是 | 仅当前演示租户 |

## API 路由核对

| 路由 | 能力 / 身份门禁 | 数据范围或关键复检 |
|---|---|---|
| `POST /api/v1/runs` | 已认证身份；Guard 要求销售能力 | actor tenant/customer，商品、库存、政策逐项检查 |
| `GET /api/v1/runs` | 销售自有；Manager/Admin 本租户 | 列表不含原始询盘 |
| `GET /api/v1/runs/{run_id}` | 销售自有；仅 Admin 可查他人记录 | tenant 过滤，未知/越界记录返回 404 |
| `GET /api/v1/quotes/{id}`、`export.csv` | 销售自有或 Admin | tenant、owner、customer 同时校验 |
| `PATCH /api/v1/quotes/{id}` | 销售自有 | 服务层再校验 owner/customer 和政策状态 |
| `GET /api/v1/policies`、`POST /policy-diffs` | `policies.read`、`policy.diff` | tenant 范围 |
| `POST /api/v1/policies/{id}/activate`、`/policy-drafts*` 写操作 | Admin | tenant 范围，拒绝管理写入有审计 |
| `/api/v1/approvals*` | Manager | tenant 范围；批准时逐项重验 |
| `GET /api/v1/metrics`、`/evaluation` | Manager/Admin | 指标本租户；Evaluation 为固定合成数据 |
| `GET /api/v1/audit`、`POST /api/v1/demo/reset` | Admin | 当前 tenant |
| `/api/v1/auth/login`、`/logout`、`/me`、`/health` | 认证流程或公开健康检查 | 不授予业务数据范围 |

## 集中能力键

角色与能力映射定义在 `backend/app/services/permissions.py`。端点/服务调用 `require_capability` 或 `has_capability` 执行角色门禁；资源所有权仍由查询条件和业务服务单独校验，不能只靠能力键授权。

| 能力键 | 使用位置 |
|---|---|
| `inquiry.run` | Guard 判断报价发起人角色；审批恢复确认申请人仍是销售 |
| `quote.read_own` / `quote.update_own` / `quote.read_tenant` | 报价读取、修改与 CSV 的角色能力；读写还校验 owner/customer/tenant |
| `runs.read_own` / `runs.read_tenant` / `runs.read_all` | 运行列表范围与详情读取范围（Admin 可读本租户详情；Manager 仅读本租户列表） |
| `policies.read` / `policy.diff` / `policies.activate` / `policy_drafts.manage` | 政策读取、对比、激活和草稿管理 |
| `approvals.read` / `approvals.decide` | 审批列表与决策 |
| `metrics.read` / `evaluation.read` / `audit.read` / `demo.reset` | 受限管理读取与演示重置 |

登录、登出和健康检查属于单独的认证/公开入口，不按业务角色能力授权。账户生命周期、跨租户管理员、CSRF、共享限流、SSO/MFA 不在本矩阵的交付范围内，仍是公开部署前置事项。
