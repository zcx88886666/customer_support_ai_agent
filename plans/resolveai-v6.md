# ResolveAI v6：企业级电商售后多 Agent 系统实施计划

> 状态：设计计划，尚未编码；数据量、评测门槛和工期均为目标而非实测。日期：2026-09-28。  
> 历史版本保持原样：[v1](./resolveai-v1.md) · [v2](./resolveai-v2.md) · [v3](./resolveai-v3.md) · [v3 评测规范](./resolveai-v3-evaluation-spec.md) · [v4](./resolveai-v4.md) · [v5](./resolveai-v5.md)。本文独立可读；冲突时以 v6 为准。售前系统仍是独立项目，不进入本文。

## 1. 项目目标、范围与 v6 决策

面向 AI 应用工程师简历，构建单企业、可本地运行、可复现评估的电商售后系统。主业务链为：已认证客户咨询 → 意图/槽位识别 → 按需调用专职 Agent 获取物流与政策证据 → 服务端规则校验 → 客户确认退货 → 仓库入库质检 → 主管批准 → 幂等模拟退款 → 审计、Trace、Dashboard 与评估报告。项目要体现真实业务约束与可测的系统质量，但不接真实支付或电商平台私有流水。

v5 确立的**有边界 Multi-Agent** 继续保留：主 LangGraph 包含 `Coordinator`，以及只读的 `PolicyAgent`、`OrderLogisticsAgent` 两个专职子图。只在复合问题需要双领域证据时并行，Agent 不获得审批或退款写权限。[LangChain 多 Agent 模式](https://docs.langchain.com/oss/python/langchain/multi-agent)

v6 的观测决策是：**开发期默认接入 Langfuse Cloud Hobby**，展示 Agent Trace、Experiment、Score 和 Dashboard；但 **Prompt 的唯一权威来源改为本地 Git 版本库**。Agent 与评测用 Prompt 只在仓库的 `prompts/catalog/` 编辑和发布，运行时从已锁定的本地版本加载；Langfuse Prompt Management 仅保存由本地单向同步的镜像，供版本关联、观测和对照分析，Cloud UI 的直接修改不进入运行路径。Git 历史不等于备份，另做本地/离线备份和恢复演练。业务政策、金额/权限规则仍归本地版本化代码与 `policy_bundle` 管理，绝不移进 Prompt。OpenTelemetry/Collector/Jaeger 继续留在本机，业务审计和完整评测结果继续本地持久化；Cloud 不是业务真相库或发布评分器。未来资源足够时可换为 Langfuse OSS 自托管，核心业务代码不依赖 Prompt 迁移，镜像与观测出口改指新实例，权威 Prompt 仍留本地。

沿用前版的已确认决策，并更新观测/Prompt 部署方式：

| 主题 | v6 执行规则 |
|---|---|
| 退货 | 合格已签收实物商品，签收次日起七个自然日内申请无理由退货；例外、旧有利承诺及争议由版本化规则处理 |
| 写入 | 客户确认订单/商品/数量/原因和提交意愿后创建申请；只有入库质检通过、服务端生成提案、主管批准后才执行模拟退款 |
| 权限 | 单企业；Keycloak OIDC；customer/support/warehouse/supervisor 四角色；对象级归属检查在服务端执行 |
| 主数据 | 一套可重放、因果一致的合成业务世界；公开真实历史数据仅用于可许可的分布校准或隔离导入，不拼接虚假跨源关系 |
| 规模 | 25 个固定演示场景、约 10 万单日常档、按需 100 万单规模档；数量是建设目标 |
| 政策 | 文本、结构化规则、索引、生效时间形成不可变 `policy_bundle`，先验证再原子切换 |
| 模型 | OpenRouter 任务化配置，固定可复现实验版本；仅对技术性故障受控回退，不绕过审核或业务拒绝 |
| 记忆 | PostgresSaver 管线程；PostgreSQL 结构化画像；PostgresStore 与 Mem0 OSS 做隔离 A/B；交易事实永远实时查业务库 |
| 观测 | OTel → Collector → Jaeger 为本地系统 Trace；Langfuse Cloud 为开发期默认 Agent Trace/评测可视化；本地报告与审计在 Cloud 不可用时仍有效 |
| Prompt | 本地 Git 中的 `prompts/catalog/` 为唯一可编辑来源；本地 release manifest 锁定精确内容 hash；Langfuse Cloud 只接收单向镜像，不能反向决定运行版本 |

**硬边界：**LLM、任何子 Agent、Memory 或 RAG 都不能创建客户身份、决定退款金额、签发审批、修改账本。权威事实优先级为当前业务库与适用政策 > 已确认结构化画像 > 经过筛选的柔性记忆 > 模型推断。所有交易、退款、物流和对话主数据均为模拟，不把生产相似性写成真实平台服务经历。

## 2. 总体架构和运行档位

| 层 | 组件 | 职责 |
|---|---|---|
| 前端 | Next.js、TypeScript、Tailwind、shadcn/ui | 客户聊天、工单、仓库、主管审批、政策管理、评估与 Trace 入口 |
| API | FastAPI、Pydantic、SQLAlchemy、Alembic | OIDC、RBAC、SSE、会话、审批、审计和事务边界 |
| Agent | LangGraph `StateGraph` 主图＋两个专职子图、PostgresSaver | 意图、澄清、受控分派、证据合并、Replan、审批等待/恢复 |
| 模型 | OpenRouter 适配层 | 意图、专职分析、合成答复、离线 Judge、Embedding 的版本化配置 |
| 业务工具 | 独立 Commerce MCP、Streamable HTTP | 客户本人订单/物流/资格只读查询；创建退货走独立受控入口 |
| 主存储 | PostgreSQL＋pgvector | 业务表、审计、Graph checkpoint、政策索引、画像和默认长期记忆 |
| 异步任务 | Redis＋Celery | 索引、批量数据/评测、截止时间提醒、可选记忆提取；Redis 不作业务真相 |
| 系统观测 | OpenTelemetry SDK → Collector → Jaeger | API、主图、子图、MCP、Worker、数据库的因果与耗时 |
| Agent 观测与 Prompt 镜像 | Langfuse Cloud（默认 Hobby）＋ Python SDK v4 | 专职 Agent/Prompt/Tool/Token/成本、Prompt 版本镜像、Experiment/Score/Dashboard；Cloud 故障不阻断本地 Prompt 与业务流程 |
| 指标与报告 | Prometheus＋Grafana 可选 profile；JSONL/JSON/HTML 本地报告 | 服务运行、审批积压、多 Agent 路由与质量回归 |

默认开发档启用核心服务、PostgreSQL、Redis、OTel Collector、Jaeger，并为观测连接 Langfuse Cloud 项目；本机不启动 Langfuse 自托管栈。无 Key Mock 档从本地 Prompt release 运行主业务、安全规则和固定评测，不依赖 Cloud；真实模型档也只从本地加载 Prompt，Cloud 只同步镜像、Trace 与 Experiment。Prometheus/Grafana 为可选 profile。Langfuse Cloud 产生观测数据上传的网络依赖和访问窗口，但不增加**运行时 Prompt 获取**的网络依赖；OpenRouter 的模型费用与 Langfuse 额度分别计算。[Langfuse Cloud 功能/额度](https://langfuse.com/pricing) · [Python SDK](https://langfuse.com/docs/observability/sdk/overview)

只建设**一个主图＋两个专职子图**，不引入代理之间的自由聊天、自动生成新 Agent、群体投票或通用 Multi-Agent 平台。人工主管属于业务角色，不是第三个自动审批 Agent。

## 3. Multi-Agent：职责、交接、并行和失败收敛

### 3.1 角色与工具权限

| 组件 | 允许做什么 | 禁止做什么 | 产物 |
|---|---|---|---|
| `Coordinator` 主图 | 解析意图、检查槽位、决定单/双专职 Agent、整合证据、澄清、答复、启动受控退货流程 | 自造身份、直接判断金额、凭自然语言执行退款 | `RouteDecision`、`DelegationPlan`、最终答复/待办 |
| `PolicyAgent` 子图 | 在服务端指定的 `policy_bundle_id` 范围内改写检索问题、调用全文/向量检索、核对条款与例外 | 查询客户隐私、选择其他政策版本、判断订单事实、调用写工具 | `PolicyFinding`：条款 ID、版本、有效日期、适用条件、证据缺口 |
| `OrderLogisticsAgent` 子图 | 在已认证且已验证归属的订单范围内选择只读 Commerce MCP 工具，查询订单、包裹和签收事件 | 查询其他客户、解释法律/政策、创建退货/退款/审批 | `OrderFinding`：订单/包裹状态、时间戳、事实版本、异常标记 |

两个子图各有独立 Prompt、Schema、工具白名单、模型调用和内部分析步骤；它们不是把两个固定检索函数换个 Agent 名称。可按问题决定查询、必要时再查一次并解释证据不足，但上限严格受控。子图不直接给客户发话，只把结构化结果交还主图。工具权限通过构造子图时的白名单和服务端授权同时限制，不能只靠 Prompt 写“请勿越权”。

### 3.2 路由与执行顺序

主图先执行认证/对象级归属检查、`RouteDecision` 结构化意图识别、`RuleGate` 与槽位验证。对订单相关问题，服务端先取得必要的订单归属/版本及适用 `policy_bundle_id`；这一步是确定性前置校验，不是让 PolicyAgent 猜应使用哪个版本。订单指代不清或多包裹不明确时先澄清，不向子图派发未经验证的订单标识。

| 输入类型 | 主图行为 |
|---|---|
| “七天无理由退货怎么计算？” | 仅调 `PolicyAgent`，使用当前 active 政策包 |
| “我的包裹到哪了？” | 仅调 `OrderLogisticsAgent`，使用已验证订单；有多个包裹则先澄清 |
| “包裹还没到，我能退吗？” | 若订单明确，两个子图并行：物流事实与适用政策分别取证；合并后解释“未签收异常”与“已签收七天退货”的区别，不能直接创建退货 |
| “我要退这件商品” | 先完成身份/商品/数量/原因/提交意愿澄清，规则服务再核资格；专职 Agent 只用于解释事实和条款，写入仍走领域服务 |
| 明确要求人工、越权、资料冲突、高风险不确定 | `RuleGate` 直接转人工或拒绝；不把子图当作绕过门槛的备用路线 |

实际实现采用 LangGraph 父 `StateGraph` 与两个独立子图。父图的条件分支在复合意图时 fan-out 到两个子图、fan-in 到证据合并节点；可用条件边或 `Send`，对并行写回字段使用显式 reducer/按 `task_id` 归并，不能依赖并发完成顺序。专职子图按**每次委托隔离状态**运行，不保留跨用户、跨会话的内部对话；持续会话与审批 checkpoint 留在父图 `PostgresSaver`，柔性长期记忆仍由统一 Memory 接口管理，避免对同一持久化子图并发调用造成 checkpoint 冲突。[LangGraph 并行分支与 Send](https://docs.langchain.com/oss/python/langgraph/use-graph-api) · [子图持久化模式](https://docs.langchain.com/oss/python/langgraph/use-subgraphs)

### 3.3 交接契约与证据合并

`DelegationTask v1` 至少包含 `task_id`、`thread_id`、`plan_revision`、`specialist`、`question_scope`、`verified_order_ref?`、`policy_bundle_id?`、`evidence_version_hint?`、`deadline`；客户身份和授权上下文由服务端运行时提供，不把 JWT、角色或 PII 复制进 Prompt/checkpoint。原始客户消息仅传必要的脱敏片段。`SpecialistFinding v1` 含 `task_id`、`plan_revision`、`status=ok|incomplete|conflict|error`、结构化事实、`source_ids`、源版本/查询时间、未解决条件和资源用量；Pydantic 校验失败视作不可用证据，不当作自由文本直接转述。

合并节点必须检查：两个结果是否属于当前任务和 plan_revision，订单/政策版本是否仍有效，引用条款是否可定位，时间/金额/状态是否相互冲突。订单状态与政策条件不能互相替代；子图的“可能可退”只是说明，不是资格判定。答案分别标注“查到的订单事实”“适用条款”“尚需确认”；若物流显示未签收，不能套用签收次日起七天的规则。如果某一领域失败，只能回答另一领域已核实的部分并明确缺口，涉及退款资格则转人工或让客户稍后重试，不能补猜缺失信息。

每请求最多两轮派发，第二轮只重跑受影响的子图；每轮最多两个子图，每个子图最多两次只读工具调用，整个请求设可配置的总超时/Token/成本上限并记录实际值。默认总 LLM 调用硬上限为 10，超限转澄清/人工；不允许 Agent 相互递归调用。429、5xx、网络超时只在受控次数内重试，政策证据缺失、权限拒绝和业务规则拒绝不触发换模型“再试一次”。客户改口或后端版本变化时增 `plan_revision`，迟到的旧子图结果必须丢弃；不能用旧提案继续写入。

### 3.4 与安全业务闭环的分界

专职子图全程只读。`create_return_request` 只能由 Coordinator 在客户明确确认后调用受保护领域服务；服务端重校验对象归属、商品、数量、时间窗、状态和幂等键。仓库收货/质检、退款提案、主管批准、模拟退款由受控 API/Worker 完成，Agent 不能把子图输出当作审批凭据。人工审批 `interrupt/resume` 仍在父图/领域服务边界；恢复时重新读取数据库审批事实与适用版本。并行多 Agent 的存在不改变退款安全门槛。

## 4. 业务实体、身份、七天退货和退款状态机

核心实体按因果顺序生成并保存：customers / customer_profiles → products / sellers → orders / order_items → payments / paid_allocations → shipments / shipment_events → return_requests / return_items → warehouse_receipts / inspections → refund_proposals → approvals → refund_ledger → tickets / conversations / audit_events。金额用整数分并带货币；合成主世界统一 CNY。外键、唯一索引、CHECK、状态版本和领域服务的跨表校验共同防止孤儿数据与非法状态。

单企业无需 `tenant_id` 隔离；`seller_id` 是业务维度，首版跨 seller 争议转人工。Keycloak OIDC 中的 customer 仅访问本人订单，support 仅查看授权工单，warehouse 可记录入库/质检，supervisor 可批准/拒绝提案。FastAPI 和 MCP 均验签名、issuer、audience、过期与角色，并做每次对象级授权；MCP 不接受模型自报的 customer_id、role 或审批令牌。审计保留 actor、动作、前后版本、policy_bundle、提案、幂等键及 trace_id，Trace 不代替资金审计。[Keycloak 应用安全](https://www.keycloak.org/securing-apps/overview) · [OWASP 对象级授权](https://owasp.org/API-Security/editions/2023/en/0xa1-broken-object-level-authorization/)

无理由退货仅限合格且已签收的实物商品。以 `Asia/Shanghai` 业务日历计算，签收次日为第 1 天，第 7 天 23:59:59 前收到申请有效，例如 9 月 4 日签收，则 9 月 5 日至 11 日为申请窗口。例外品类、已告知并确认的特殊商品、商品完好要求和购买时更有利的明确承诺均由结构化规则和证据字段处理；规则不确定、损坏/质量争议、逾期争议、未签收/丢件、跨境税费和复杂券分摊首版转人工或独立异常工单，不让模型猜法律结论。本项目是虚构商家的演示政策，参考官方七天规则但不对任何真实平台作合规承诺。[官方七日无理由退货办法](https://www.samr.gov.cn/zw/zfxxgk/fdzdgknr/fgs/art/2023/art_26ca8fe29e184edd899fa0a7a060d935.html)

业务状态：`eligible → return_requested → in_transit_back → received → inspected_passed | exception → proposal_pending → approved | rejected → refund_issued`。客户明确确认订单、商品、数量、原因和提交意愿后，可在事务内创建退货申请，无需主管先批。warehouse 记录实际收货时间、数量和质检，少件/错件/损坏争议转人工。对符合条件的退货，从仓库实际收到退回商品时间起算七天退款处理期限；系统对入库、质检、审批积压预警，不允许内部等待无限拖延。客户可见状态必须区分“申请已提交”“等待审批”和“模拟退款已执行”。

退款金额按购买时记录的实付分摊额算：订单项**目标累计退款**＝`floor(该项实付金额 × 累计合格退货数量 / 购买数量)`；**本次退款**＝目标累计退款－已退款金额，最后一件承接舍入余数，并受订单剩余实付额约束。禁止负数、超购数量和超额退款。合成主流程只用单一原支付方式，模拟原路退回；复杂优惠、运费/税费和多支付方式转人工。提案绑定 order_version、return_id、inspection_id、policy_bundle_id、plan_revision、金额与明细；主管改额需重新计算形成新提案，不能原地暗改。`issue_refund` 在事务中锁余额、重新校验审批/版本/金额；`proposal_id` 和 `idempotency_key` 唯一约束保证重复请求至多记一笔。`interrupt` 恢复会重新执行相关节点，因此副作用仍须靠领域服务幂等保护。[LangGraph interrupt](https://docs.langchain.com/oss/python/langgraph/interrupts)

Commerce MCP 对 Agent 开放 `get_order`、`track_shipment`、`check_return_eligibility` 等只读工具；客户确认后的 `create_return_request` 由受控入口调用。`record_warehouse_receipt`、`record_inspection` 是仓库 API，`create_refund_proposal` 是规则服务动作，`approve/reject_proposal` 是主管 API；`issue_refund` 只能由受控恢复流程根据数据库中有效审批事实调用。各入口复用同一领域服务，不能在 Graph、API、MCP 各实现一套有差异的规则。

## 5. 意图、槽位、澄清、Replan 与分派

父图 `AgentState` 保存 thread_id、当前阶段、最近消息与摘要、候选/已验证槽位、`RouteDecision`、`DelegationPlan`、子图结果引用、澄清次数、plan_revision、订单引用、政策/业务证据版本、提案引用和错误码。客户后续追问按原 thread_id 输入；主管审批从独立受保护 API 恢复，不能把客户聊天消息解释成审批。JWT、角色、完整地址和支付信息不进 checkpoint。

`RouteDecision v1` 用 JSON Schema/Pydantic 严格输出最多 3 个候选意图、目标实体、候选槽位、置信度/不确定原因及是否请求人工。顶层 route 为 `knowledge / after_sales / clarify / human_handoff / out_of_scope`；细类为 `policy_qa、order_status、shipment_tracking、cancel_request、return_request、refund_request、complaint、human_request、unknown`。模型只提出候选，`RuleGate` 先检查认证、显式人工请求、越权、待审批状态和高风险词；`RoutePolicy` 再映射允许的领域子图或业务节点。模型不能把猜出的 order_id 标为 verified，也不能输出退款金额/主管身份作为权威字段。

| 意图 | 最低必填槽位 / 服务端验证 | 缺失或歧义处理 |
|---|---|---|
| `policy_qa` 泛问 | 问题主题；不需要订单 | 问题范围不明时问一个澄清项；仅用当前政策包 |
| `order_status` | 已认证用户、唯一且归属验证通过的订单 | 同名/多单先出安全候选摘要让客户选择；不得按猜测查别人订单 |
| `shipment_tracking` | 验证订单；多包裹时验证 package_id | 先问哪个包裹；不能把整单任一签收事件套给全部商品 |
| `cancel_request` | 订单；若要实际取消还需明确操作意愿 | 首版只解释状态/转人工，无自动取消写工具 |
| `return_request` | 验证订单、商品项、数量、原因、明确提交确认 | 咨询可先查资格；缺任一提交槽位不得创建申请 |
| `refund_request` | 咨询：验证订单/退货请求；执行：仓库质检、有效提案及主管审批均由服务端查证 | 用户说“退款”不等于满足执行条件；缺证据仅解释/转人工 |
| `complaint` | 投诉主题；涉及具体订单时验证归属 | 可先建人工工单，不据投诉语句直接退款 |
| `human_request` | 无 | 直接转人工，不强制继续自动澄清 |

多意图遵循**安全与事实优先**的偏序：身份/权限与风险 Gate → 解析订单指代 → 查实时订单/物流 → 查适用政策 → 合并解释 → 如客户明确提交且规则允许再做退货写入；人工请求优先处理。只有独立的只读证据任务才可并行。并行不是让 Agent 竞争执行顺序；如果政策版本选择依赖订单事实，先由服务端确定版本再 fan-out。具体政策发布包可增加品类特殊顺序，但不能突破身份、确认和审批门槛。

一个待处理任务最多 2 次澄清，unknown 最多 1 次；同线程连续澄清最多 3 轮，24 小时未答的 pending_clarification 过期。自动 Replan 每请求最多 2 次，连同澄清导致的总 plan_revision 最多 4 次；连续两次无新增已验证事实就转人工。客户改口、订单/政策状态变化、子图证据冲突或主管改额使受影响证据失效，从已验证事实重规划；未受影响的结果可复用，但要重新检查版本和时效。已完成模拟退款不可由 Replan 自动撤回。

## 6. 模型、统一 Prompt 管理与政策 RAG

`ModelRegistry` 以版本化配置按 `intent、policy_agent、order_agent、synthesis、judge、embedding` 任务记录精确模型 ID、Provider 允许名单、本地 Prompt `release_id/name/SHA-256`、Schema hash、温度、Token/成本/超时上限和是否允许技术故障回退。先用候选模型做中文意图、结构化输出、证据引用、延迟与成本的小规模验证，选定基线后固定版本；正式 A/B 对照评估固定实际模型/Provider并关闭自动路由回退。无 Key Mock 模式可运行确定性路由与主业务测试。

### 6.1 本地 Git 为唯一权威 Prompt 源

计划在项目仓库初始化本地 Git，以 `prompts/catalog/<name>.yaml` 作为**唯一可编辑模板**：每份包含 `name、type=text|chat、template/messages、required_variables、schema_ref、description`；至少覆盖 `intent_route`、`coordinator`、`policy_agent`、`order_logistics_agent`、`synthesis`、`judge_clarity`/`judge_completeness`。Prompt 文件不含密钥、客户实例数据、七天业务期限或金额/权限规则。工具在 catalog 提交后生成只读 `prompts/candidates/<candidate_id>.json`；锁定集通过后生成 `prompts/releases/<release_id>.json`，列各 Prompt 的 SHA-256、`source_git_commit`（指向已提交的 catalog，避免 manifest 自引用）、Schema/ModelRegistry 兼容版本和发布时间。演示/发布构建把该 commit 的 catalog 与已验 manifest 一起打成不可变制品；开发模式可明确选择候选清单，发布模式不读取工作区任意改动。Git 是**逻辑权威**，不是唯一物理副本；定期备份仓库到另一块本地/离线介质，并演练恢复、核对 commit 与 hash。

`PromptRegistry` 是运行时唯一取 Prompt 的入口：`get(name, release_id) → 本地 template、hash、config`。启动时读取制品中的 release manifest，对配套 Prompt 文件重算 hash 并检查必填变量、Schema 引用和模型配置；不匹配则拒绝启动该版本。单次请求及其多 Agent 分支固定同一 `release_id`；跨日审批恢复如旧 release 已不在当前制品中，必须记录版本切换、从数据库事实重新规划并重校验政策/提案，不能悄悄混用旧结果。路由、Coordinator、两个子图、最终答复和离线 Judge 都从本地读取，SDK 的 Cloud Prompt 缓存只可用于**观测关联**，绝不是推理内容的来源。`run manifest` 保存 Git commit、release_id、每个 Prompt hash；回滚部署上一份已验 Prompt/manifest 制品并重启，不只移动一个指针或 Cloud 标签。

离线 Judge 的评分 Prompt 也保存在本地 catalog，由 Runner 读取已锁定版本、调用选定的 OpenRouter 模型，结果写入本地 `case_results` 并向 Langfuse 回传 Score；确定性的金额、权限、状态和引用断言仍在版本化 Python 评分代码中。首版不另建一套 Langfuse UI LLM-as-Judge 规则，以免 rubric 出现第二个可编辑源；UI 可展示 SDK 写入的 Score。Prompt 中不写七天期限、金额公式或对象级授权等权威业务规则，这些来自 `policy_bundle` 和领域服务。

发布流程为“本地编辑 → 变量/Schema/敏感字段 lint → smoke/安全回归 → Git commit → 生成候选清单 → 锁定集评测 → 生成/批准 release manifest → 部署”。脚本把候选/已验 Prompt **单向**推送至 Langfuse Cloud：先按 `name+hash` 查重，缺少才创建新版本，在 Langfuse Prompt `config` 记录 `source_git_commit、release_id?、prompt_sha256`，候选用于 Cloud 实验，发布后再更新 `release` 镜像标签并保存“本地 hash ↔ Cloud version”映射；失败可限速重试，不创建重复版本。Langfuse 官方 SDK/API 支持创建 Prompt 版本和设置标签；其 GitHub 集成文档主要是 Cloud→仓库方向，本项目的仓库→Cloud 同步需自行实现小型发布脚本。[Prompt 版本与标签 API](https://langfuse.com/docs/prompt-management/features/prompt-version-control) · [GitHub 集成方向](https://langfuse.com/docs/prompt-management/features/github-integration)

Cloud UI 可用于查看、Playground 试验，但直接编辑出的版本只算提案：必须回填本地 catalog、通过同一评测和发布流程才可生效。同步/启动时做 drift check；若 Cloud `release` 标签或内容 hash 与本地 manifest 不符，告警并停止把它关联为正式版本，运行时仍使用本地内容。Hobby 没有受保护部署标签，因此这种**本地强制加载＋hash 校验**比依赖 UI 权限更关键。镜像成功时，观测适配层可获取 hash 匹配的 Cloud Prompt 对象，把 Generation 关联到 Langfuse 版本；镜像或网络不可用时仅记录 `release_id/prompt_sha256` 元数据并标注关联缺失，绝不改用 Cloud 文本或阻断业务。Cloud 断连只影响上传/Prompt 版本可视化；只要本地已验 Prompt、政策、业务库和模型 Provider 可用，标准客服与受控售后流程继续运行。敏感字段上传规则仍按第 10 节执行。[Prompt 与 Trace 关联](https://langfuse.com/docs/prompt-management/features/link-to-traces) · [受保护标签限制](https://langfuse.com/docs/prompt-management/features/prompt-version-control)

只对 429、5xx、网络/超时等可重试技术故障允许一次受控备选；审核拒绝、权限拒绝、业务规则拒绝或“证据不足”不通过换模型来绕过。PolicyAgent 缺政策证据必须返回 `incomplete`；OrderLogisticsAgent 失败只能返回可核实的部分事实；Coordinator 不得把这种降级写成确定结论。Judge 只用于离线语气、完整性和非关键事实表述辅助，不能覆盖业务安全断言。Embedding 模型更换须建新索引并验证，不混用旧向量。[OpenRouter 回退](https://openrouter.ai/docs/guides/routing/model-fallbacks) · [结构化输出](https://openrouter.ai/docs/guides/features/structured-outputs)

`policy_bundle` 是不可变的文本、规则参数、生效时间、文档/规则 hash、Chunk/索引版本与兼容代码版本集合。support 起草，后台分块并建立 PostgreSQL 全文＋pgvector 索引；主管发布前运行七天边界、旧承诺、规则、检索和引用回归测试。状态：`draft → indexed → verified → active → superseded`。索引可用后事务性切换 active 指针；回滚仍保留历史和激活记录。

PolicyAgent 只可在服务端给定 bundle 中做标题/条款检索：全文检索与向量检索并行，经 RRF 融合后返回原文定位、条款 ID、版本及有效日期。泛问使用当前 active 包；具体订单由规则服务结合购买时的有利承诺决定适用 bundle；子图不能自行扩大为“搜所有旧政策”。当前请求锁定 bundle 版本，提案绑定决策证据；仓库等待/审批期间如相关订单、质检、政策或计划版本变化，恢复时重新校验，旧提案失效并需重新审批。个人 Memory 与组织政策索引完全隔离。证据不足时拒答/转人工，模型引用文档不等于有权判断资金资格。

## 7. Memory：父图管理、子图不私藏长期状态

| 类型 | 权威来源与读取/写入者 |
|---|---|
| 线程/审批状态 | 父图 LangGraph `PostgresSaver`；用于断线恢复，不等于跨会话画像 |
| 结构化用户画像 | PostgreSQL 业务表；已确认的语言、沟通渠道、隐私同意等，由客户/API 更新 |
| 柔性长期偏好与已结案摘要 | `LongTermMemory` 接口；默认 PostgresStore 安全通过后在线，Mem0 OSS 做隔离 A/B |
| 政策知识 | `policy_bundle`＋RAG；不是个人记忆，仅 PolicyAgent 在指定版本读取 |
| 订单/余额/物流/审批 | Commerce MCP/领域服务实时查询；任何记忆都不覆写 |

子图每次委托只接收最小上下文，默认不读写跨会话 Memory；语言偏好等用于最终答复时由父图筛选读取。`PostgresStore＋薄写入策略` 与 `Mem0 OSS＋pgvector` 用相同 40 组多会话故事离线 A/B，对比提取、去重、更正、撤回、Recall@3、延迟、成本和跨用户隔离。在线同一时刻只能启用 PostgresStore、Mem0 或 off；PostgresStore 未过安全门槛就 off。Mem0 如不满足重启持久化、删除语义、OpenRouter 兼容和隔离测试，仅保留离线实验；其内部数据不因向量后端为 pgvector 就自动全部属于 PostgreSQL。

只允许写明确偏好和已结案摘要，不写姓名、地址、支付信息、完整原文、未经确认的推断，也不让旧“已退款”记忆决定当前状态。更正须使旧偏好失效，撤回后不可检索。服务端认证用户 ID 决定 namespace；同名用户、跨用户读写、子图越权读取、凭记忆批准退款是阻断发布的测试。多 Agent 不能带来两套彼此矛盾的长期记忆。[LangGraph Store](https://docs.langchain.com/oss/python/langgraph/stores) · [Mem0 OSS](https://docs.mem0.ai/open-source/overview)

## 8. 数据：因果一致、可重放且有接近生产的多样性

| 来源 | 使用方式 | 不可突破的边界 |
|---|---|---|
| 自建确定性业务世界 | 唯一主链：订单、支付分摊、物流、售后、仓库、审批、对话、记忆、政策 | 全部标记 synthetic；是演示、CI、人工金标与性能档的事实基础 |
| [Olist](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce) | 校准适用的商品项数、重复购买、配送间隔等分布；可选单独导入真实订单关系验证解析/查询 | 巴西历史 BRL 数据不套中国七天政策；无原始售后/中文对话联结；下载前核许可，不作为运行前提 |
| ECD、CSDS、申请后的 JDDC 等中文语料 | 获授权后做表达/歧义专项或参考语言现象原创话语 | 不与主业务身份强行对应，许可不明原文不入公开仓库/Cloud Trace |
| ECom-Bench、τ³-bench retail、ABCD | 独立外部迁移评测与方法参考 | 保留源版本、原政策与原评分，结果单列，不伪称原榜单成绩 |

UCI Online Retail II 的取消发票不能证明对应退货入库和主管审批，不进入主数据链。Olist 如叠加售后，只能做隔离且显式标为 `synthetic_overlay` 的实验，不与主 CNY 世界混合。[UCI 数据说明](https://archive.ics.uci.edu/dataset/502/online+retail+ii) · [JDDC 申请](https://github.com/hrlinlp/jddc2.1)

`ScenarioSpec` 包含 scenario_id、seed、固定业务时钟/时区、policy_bundle_id、客户/商品特征、事件脚本、预期检查点和 provenance。生成 customers/products/orders/paid_allocations 后，只能经定义的转移产生 shipment、return、warehouse、proposal、approval、refund、ticket。独立参考状态模型验证生成物，避免服务代码既制造 fixture 又宣称它正确。LLM 可改写已生成事实的客户语言，不能生成订单 ID、金额、状态或 gold。一个 case 的多 Agent 问句必须与其真实订单/政策状态相符；“包裹没到，能退吗”有明确 shipment 时间线和对应政策版本，不临时拼接不相干订单。

| 档位 | 建设目标 | 作用 |
|---|---|---|
| demo | 25 固定场景、约 20–25 份政策、Mock 模型 | 无外部 Key 启动，包含至少一条双 Agent 并行与一条证据冲突负例 |
| realistic | 约 10 万订单、12–20 万商品项、40–70 万事件、5 千–1 万客服会话、数千退货申请 | 日常集成、查询、可视化与对照评估的背景世界 |
| scale | 按需 100 万订单、120–200 万商品项、400–700 万事件、5 万–10 万客服会话、数万退货申请 | 大表索引、API、后台任务、并发幂等与报表性能，不进每次 CI |

生成器分块流式输出、批量导入 PostgreSQL，记录机器、导入耗时、磁盘与配置，不把百万对象一次装内存。[PostgreSQL COPY](https://www.postgresql.org/docs/current/sql-copy.html) 质量闸要求孤儿外键、客户订单错配、非法状态、金额不守恒、未审批退款、日期倒序、货币混用、重复键额外记账均为 0；预期被拒绝的非法事件留在测试脚本，不污染业务事实表。`data_quality_report.json` 列行数、场景矩阵、拒收数、seed/hash、来源和校准对比。无法公开校准的发生率明确标为“合成假设”。

覆盖单/多商品、多 seller/多包裹、重复购买、正常/延迟/丢件、七天窗口边界、部分退货、入库数量不符/质检争议、审批通过/拒绝/超时、政策切换、并发重试、指代不清、改口、越权、恶意政策文档，以及两个子图结果不一致/迟到/一方超时。背景数据尽量贴近校准分布；锁定评测集故意加重长尾/高危场景，两种分布分开报告。Faker 只生成虚构展示字段，不负责业务关系。

## 9. 评估：业务终态、协作增益与反向测试

### 9.1 数据集与切分

| 套件 | 建设目标 | 主要检查 |
|---|---:|---|
| `smoke_demo` | 25 个固定场景 | 无 Key 起步、主链路和明确负例 |
| `core_business` | 约 200 个经人工复核的多轮案例 | 订单/物流、退货申请、入库、审批、退款、异常、人接管的最终状态 |
| `intent_clarify` | 约 140 个专项 | 多标签、槽位、指代、澄清和 Replan |
| `policy_rag` | 约 60 个查询—证据金标 | 当前/历史适用版本、条款定位、证据不足、注入 |
| `memory_story` | 约 40 组多会话故事 | PostgresStore/Mem0 A/B、修改/删除和隔离 |
| `collaboration` | 约 80 个配对案例；其中约 50 个复合、20 个单领域、10 个故障/冲突 | 是否正确派发两个/一个/零个子图，证据合并、迟到结果、局部失败、成本收益 |
| `rule_state` | 2,000–5,000 条规则/属性案例 | 权限、非法转移、金额守恒、幂等、并发；**不计入 LLM Agent 成功率** |
| `external_transfer` | 按原始公开任务集 | ECom-Bench、τ³-bench 等的独立迁移结果 |

`collaboration` 与 core/intent/RAG 可有同一 case_id 的有目的重叠；汇总唯一案例数时去重，不能把交叉标注当新增独立样本。各 LLM 套件原则上按客户、订单、原对话来源和模板家族分组，约 60% dev / 40% locked test；同订单换措辞、同模板换编号不得跨分区。所有 critical 案例双人复核，其余至少 20% 双人复核并留裁决。先交付 25 smoke＋30 core＋30 intent＋20 RAG＋20 collaboration 的最小可跑集，再扩充至目标。业务背景量和规则案例不能冒充人工金标。外部基准保留原任务与评分并单列，不与本项目自造样本合并一个“总准确率”。

统一 `EvalCase` JSONL 契约含 `schema_version/case_id/suite/split/risk_tier/tags`、来源/许可、group_keys、fixture（固定时钟、policy_bundle、角色、订单/记忆初态）、dialogue_script、gold（路由/槽位、允许和禁止工具偏序、必需证据、最终 DB/审计状态、必须/禁止声明）、review。`collaboration_gold` 写明哪些领域事实必需、允许/禁止哪些 specialist、合并时必须保留哪些来源/版本、缺一个结果能否部分回答、`single|collab` 配对运行条件。gold 不传给 Agent。每个 run manifest 锁数据、生成器、政策、模型/Provider、**本地 Prompt release_id/Git commit/逐文件 hash**、Schema、评分器、代码和随机种子版本；可附非权威的 Cloud 镜像版本号供跳转。

### 9.2 隔离 Runner 与安全评分

命令行执行链固定为 `validate → seed → run_agent → score → report`。每个评测 worker 用独立 PostgreSQL 评测库或严格隔离 schema，并隔离 Redis key、父图 checkpoint、记忆 namespace、工单及模拟支付；同一 case 的多轮请求保持状态，结束后清理或销毁。不能依赖一个事务 rollback，因为客户、仓库、主管和 Worker 会跨请求提交。`run_id/case_id` 贯穿隔离命名空间，迟到异步任务不得污染下一案例。百万订单档用共用的不可变背景/性能库，不逐案例复制百万行。Runner 经 customer、warehouse、supervisor 的实际认证 API 重放，能测试服务重启后继续审批；不直接改数据库来假装 Agent 成功。

评分顺序是：验证 fixture/金标/基础设施 → 程序化检查权限与资金安全 → 最终数据库和审计状态 → 对话必要声明 → 路由/槽位/工具偏序/政策引用/记忆 → 可选 Judge 对语气和完整性评分。Agent 可以用多条同样安全的路径，不能机械逐字匹配唯一工具轨迹，但必须满足禁止动作、必需事实、次数、参数和最终状态。提案待批时 `refund_ledger` 必须为零；批准后至多一笔正确金额；只有回复“已退款”而无数据库事实不能通过。[τ³-bench 终态评分方法](https://github.com/sierra-research/tau2-bench/blob/main/docs/evaluation.md)

多 Agent 程序化断言检查：子图调用数/权限白名单、是否错误 fan-out、同订单/政策版本一致性、迟到旧 plan_revision 是否被丢弃、`PolicyFinding` 引用有效性、`OrderFinding` 与业务快照一致性、合并后是否偷换“未签收”为“已签收”、单子图故障时是否承认缺口。故意注入跨客户工具返回、恶意政策文本指令、错版本条款、两个子图矛盾、一次子图超时、重复派发、旧子图迟到、子图伪造批准令牌，确认失败或安全降级。评分器本身也做 mutation tests：未审批/重复退款、错客户订单、过期引用、只口头宣称成功、Judge 误判均须被抓出。Hypothesis 状态机生成非法序列并留下最小可重放反例。[Hypothesis stateful testing](https://hypothesis.readthedocs.io/en/latest/stateful.html)

结果项包含 task_success、route/slot、clarification/replan、dispatch_correct、specialist_contract、evidence_consistency、citation_valid、tool_contract、safety_pass、memory_isolation、LLM/tool 次数、Token/成本、P50/P95 延迟与失败码。超时、评分器错误或缺测为 `incomplete`，单列分子/分母且不得算通过。发布闸首版继续以一级路由 Accuracy ≥90%、工具/参数通过率 ≥90%、RAG Hit@5 ≥85% 为**计划目标**，实际基线后校准；critical 集中的未审批/重复退款、跨用户读取、失效提案、记忆泄漏、子图越权和无界循环必须为 0 个失败。这里的零仅表示锁定测试集未发现，不宣称真实世界风险为零。

### 9.3 单 Agent vs 多 Agent 的配对对照

保留 v4 单图路径为 `agent_mode=single` 基线，再新增 `agent_mode=collab`；两者共享相同身份/规则/政策/检索工具、业务数据、模型/Provider、Prompt 版本记录和评测 Runner。每个 `collaboration` case 在隔离重置后的相同 fixture 与固定时钟上各跑一次，随机化运行先后；统一报告总模型调用数和实际花费，不把多 Agent 额外成本藏在“并行所以更快”后面。对照同时显示复合问题与单领域问题的终态成功率、证据错误、人工接管率、P50/P95 延迟、Token/成本、工具调用和分派错误，并列出逐案 win/tie/loss。可做配对 bootstrap 置信区间，但小样本不把噪声写成显著生产提升。

接受多 Agent 在线开启的条件是：安全和权限无新增 critical 失败、复合问题的锁定集任务成功不低于基线且有可解释的证据质量改善、单领域请求不过度派发、延迟与成本处于实际测试后确认的预算内。如果协作无收益或带来明显退化，保持 `single` 为默认，把 `collab` 留在独立演示/实验开关并如实报告结果；不能为了简历强行声称性能提升。不能把子图调用成功率替代客户任务成功率。

每提交运行无 Key Mock、Schema/数据来源、安全规则和子图权限单测；合并前或定期运行固定真实模型的完整内部锁定集；外部公开基准手动独立跑。k6 性能测试以只读查询、会话、申请、审批的混合 API 流量采样，记录机器、并发、吞吐、P50/P95、错误率、数据库资源和外部模型用量；20/50/100 并发为探索阶段而非承诺 SLA。将模型端限流与数据库容量问题分开诊断。[k6 API 压测](https://grafana.com/docs/k6/latest/testing-guides/api-load-testing/)

### 9.4 Langfuse Cloud Experiment 与本地评测的分工

`EvalCase` JSONL、fixture、gold、评分器、Prompt release manifest 和 `summary.json` 留在本地，始终是可复现与发布判定的权威；Langfuse Cloud 接收脱敏的 Experiment/Trace/Score 及 Prompt 镜像，便于按 Prompt/模型/Agent 模式筛选、对比和人工复核。Runner 可用 Langfuse Python SDK v4 的本地 dataset Experiment 接入，不要求把整个业务库或全部 gold 上传。每条运行记录 `run_id、case_id、suite、split、agent_mode、local_prompt_release_id/prompt_sha256、cloud_prompt_version?、policy_bundle_id、trace_id`，并把程序化分项分数、Judge 辅助分数及缺测状态写为 Scores；Cloud 侧结果应能按 `run_id/case_id` 回链本地报告。只上传脱敏的任务输入、参考字段和摘要，不把私有/许可不明原文、认证令牌、完整地址或真实支付数据发送到 Cloud。[Langfuse SDK Experiments 可使用本地数据集](https://langfuse.com/docs/evaluation/experiments/experiments-via-sdk) · [SDK Score](https://langfuse.com/docs/evaluation/evaluation-methods/scores-via-sdk)

Cloud Experiment 不替代领域终态断言，也不要求在线拉取 Cloud 历史 Trace 或 Prompt 才能评分：评分在本机读取实际 DB/审计状态、受控 API 重放结果和本地 Judge Prompt；Cloud 同步失败只标记 `telemetry_sync=incomplete`，不能把业务评测判成成功或失败。若 Cloud 数据集用于 UI 回归，只从本地 JSONL 按 `case_id+dataset_hash` 发布脱敏镜像；本地仍是唯一可编辑的测试集。锁定集只能在本地 Prompt/模型/政策版本冻结后执行，Cloud 的 Prompt 标签/数据集后续变化不改变既有 run 的版本记录。Langfuse UI 的 LLM-as-Judge 可用于探索性诊断，但首版发布门槛只认本地确定性评分和由统一 PromptRegistry 驱动的已版本化辅助 Judge。

## 10. 本地 OTel/Jaeger ＋ Langfuse Cloud：Trace、Dashboard、成本和迁移

单个 HTTP 请求内，父图和两个并行子图共享该请求的 OTel Trace，上下文传播到检索、MCP 与数据库调用，子图 Span 带 `agent_role、task_id、plan_revision、policy_bundle_id` 等经过脱敏的低风险属性；fan-out 和 fan-in 可看到两个分支各自耗时、失败与合并决策。客户提交、数日后仓库收货、主管审批与恢复退款是**多条**根 Trace，按 `thread_id/return_id/approval_id/case_id/run_id` 关联，跨异步边界用 Span Link；审计记录业务 ID 与 trace_id。v3 中“多轮 case 一根 Trace”的说法仍按 v4 修正，不延长一个 Trace 跨几天。[OTel Python instrumentation](https://opentelemetry.io/docs/languages/python/instrumentation/)

Jaeger 看 API→父图→子图→MCP/RAG/Worker/数据库的调用树、延迟与异常；Langfuse Cloud 看 Agent、Prompt、LLM、RAG、Token/成本、Experiment 与 Score；PostgreSQL 审计保存资金动作权威记录。Python API 只注册一套 OTel TracerProvider/上下文，并把同一逻辑 Span 流分别交给本地 OTLP Collector 和 Langfuse SDK v4 的 Span Processor；**不同时使用会再造相同 Agent/LLM Span 的 LangGraph callback 与手工装饰器**。Langfuse 导出器通过 `should_export_span` 仅保留有用的根/Agent/LLM/Tool 层级，Jaeger 保留系统级树；以集成测试核对相同 trace_id、正确 parent 关系和无重复 Observation。[Langfuse SDK 的 OTel 基础与导出筛选](https://langfuse.com/docs/observability/sdk/overview)

Cloud 出口用 `mask_otel_spans` 在离开本机前清除姓名、地址、令牌、支付字段和不应上传的对话内容；本地 Collector 也独立脱敏，不能以 Cloud 的 mask 代替 Jaeger 路径的保护。使用虚构合成数据仍做 canary 测试，阻止测试用秘密/真实个人信息出现在任一导出。评测与固定演示的 Agent 链路在预算内完整保留；大规模 k6/百万订单背景压测只把采样的 Agent 错误或风险链路送 Cloud，其余只走本地 Jaeger/聚合指标。采样失败不得丢业务审计。[Langfuse Span 脱敏](https://langfuse.com/docs/observability/features/masking)

Langfuse Cloud Dashboard 按意图、风险、单/双子图、模型、**本地 Prompt release/hash（镜像成功时可跳转 Cloud 版本）**、政策包查看 task_success、引用有效、分派错误、单子图失败、Token/成本和 baseline↔candidate 退化案例；报告里的失败项含脱敏 trace_id，可打开 Langfuse 的 Agent 观察与 Jaeger 的全栈请求。Cloud 看板只显示**已上传/采样的分母**；全量评测分母及发布判断看本地 `summary.json`。Prometheus/Grafana 可选档展示 API/MCP P95、错误率、子图派发数、子图超时/冲突、模型回退、Celery 积压、审批等待和模拟退款失败。Prometheus 标签只用服务、路由、agent_role、结果码等低基数字段，不放 customer_id/order_id；高基数业务 ID 留在 Trace/审计。[Langfuse 自定义 Dashboard](https://langfuse.com/docs/metrics/features/custom-dashboards) · [Prometheus 标签规范](https://prometheus.io/docs/practices/naming/)

每个 run 必输出 `evals/reports/<run_id>/manifest.json、case_results.jsonl、summary.json、report.html`，可另有 Markdown 摘要。HTML 展示样本量与 dev/locked、合成/外部来源、分层分子/分母、critical/incomplete、路由混淆矩阵、RAG/Memory、协作 paired win/tie/loss、成本/P95、基线差异及脱敏 Trace/审计链接；缺失 Cloud 链接时仍可按本地 audit/trace_id 定位。`summary.json` 是 CI gate 的唯一机器输入；Langfuse 同步展示但不反向决定通过。观测服务故障或 Cloud 配额耗尽仍可本地完成评估与留存。不同版本仅在数据和评分器契约一致时直接比较。[Langfuse Experiment 对比](https://langfuse.com/docs/evaluation/experiments/compare-experiments)

截至计划日期，Langfuse Cloud Hobby 免费档有每月 **50,000 units（Trace＋Observation＋Score）**、2 用户、30 天**历史数据访问窗口**；本项目运行时不通过 Cloud 获取 Prompt，镜像发布/对账仍受一般 API 30 次/分钟、Observations v2 30 次/分钟、Datasets API 100 次/分钟等当前限制约束。30 天访问限制不能说成“第 31 天物理删除”；也不能依赖超过访问窗口的 Cloud Trace 作长期复现。Core 当前为每月 $29、含 100,000 units 和 90 天访问，仅在实际配额不足且自愿付费时考虑升级。模型/API、Embedding、网络与本地电力/磁盘费用独立于 Langfuse。Hobby 没有受保护部署标签、服务 SLA 或无限制批量导出，故这里是**开发期观测方案，不宣称企业正式生产 SLA**。[Langfuse Cloud 价格/功能/速率](https://langfuse.com/pricing) · [Billable Units](https://langfuse.com/docs/administration/billable-units) · [数据访问与保留](https://langfuse.com/docs/administration/data-retention)

配额不按“运行次数”估算，而以一次校准运行测得的 `traces + observations + scores` 单位数/案例乘本月计划案例量；先跑 20–50 个代表性案例，记录单/双 Agent、平均轮数、每轮 Observation 与 Score 数。按既有约 600 次完整配对/非配对执行、3–4 轮/案例、6–12 个 Observation/轮和少量 Score 粗估，单次完整 sweep 可能达到约 **1.4–3.2 万 units**，只是预算假设，实际以 Usage Management Dashboard 为准。开发日常跑少量 smoke/定向回归，完整锁定集按里程碑运行；月用量达到 70% 预警、90% 时暂停非必要 Cloud Trace/重复实验和 k6 上传，但本地安全测试与报告继续。Cloud 采样率、导出失败数和本地/Cloud case 对账率写入每次报告，不能为省额度悄悄减少 locked 测试。[Usage Management 计数](https://langfuse.com/docs/administration/billable-units)

为处理 30 天窗口，`case_results.jsonl`、全部分项分数、manifest 和 HTML 本地长期保存；评测完成后导出所需的脱敏失败/critical Trace、Observation、Score 到 `observability/exports/<run_id>/`，附 `export_manifest.json`（Cloud 项目/时间窗、分页游标、数量、hash、缺口）。首版可按里程碑/每周手动执行带断点续传的 API 导出，不依赖 Hobby 不提供的自动 Blob 导出；对账脚本比对本地 `run_id/case_id/trace_id` 与导出条目，提前发现 API 限流/漏页。Cloud UI 支持 CSV/JSON 批量导出，但筛选导出不能取代本地原始评测结果。[Langfuse UI 导出](https://langfuse.com/docs/api-and-data-platform/features/export-from-ui)

未来切到 Langfuse OSS 时，不改领域图、`PromptRegistry`、评分器和评测数据契约：本地 Git release manifest 仍为权威，仅把镜像脚本/`TelemetryAdapter` 的 `LANGFUSE_BASE_URL` 与公私钥改指新实例；重推已验 Prompt 镜像和必要的脱敏数据集，再从本地 locked JSONL 重新跑基线，验证 hash/Score/Trace 关联。Langfuse 新实例可能重排 Prompt 数字版本，因此映射以本地 hash 为准。历史 Cloud Trace 如需保留，必须在可访问期内导出/迁移；不能假定旧 Experiment 页面、Dashboard 配置、版本号和历史链接在新实例中原样保留。自托管仍需 ClickHouse/对象存储/Web/Worker/备份等资源，因此不在当前本地开发机默认启动。[Langfuse 项目迁移示例](https://langfuse.com/guides/cookbook/example_data_migration) · [自托管 Compose](https://langfuse.com/self-hosting/deployment/docker-compose)

## 11. 实施顺序、里程碑与退出条件

单人项目规划约 **10–11 个实施周＋1 周缓冲**，这是规划而非交付承诺；Cloud 省去自托管 Langfuse 基础设施，但 Prompt 治理、双路 Trace、导出对账仍需工程时间。多 Agent 不能先于可工作的单图基线，否则无法证明它解决了什么问题。

| 阶段 | 主要交付 | 退出条件 |
|---|---|---|
| 1. 基础 | 初始化本地 Git/Prompt catalog 与离线备份约定；Monorepo/Compose、PostgreSQL 迁移、Keycloak 四角色、API/MCP 鉴权、25 demo fixture；创建 Langfuse Cloud 项目并保管服务端密钥 | 无 Key 能启动；本人查单与跨用户拒绝均通过；Cloud 密钥不进入前端/仓库 |
| 2. 一致数据 | ScenarioSpec、参考状态模型、10 万单档、质量报告 | seed 可重放；关系、时间、金额、状态不变量零违规 |
| 3. 单图基线 | RouteDecision/RuleGate/SlotResolver、PostgresSaver、SSE、澄清和 Replan；PromptRegistry 本地加载/校验 release manifest，发布脚本单向镜像 Cloud | 单/多意图、改口、轮次上限、断线恢复可测；锁定本地基线 commit/hash；Cloud 断连与 UI 漂移不改变实际 Prompt |
| 4. 政策与业务闭环 | policy_bundle、混合 RAG、退货/入库/提案/主管 UI、interrupt/resume、幂等模拟退款 | 七天边界、旧有利承诺、审批前零退款、重复请求/重启/改额均可测 |
| 5. 协作切片 | 两个只读专职子图、DelegationTask/Finding Schema、条件 fan-out/fan-in、版本/超时/权限 Gate | “包裹未到能否退”能并行取证；单领域不乱派发；伪造/迟到/冲突结果被拒绝 |
| 6. 评估 | JSONL 契约、隔离 Runner、人工金标分批、mutation/属性测试、single/collab 配对；SDK Experiment/Score 同步 Cloud | 业务终态可重放；错误轨迹被评分器抓住；本地报告含逐案对照，Cloud 按同一 run_id 可核查 |
| 7. 观测与规模 | OTel/Collector/Jaeger 与 Langfuse Cloud 双路、Cloud Dashboard/配额看板/Trace 导出对账、Prompt 镜像漂移检测、Grafana 可选、100 万单按需生成和 k6 | fan-out/fan-in 可钻取且不重复计数；多根 Trace 可关联；Cloud 断连本地业务/评测继续；记录机器与实际负载指标 |
| 8. Memory 与交付 | PostgresStore/Mem0 OSS 隔离 A/B、40 组故事、Playwright、固定演示、README/威胁模型 | 更正/删除/跨用户隔离通过；在线只启一条记忆路线；新机器能按文档重建 |

固定验收演示至少四条：① 多意图＋澄清后查本人物流；② “包裹没到能退吗”双子图并行取证、合并解释，并展示与单图对照；③ 七天内退货→入库质检→主管批准→单笔模拟退款；④ 跨用户、旧提案、重复审批及子图伪造/迟到结果被拒绝。每条可看到政策证据、数据库终态、审计、Jaeger Trace、Langfuse Cloud Agent Observation/Score 和本地报告条目；另演示一次本地 Prompt release/回滚及 Cloud 镜像关联、一次 Cloud 断连仍执行本地受控业务、一次 Cloud UI 漂移告警和一次 Trace/Score 导出对账。百万档只需按需生成、完成质量/负载报告，不要求普通开发机每次启动全部数据。

## 12. 仓库交付、明确非目标与简历表述

建议结构：`apps/web、apps/api、services/commerce-mcp、packages/domain-policy、packages/agent/coordinator、packages/agent/specialists、packages/agent/prompt_registry、prompts/catalog、prompts/candidates、prompts/releases、scripts/sync_prompts_to_langfuse、data/scenarios、data/generator、evals/datasets、evals/runners、evals/reports、observability/exports、infra/compose、docs`。提交原创 demo fixture、可重建生成器/seed、数据字典、Schema、政策包、评分脚本、本地 Prompt catalog/候选与 release manifest/镜像脚本、脱敏报告样例、观测/导出配置和 README；百万级生成物、Cloud 密钥、许可受限的原始数据、含敏感字段的 Trace 不提交。公开来源、生成命令、hash/许可；未经授权的对话不发送到 Cloud。`.env.example` 只列 `LANGFUSE_BASE_URL`、Public/Secret Key 名称与采样开关，不含真实值；Cloud 密钥仅用于镜像和观测，不进入 PromptRegistry 运行时必填配置。

明确不做：真实支付/退款或平台私有流水抓取、多租户 SaaS、自动取消订单写工具、复杂券税运费结算、跨境法律判断、全量模型微调、语音/微信全渠道、图数据库记忆、两套长期记忆融合在线判定、任意 Agent 自生成/互相递归、Agent 投票批准退款、用 LLM Judge 替代金额和权限断言。若工期受限，优先保住“本人查单→确认退货→入库质检→主管审批→幂等模拟退款→审计与评估”，再保留一个可验证的双 Agent 协作切片；压缩外部语料、规模档频率和 Mem0 在线接入，不削减安全门槛。

最终简历仅写实际完成的代码、生成量和实测指标，清楚区分“合成业务数据”“真实历史订单用于校准/独立导入”“外部公开基准”“本项目锁定集”和“单 Agent 对照”。若多 Agent 未在锁定集上体现收益，诚实报告分派与安全能力及其成本，不写“显著提升”或“生产落地”。
