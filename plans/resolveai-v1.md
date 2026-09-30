# ResolveAI：企业级电商售后智能客服系统

## 1. 项目目标与总体架构

构建一个可在本地完整演示的全栈系统，覆盖企业 AI 应用工程师常见能力：多智能体编排、RAG、MCP 工具、结构化输出、会话记忆、人工审批、Guardrails、流式交互、评测、可观测性和容器化部署。

核心演示闭环：

```text
客户提问
  → 安全检查与意图路由
  → 知识客服 / 订单售后专员
  → RAG 检索或 MCP 业务工具
  → 确定性策略校验
  → 普通操作直接回答
  → 退款等高风险操作暂停并等待人工审批
  → 审批后恢复执行
  → 引用来源、审计记录、链路追踪与用户反馈
```

主技术栈：

- 前端：Next.js App Router、TypeScript、Tailwind CSS、shadcn/ui。
- API：Python、FastAPI、Pydantic、SQLAlchemy、Alembic。
- Agent：LangGraph StateGraph，使用 PostgreSQL Checkpointer 实现持久化和 interrupt/resume。LangGraph 官方将持久化、流式处理和 Human-in-the-loop 作为核心能力，适合这种长生命周期客服流程。[LangGraph 文档](https://docs.langchain.com/oss/python/langgraph/overview)
- 模型网关：OpenRouter，采用 OpenAI 兼容接口。
- 数据：PostgreSQL、pgvector、Redis。
- 异步任务：Celery，用于文档解析、Embedding 和批量评测。
- 工具协议：官方 MCP Python SDK，独立电商业务 MCP 服务。
- 可观测性：OpenTelemetry + Jaeger，本地可用；可选接入 LangSmith 做 Agent 轨迹分析与评测实验。
- 运行：Docker Compose 一键启动，GitHub Actions 做 CI。

## 2. 核心功能与实现

### Agent 编排

定义共享 `AgentState`，包含会话、身份、路由、订单上下文、检索证据、待执行动作、审批状态、错误与成本统计。

编排节点固定为：

1. 输入清洗、越权和 Prompt Injection 检查。
2. 使用结构化输出生成 `RouteDecision`。
3. 路由至知识客服、订单售后专员或人工升级流程。
4. 执行 RAG 或只读 MCP 工具。
5. 生成 `RefundProposal`、`ReturnProposal` 等业务动作。
6. 确定性策略引擎检查资格、金额和权限。
7. 高风险动作通过 LangGraph `interrupt()` 暂停；审批后使用同一 `thread_id` 恢复。[Interrupts 文档](https://docs.langchain.com/oss/python/langgraph/interrupts)
8. 输出事实一致性、引用和敏感信息检查。
9. 保存消息、轨迹、反馈和审计日志。

不让模型直接执行退款。模型只能提出结构化方案，真正的 `issue_refund` 必须携带后端生成的一次性审批凭证和幂等键。

### OpenRouter 模型分工

初始配置：

- `ROUTER_MODEL=google/gemini-2.5-flash`：分类、摘要和查询改写。
- `AGENT_MODEL=anthropic/claude-sonnet-4.5`：复杂回答和工具调用。
- `JUDGE_MODEL=google/gemini-2.5-pro`：离线评测，避免与生成模型完全同源。
- `EMBEDDING_MODEL=openai/text-embedding-3-small`：知识库向量化。

所有模型名均通过环境变量替换。启动时检查工具调用、JSON Schema 和 Embedding 能力；结构化调用启用严格 Schema 与 `require_parameters`。OpenRouter 已统一工具调用接口，并只对兼容模型提供严格结构化输出。[工具调用](https://openrouter.ai/docs/guides/features/tool-calling) / [结构化输出](https://openrouter.ai/docs/guides/features/structured-outputs)

### MCP 与业务模拟

独立 `commerce-mcp` 服务通过 Streamable HTTP 暴露：

- `get_customer_profile`
- `get_order`
- `track_shipment`
- `check_return_eligibility`
- `create_return_request`
- `issue_refund`

前三类工具只读；写工具要求用户身份、幂等键和审批凭证。MCP 服务不信任模型传入的角色、金额或客户 ID，全部由服务端重新校验。MCP 的工具发现与调用遵循官方 Tools/Resources 协议。[MCP 架构](https://modelcontextprotocol.io/docs/2026-07-28/learn/architecture)

### RAG 与知识库

- 管理端支持 PDF、Markdown、HTML 上传、版本管理、停用和重建索引。
- Celery 执行解析、按标题分块、Embedding 和索引写入。
- PostgreSQL 全文检索与 pgvector 向量检索并行，通过 RRF 融合排序；pgvector 官方也推荐与 PostgreSQL 全文检索组合实现混合搜索。[pgvector 文档](https://github.com/pgvector/pgvector)
- 回答必须返回文档标题、版本和片段定位；低相关度时禁止猜测并创建人工工单。
- 检索内容视为不可信数据，文档中的指令不得覆盖系统提示或触发工具。

### 前端与角色

提供三个演示账号角色：

- 客户：流式聊天、订单上下文、引用展开、审批状态、满意度反馈。
- 客服坐席：处理升级工单、人工回复、查看会话与 Agent 执行摘要。
- 主管：审批或驳回退款、管理知识库、查看审计日志和评测结果。

前端不重复开发完整追踪平台，只展示业务所需的工具时间线、审批状态和 token/cost 摘要；完整 Trace 在 Jaeger/LangSmith 查看。

### 对外接口和类型

主要接口：

- `POST /api/v1/conversations`
- `POST /api/v1/conversations/{id}/messages`：返回 SSE 流。
- `GET /api/v1/conversations/{id}`
- `GET /api/v1/approvals`
- `POST /api/v1/approvals/{id}/decision`
- `GET/PATCH /api/v1/tickets/{id}`
- `POST /api/v1/knowledge/documents`
- `GET /api/v1/knowledge/jobs/{id}`
- `POST /api/v1/feedback`

SSE 事件固定为：

- `message.delta`
- `tool.started`
- `tool.completed`
- `approval.required`
- `run.completed`
- `run.failed`

核心公共 Schema：`RouteDecision`、`SourceCitation`、`ToolCallRecord`、`RefundProposal`、`ApprovalDecision`、`StreamEvent`。前端类型从 OpenAPI 自动生成，避免手写接口漂移。

## 3. 6 周实施安排

- 第 1 周：Monorepo、数据库模型、JWT/RBAC、模拟订单数据、Docker Compose、基础聊天纵向链路。
- 第 2 周：LangGraph 状态图、OpenRouter 模型适配、结构化路由、SSE 流式输出、会话持久化。
- 第 3 周：知识库上传、Celery 索引任务、混合检索、引用回答及低置信度降级。
- 第 4 周：MCP 电商服务、售后工具链、退款策略、人工审批、interrupt/resume 和幂等执行。
- 第 5 周：Prompt Injection 防护、PII 脱敏、审计日志、OpenTelemetry、Jaeger、LangSmith 可选集成。
- 第 6 周：评测集、端到端测试、CI、演示数据、架构图、威胁模型、部署与面试说明。
- 第 7 周作为缓冲，只修复问题和打磨展示，不扩展新功能。

## 4. 测试与验收标准

测试分层：

- 单元测试：业务策略、权限、Schema、混合排序、幂等键、敏感信息处理。
- Agent 测试：使用可编程 Mock Model 验证路由、工具序列、异常重试和审批暂停恢复。
- 集成测试：真实 PostgreSQL/Redis/MCP，模型请求使用录制响应或 Fake Provider。
- E2E：Playwright 覆盖知识问答、物流查询、退款批准/拒绝、人工接管。
- 离线评测：至少 40 个版本化案例，包含 FAQ、订单物流、退款、恶意指令和无法回答问题；同时使用规则评测与 LLM-as-judge。离线基准、回归测试和线上反馈分开管理。[LangSmith 评测类型](https://docs.langchain.com/langsmith/evaluation-types)

发布门槛：

- 意图路由准确率 ≥ 90%。
- 预期工具与参数通过率 ≥ 90%。
- 未审批退款、跨用户订单访问等禁止动作通过率必须为 100%。
- 知识问题 Hit@5 ≥ 85%，最终回答必须包含有效引用。
- 所有审批流程可在服务重启后继续。
- CI 在没有 OpenRouter Key 时仍能完成静态检查、单元测试和 Mock Agent 集成测试。
- README 提供 5 分钟启动步骤和 3 条可重复的面试演示脚本。

## 5. 边界与默认假设

- 第一版为单企业、三角色，不实现完整多租户计费。
- 订单、物流、退款均使用可信的模拟数据，不连接真实支付或物流平台。
- 不包含语音、微信/邮件等全渠道接入、Kubernetes、模型微调和自动学习用户隐私。
- Redis 只承担任务队列、限流和短期缓存；核心业务、会话状态与审计数据全部落 PostgreSQL。
- 本地运行需要 Docker 和 OpenRouter API Key；缺少 Key 时仍提供 Mock 模式用于完整 UI 与审批流程演示。
- 项目重点定位为 AI 应用工程，而不是堆砌微服务；只有前端、API/Worker、MCP、PostgreSQL、Redis 和可观测性组件。
