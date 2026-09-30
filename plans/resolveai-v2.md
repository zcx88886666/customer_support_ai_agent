# ResolveAI v2：企业级电商售后智能客服系统

> 状态：待确认的实施计划，尚未开始编码。  
> 版本：v2（2026-09-26）。  
> 基线：[v1 原始计划](resolveai-v1.md)。v1 原样保留；本文是后续讨论的整合版，不覆盖历史版本。

## 1. 定位与成功标准

面向 AI 应用工程师简历，做一个**单企业、可本地运行、可评估、可解释**的电商售后系统，而不是万能聊天机器人。重点展示一条完整业务闭环：真实业务背景数据 → Agent 决策 → RAG / MCP 工具 → 确定性策略 → 人工审批 → 审计和恢复 → 评估与追踪。

演示必须能回答三个问题：

1. Agent 为什么选择该知识文档、订单工具和处理路径？
2. 它能否在退款、越权访问和错误数据面前安全停止或升级人工？
3. 修改模型、Prompt、知识库或政策后，如何证明系统没有退化？

验收范围只包含：政策问答、订单/物流查询、退货退款提案与主管审批、人工工单、知识库管理、观测和评估。**不接真实支付、不替真实平台发起退款**。

## 2. 版本变化

| 主题 | v1 | v2 决定 |
|---|---|---|
| 主数据库 | PostgreSQL + pgvector | 保留；开发和 CI 都用 PostgreSQL，不维护 SQLite 第二套实现 |
| Agent 平台 | LangGraph | 保留；持久化 checkpoint、暂停与恢复 |
| 模型接入 | OpenRouter，给出示例模型名 | 保留 OpenRouter；模型按职责配置，版本与能力校验，避免把容易变化的模型名写死 |
| Agent 观测 | LangSmith 可选 | 改为 Langfuse 可选；代码评测集仍是事实来源 |
| 系统观测 | OpenTelemetry + Jaeger | 保留，跨 FastAPI、Worker、MCP 传播 Trace Context |
| 业务数据 | 模拟订单 | 真实 Olist 匿名订单作可选本地业务骨架；缺失的售后流程按规则合成并标注来源 |
| 对话数据 | 合成表达 | CSDS / 淘宝 ECD 作中文表达和摘要评估参考；JDDC 2.1 仅作申请成功后的增强 |
| 评估 | 至少 40 条 | 扩为版本化、多层评估：确定性安全/轨迹 + RAG + 多轮 + Judge + 人工反馈 |
| 部署 | Compose + 可选 SaaS | 默认轻量 Compose；Langfuse Cloud 或完整自托管作为可选观测档位 |

## 3. 端到端业务闭环

```text
客户问题 + 已认证身份
  → 输入检查、意图路由与澄清
  → 知识问答 / 订单售后 / 人工升级
  → 混合检索或 MCP 只读工具
  → 结构化售后提案
  → 确定性政策、金额、权限校验
  → 需要资金动作时 LangGraph interrupt 等待主管审批
  → 审批后用同一 thread_id 恢复，MCP 幂等执行模拟退款
  → 含证据的客户回复、审计事件、Langfuse Agent Trace、Jaeger 系统 Trace
```

模型只生成解释与提案；**权限、金额、可退资格、审批凭证和幂等性由服务端校验**。文档内容和工具返回值都视作不可信输入，不能变成系统指令。

## 4. 技术架构与职责

| 层 | 技术 | 主要职责 |
|---|---|---|
| 前端 | Next.js、TypeScript、Tailwind CSS、shadcn/ui | 客户聊天、坐席工单、主管审批与知识管理；SSE 流式显示 |
| API | FastAPI、Pydantic、SQLAlchemy、Alembic | 身份认证/RBAC、会话、工单、审批、审计、SSE |
| Agent | LangGraph StateGraph + PostgreSQL Checkpointer | 路由、工具编排、持久状态、interrupt/resume、超时与错误降级 |
| 模型 | OpenRouter | 路由/生成/Judge/Embedding 按配置选择；启动检查工具调用和结构化输出能力 |
| 业务工具 | 独立 Commerce MCP 服务，Streamable HTTP | 查询客户/订单/物流/可退资格；创建退货申请和模拟退款 |
| 数据 | PostgreSQL + pgvector | 订单、会话、工单、审批、checkpoint、知识库、全文与向量检索 |
| 后台任务 | Redis + Celery | 文档解析/索引、批量评估；Redis 不保存核心业务真相 |
| 系统观测 | OpenTelemetry → Collector → Jaeger v2 | API、Worker、MCP、数据库、模型调用的耗时和错误链路 |
| Agent 观测 | Langfuse（可选） | Graph/LLM/Tool/RAG 轨迹、Prompt 版本、Token/成本、数据集运行和分数 |
| 交付 | Docker Compose、GitHub Actions、Playwright、pytest | 一键启动、无 API Key Mock 演示、自动回归 |

初期只做**一个编排图 + 两条专门路径**（知识客服、订单售后），不要并行引入第二个 Agent 框架或大规模微服务。Langfuse 和 Jaeger 职责不同：前者解释 Agent 行为与实验结果，后者定位跨服务性能和故障。长时间人工审批分成发起与恢复两个 Trace，使用 `approval_id`、`thread_id` 和 Span Link 关联，而不是保持一个 Span 开启数小时。

## 5. 数据方案：真实骨架、规则合成、严格溯源

### 5.1 数据来源优先级

1. **订单骨架：** [Olist 匿名真实电商订单](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce)。本地导入约 1–2 万笔订单以及其商品、支付、配送时间、预计送达与评价的真实关联。原数据是巴西 2016–2018 年订单，不冒充当前中国平台订单；不要把缺失的售后字段说成原始真实字段。
2. **中文对话参考：** [CSDS](https://github.com/xiaolinAndy/CSDS) 用于会话摘要和话题；[淘宝 ECD](https://github.com/cooelf/DeepUtteranceAggregation) 用于口语表达、多轮追问和回复选择。二者**不与 Olist 客户/订单建立真实身份关联**。发布原文或加工样本前分别核对授权。
3. **增强来源：** [京东 JDDC 2.1](https://github.com/hrlinlp/jddc2.1) 需申请；[阿里天池电商数据](https://tianchi.aliyun.com/specials/promotion/collectionofdataset_2021)、[Amazon ESCI](https://github.com/amazon-science/esci-data)、[Shopee 物流挑战赛](https://www.kaggle.com/competitions/open-shopee-code-league-logistic/data) 仅用于可选检索、商品或物流评估，不成为 MVP 依赖。逐项遵守协议；[天池数据集使用条款](https://terms.aliyun.com/legal-agreement/terms/suit_bu1_ali_cloud/suit_bu1_ali_cloud202105290939_49262.html)对相关数据有科研用途限制。
4. **业务规则和知识库：** 为虚构商家原创 20–25 份带版本/生效日期的政策。借鉴公开电商 API 的状态设计，但不复制真实平台条款，也不声称与阿里、京东、亚马逊或 Shopee 官方政策相同。

Olist 标注 CC BY-NC-SA 4.0。公开仓库默认提交**生成脚本、数据字典、来源与许可证记录、少量原创合成演示数据**；不直接提交原始 Olist、CSDS 或 ECD 文件。用户按各来源规则自行获取后在本地导入；若未来公开托管演示或商业使用，先复核授权。未找到可核验授权的拼多多完整订单+对话公开集，不纳入方案。

### 5.2 生成与溯源

业务表按 `customers → orders → order_items/payments → shipments → returns/refunds → tickets/conversations/approvals` 建模。基于真实订单状态和时间戳筛选“超时未达、取消、低评价”等候选，再用**确定性规则**生成物流扫描、退货、退款及客服工单；绝不随机独立造表。

每个导入或生成记录至少保留 `source_dataset`、`source_record_id`、`origin_type`（`real_anonymized` / `synthetic_rule` / `manual_gold`）、`transform_version`、`seed_version`。如因演示需要平移历史时间，记录原时间及平移规则，并保持时间间隔、先后顺序和来源说明。绝不把 Olist 订单与京东/淘宝真实对话表述为同一真实交易。

核心约束示例：未发货不能已签收；累计退款不超过实付可退余额；已发货不直接取消；审批前不执行资金动作；`pending` 退款不能回复“已经到账”；重复请求不能重复退款。用数据库约束、状态机和测试共同保证这些不变量。

### 5.3 两个 Seed 档位

| 档位 | 用途 | 建议规模 |
|---|---|---|
| `demo` | 无外部数据即可重复演示 | 约 25 个原创确定性场景、20 份政策、Mock 模型响应 |
| `realistic` | 授权数据本地导入后的系统评测 | 约 1–2 万 Olist 订单样本、300–500 条合成工单、80–120 条人工复核金标评测案例 |

`demo` 固定提供延迟包裹、损坏商品、可退/不可退、部分退款、跨用户查单、恶意知识文档、审批拒绝和服务重启恢复等场景。`realistic` 使用真实订单分布，但所有新增售后/工单字段仍明确标为合成。

## 6. Agent、RAG 与 MCP 的实现边界

### 6.1 LangGraph 状态与节点

`AgentState` 包含用户身份、会话 ID、路由、当前订单、检索证据、结构化提案、审批状态、错误码和恢复信息；只把**必要的状态摘要**写入观测平台，避免传播 PII。

节点为 `input_guardrail → route → clarify / knowledge / after_sales → policy_check → interrupt / answer / human_handoff → output_guardrail`。需要时再增加查询改写和检索结果评分，不构造无业务价值的多层 Agent。执行步数有上限，工具失败有明确重试/人工降级。

### 6.2 RAG

原创政策 PDF/Markdown/HTML 支持上传、解析、标题分块、版本、生效日期和停用。PostgreSQL 全文检索与 pgvector 向量检索并行，RRF 融合；回答显示文档标题、版本和片段位置。旧版政策不参与在线引用，但留在专用评测场景验证版本冲突。证据不足则澄清或转人工，不能编造政策。

### 6.3 Commerce MCP 工具

只读：`get_customer_profile`、`get_order`、`track_shipment`、`check_return_eligibility`。受控写入：`create_return_request`、`issue_refund`。服务端从认证上下文取身份，不接受模型自报角色或客户 ID；写操作需要审批凭证、幂等键和事务内再次校验。MCP 服务模拟业务系统，不接真实支付、物流或电商账号。

### 6.4 UI 与 API

客户可流式聊天、查看订单与引用并反馈；坐席可接管工单；主管可审批/驳回和查看审计。SSE 事件固定为 `message.delta`、`tool.started`、`tool.completed`、`approval.required`、`run.completed`、`run.failed`；前端类型从 OpenAPI 生成。主管修改退款金额后须重新执行政策/余额校验。

## 7. 观测、评估与发布门槛

### 7.1 观测

- **OpenTelemetry + Jaeger：** 查询一次请求在路由、检索、模型、MCP、PostgreSQL、Celery 上的耗时、失败和重试。传播标准 Trace Context；不记录原始客户姓名、地址或完整支付信息。
- **Langfuse：** 使用 LangGraph/LangChain Callback 与必要的手工 Observation 记录路由、RAG、工具、政策和审批摘要；保存模型/Prompt/知识库版本、Token、估算成本和评测分数。它是**可选**组件，故障不能阻断客服主流程。
- **两个运行档位：** 默认 Compose 启动核心服务与 Jaeger；可通过环境变量接 Langfuse Cloud，或使用单独 Compose Profile 按官方依赖启动自托管 Langfuse。软件开源不等于云资源免费；不要把 ClickHouse/对象存储等附加依赖塞进最小启动链路。

### 7.2 评测设计

`evals/datasets/*.jsonl` 是版本化事实来源；Langfuse 负责展示 Dataset Run、Observation 与 Score，而不是唯一存储。每条案例固定身份、订单/文档版本、输入、多轮上下文、期望路由、必需/禁止工具、期望引用、审批与安全条件、来源类型。按订单/会话分组切分开发集和锁定测试集，避免同一业务事实泄漏到两侧。

| 层 | 核心检查 | 方法 |
|---|---|---|
| 路由与多轮 | FAQ/售后/人工分支，订单上下文是否混淆 | 金标 Accuracy/F1、会话测试 |
| 工具轨迹 | 工具名、参数、顺序、重试、禁止调用、步骤上限 | 确定性轨迹断言 |
| RAG | Hit@5、MRR、引用有效性、旧文档误引、证据不足拒答 | 检索金标 + 规则检查 |
| 业务安全 | 跨用户访问、越权退款、重复退款、审批恢复、PII | 单元与 PostgreSQL/MCP 集成测试；零容忍 |
| 回答质量 | 事实忠实度、政策合规、完整性、语气 | 独立 OpenRouter Judge + 人工校准 |
| 性能成本 | 首 Token、P50/P95、Token、模型成本、工具次数 | OTel/Langfuse 基线与回归对比 |

首版至少 80 条案例，覆盖真实语料启发的问题、订单事实约束、退款边界、多轮与 Prompt Injection；其中关键安全样本由人工复核。初始目标：路由 Accuracy ≥ 90%，工具/参数通过率 ≥ 90%，RAG Hit@5 ≥ 85%；**未经审批退款、跨用户订单访问和重复退款必须为 0**。这些是计划门槛，需在基线运行后确认难度；主观 Judge 分数不单独作为安全发布依据。

每次提交执行无 Key 的 Mock/规则/集成评测；手动或定期执行真实 OpenRouter 实验并与固定基线比较。失败 Trace 经脱敏、人工核验后进入回归集。Prompt、模型、数据、政策和代码版本都写入评测报告，确保可复现。

## 8. 六周实施路线

| 周 | 交付物 | 退出条件 |
|---|---|---|
| 1 | Monorepo、Compose、PostgreSQL/pgvector、迁移、RBAC、`demo` Seed、Olist 本地导入脚本和数据字典 | 无 Key 能启动并查询真实/合成来源标记；核心表关联与状态约束通过 |
| 2 | OpenRouter 适配、LangGraph 路由/状态持久化、SSE 会话、客户 UI | FAQ、澄清、订单路由可端到端运行；重启不丢会话状态 |
| 3 | 原创知识库、Celery 索引、混合检索、引用与版本控制 | 正确引用当前政策；证据不足可拒答/升级 |
| 4 | Commerce MCP、策略引擎、主管 UI、审批 interrupt/resume、幂等模拟退款 | 批准/拒绝/改额/重复请求/重启恢复均通过集成测试 |
| 5 | OTel/Collector/Jaeger、可选 Langfuse、PII 脱敏、审计、安全测试 | 可追踪一次跨 API/MCP 请求与审批恢复；关键越权测试全通过 |
| 6 | 80+ 金标评测、Judge 校准、CI、Playwright、演示脚本、架构图和 README | 报告可复现；关键门槛达标；5 分钟启动说明可由新环境照做 |

第 7 周只作缓冲、缺陷修复和简历展示打磨，不扩展新功能。若时间不足，优先保留**查单 → 退款提案 → 人工审批 → 幂等执行 → 评估/追踪**主链，先削减可选多平台数据源、复杂管理 UI 和 Langfuse 自托管档位。

## 9. 明确不做与最终交付

不做多租户计费、真实支付/退款、真实电商平台账号连接、语音/微信等全渠道接入、Kubernetes、模型微调、自动成本路由，或把各平台数据伪装为同一套真实交易。对来源不明或许可不清的客户记录，不抓取、不公开再分发。

最终仓库应包含：可运行的前后端与 MCP、迁移和两档 Seed、数据来源/许可说明、版本化政策和评测集、规则与集成测试、可选 Langfuse 配置、Jaeger 示例 Trace、架构图、威胁模型、3 条固定面试演示脚本，以及可复现的指标报告。README 的数据真实性声明应明确区分“匿名真实订单”“公开客服语料”和“规则合成售后事件”。
