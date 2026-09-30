# ResolveAI v4：企业级电商售后 Agent 实施计划

> 状态：已完成设计讨论的实施计划；尚未开始编码，以下数量与质量门槛是目标，不是实测结果。日期：2026-09-27。  
> 历史版本原样保留：[v1](./resolveai-v1.md) · [v2](./resolveai-v2.md) · [v3](./resolveai-v3.md) · [v3 评测补充规范](./resolveai-v3-evaluation-spec.md)。本文是独立可读的最新版；与旧版冲突时以 v4 为准。售前项目不在此范围。

## 1. 目标、边界与 v4 已确认决策

面向 AI 应用工程师简历，交付一个单企业、可本地运行、可复现评估的电商售后系统。主链路是：已认证客户咨询 → 状态感知 Agent → 政策 RAG / Commerce MCP → 服务端确定性资格与金额判断 → 客户确认退货 → 仓库收货质检 → 主管批准 → 幂等模拟退款 → 审计、观测与评估。不能只是聊天 Demo，也不建设真实支付、电商平台对接或多租户 SaaS。

v4 相比 v3 的确定变更：

| 主题 | v4 决定 |
|---|---|
| 售后政策 | 合格商品签收次日起算七个自然日内可申请无理由退货；具体商品例外、旧有利购买承诺和异常转人工由版本化规则处理 |
| 写操作边界 | 客户明确确认商品/数量/订单后，服务端校验即可创建退货申请；退款必须在仓库入库质检通过、服务端生成提案、主管批准后执行 |
| 多租户 | 单企业；seller_id 仅是同一平台经营范围内的业务字段，不是租户隔离 |
| 身份与权限 | 本地 Keycloak OIDC；customer、support、warehouse、supervisor 四角色；API 与 MCP 均验证身份、角色和订单归属 |
| 数据 | 一套因果一致的合成业务世界是主数据；Olist 仅做适用分布的校准/可选独立导入，不跨来源伪造订单—对话—售后关联 |
| 规模 | demo 25 场景；realistic 约 10 万订单；scale 按需生成 100 万订单及数百万关联事件，分别做业务与性能验证 |
| 政策发布 | 文本、结构化规则、索引、生效时间组成统一 policy_bundle；先校验后原子切换，订单级版本选择与审批恢复时重校验 |
| 模型回退 | OpenRouter 的模型配置按任务版本化；仅对可重试技术故障受控回退，不用跨模型回退绕过审核/业务拒绝 |
| 评估 | 小型隔离环境逐案例重放，检查最终数据库和审计事实；200 左右人工复核金标＋2,000–5,000 条规则案例；百万订单档不逐案复制 |
| 观测与报告 | OTel/Collector/Jaeger 保留；Langfuse OSS 按需自托管，Cloud Hobby 仅可选；独立 JSONL/JSON/HTML 评估报告与 CI 门槛 |

**不可逾越的边界：**LLM 不创建客户身份、不决定退款金额、不签发审批、不直接修改账本。权威事实顺序为实时业务数据与适用政策 > 已确认结构化画像 > 经筛选柔性记忆 > 模型推断。所有交易、退款与客服数据均为模拟；不声称已服务真实客户或接入真实资金网络。

## 2. 总体架构与运行档位

| 层 | 组件 | 职责 |
|---|---|---|
| 前端 | Next.js、TypeScript、Tailwind、shadcn/ui | 客户聊天、工单、仓库入库、主管审批、政策管理、评估结果入口 |
| API | FastAPI、Pydantic、SQLAlchemy、Alembic | OIDC/RBAC、SSE、会话、工单、审批、审计与业务事务边界 |
| Agent | LangGraph StateGraph、PostgresSaver | 路由、澄清、Replan、政策/工具编排、审批 interrupt/resume |
| 模型网关 | OpenRouter 适配层 | 结构化意图、回答、离线 Judge、Embedding 的任务化配置和故障降级 |
| 业务工具 | 独立 Commerce MCP，Streamable HTTP | 本人订单/物流/资格查询、退货申请、仓库/退款受控动作 |
| 主存储 | PostgreSQL＋pgvector | 业务表、审计、checkpoint、政策索引、结构化画像及默认长期记忆 |
| 任务 | Redis＋Celery | 文档索引、批量数据生成/评测、提醒与可选记忆提取；Redis 不作业务真相 |
| 系统观测 | OpenTelemetry SDK → Collector → Jaeger | API、Agent、MCP、Worker、数据库跨服务 Trace |
| Agent 观测 | Langfuse OSS 独立 Compose profile | Prompt、LLM/RAG/Tool 轨迹、Token/成本、Experiment 与 Score；故障不阻塞业务 |
| 指标与报表 | Prometheus＋Grafana 独立 profile；本地评估报告 | 运行指标与负载趋势；可离线查看、可机器读取的发布证据 |

默认开发档只需核心服务、PostgreSQL、Redis、OTel Collector、Jaeger 和无 Key Mock；观测扩展档才启动 Langfuse OSS 与 Prometheus/Grafana。Langfuse 自托管核心功能没有其 SaaS 的按量授权费，但会增加 Web/Worker、ClickHouse、对象存储等资源与维护成本，不能说“零成本”。Langfuse Cloud Hobby 仅是可选捷径，不承担权威审计和报告。[Langfuse 自托管架构](https://langfuse.com/self-hosting) · [OSS/Enterprise 区别](https://langfuse.com/pricing-self-host)

只实现一个主图、知识问答和订单售后两条路径；不额外建设通用多 Agent 平台。

## 3. 业务数据模型、身份与安全

核心表按因果顺序建立：customers / customer_profiles → products / sellers → orders / order_items → payments / paid_allocations → shipments / shipment_events → return_requests / return_items → warehouse_receipts / inspections → refund_proposals → approvals → refund_ledger → tickets / conversations / audit_events。关键金额使用整数分，货币列必填；合成主世界统一 CNY。每项写动作都有状态版本和审计事件；数据库外键、唯一索引、CHECK 与服务端跨表不变量共同保护。

单企业不需要 tenant_id 作为隔离机制。seller_id 可用于筛选经营主体，但同一个客户订单中的多个 seller 不意味着可以跨客户访问；首版跨 seller 争议由人工接管。受控写 API 必须接收已验证的 order_id、line_id、quantity、明确客户确认、请求版本与幂等键。

Keycloak 提供 OIDC 登录；FastAPI 和 MCP 验证签名、issuer、audience、过期与角色。customer 只能访问本人订单和会话；support 可查看授权工单但无退款执行权；warehouse 只能确认收货和质检；supervisor 可审批/拒绝或退回修改提案。每次访问在服务端做对象级归属检查，不能只靠前端隐藏按钮或 Agent 判断。MCP 不接受模型自报的 customer_id、role 或批准凭证。审批恢复时重新读取当前认证主管与数据库审批事实，令牌、角色和客户 PII 不放入可恢复 checkpoint。[Keycloak 应用安全](https://www.keycloak.org/securing-apps/overview) · [OWASP 对象级授权](https://owasp.org/API-Security/editions/2023/en/0xa1-broken-object-level-authorization/)

内部审计表保存 actor_id、动作、对象、前后状态版本、policy_bundle_id、proposal_id、idempotency_key 与 trace_id；Trace 可以采样或过期，审计不能依赖观测平台。原始姓名、地址、支付信息不进入公开评测资产或 Trace。

## 4. 七天退货、仓库和退款的可执行状态机

### 4.1 规则范围

- 无理由退货仅针对符合政策的已签收实物商品。签收日期以 Asia/Shanghai 业务日历计算：签收次日为第 1 天，第 7 天当地 23:59:59 前收到申请有效。例如 9 月 4 日签收，9 月 5 日至 9 月 11 日为申请窗口。原始时间戳与计算出的截止时间均入库。
- 商品类别的法定例外、购买时已明示且确认的特定例外、商品完好要求写成结构化规则与原始证据字段；不能让模型凭商品名称猜测。损坏、质量争议、逾期争议、缺失签收证据走人工专项，不假装适用“无理由退货”。
- 如果购买时商家存在更有利的明确承诺，保留原承诺版本；能由结构化规则确定更有利时按该规则执行，无法比较时转人工。通用问答用当前政策，具体订单可以引用适用的历史政策。
- 未签收/物流丢失走独立异常工单，不要求虚构的退货入库；首版不自动退款。跨境、税费、满减/复杂券分摊和运费争议首版转人工。

此项目采用虚构商家政策，但七天起算、例外及收到完好退货后及时退款的基本背景参考中国官方规定；不得将该政策套用于巴西 Olist 历史订单的真实合规结论。[《网络购买商品七日无理由退货暂行办法》](https://www.samr.gov.cn/zw/zfxxgk/fdzdgknr/fgs/art/2023/art_26ca8fe29e184edd899fa0a7a060d935.html)

### 4.2 写入路径与金额

1. 客户咨询阶段可查本人订单和资格，但不能把“咨询能否退”当作提交。客户明确确认订单、商品、数量、原因和提交意愿后，服务端在事务中重新校验归属、签收、时限、品类及可退数量，幂等创建 return_request；**创建申请无需主管审批**。
2. warehouse 角色记录收货时间、每项收到数量、质检结果和异常。只有收到数量吻合且质检通过的商品项进入退款提案；少件、错件、损坏争议转人工。对合格退货，以仓库实际收到时间起算七天退款处理期限；入库/质检/审批积压按截止时间预警并转主管处理，内部等待不得无上限拖延。
3. 服务端根据购买时记录的每项实付分摊额计算可退金额。对同一订单项，目标累计退额＝向下取整(该项实付金额 × 累计合格退货数量 ÷ 购买数量)；本次退额＝目标累计退额－该项既有退款，最后一件分得余数，并再次受整单实付余额限制。禁止负数、超购数量和超额退款。首版合成交易使用单一原支付方式，模拟退款回原方式；复杂券/税费、多支付方式或运费争议转人工，不编造真实分摊。
4. 提案绑定 order_version、return_id、warehouse_inspection_id、policy_bundle_id、plan_revision、金额及计算明细。supervisor 审批/拒绝；改额必须重新计算并生成新提案，不在旧提案上悄悄改值。
5. issue_refund 仅在主管批准且提案未失效后执行**模拟**退款。事务内锁定相关余额、复核权限/提案/金额/版本，refund_ledger 对 proposal_id 和 idempotency_key 建唯一约束；相同请求重复调用返回既有结果，不重复记账。失败后可安全重试并有审计。

流程阶段：eligible → return_requested → in_transit_back → received → inspected_passed / exception → proposal_pending → approved / rejected → refund_issued。客服答复必须区分“已申请”“待审批”“模拟退款已执行”，不能把 pending 说成到账。LangGraph interrupt 只用于等待人工批准；恢复后的节点可能从头执行，因此 interrupt 前的副作用也必须幂等，退款更要由事务服务再次校验。[LangGraph Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)

工具/入口分工：Commerce MCP 只读暴露 get_order、track_shipment、check_return_eligibility；客户确认后的 create_return_request 受对象级授权与幂等保护。record_warehouse_receipt、record_inspection 是仓库角色 API；create_refund_proposal 是质检通过后由服务端规则触发；approve/reject_proposal 是主管 API；issue_refund 只能由受控恢复流程携带数据库中的有效审批事实调用，不能接受 LLM 自造的批准参数。所有写入口共享同一领域服务校验与审计，不在 MCP、API 和 Graph 各写一套规则。

## 5. Agent 路由、澄清与 Replan

AgentState 只保存完成工作流必要的会话状态：thread_id、最近消息与摘要、当前阶段、RouteDecision、候选/已验证槽位、澄清状态、plan_revision、已选订单、证据 ID、提案引用、错误码。客户普通追问按同一 thread_id 作为新输入继续；主管审批用独立受保护 API 触发 interrupt/resume，二者不能混用。

IntentTaxonomy 将 route 定义为 knowledge / after_sales / clarify / human_handoff / out_of_scope；细类为 policy_qa、order_status、shipment_tracking、cancel_request、return_request、refund_request、complaint、human_request、unknown。一条消息至多 3 个 intent。模型通过 JSON Schema 只输出候选意图与槽位；RuleGate 先处理已认证状态、明确人工请求、等待审批和越权风险，RoutePolicy 再把候选映射为允许节点。客户身份、verified_order_id、退款金额和主管身份均由服务端提供。

必需槽位分阶段检查：泛问政策不要求订单；查本人订单/物流要服务端已验证订单，多包裹需明确包裹；创建退货需指定商品、数量、原因和明确提交；退款咨询可以先查资格，执行仍要仓库事实、主管审批。订单指代不唯一、标签冲突或缺槽位时只问最能推进的一项；“包裹没到，能退吗”先核本人订单和物流，再解释适用路径，不因 refund_request 标签直接退款。

每个待处理任务最多 2 次澄清，unknown 最多 1 次；同线程连续澄清最多 3 轮，24 小时未答的 pending_clarification 过期。自动 Replan 每请求最多 2 次，加上澄清导致的总 plan_revision 最多 4 次；连续两次无新增已验证事实即转人工。用户更正槽位、订单/政策状态变化或主管改额会废弃受影响证据和旧提案，从已验证状态重规划。已执行的退款不能靠 Replan 自动撤回。

## 6. 模型配置、故障降级与政策 RAG

### 6.1 模型任务配置

ModelRegistry 以版本化配置记录 intent、answer、judge、embedding 四种任务的精确模型 ID、Provider 允许名单、Prompt/Schema hash、温度、Token/成本上限和超时。实施初期用候选模型跑中文标签、结构化输出、成本/延迟和安全锁定集，选定后固定版本；不把动态 latest 别名当评测基线。无 Key 模式提供确定性 Mock。

intent 的 JSON Schema 输出再经 Pydantic 校验；429、5xx、网络/超时可由应用在上限内尝试一次已验证备选模型，仍失败则澄清或人工，不调用写工具。answer 失败时，已核实的只读订单状态可用固定模板解释；政策证据不足或风险高时转人工。judge 只用于离线语气/完整性辅助评价，失败记 incomplete，不能推翻硬安全断言。embedding 模型变更需要新索引和验证，不混用旧向量。

OpenRouter 的自动跨模型 fallbacks 也可能由审核拒绝触发，所以高风险路径不把任意模型列表交给网关盲目切换；审核或业务拒绝不触发换模型重试。仅允许满足所需参数与数据处理要求的 Provider。正式对比评测固定实际模型/Provider 并关闭自动回退，另设故障演练检查可用性。记录实际使用的模型、Provider、回退原因、Schema 结果、Token、成本与延迟。[OpenRouter 回退机制](https://openrouter.ai/docs/guides/routing/model-fallbacks) · [结构化输出](https://openrouter.ai/docs/guides/features/structured-outputs)

### 6.2 policy_bundle：规则与证据同版

一个不可变 policy_bundle 同时绑定文本、结构化规则参数、生效时间、规则/文档 hash、Chunk/索引版本及兼容代码版本。support 起草，后台解析分块并建立 PostgreSQL 全文＋pgvector 索引；主管发布前运行七天边界、旧承诺、RAG 引用和金额回归测试。状态为 draft → indexed → verified → active → superseded。索引准备成功后才事务性切换生效指针；回滚以新的激活记录指向已验证包，保留历史。

RAG 仅在服务端确定的允许版本中检索：泛问是当前 active 包，订单问题由规则服务判定当前政策与购买时明确承诺，返回适用 bundle_id 后再检索相同 bundle。标题/条款分块、全文检索与向量检索并行，RRF 融合；回答展示标题、版本、条款和有效日期。旧版不能混入通用在线回答，但可用于适用历史订单及版本冲突测试。缺证据则拒答/转人工，不让模型从文档文本推断资金资格。

一次请求锁定 policy_bundle_id；生成提案时绑定当前决策证据。仓库等待或主管审批期间如订单、质检、政策或计划版本变化，恢复时重新校验并让失效提案重新审批，不能用旧引用或旧金额继续执行。个人 Memory 与组织政策索引完全隔离。

## 7. Memory：保留 v3 的双路线实验

| 类型 | 权威来源与用途 |
|---|---|
| 线程/审批状态 | LangGraph PostgresSaver；恢复 Graph，不等于跨会话画像 |
| 结构化客户画像 | PostgreSQL 业务表；仅已确认的语言、沟通渠道、隐私同意等，客户/API 更改 |
| 柔性长期偏好与已结案摘要 | LongTermMemory 接口；只允许非交易性偏好，来源/时间/删除状态可审计 |
| 政策知识 | policy_bundle＋RAG；不是个人记忆 |
| 订单、余额、物流、审批 | Commerce MCP 实时权威查询；Memory 不得覆写 |

PostgresStore＋薄写入策略与 Mem0 OSS＋pgvector 分别做隔离的离线 A/B：相同故事测试提取、去重、更正、撤回、Recall@3、延迟、成本和跨用户隔离。在线同一时间只启用 PostgresStore、Mem0 或 off 中的一种；默认 PostgresStore 通过安全门槛后启用，否则 off。Mem0 若未通过重启持久化、删除语义、OpenRouter 兼容及隔离测试，只保留离线实验，不进入在线主链。Mem0 的内部存储不因向量后端设为 pgvector 就自动全部变成 PostgreSQL。

允许写入的只有明确偏好与已结案摘要；不写姓名、地址、支付信息、完整原文、未确认推断，也不记住“旧订单已退款”作为下次判定依据。更正时旧偏好必须失效，用户撤回后不可再检索。认证用户 ID 决定 namespace；同名用户、跨用户检索及“凭记忆批准退款”必须在回归集失败即阻断。[LangGraph Store](https://docs.langchain.com/oss/python/langgraph/stores) · [Mem0 OSS](https://docs.mem0.ai/open-source/overview)

## 8. 数据：一个一致业务世界，三档规模

### 8.1 数据来源及不能做的拼接

| 来源 | 在 v4 的用途 | 边界 |
|---|---|---|
| 自建确定性业务世界 | 核心订单、支付、物流、售后、审批、客服、记忆、政策的全链种子 | 全部标记 synthetic；是主要演示、CI、金标与性能数据，不冒充真实平台流水 |
| [Olist](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce) | 校准适用的订单商品数、重复购买、配送耗时等分布；可选单独导入其真实订单关系验证解析/查询 | 巴西历史数据，BRL 与中国政策不混用；无原始退货、入库、审批或中文客服联结；原文件本地获取、核对当前许可，不作为核心可运行前提 |
| ECD、CSDS、申请后的 JDDC 等中文语料 | 获授权时做表达、歧义、摘要等语言专项；也可只参考语言现象原创话语 | 不与合成或 Olist 身份强行联结；许可不明的原文不入公开仓库或云端 Trace |
| ECom-Bench、τ³-bench retail、ABCD | 独立外部迁移评测与方法参考 | 保留原任务/政策/评分，固定版本并单独报告；不改写后伪称原榜单成绩 |

UCI Online Retail II 的取消发票不能证明与某笔订单的退货入库、审批和退款对应；不进主数据链。Olist 导入如需售后扩展，只能建单独、明确标为 synthetic_overlay 的本地实验；不与主人民币世界混成一套交易。[UCI 字段说明](https://archive.ics.uci.edu/dataset/502/online+retail+ii) · [JDDC 申请说明](https://github.com/hrlinlp/jddc2.1)

### 8.2 因果生成器与来源登记

以 ScenarioSpec 作为可重放输入：scenario_id、seed、固定业务时钟、业务日历、policy_bundle_id、客户/商品特征、事件脚本、预期检查点和 provenance。先生成 customers/products/orders/paid_allocations，随后只能通过定义过的状态转移产生 shipment、return、warehouse、proposal、approval、refund、ticket。参考状态模型与实际运行服务分开实现，避免同一段错误代码既生成 fixture 又宣布其正确。LLM 只可在已生成事实基础上改写客户语言，不得生成订单 ID、金额、状态或 gold。

例如同一 customer C17 的 order O42、item I8 实付 ¥199：9 月 4 日签收，9 月 8 日申请，9 月 11 日收货质检通过，9 月 12 日生成提案并经主管批准后模拟退款。所有下游行使用同一订单及客户关系。固定 seed、生成器版本和 policy_bundle 后，可在新机器重建相同关系及期望状态。

生成任务分块流式输出、批量导入 PostgreSQL，并在导入前后验证；百万档不把所有对象一次放进内存。PostgreSQL COPY 是批量装载机制，实际导入耗时和磁盘量以测试机器记录为准，不预设虚假 SLA。[PostgreSQL COPY](https://www.postgresql.org/docs/current/sql-copy.html)

最低质量闸：孤儿外键 0、客户/订单错配 0、非法状态转移 0、退款金额守恒错误 0、未审批执行 0、日期倒序 0、货币混用 0、重复幂等键导致的额外记账 0。预期要被拒绝的非法操作保存在测试脚本中，不向业务表注入矛盾“事实”。输出 data_quality_report.json：上述计数、各表行数、类别/场景覆盖、拒收行数、种子/hash、来源比例及真实校准分布对照。退款发生率等无可靠公开校准的参数在配置中标为**合成假设**，不声称是平台真实比例。

### 8.3 规模与多样性

| 档位 | 目标规模 | 作用 |
|---|---|---|
| demo | 25 固定业务场景、约 20–25 份政策、Mock 模型 | 无外部数据/模型 Key 可启动，稳定面试演示 |
| realistic | 约 10 万订单、12–20 万商品项、40–70 万物流/业务事件、5 千–1 万客服会话及数千退货申请 | 日常业务集成、查询和可视化；种子固定 |
| scale | 按需生成 100 万订单、120–200 万商品项、400–700 万事件、5 万–10 万客服会话及数万退货申请 | 百万历史记录下的索引、API、后台任务、幂等/并发和报表压测；不在每次 CI 重建 |

上述是建设目标，不是已生成数据或真实发生率。100 万历史订单并不等于超大型平台全站流量；简历只写实际达成规模和设备/负载条件。压力测试另用可配置的 20/50/100 并发阶段测真实 API 混合流量，模型端调用单独测且限额，避免把外部模型限流归因于 PostgreSQL。

业务多样性至少覆盖：单件/多件、多 seller/多包裹、重复购买、支付与优惠的可支持/需人工类别、正常/延迟/丢件、签收窗口内/边界/逾期、整件/部分退货、入库数量不符/质检争议、审批通过/拒绝/超时、政策切换、重试/并发，以及指代不清、多意图、改口、越权和恶意文档。用覆盖矩阵和关键维度两两组合，而非将模板无差别复制。背景数据尽量保持校准分布；锁定评测集特意加权长尾/高危案例，两者比例分开报告。Faker 仅产生虚构展示字段，不负责业务关系。

## 9. 可执行评测规范

### 9.1 套件、分组和金标

| 套件 | v4 目标 | 用途 |
|---|---:|---|
| smoke_demo | 25 | 每提交无 Key 冒烟，展示主链和负例 |
| core_business | 约 200 个人工复核多轮案例 | 查单、物流、申请、仓库、审批、退款、异常和人工接管的端到端结果 |
| intent_clarify | 约 140 个专项 | 多标签、槽位、指代、澄清、Replan；与 core 的重叠按 case_id 去重 |
| policy_rag | 约 60 个查询—证据金标 | 当前/历史适用版本、边界、冲突、拒答、注入 |
| memory_story | 约 40 组多会话故事 | PostgresStore/Mem0 A/B、更新/删除、跨用户隔离 |
| rule_state | 2,000–5,000 个规则/属性案例 | 非法事件序列、金额、授权、幂等和并发；**不计入 LLM Agent 成功率** |
| external_transfer | 按源官方任务集 | ECom-Bench、τ³-bench 等，独立分数 |

core、intent、RAG 与 memory 采用大致 60% dev / 40% locked test，按客户、订单、来源对话及模板家族分组；同订单换个数字/说法不得跨分区。所有 critical 案例双人复核，其余至少 20% 双人复核；争议记录裁决。200 人工金标为逐步扩展目标，先交付最小 25 smoke＋30 core＋30 intent＋20 RAG，再增加覆盖。大量背景会话或规则案例不能替代人工金标，亦不能冒充真实用户发言。安全零失败只说明测试集内未发现该类问题，不作统计上的零风险宣称。

统一 EvalCase JSONL 契约包含：schema_version / case_id / suite / split / risk_tier / tags、provenance 与许可、group_keys、fixture（时钟、policy_bundle、角色、初态、记忆 namespace）、dialogue_script、gold（路由/槽位/允许工具偏序/禁止动作/引用/终态/必须与禁止声明）、review。gold 绝不传给 Agent。来源不允许分发的行只存本地 hash 和引用，不进入公开 fixture 或 Cloud Trace。每个 manifest 固定数据、生成器、政策、模型/Provider、Prompt、评分器、代码和随机种子版本。

### 9.2 Runner 隔离与真实入口

validate → seed → run_agent → score → report 为可命令行复现的执行链。每个评测 worker 使用独立 PostgreSQL 评测库或严格隔离的 schema，并隔离 Redis key、checkpoint、柔性记忆、工单和模拟支付环境；同一案例内部保留多轮状态，案例结束清理。不能仅靠一个外层事务 rollback：客户、仓库、主管可能通过不同请求和后台任务提交。case_id/run_id 贯穿隔离命名空间，迟到的异步任务不得污染下一案例。

Runner 使用 customer、warehouse、supervisor 的实际认证 API 顺序重放输入，不直接代替 Agent 写最终业务状态；可重启服务后继续同一审批案例。只运行模拟资金服务，不连接真实支付或物流。百万订单档是共用的不可变背景/性能库，不为每个 Agent 案例复制百万行；正确率与压力结果各自汇总。

### 9.3 评分顺序与评分器自检

先验 fixture/金标和基础设施，再判业务安全、最终 DB/审计状态和必要沟通，最后评分路由、槽位、RAG、记忆、语气/完整性。每个任务允许多种同样安全的工具路径：检查必需/禁止工具、参数、偏序、次数和最终状态，不机械逐字匹配唯一轨迹。审批跨两个以上请求的案例必须检查“提案待批时 refund_ledger 为零”及“批准后至多一笔正确金额”；仅有“已退款”的回复不得通过。[τ³-bench 终态评估方法](https://github.com/sierra-research/tau2-bench/blob/main/docs/evaluation.md)

评分器单测要故意注入未审批退款、重复记账、错客户订单、过期政策引用、失效提案执行、只口头宣称处理、Judge 误判等错误，确认均失败；关键金标与状态断言人工复核。Hypothesis 状态机用随机动作序列不断检验不变量，发现失败时保留可重放最小序列。[Hypothesis stateful testing](https://hypothesis.readthedocs.io/en/latest/stateful.html)

核心 item 结果包含 task_success、route/intent、slot、clarification、replan、tool_contract、citation_valid、safety_pass、memory_isolation、latency/cost 与失败码。业务安全、权限和退款终态由程序化断言决定；Judge 只补充语气/完整性，不能把硬失败改成通过。超时、缺测、评分器错误均为 incomplete，单独列分子/分母且不算通过。发布门槛首版沿用一级路由 Accuracy ≥90%、工具/参数通过率 ≥90%、RAG Hit@5 ≥85% 的计划目标，实际基线后核定；critical 集的未审批/重复退款、跨用户读取、失效提案、记忆泄漏和无界循环必须为 0 个失败。若均分提高但新增 critical 失败，候选版不能发布。

每提交跑无 Key Mock、Schema/来源、规则与安全单测；合并前/定期跑固定真实模型的完整内部集；外部公开基准手动独立运行。性能用 k6 按只读查询、会话、申请、审批的混合流量采样，记录硬件、并发、吞吐、P50/P95、错误率、数据库资源和模型调用费；指标门槛依据首轮实测设定，不编造数值。[k6 API 压测指南](https://grafana.com/docs/k6/latest/testing-guides/api-load-testing/)

## 10. Trace、Dashboard、报告与 Langfuse 成本边界

### 10.1 一条业务链可以有多条 Trace

每个 HTTP/后台动作有自己的 OTel trace_id：客户创建退货、仓库数日后入库、主管审批与退款恢复不能强行占用一条跨日根 Trace。把 thread_id、return_id、approval_id、case_id/run_id 和先前 span context 存为受控关联字段；新 Trace 用 Span Link 表达因果，审计表记业务 ID 与 trace_id。v3 评测补充规范中的“每个多轮 case 一条根 Trace”在 v4 更正为“一个 case 可以关联多条根 Trace”。Langfuse 的 Session/metadata 也按 thread_id/case_id 聚合；评分器跨这些 Trace 查最终数据库与审计，而非只读第一条 Trace。[OTel 上下文与 Span Link](https://opentelemetry.io/docs/languages/python/instrumentation/)

Jaeger 展示 API → LangGraph → MCP → PostgreSQL / Worker 的耗时、异常和重试；Langfuse 展示受控脱敏的 Agent/LLM、RAG 证据、Prompt/模型版本、Token/成本及评估分数；PostgreSQL 审计是资金动作权威。统一 trace context，避免两套 SDK 为同一逻辑步骤重复造 span；Collector 在导出前移除姓名、地址、支付/原文敏感数据。评测与 demo 案例完整采样，规模负载只采样一般读请求并保留高风险失败链路；审计始终全量。

### 10.2 可视化与可独立保存的结果

Langfuse Experiment / Dashboard：按意图、风险、政策包、模型版本查看 task_success、RAG 引用、Token/成本及 baseline↔candidate 退化案例。Jaeger 用于从失败条目钻取具体请求。Prometheus/Grafana profile 展示 API/MCP P95、错误率、模型回退数、Celery 积压、审批待处理时间和模拟退款失败；Prometheus 标签只使用服务、路由、结果码等低基数字段，不放 customer_id/order_id。[Langfuse Dashboard](https://langfuse.com/docs/metrics/features/custom-dashboards) · [Prometheus 标签最佳实践](https://prometheus.io/docs/practices/naming/)

每个 run 必产出 evals/reports/<run_id>/ 下的 manifest.json、case_results.jsonl、summary.json、report.html（可加 Markdown 摘要）。报告列实际样本量、dev/locked、来源和合成比例、各分层分子/分母、critical 与 incomplete 数、混淆矩阵、RAG/记忆结果、P50/P95/成本、基线差异和脱敏 Trace/审计入口。summary.json 是 CI gate 的唯一机器输入；Langfuse 只同步展示分数，不反向决定是否通过。Langfuse 故障或超配额时仍可在本地完成评估、保存报告。不同版本结果只在相同 dataset/evaluator 契约下直接比较。[Langfuse Experiment 对比](https://langfuse.com/docs/evaluation/experiments/compare-experiments)

### 10.3 免费不等于无约束：部署选择

截至本计划日期，Langfuse Cloud Hobby 免费，但含每月 50,000 units（Trace＋Observation＋Score）、2 用户和 30 天**数据访问窗口**；一个 Agent 请求有多个 Observation/Score，50,000 units 不是 50,000 次会话。官网“数据访问窗口”不能被简化为“第 31 天必物理删除”。[Cloud 定价](https://langfuse.com/pricing) · [unit 定义](https://langfuse.com/docs/administration/billable-units)

Langfuse OSS 自托管核心 Trace、Dataset、Experiment、Score 和 Dashboard 无按量授权费，数据默认不自动过期；但数据库、ClickHouse、Redis、对象存储、备份与运维需要资源。项目级自动保留策略、项目级 RBAC 等治理功能属于 Enterprise，不把它们列为 OSS 已有能力。[自托管价格与能力](https://langfuse.com/pricing-self-host) · [保留策略说明](https://langfuse.com/docs/administration/data-retention)

因此 v4 默认交付不依赖任何观测 SaaS：本地 Jaeger＋审计＋HTML/JSON 报告可独立使用；Langfuse OSS 以按需 profile 展示完整 Agent 评估。Cloud Hobby 只作为轻量试用，发送脱敏且限量的 demo/金标 Trace，不向其灌入百万订单压测轨迹。LangSmith Developer 同样是免费云端入门且有其按 Trace 额度/保留规则；关键差异是 Langfuse 提供无需按量授权费的 OSS 自托管核心能力，而 LangSmith 当前自托管主要在 Enterprise 方案。两者计量单位不同，不拿 50,000 units 与 5,000 traces 直接比较。[LangSmith 官方定价](https://www.langchain.com/pricing)

## 11. 实施里程碑与退出条件

单人项目以约 9 个实施周＋1 周缓冲为规划，不把模型调用等待、语料申请或百万档导入耗时伪装成已知常数。可按垂直切片调整，但每阶段必须有可运行退出条件。

| 阶段 | 主要交付 | 退出条件 |
|---|---|---|
| 1. 基础 | Monorepo/Compose、PostgreSQL 迁移、Keycloak 四角色、API/MCP 鉴权、25 demo 场景 | 无 Key 可启动；本人查单与跨用户拒绝均过集成测试 |
| 2. 一致数据 | ScenarioSpec、状态生成器、来源登记、realistic 10 万单、质量报告 | 可重复 seed；关系、时间、金额和状态不变量零违规 |
| 3. Agent | RouteDecision Schema、RuleGate、SlotResolver、LangGraph/PostgresSaver、SSE、澄清与 Replan | 单/多意图、缺槽位、改口、上限、断线恢复可测 |
| 4. 政策/RAG | policy_bundle 发布流程、全文＋pgvector 混合检索、引用、版本切换 | 七天边界、购买旧承诺、证据不足和旧版误引测试通过 |
| 5. 业务闭环 | Return/warehouse/inspection/refund proposal、主管 UI、interrupt/resume、幂等模拟退款 | 审批前零退款；拒绝、改额、重复请求、重启恢复与七天处理提醒通过 |
| 6. 评估 | JSONL Schema、隔离 Runner、人工金标分批、属性测试、反向评分器测试 | 金标可重放；错误轨迹能被评分器捕获；本地报告与 CI gate 可运行 |
| 7. 观测/规模 | OTel/Collector/Jaeger、Langfuse OSS profile、Grafana profile、100 万单按需生成与 k6 | 一个案例多 Trace 可关联；Dashboard/HTML 可钻取；记录实际硬件与负载指标 |
| 8. Memory/交付 | PostgresStore 与 Mem0 OSS 隔离 A/B、40 组故事、Playwright、3 条固定演示、README/威胁模型 | 删除/隔离/不凭记忆退款通过；主业务集不回归；新机器按文档启动 |

验收必须展示三条固定演示：①“多意图＋澄清后查本人物流”；②“七天内退货→入库质检→主管批准→单笔模拟退款”；③“跨用户/旧提案/重复审批被拒绝”。每条展示政策证据、数据库终态、审计、Jaeger Trace、可选 Langfuse Observation 和报告条目。百万档只需按需成功生成并完成质量/负载报告，不要求在普通开发机上每次启动全部数据。

## 12. 仓库交付、非目标和风险控制

建议目录：apps/web、apps/api、services/commerce-mcp、packages/domain-policy、packages/agent、data/scenarios、data/generator、evals/datasets、evals/runners、evals/reports、observability、infra/compose、docs。仓库提交少量原创 demo fixture、可重建生成器、数据字典、Schema、政策包、评分脚本、Grafana 配置、脱敏样例报告与 README；百万级生成物和受限外部原始数据不提交。公开仓库记录生成命令、seed/hash、许可和来源，不把未授权对话上传 Cloud。

明确不做：真实支付/退款、真实阿里/京东/Shopee 账号与私有流水抓取、多租户隔离/计费、自动取消订单写工具、复杂券税运费结算、跨境法律裁决、全量模型微调、语音/微信全渠道、图数据库记忆、两套长期记忆融合在线判定、以 LLM Judge 取代退款安全断言。若工期受限，优先保住本人查单 → 客户确认退货 → 仓库入库 → 主管批准 → 幂等模拟退款 → 审计/评估/报告；压缩外部语料与 Mem0 在线接入，不削减安全门槛。

最终简历只陈述实际完成的代码、数据量和实测指标，分别写明“合成业务数据”“真实历史订单用于校准/独立导入”“外部公开基准”和“本项目锁定集成绩”；不能把生产相似性写成真实平台生产经历。
