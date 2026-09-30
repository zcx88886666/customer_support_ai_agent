# ResolveAI v3：企业级电商售后智能客服系统

> 状态：待确认的实施计划，尚未开始编码。  
> 版本：v3（2026-09-27；意图识别、澄清与 Replan 方案已细化）。  
> 历史版本：[v1 原始计划](resolveai-v1.md) · [v2 整合计划](resolveai-v2.md)。两个文件原样保留；本文是可独立阅读的最新版计划。

## 1. 定位与成功标准

面向 AI 应用工程师简历，做一个**单企业、可本地运行、可评估、可解释**的电商售后系统，而不是万能聊天机器人。主链路展示真实业务背景数据 → Agent 决策 → RAG / MCP 工具 → 确定性策略 → 人工审批 → 审计和恢复 → 评估与追踪。v3 增加一条有边界的长期记忆链路，并对 LangGraph PostgresStore 与 Mem0 做可复现对照。

演示必须能回答四个问题：

1. Agent 为什么选择某份知识文档、某个订单工具和某条处理路径？
2. 它能否在退款、越权访问和错误数据面前安全停止或升级人工？
3. 修改模型、Prompt、知识库或政策后，如何证明系统没有退化？
4. 它能否跨会话记住**可记住的**用户偏好，同时正确更正旧记忆、隔离其他用户，并拒绝把记忆当成订单或退款事实？

验收范围：政策问答、订单/物流查询、退货退款提案与主管审批、人工工单、知识库管理、观测、评估，以及小范围长期记忆对照。**不接真实支付，不替真实平台发起退款。**

## 2. 从 v2 到 v3 的变化与保留项

| 主题 | v2 | v3 决定 |
|---|---|---|
| 会话记忆 | LangGraph + PostgreSQL Checkpointer | 保留 `PostgresSaver`；它保存线程内 Graph 状态、审批暂停/恢复，不承担跨会话用户画像 |
| 长期记忆 | 未正式设计 | 新增统一 `LongTermMemory` 接口，分别试验 `PostgresStore` 和 Mem0 OSS；默认只启用一个在线读取实现 |
| 用户画像 | 只有业务工具 `get_customer_profile` | 明确区分 PostgreSQL 中的权威结构化资料与可检索的柔性记忆；同一字段不得有两个权威来源 |
| 记忆评估 | 无专项测试 | 新增跨会话召回、更正/失效、删除、隔离、延迟与成本对照集 |
| 意图识别 | 只定义粗粒度 `route` 节点 | 明确分层意图、结构化槽位、状态/规则优先、多意图编排、安全降级及独立回归评测 |
| 数据库 | PostgreSQL + pgvector | 保留为业务数据、RAG、Checkpointer 和长期记忆实验的主要存储；不新增应用自建 SQLite 分支 |
| 其余架构 | OpenRouter、LangGraph、MCP、OTel/Jaeger、可选 Langfuse、Olist + 合成售后 | 全部保留；不因记忆实验更换 Agent 框架或业务主链 |
| 工期 | 六周 + 一周缓冲 | 核心链仍按六周完成；第七周专用于受控记忆 A/B 与结果报告，第八周只作缓冲与打磨。若只能投入六周，记忆对照缩为离线实验，不影响主链交付 |

`PostgresStore` 是跨线程键值存储，可选 embedding 语义检索；Mem0 提供从消息提取、去重和检索记忆的流程。二者不是 `PostgresSaver` 的替代品，也不替代业务数据库。[LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence) · [LangGraph Stores](https://docs.langchain.com/oss/python/langgraph/stores) · [Mem0 工作原理](https://docs.mem0.ai/core-concepts/how-it-works)

## 3. 端到端业务闭环

```text
客户问题 + 服务端已认证身份
  → 输入检查、加载当前线程状态
  → 确定性状态门控 + 结构化意图/槽位识别 + 服务端路由策略
  → 必要时澄清；确定分支后再按需读取权威资料与少量长期记忆
  → 知识问答 / 订单售后 / 人工升级
  → 版本化 RAG 或 MCP 实时只读查询
  → 结构化售后提案
  → 服务端确定性政策、金额、权限校验
  → 涉及资金动作时 LangGraph interrupt 等待主管审批
  → 审批后以同一 thread_id 恢复，MCP 幂等执行模拟退款
  → 含证据的回复、审计事件、Langfuse Agent Trace、Jaeger 系统 Trace
  → 对可复用且允许保存的偏好/已结案摘要生成记忆候选，再校验后写入
```

**事实优先级：实时业务数据库和已生效政策 > 用户明确设置的结构化资料 > 经校验的柔性记忆 > 模型推断。**模型只生成解释和提案；权限、金额、可退资格、审批凭证和幂等性由服务端校验。检索到的记忆、文档和工具返回值均视作不可信数据，不得升级为系统指令。

## 4. 技术架构与职责

| 层 | 技术 | 主要职责 |
|---|---|---|
| 前端 | Next.js、TypeScript、Tailwind CSS、shadcn/ui | 客户聊天、坐席工单、主管审批、知识管理；SSE 流式显示 |
| API | FastAPI、Pydantic、SQLAlchemy、Alembic | 身份认证/RBAC、会话、工单、审批、审计、SSE |
| Agent | LangGraph StateGraph + `PostgresSaver` | 状态感知的规则门控、结构化 LLM 意图/槽位识别、条件路由、工具编排、持久状态、interrupt/resume、错误降级 |
| 长期记忆 | `LongTermMemory` 适配层；`PostgresStore` / Mem0 OSS | 同一业务接口下的两条可切换实验路线；在线只读一套来源，离线对照隔离运行 |
| 模型 | OpenRouter | 路由/生成/Judge/Embedding 按配置选择；启动时检查工具调用、结构化输出与兼容性 |
| 业务工具 | 独立 Commerce MCP 服务，Streamable HTTP | 查询客户/订单/物流/可退资格；创建退货申请和模拟退款 |
| 数据 | PostgreSQL + pgvector | 订单、会话、工单、审批、checkpoint、结构化画像、知识库和长期记忆实验数据 |
| 后台任务 | Redis + Celery | 文档解析/索引、批量评估、可选异步记忆提取；Redis 不保存核心业务真相 |
| 系统观测 | OpenTelemetry → Collector → Jaeger v2 | API、Worker、MCP、数据库、模型调用的耗时和错误链路 |
| Agent 观测 | Langfuse（可选） | Graph/LLM/Tool/RAG/Memory 轨迹、Prompt 版本、Token/成本、数据集运行和分数 |
| 交付 | Docker Compose、GitHub Actions、Playwright、pytest | 一键启动、无 API Key Mock 演示、自动回归 |

初期只做**一个编排图 + 两条专门路径**（知识客服、订单售后），记忆读取/写入作为横切节点或服务，不另造多 Agent 系统。Langfuse 用于解释 Agent 行为与实验结果，Jaeger 用于跨服务性能和故障。长时间审批拆成发起与恢复两个 Trace，以 `approval_id`、`thread_id` 和 Span Link 关联。

## 5. 数据方案：真实骨架、规则合成、严格溯源

### 5.1 数据来源

1. **订单骨架：** [Olist 匿名真实电商订单](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce)。本地可选导入约 1–2 万笔订单及商品、支付、配送时间、预计送达与评价的真实关联。原数据是巴西 2016–2018 年订单，不冒充当前中国平台订单；缺失的售后字段不称为原始真实字段。
2. **中文对话参考：** [CSDS](https://github.com/xiaolinAndy/CSDS) 用于摘要和话题；[淘宝 ECD](https://github.com/cooelf/DeepUtteranceAggregation) 用于口语表达、多轮追问和回复选择。二者**不与 Olist 客户/订单建立真实身份关联**。发布原文或加工样本前分别核对授权。
3. **增强来源：** [京东 JDDC 2.1](https://github.com/hrlinlp/jddc2.1) 需申请；[阿里天池电商数据](https://tianchi.aliyun.com/specials/promotion/collectionofdataset_2021)、[Amazon ESCI](https://github.com/amazon-science/esci-data)、[Shopee 物流挑战赛](https://www.kaggle.com/competitions/open-shopee-code-league-logistic/data) 仅用于可选检索、商品或物流评估，不成为 MVP 依赖。逐项遵守协议；[天池数据集使用条款](https://terms.aliyun.com/legal-agreement/terms/suit_bu1_ali_cloud/suit_bu1_ali_cloud202105290939_49262.html)对相关数据有科研用途限制。
4. **业务规则与知识库：** 为虚构商家原创 20–25 份带版本/生效日期的政策，不声称是阿里、京东、亚马逊或 Shopee 官方政策。
5. **记忆实验数据：** 原创合成 30–50 组多会话客户故事，包含偏好建立、更正、撤回、重复表达、结案摘要、跨用户同名、恶意指令、过期信息和“不得凭记忆退款”。不把公开对话语料里的发言人当成真实 Olist 客户。

Olist 标注 CC BY-NC-SA 4.0。公开仓库默认只提交生成脚本、数据字典、来源/许可证记录与少量原创合成演示数据；不直接提交原始 Olist、CSDS 或 ECD 文件。真实来源由用户按协议本地获取后导入；公开托管演示或商业使用前重新核对授权。未找到可核验授权的拼多多完整订单+对话公开集，不纳入方案。

### 5.2 生成、标注与约束

业务表按 `customers → orders → order_items/payments → shipments → returns/refunds → tickets/conversations/approvals` 建模。根据真实订单状态和时间戳选出“超时未达、取消、低评价”等候选，再用**确定性规则**生成物流扫描、退货、退款及工单；不随机独立造表。记录至少包含 `source_dataset`、`source_record_id`、`origin_type`（`real_anonymized` / `synthetic_rule` / `manual_gold`）、`transform_version`、`seed_version`。历史时间平移时保留原时间、平移规则和事件先后顺序。

核心不变量：未发货不能已签收；累计退款不超过实付可退余额；已发货不直接取消；审批前不执行资金动作；`pending` 退款不能回复“已经到账”；重复请求不能重复退款。通过数据库约束、状态机和测试共同保证，**记忆不得覆盖任何不变量**。

### 5.3 两个 Seed 档位

| 档位 | 用途 | 建议规模 |
|---|---|---|
| `demo` | 无外部数据和模型 Key 即可重复演示 | 约 25 个原创确定性业务场景、20 份政策、若干跨会话记忆案例、Mock 模型响应 |
| `realistic` | 授权数据本地导入后的系统评测 | 约 1–2 万 Olist 订单样本、300–500 条合成工单、80–120 条人工复核金标业务案例、30–50 组独立记忆案例 |

`demo` 固定包含延迟包裹、损坏商品、可退/不可退、部分退款、跨用户查单、恶意知识文档、审批拒绝、服务重启恢复以及用户偏好更正。`realistic` 保留真实订单分布，但新增售后/工单字段仍标为合成。

## 6. Memory 设计：职责、实验与安全边界

### 6.1 不同“记忆”不可混为一谈

| 类型 | 内容 | 存放位置与规则 |
|---|---|---|
| 工作流/短期记忆 | 当前线程消息、订单上下文、工具结果、审批状态 | LangGraph `AgentState` + `PostgresSaver`；长会话只向模型提供必要近期消息和受控摘要，checkpoint 不自动等于摘要 |
| 权威结构化画像 | 客户已确认的语言、联系渠道、隐私同意等 | PostgreSQL 业务表，由认证用户/API 更改；Agent 可读，不能用模糊检索结果覆盖 |
| 柔性长期偏好 | 跨会话可复用、非交易性的明确沟通风格等；不重复保存结构化画像的权威字段 | `PostgresStore` 或 Mem0 OSS 的一个在线实现；须有来源、更新时间和撤回机制 |
| 情景/案例记忆 | 已结案工单的短摘要、曾采用的沟通方式 | 只存已确认结果和必要摘要；用于个性化或坐席参考，不作为新订单的裁决证据 |
| 组织知识与程序规则 | 版本化退货政策、操作规范、Prompt/工具契约 | 原创政策 RAG 与版本化代码/配置；不是客户个人记忆，不能让 Mem0 自行改写 |
| 实时业务事实 | 当前订单、支付、物流、退款余额和审批结果 | Commerce MCP → PostgreSQL 业务系统实时查询；**永远不从长期记忆作最终判断** |

这沿用 LangGraph 的“Checkpointer 管线程内状态，Store 管跨线程数据”划分；Mem0 的提取式记忆是可替换的长期记忆实现，不影响短期 checkpoint。[LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)

### 6.2 两条路线分别负责什么

| 维度 | A：`PostgresStore` + 自建薄策略 | B：Mem0 OSS + pgvector |
|---|---|---|
| 核心抽象 | `namespace + key → JSON`，精确读取/覆盖/删除；可选 embedding 语义搜索 | `add` 从对话提取事实，`search` 检索相关记忆；向量库可配置 pgvector |
| 写入策略 | 自己实现候选提取、字段白名单、稳定 key、去重、更正 | 使用内置提取/去重；外围仍需字段白名单、PII 过滤、人工更正/删除路径 |
| 可控性 | 事实结构和更新语义透明，易与业务审计结合；开发工作较多 | 上手快，适合自然语言偏好；抽取结果和消歧需额外评测 |
| 语义检索 | 默认不是向量索引；显式配置 embedding/index 后启用 | 以 embedding 检索为主要路径，可配置后端和 reranker；OSS 与托管 Platform 能力不能混称 |
| 更新与过期 | 同 key 覆盖、删除；PostgreSQL 实现支持可选 TTL，但应用仍负责判定何时失效 | `add` 对事实变更可能只追加新事实；关键更正须显式 update/delete 或应用层失效处理 |
| 成本 | 纯 KV 读写无模型费用；加入 LLM 提取/向量检索后同样产生模型费用 | 自动提取与 embedding 会产生调用费用/延迟；OSS 软件自托管不等于模型免费 |
| 框架依赖 | 与 LangGraph 自然集成，迁移 Agent 框架时需重做适配 | 与 Agent 编排相对独立，但增加 Mem0 配置、存储和版本维护 |

资料依据：[LangGraph Stores](https://docs.langchain.com/oss/python/langgraph/stores) · [PostgresStore 实现与可选 TTL](https://github.com/langchain-ai/langgraph/blob/main/libs/checkpoint-postgres/langgraph/store/postgres/base.py) · [Mem0 工作原理](https://docs.mem0.ai/core-concepts/how-it-works) · [Mem0 OSS 配置](https://docs.mem0.ai/open-source/configuration)。不把 Mem0 Platform 的托管 Graph Memory 特性算进 OSS 对照。

### 6.3 公共接口、写入和读取规则

`LongTermMemory` 在业务层暴露 `add_candidate`、`search`、`correct`、`delete`、`forget_user`；适配器负责映射到各自 SDK。统一的可审计候选记录至少有 `memory_id`、`user_id`、`kind`、规范化内容、来源会话/工单、创建/更新时间、有效状态、提取版本与是否经过人工确认。单企业项目也固定按认证的 `user_id` 隔离，未来扩展租户时预留 `tenant_id`；不得信任模型在 Prompt 中给出的 ID。

写入：仅处理允许的明确偏好或已结案摘要；先脱敏并做字段白名单检查。A 路线自己提取候选、去重/判断更正，验证后 `put`；B 路线只将允许的内容交给 Mem0 `add`，再检查提取结果、清除错误记忆并记录审计。默认不保存姓名、地址、支付信息、口令、完整聊天原文、未确认的模型推断。用户先说“客服回复请简短”，后来改为“以后请详细解释”时，A 路线更新同一稳定 key；B 路线测试旧事实是否仍被召回，必要时显式更正/删除。联系渠道等权威画像字段直接更新业务表，不经这两条柔性记忆路线。用户撤回偏好时，两条路线均需支持可验证删除。

读取：按认证用户范围检索 Top-K 少量记忆，带来源和时间标记进入模型的**不可信上下文**；没有相关记忆就不注入。订单状态、退款资格、金额、政策版本必须重新从业务系统/RAG 查询。内存服务超时或失败时降级为“无个性化记忆”，不阻断主客服流程或放宽安全校验。

### 6.4 两种运行模式与公平对照

1. **在线演示：** `MEMORY_BACKEND=postgres_store|mem0|off`，同一时刻只有一条长期记忆读取路线。`PostgresSaver` 始终运行；结构化画像和业务真相不受切换影响。
2. **离线 A/B：** 同一组合成多会话轨迹分别重放到隔离的 PostgresStore namespace 与 Mem0 collection/user scope。不得在生产用户空间双写同一偏好，也不得把两套召回结果直接拼接。
3. **存储能力对照：** 给两边喂相同、已确认的规范化事实，比较精确查找、语义召回、更正/删除、延迟。Mem0 需要以非自动推断路径或等价输入完成这一组，避免把提取能力误算作存储能力。
4. **端到端能力对照：** A 路线使用受限的自建候选提取器，B 路线使用 Mem0 内置提取；比较从原始多轮对话到最终回答的效果。固定输入、模型/embedding（若兼容）、Top-K、提示词预算、版本和硬件；不兼容处在报告中注明，不能宣称严格同条件。
5. **选型门槛：** 两边都必须做到跨用户串扰 0、记忆导致的越权/错误退款 0、用户删除后再次检索不到；其余指标报告 Recall@3、记忆写入 Precision/Recall、过期事实误用率、答案正确率、P50/P95、Token/模型成本与实现维护工作量。主业务 80+ 案例不得回归。

Mem0 OSS 的**库模式**默认使用本地 Qdrant、SQLite 历史记录及默认模型；配置 pgvector 仅改变向量后端，不等于全部状态都已在 PostgreSQL。实验需显式设置并记录 LLM、embedding、pgvector 与历史记录路径/持久化策略，隔离其内部 SQLite 历史文件；它不是应用业务数据库的第二套实现。若准备把 Mem0 作为正式在线后端，先完成重启持久化、备份、删除语义及 OpenRouter 接入兼容性 smoke test；不通过则保留离线实验结果，在线继续使用 PostgresStore。[Mem0 OSS 默认组件](https://docs.mem0.ai/open-source/overview) · [Mem0 OSS 配置](https://docs.mem0.ai/open-source/configuration)

## 7. Agent、RAG 与 MCP 的实现边界

### 7.1 LangGraph 状态与节点

`AgentState` 包含认证身份、会话 ID、当前工作流阶段、最近对话摘要、版本化 `RouteDecision`、已验证槽位、`pending_clarification`、`plan_revision`/Replan 计数、当前订单、检索证据、结构化提案、审批状态、错误码和恢复信息。主节点为 `input_guardrail → load_context → resume_clarification? → route → clarify / knowledge / after_sales → policy_check → interrupt / answer / human_handoff → output_guardrail`；完成后单独触发受控记忆候选写入。`load_context` 只加载已认证身份、近期消息和必要的线程状态；柔性长期记忆在路由后按需读取，避免旧记忆先行左右当前意图。只把必要的状态摘要写入观测平台，避免传播 PII。执行步数和 Replan 次数均有上限，工具失败有明确重试/人工降级。

### 7.2 意图识别与路由的具体实现

采用与现有技术栈一致的**混合式、状态感知路由**：FastAPI/Pydantic 做边界校验，LangGraph `route` 节点编排，OpenRouter 上经能力验证的轻量模型做结构化语义识别，Python 确定性策略决定最终分支。分类结果只表达用户诉求，不授予业务权限。LangGraph 官方的 routing workflow 使用结构化模型输出和条件边；OpenRouter 的 JSON Schema 输出仅在兼容的模型/端点上可用，端点支持需在启动与回归测试中验证。[LangGraph Routing](https://docs.langchain.com/oss/python/langgraph/workflows-agents) · [OpenRouter Structured Outputs](https://openrouter.ai/docs/guides/features/structured-outputs)

**1）分层标签与槽位。**第一层 `route` 只决定 `knowledge` / `after_sales` / `clarify` / `human_handoff` / `out_of_scope`，与现有两条专业路径一致。第二层 `intent` 细分为 `policy_qa`、`order_status`、`shipment_tracking`、`cancel_request`、`return_request`、`refund_request`、`complaint`、`human_request`、`unknown`；一条消息可有多个标签，但首版最多保留 3 个，并记录优先级。`complaint` 可与订单问题并存，不单独创建 Agent。槽位只抽取 `order_ref`、商品/包裹指代、问题描述、期望动作和时间线索；客户身份由认证上下文提供，模型不得创建 `customer_id`、退款金额或审批身份。标签和所需槽位在配置中版本化，每类有定义、正反例与相邻类别边界。

**2）固定决策顺序。**`input_guardrail` 先做认证、输入大小/格式和注入风险检查。`route` 内先读取 `PostgresSaver` 中的工作流阶段：等待主管审批的线程只允许由已认证主管通过审批 API 恢复；客户追问只返回待审批状态，不重新创建退款。用户明确请求人工、系统已有人工接管、或确定性安全规则命中时直接走预定分支。其余请求交给模型识别；规则只处理可靠的身份/状态/显式命令，不靠大量关键词硬猜自然语言语义。

**3）受约束的模型输出。**使用专门的路由 Prompt，只传当前消息、最近必要轮次、线程摘要和允许的标签定义，不传完整订单历史、无关长期记忆或 RAG 文档。`RouteDecision` 用 Pydantic/JSON Schema 定义：`schema_version`、`intents[]`、`candidate_slots`、`missing_slots[]`、`needs_clarification`、`route_hint`、`reason_code`；对枚举、数量上限、互斥关系和槽位类型再次做服务端校验。优先使用 OpenRouter 的 `response_format: {type: "json_schema"}` 和 `require_parameters: true` 限定兼容端点；即使请求 strict mode，仍须在本地验证，不能假定所有提供方都严格遵循 Schema。固定并记录模型 ID、端点/提供方、Prompt 与标签版本，不把可变的“最新模型”别名作为评测基线。[OpenRouter Structured Outputs](https://openrouter.ai/docs/guides/features/structured-outputs)

**4）多意图与槽位消解。**“包裹没到，能退吗？”应识别 `shipment_tracking + refund_request`，服务端先查询本人订单/物流，再检索当前政策与可退资格，最后只生成提案；不因为分类含 `refund_request` 就调用 `issue_refund`。对“这个能退吗”“还是上次那个订单”等指代，只在已验证的当前线程订单中消解；没有唯一匹配时询问订单，不猜 ID。不同意图共享已验证订单上下文，但高风险动作仍逐项经过政策、余额、权限和人工审批。模型输出的工具名或执行顺序只能作候选，由确定性路由策略映射到允许的 LangGraph 节点。

**5）不确定性与故障降级。**不用模型自报的 `confidence=0.9` 作为可靠概率。未校准前以可观察信号决策：Schema 失败、标签冲突、缺少必需槽位、指代无法消解、跨分支多意图、输入超出已知范围。可解决的缺信息进入下节有次数上限的 `clarify`，投诉/高风险/超限则 `human_handoff`；模型超时或结构化输出无效可对已验证的备用路由模型重试至多一次，仍失败则安全澄清/人工接管，**不尝试写业务工具**。第二模型只在必要时使用，是否保留以路由评测的收益、延迟和成本决定。

**6）交付模块与测试边界。**实现 `IntentTaxonomy`（版本化标签及示例）、`RuleGate`（状态/安全优先级）、`IntentClassifier`（OpenRouter 结构化调用）、`SlotResolver`（指代和缺失字段）、`RoutePolicy`（受控分支映射），以及纯函数可测试的 LangGraph 条件边。无 Key 模式以固定 Mock 分类结果覆盖所有路由路径；真实模型模式做端点能力、Schema 遵循和延迟 smoke test。不引入另一套 Rasa/BERT 服务或模型微调；只有在评测显示小模型/规则无法满足目标时再考虑训练专用分类器。

**7）反馈与版本治理。**坐席纠正的错路由、客户连续澄清及人工接管原因经脱敏和复核后进入候选回归集，不直接自动修改 Prompt。每次调整标签定义、示例、模型或规则均提升版本号，先跑锁定测试集，再在合成/回放流量中比较新旧路由；出现高风险漏判、结构化失败或延迟明显恶化时回滚到上一版配置。按意图类别监控分布变化，避免总 Accuracy 掩盖少数退款/越权类别的退化。

### 7.3 澄清与 Replan：槽位契约和有界状态机

**槽位先区分候选与已验证值。**模型只能抽取 `candidate_order_ref`、商品/包裹指代等候选；`verified_order_id` 必须由服务端结合认证身份经 Commerce MCP 验证。可复用的订单仅限当前线程中已验证且唯一的订单，长期记忆或相似文本不能补全订单 ID。业务所需字段分阶段检查：能否路由、能否只读查询、能否生成提案、能否写入，不能把所有缺项一次性当成阻断条件。

| 意图 | 回答/只读查询前的最小槽位 | 提案或受控写入前还需什么 | 缺失时优先澄清 |
|---|---|---|---|
| `policy_qa` | 能确定政策主题；泛问退货规则不要求订单 | 若问“我的订单能否退”，再需要本人已验证订单和现行政策证据 | 主题过泛时问具体是取消、退货还是退款；不为通用政策强问订单号 |
| `order_status` | 本人 `verified_order_id` | 无写入 | 先问订单号；若当前线程已有唯一已验证订单可直接沿用 |
| `shipment_tracking` | 本人 `verified_order_id`；多包裹时还需目标包裹 | 无写入 | 先确定订单，再在必要时确定包裹 |
| `cancel_request` | 本人 `verified_order_id` 才能查询可取消状态 | 取消范围（整单/指定商品）和明确请求；当前 MVP **没有取消订单写工具**，只能解释政策、建工单或转人工 | 先确定订单；用户只是问“能否取消”时不提前要求确认执行 |
| `return_request` | 本人 `verified_order_id` 可先查资格 | 涉及商品/数量、退货原因和明确提交意愿；创建申请前重新校验 | 多商品先问哪件；提交前再问必要原因，不一次索要无关字段 |
| `refund_request` | 本人 `verified_order_id` 可先查可退资格 | 退款范围/相关商品、原因、明确诉求；金额由服务端计算，执行还需主管批准与幂等键 | 先确定订单，再问退款范围或原因；不得让模型补退款金额 |
| `complaint` | 可理解的问题描述；一般投诉不必有订单 | 订单相关投诉若要查交易，才需本人已验证订单；升级人工不要求更多槽位 | 描述不清时问发生了什么；高风险或明确要求人工直接转接 |
| `human_request` | 无 | 无 | 不澄清，直接人工接管 |
| `unknown` / `out_of_scope` | 无 | 无 | `unknown` 最多问一次“您想查订单、问政策还是申请售后？”；仍不明则人工。`out_of_scope` 直接说明范围，必要时人工 |

**澄清决策优先级：**认证/越权检查 → 工作流阶段 → 意图冲突 → 目标订单/包裹 → 商品与范围 → 原因/提交意愿。每轮只问一个最能推进流程的问题；同一层紧密相关的字段可合并在一句话中，不能要求客户重复已验证的信息。候选订单号不存在或不属于当前用户时，不泄露该订单详情；按统一的“无法核验，请检查订单号或联系人工”处理，不靠继续猜测解决越权。标签冲突如“取消还是退款”先查询本人订单的发货状态，再问客户希望的处理方式；兼容意图如“查物流并问能否退”先做安全的只读查询，再处理退款咨询，不强行澄清整个请求。

**次数上限与状态。**一个待处理任务最多发出 **2 次澄清提问**（首次询问 + 至多一次针对未解决问题的改问）；`unknown` 只问 1 次。同一线程连续澄清最多 **3 轮**，跨话题也不靠重置无限追问；完成一次正常回答或人工接管才清零。超过任一上限立即转人工，并附已验证事实及未解决问题。`AgentState.pending_clarification` 保存 `question_id`、原始意图集、待补槽位、已验证槽位、提问次数、连续澄清次数、创建时间和路由版本；24 小时未回复则标为过期，下次输入重新分类，不将旧提案视为有效。计数由服务端维护，不信任模型输出。

**客户澄清的生命周期。**生成问题后 checkpoint 写入 `pending_clarification`、向客户回复并结束本轮 Graph；同一 `thread_id` 的下一条客户消息以普通输入进入 `load_context → resume_clarification → route`。先判断它是在补槽位、纠正旧信息，还是提出了独立新诉求；只合并经验证的槽位。新诉求会将旧待处理任务标为 `superseded`，保留审计而不继续执行旧工具。同一线程的客户消息串行处理，更新时检查预期 `plan_revision`，防止并发回复覆盖新状态。这里不使用主管审批的 `interrupt/Command(resume=...)` 作为普通客户消息入口；LangGraph 官方区分多轮普通输入与真实 interrupt 恢复，后者仍专用于审批。[LangGraph Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts) · [LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)

**Replan 触发与执行。**用户补充/更正槽位、明确改意图、只读工具显示订单或包裹已变化、证据/政策版本过期、主管改动退款提案时，更新 `plan_revision`，废弃受影响的旧候选槽位、检索证据和待执行步骤，再从**已验证状态**重新运行 `RoutePolicy`；必要时才重跑 LLM 分类。只读工具瞬时失败可先重试一次，仍失败则澄清或人工；授权失败不是可绕过的 Replan 条件。自动 Replan 每个请求最多 **2 次**，加上客户澄清形成的总 `plan_revision` 最多 **4 次**；同一计划连续两次没有新增已验证事实或有效路径变化，视为循环并转人工。用户明确提出新的独立诉求则开启新任务，而不是给旧计划无限续命。

**资金动作的失效规则。**Replan 后任何旧退款提案、审批凭证与预排写工具均标为失效，审批记录绑定 `plan_revision`、订单状态/政策版本；主管若修改金额，必须重新做资格、余额、权限与幂等校验。若模拟退款已经提交成功，不自动撤销或重放，而是形成新的受控工单/人工流程；所有写工具继续依赖事务校验和幂等键。Replan 决定“下一步该做什么”，不是绕过审批的自由重试。

**示例：**“我那个包裹没到，能退吗？”且当前线程有两个已验证订单 → 先问哪一个订单（第 1 次澄清）；用户回复订单 A → 验证归属并 Replan 为“查物流 → 核对政策/资格 → 只生成退款提案”。若用户改说“算了，只查订单 B 的物流”，将旧退款任务标为 `superseded`，新任务只查 B；旧提案或审批不能继续执行。

### 7.4 RAG

原创政策 PDF/Markdown/HTML 支持上传、解析、标题分块、版本、生效日期和停用。PostgreSQL 全文检索与 pgvector 向量检索并行，RRF 融合；回答显示文档标题、版本和片段位置。旧版政策不参与在线引用，但留在专用评测场景验证版本冲突。证据不足则澄清或转人工，不编造政策。客户记忆检索与政策 RAG 分别执行，不把个人偏好混入政策索引。

### 7.5 Commerce MCP 与 UI

只读工具：`get_customer_profile`、`get_order`、`track_shipment`、`check_return_eligibility`。受控写入：`create_return_request`、`issue_refund`。服务端从认证上下文取身份，不接受模型自报角色或客户 ID；写操作需要审批凭证、幂等键和事务内再次校验。主管改额后重新执行政策/余额校验。MCP 只模拟业务系统，不接真实支付、物流或电商账号。

客户可流式聊天、查看订单与引用并反馈；坐席可接管工单；主管可审批/驳回和查看审计。SSE 事件固定为 `message.delta`、`clarification.required`（含 `question_id` 和可选回答提示）、`tool.started`、`tool.completed`、`approval.required`、`run.completed`、`run.failed`；前端类型由 OpenAPI 生成。

## 8. 观测、评估与发布门槛

### 8.1 观测

- **OpenTelemetry + Jaeger：** 查看路由规则门控、分类模型、槽位消解、澄清/Replan、记忆检索、RAG、模型、MCP、PostgreSQL、Celery 的耗时、失败和重试；传播 Trace Context，不记录原始姓名、地址或支付信息。
- **Langfuse（可选）：** 用 LangGraph/LangChain Callback 与手工 Observation 记录脱敏的路由来源（规则/模型/降级）、标签、澄清/重规划原因码与 `plan_revision`、Schema/Prompt/模型版本、RAG、记忆候选/命中摘要、工具、政策和审批；保存 Token、估算成本与评测分数。故障不得阻断主流程。
- **运行档位：** 默认 Compose 启动核心服务与 Jaeger；可接 Langfuse Cloud，或以单独 Profile 自托管 Langfuse。ClickHouse/对象存储等不塞进最小启动链路。

### 8.2 版本化评测

`evals/datasets/*.jsonl` 是版本化事实来源；Langfuse 用于展示 Dataset Run、Observation 与 Score，不是唯一存储。每条业务案例固定身份、订单/政策版本、输入、多轮上下文、期望路由、必需/禁止工具、引用、审批与安全条件及来源。按订单/会话分组切分开发集和锁定测试集。

| 层 | 核心检查 | 方法 |
|---|---|---|
| 意图路由与多轮 | 一级分支、细粒度多标签、槽位、指代、澄清/人工升级、Replan 与循环检测；订单上下文是否混淆 | 金标 Accuracy、Macro-F1、各类 Recall、槽位 F1、多意图 Exact Match、澄清成功/误澄清率、平均澄清轮数、Replan 正确率、混淆矩阵和会话测试 |
| 工具轨迹 | 工具名、参数、顺序、重试、禁止调用、步骤上限 | 确定性轨迹断言 |
| RAG | Hit@5、MRR、引用有效性、旧政策误引、证据不足拒答 | 检索金标 + 规则检查 |
| 业务安全 | 跨用户访问、越权退款、重复退款、审批恢复、PII | 单元与 PostgreSQL/MCP 集成测试；零容忍 |
| Memory | 跨会话 Recall@3、写入 Precision/Recall、更正/失效、撤回删除、跨用户隔离 | 30–50 组合成故事，离线 A/B + 关键路径集成测试 |
| 回答质量 | 事实忠实度、政策合规、完整性、语气 | 独立 OpenRouter Judge + 人工校准 |
| 性能成本 | 首 Token、P50/P95、Token、模型成本、工具与记忆调用次数 | OTel/Langfuse 基线与回归对比 |

业务首版至少 80 条案例，覆盖真实语料启发的问题、订单事实约束、退款边界、多轮和 Prompt Injection，关键安全样本人工复核。另建设约 120–160 条版本化意图专项样本（可与 80 条业务案例重叠），覆盖普通问答、相近类别、至少 30 条多轮指代、至少 20 条多意图、至少 20 条高风险/注入/越权表达，以及至少 25 条缺槽位、歧义、标签冲突、改意图、工具状态变化和 Replan 上限的多轮脚本；类别可重叠，按**客户/会话**分组切分开发集和锁定测试集。离线比较规则-only、结构化模型-only 与混合路由三种消融基线；人工复核混淆类别和失败路径。初始目标：一级路由 Accuracy ≥ 90%，工具/参数通过率 ≥ 90%，RAG Hit@5 ≥ 85%；报告细类 Macro-F1、槽位 F1、澄清率、误澄清率、平均澄清轮数、Replan 正确率、P95 和单次路由成本，基线后再确定细类门槛。**未经审批退款、跨用户订单访问、重复退款、记忆跨用户泄漏、路由失败触发业务写操作、失效提案执行和无界澄清/Replan 循环均必须为 0**。这些是计划门槛，基线运行后确认难度；主观 Judge 分数不单独作安全发布依据。记忆的 Recall/成本不预设谁胜，报告绝对值与权衡。

每次提交执行无 Key Mock/规则/集成评测；手动或定期执行真实模型实验，与固定基线比较。失败 Trace 脱敏、人工核验后进入回归集。Prompt、模型、数据、政策、记忆后端和代码版本写入报告，确保可复现。

## 9. 七周路线与范围控制

| 周 | 交付物 | 退出条件 |
|---|---|---|
| 1 | Monorepo、Compose、PostgreSQL/pgvector、迁移、RBAC、`demo` Seed、Olist 本地导入脚本、数据字典和意图标签/分阶段必需槽位契约 | 无 Key 能启动并查询真实/合成来源标记；核心表约束和路由 Schema/规则单测通过 |
| 2 | OpenRouter 适配、`RuleGate`/`IntentClassifier`/`SlotResolver`/`RoutePolicy`、`pending_clarification` 与有界 Replan 状态、LangGraph 条件边/`PostgresSaver`、SSE 会话、客户 UI | 单/多意图、缺槽位与歧义澄清、次数上限、改意图重规划、人工请求和分类失败降级端到端可运行；重启不丢状态 |
| 3 | 原创知识库、Celery 索引、混合检索、引用与版本控制 | 正确引用当前政策；证据不足可拒答/升级 |
| 4 | Commerce MCP、策略引擎、主管 UI、审批 interrupt/resume、提案版本失效和幂等模拟退款 | 批准/拒绝/改额/旧审批凭证失效/重复请求/重启恢复通过集成测试 |
| 5 | OTel/Collector/Jaeger、可选 Langfuse、路由来源/版本追踪、PII 脱敏、审计、安全测试 | 可追踪分类、降级、跨 API/MCP 请求与审批恢复；关键越权测试全通过 |
| 6 | 80+ 业务金标与 120–160 条意图专项评测（含澄清/Replan 脚本）、Judge 校准、路由消融对照、CI、Playwright、演示脚本、架构图和 README | 路由混淆矩阵、澄清/重规划及成本/延迟报告可复现，安全门槛达标，主链可在新环境按说明运行 |
| 7 | `LongTermMemory` 两个适配器、30–50 组记忆故事、离线 A/B、在线单后端演示与选型报告 | 记忆更正/撤回/隔离通过；指标、成本和取舍可复现；主业务集不回归 |

第八周只作缓冲、缺陷修复与简历展示打磨，不继续扩展新功能。若时间只够六周，优先保留**查单 → 退款提案 → 人工审批 → 幂等执行 → 评估/追踪**主链；记忆对照交付隔离的离线实验报告，不将未经验证的 Mem0 接入在线链路。先削减可选多平台数据源、复杂管理 UI、Langfuse 自托管档位，不削减权限和退款安全测试。

## 10. 明确不做与最终交付

不做多租户计费、真实支付/退款、真实电商平台账号连接、语音/微信全渠道、Kubernetes、模型微调、自动成本路由、图数据库记忆、让两个长期记忆后端同时在线融合结果，或把不同平台数据伪装成同一套真实交易。对来源不明或许可不清的客户记录，不抓取、不公开再分发。

最终仓库包含：可运行的前后端与 MCP、迁移和两档 Seed、数据来源/许可说明、版本化政策与评测集、规则/集成测试、可选 Langfuse 配置、Jaeger 示例 Trace、架构图、威胁模型、3 条固定面试演示脚本，以及可复现的业务与记忆 A/B 报告。README 明确区分“匿名真实订单”“公开客服语料”“规则合成售后事件”和“原创合成记忆故事”；只展示实际测得的指标，不把计划目标写成已实现结果。
