# CommerceGuide v1：企业级电商售前智能购物顾问

> 状态：独立设计计划，尚未实施。更新日期：2026-09-27。
>
> 本文与 `resolveai-v1.md`、`resolveai-v2.md`、`resolveai-v3.md` **并列而非覆盖**。ResolveAI 继续代表售后客服；CommerceGuide 是新的售前项目。可复用技术经验，但业务模型、评测集、演示脚本和未来代码边界独立。此阶段只制定计划，不修改售后设计或搭建系统。

## 1. 产品定位与边界

为一家**虚构的单商家家居/办公用品电商**做中文售前顾问：帮助用户表达需求、发现合适商品、比较候选、解释价格与促销、确认可售状态，并把用户明确选择的 SKU 放入模拟购物车。它不是会聊天的商品目录，也不声称已经接入亚马逊/阿里/京东/Shopee 的实时系统。

选择家居/办公用品，是因为预算、尺寸、材质、颜色、承重、风格等条件既能体现多轮澄清与硬约束过滤，也便于对比；真实商品元数据可从公开 ABO 数据集抽取。先覆盖 4–6 个相近子类，例如办公椅、书桌、置物架、台灯、沙发/边几；最终以可用数据的属性完整度决定，不能为了凑品类虚构关键规格。

**核心闭环：**需求表达 → 结构化槽位和澄清 → 混合检索 → 硬约束校验与排序 → 商品对比 → 实时报价/库存/促销核验 → 可解释推荐 → 用户确认加购 → 购物车反馈或转人工。

本期不做支付、真实下单、真实保留库存、退款/退货/物流，也不把售后退款审批逻辑搬过来。最终成交价格以模拟结算服务的再次核验为准；售前回答里的价格是有时间戳的报价快照，不能被长期记忆或向量索引替代。

### 成功标准

1. 用户能以自然语言完成至少 3 类真实售前任务：带约束找商品、对比商品、了解优惠并加购。
2. 推荐结果**零硬约束违规**；价格、库存、优惠资格均有确定性工具证据和版本/时间戳。
3. 预算变化、缺货、报价过期、多意图、指代不清时能够澄清或有界 Replan，不编造商品参数、不悄悄放松硬条件。
4. 完成可复现的离线评测、安全回归和跨服务 Trace；简历展示实际测量数据，不预填“转化率提升”等未经线上实验支持的数字。

## 2. 范围分级：复杂但可交付

| 层级 | 内容 |
| --- | --- |
| 必做主链 | 商品导入与数据质量、结构化意图/槽位、LangGraph 有界工作流、混合商品检索、硬约束过滤、比较、实时价格/库存/促销、确认加购、人工转接、评测/追踪 |
| 必做工程护栏 | 用户与购物车鉴权、幂等、价格/库存二次校验、模型输出校验、提示注入防护、数据来源标注、失败降级 |
| 可选实验 | PostgresStore 与 Mem0 OSS 对“明确、可撤回的柔性偏好”做隔离离线对照；不作为主链上线前置条件 |
| 暂不做 | 多商家平台聚合、真实支付/结算、广告竞价、在线推荐模型训练、复杂协同过滤、视觉搜图、多 Agent 群、Kafka/Kubernetes/图数据库、全面微调 |

默认只做**一个 Agent 编排图 + 一个业务工具服务**。复杂度放在数据契约、检索与规则结合、状态变化、评估和安全上，而非框架数量上。

## 3. 技术架构与选择理由

```text
Next.js 购物顾问 UI（聊天、商品卡、对比、购物车）
  → FastAPI（认证、SSE、请求校验、会话 API）
  → LangGraph StateGraph + PostgreSQL Checkpointer
      ├─ OpenRouter：结构化路由/槽位抽取、回答生成；能力受检的固定模型/端点
      ├─ PostgreSQL FTS + pgvector：商品文本与语义候选召回
      ├─ Commerce MCP：商品详情、当前报价/库存/促销、购物车写入、人工转接
      └─ PostgresStore：可撤回的跨会话柔性偏好（可选 Mem0 离线对照）
  → PostgreSQL：目录、SKU、报价、库存、促销、购物车、审计、会话与向量
  → Redis + Celery：离线导入/清洗/Embedding/索引重建（不承载关键交易状态）
  → OpenTelemetry → Collector → Jaeger；可选 Langfuse Agent Trace/评测展示
```

| 组件 | 职责与边界 |
| --- | --- |
| Next.js | 中文聊天、可点选筛选器、来源/更新时间清晰的商品卡、并排比较、价格变更提示、明确“加入购物车”确认 |
| FastAPI + Pydantic | API 契约、鉴权、会话串行化、限流、SSE、模型及工具结构化输出的二次校验 |
| LangGraph | 路由、澄清、检索、校验、生成、Replan、工具失败降级；用 `PostgresSaver` 保存线程内状态。官方持久化文档区分线程 checkpoint 与跨线程 Store：[LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence) |
| OpenRouter | 为路由/生成选可固定版本和提供方的模型；JSON Schema 能力须实际验证，本地仍执行 Pydantic 校验：[Structured Outputs](https://openrouter.ai/docs/guides/features/structured-outputs) |
| PostgreSQL + pgvector | 业务真相源、关键词检索和向量候选。向量只辅助找候选，不能证明某 SKU 有货或价格正确；多语言 Embedding 模型、维度和索引版本固定记录 |
| Commerce MCP | 明确输入/输出 Schema 的业务工具接口；服务端做鉴权、限流、入参验证。购物车是显式持久状态，不能靠 MCP 连接隐式保存：[MCP Tools 规范](https://modelcontextprotocol.io/specification/2026-07-28/server/tools) |
| OpenTelemetry / Jaeger | 串起前端请求、Graph 节点、检索、模型、MCP、数据库的耗时和失败。Jaeger 处理分布式 Trace，不代替业务评测 |
| Langfuse（可选） | 脱敏的 Prompt/模型/Agent 步骤、Token/成本、Dataset Run 和评分展示。它故障时主链可继续；评测数据的事实来源仍为仓库里的版本化 JSONL |

依赖使用固定版本并记录容器镜像/模型 ID。先以 Docker Compose 本地启动；不要把“用了微服务/MCP”误写成“承受生产流量”。

## 4. 数据来源与真实度

### 4.1 两类公开数据各司其职

| 来源 | 用途 | 不应用来做什么 |
| --- | --- | --- |
| [Amazon Berkeley Objects（ABO）](https://amazon-berkeley-objects.s3.amazonaws.com/index.html) | 选取有许可和出处的商品标题、类目、品牌、材质/尺寸等元数据，构建模拟商家目录；能用中文字段则优先用，缺失时标注翻译/清洗来源 | 不把历史 listing 当成此商家的真实在线售卖、实时价格、库存或优惠；不暗示 Amazon 授权本项目店铺 |
| [Amazon ESCI Shopping Queries](https://github.com/amazon-science/esci-data) | 用 Exact/Substitute/Complement/Irrelevant 标注做**独立的英文/日文/西班牙文检索/排序基准** | 不把 ESCI 指标当作中文对话效果；不直接把其商品 ID 与 ABO 或模拟目录强行拼接 |

ABO 提供约 14.7 万条商品与多语言元数据；元数据归档约 83 MB，原图归档约 110 GB，因此只下载/导入相关元数据子集，图片最多采用合规的小图子集。ABO 页面明确采用 CC BY 4.0：公开仓库若包含抽取数据/图片须署名、附许可证链接并标注改动。ESCI 则适合检索算法对比，不是店铺交易数据。实际动手前再次核对版本、许可证和数据下载条件；默认仓库仅放导入脚本、字段映射、来源清单以及足够小的合规示例。

### 4.2 模拟业务数据如何逼真但不造假

ABO 元数据经清洗、去重、归类和字段标准化后，生成一套**明确标为 synthetic** 的商家 SKU、可售状态、人民币价格、价格变更历史、仓库库存、促销规则、匿名购物车和用户旅程。用确定性随机种子与规则约束生成，确保可重放。例如同类商品价格落在合理区间；颜色变体共享部分规格但 SKU 独立；某促销只在指定时间、品类与用户资格下生效。所谓“价格变化/缺货”由可控事件脚本模拟，不是从外部平台爬取。

先交付两档 Seed：

- **快速档：**100–200 个清洗后商品，覆盖每类的预算、尺寸、材质和缺货边界；无需云模型 Key 时用固定 Mock 工具/模型结果跑完整业务与安全测试。
- **展示档：**500–1,000 个清洗后商品、约 800–2,000 个合成 SKU、10–20 条促销规则、120–200 条多轮售前旅程；仅当抽样检查字段完整度达标时扩充。缺关键属性的商品不参与要求该属性的硬约束推荐。

每条数据保留 `source_type`（ABO 原字段/翻译/规则合成/人工标注）、`source_ref`、`updated_at`、`catalog_version`；导入报告统计缺失率、重复率、单位归一化错误、不可比属性和图片许可状态。中文旅程由人工定义任务与判定条件，LLM 可帮助生成候选问法，但人工审核后才能进入锁定测试集。不能用模型自己生成并给自己评分的方式声称“真实用户验证”。

### 4.3 最小业务表与版本

`product`（商品/类目/来源）、`sku`（规格组合）、`sku_attribute`（带单位与证据的可比较属性）、`product_search_doc`（可重建的文本/向量索引）、`price_book`（价格、币种、生效期、版本）、`inventory`（可售量/更新时间/版本）、`promotion_rule`（资格、互斥/叠加、有效期、版本）、`cart`/`cart_item`、`user_preference`、`handoff_ticket`、`audit_event`。身份、会话和 checkpoint 使用隔离表/Schema。

权威数据优先级：**业务表与确定性规则 > 已确认用户输入 > 检索文本 > 长期记忆/模型推断**。所有展示价格都指向一个 `offer_snapshot_id` / `offer_version` 和短 TTL；用户加购时重新核验，失效则展示新报价并要求再确认。购物车写入不等于下单或库存预留。

## 5. Agent 工作流、意图与澄清

### 5.1 版本化意图及结构化 Schema

路由标签：`discover_product`（找商品）、`refine_requirements`（修改条件）、`compare_products`、`ask_product_fact`、`ask_price_or_promo`、`ask_availability`、`add_to_cart`、`view_cart`、`request_human`、`off_topic_or_unsafe`。多意图允许同时存在，例如“这两把椅子有什么区别？哪个有货，打折后多少钱？”；先消解商品指代，再并行读取各 SKU 事实，最后综合回答。`add_to_cart` 必须在只读问题和报价核验完成后由用户确认。

`IntentDecision`（Pydantic/JSON Schema）包含 `schema_version`、`intents[]`、`candidate_slots`、`references[]`、`missing_slots[]`、`needs_clarification`、`confidence_band`、`reason_code`、`detected_constraints`。模型只给候选，不直接选工具或决定业务权限；`RoutePolicy` 以已验证状态和白名单路由。模型返回无效 JSON/非法枚举时最多重试一次，然后降级为保守澄清或人工入口。固定模型、Provider、Prompt、Schema、标签版本以便评测。

### 5.2 槽位契约、硬/软约束

| 任务 | 启动所需 | 操作前必须有 | 缺失/冲突处理 |
| --- | --- | --- | --- |
| 找商品 | 商品用途或类目；不明则先问一个最有价值的问题 | 硬条件须可解析为合法单位/区间；预算币种明确 | 预算“2000 左右”默认为软目标并提示；“绝不能超过 2000”才是硬上限；尺寸“80”须问单位/指哪个维度 |
| 对比 | 至少 2 个唯一商品或当前候选中的明确序号 | 同类可比较属性、SKU/变体已确认 | “第一个和那个黑色的”若非唯一则要求点选；跨品类只比较共通字段并提示不可比部分 |
| 价格/促销/库存 | 商品或 SKU 唯一 | 当前报价、库存、规则版本和查询时间 | 变体不唯一则问颜色/尺寸；工具不可用时不报具体价格/有货结论 |
| 加购 | SKU、数量、明确用户动作 | 认证购物车、有效报价/库存复核、幂等键 | 未确认/变价/缺货时停止写入并让用户重新确认，不自动换 SKU |
| 人工转接 | 用户主动要求或系统兜底 | 最小必要的脱敏上下文 | 不把完整聊天或敏感画像无条件发给人工渠道 |

用户可把条件标为 `hard`、`soft`、`unknown`。例如“宽度≤80 cm”和“预算≤2000 元”是硬过滤；“最好浅木色”是软排序。互斥/不可能条件（“必须宽度≤60 cm，但指定 SKU 宽 120 cm”）先指出冲突，不制造满足条件的假结果。无结果时只建议放宽**软条件**；放宽硬条件需用户明确同意并记录 `constraint_revision`。

### 5.3 Graph 状态与有界 Replan

主要节点：`guard_input → route_and_extract → resolve_reference → validate_slots → clarify_or_plan → retrieve_candidates → verify_constraints → rank_and_diversify → fetch_live_offer → compose_grounded_answer → await_user_cart_confirmation → execute_cart_or_handoff`。工具失败/数据空缺进入 `safe_fallback`。建议 Graph 状态包含 `thread_id`、`user_id`、`turn_id`、`plan_revision`、`intent_decision`、`verified_slots`、`constraints`、`pending_clarification`、`candidate_skus`、`evidence`、`offer_snapshot_id`、`quote_expires_at`、`cart_confirmation_token`、`tool_errors`。不要把完整商品目录塞进 checkpoint。

- 同一线程最多连续澄清 **2 轮**。每轮只问最阻断的一到两个问题，并提供可点选项。超过上限、用户拒答或矛盾无法消解时，展示当前可做范围与人工转接；不得猜必填 SKU/尺寸。
- 下一条消息先判断是补槽位、纠错、新任务还是取消；新任务将旧计划标为 `superseded`。同一线程串行处理，`plan_revision` 乐观锁防止并发覆盖。
- 预算/尺寸/品类变化、候选下架/缺货、促销到期、报价过期触发 Replan。最多 **2 次自动重规划**；每次丢弃旧候选或旧报价证据，重新跑硬过滤与实时报价。超过上限就解释原因并转人工，不循环调用模型。
- 用户点“加入购物车”时带上展示过的 SKU、数量、报价快照和确认 token；服务器再次查价/库存，任何变化都回到确认状态。普通用户多轮消息使用同一线程的新输入，不把主管审批式 `interrupt` 当作常规聊天机制。

## 6. 检索、排序、比较与回答

1. **候选召回：**规范化中文词、同义词和单位；中文标题/描述在入库与查询时使用同版本中文分词（如 jieba），把分词结果建入 PostgreSQL `tsvector`/GIN 索引，不假设数据库默认全文检索器能正确理解中文词界；再与 pgvector 语义候选按 RRF 融合。固定多语言 Embedding 模型/向量维度，更新模型时重建并切换索引版本。结构化字段先做可索引的硬过滤；召回后对每个 SKU 再做一次权威字段校验。不要只用向量相似度决定推荐。[pgvector Hybrid Search](https://github.com/pgvector/pgvector#hybrid-search)
2. **排序：**确定性主分结合文本相关性、硬条件通过、软条件匹配、属性完整度与可售状态；可做轻量 LLM rerank 作为可开关实验，但不得覆盖规则门控。Top 3 控制同款/同品牌重复，给用户不同取舍（省钱、材质更佳、尺寸更合适）。
3. **商品比较：**服务端按 `attribute_schema_version` 归一化单位，只显示可证实、可比较的属性；缺失标“未知”，不让模型补齐。差异摘要附商品 ID/字段证据。可选“为什么推荐”必须对应用户条件，而非空泛营销语。
4. **知识 RAG：**只用于原创的商品选购指南、材质维护、配送范围/售前规则等版本化文档。RAG 文本不能覆盖 SKU 属性、价格、库存、促销规则。外部商品描述和评论视为不可信数据，剥离其中的指令式文本。
5. **回答：**每个商品卡展示实时报价查询时间、库存状态、促销是否符合资格、关键匹配/不匹配项、可点击来源。无符合硬条件的商品时如实说明，并给出“修改条件/人工帮助”而非凭空推荐。

## 7. Commerce MCP 工具契约与交易边界

| 工具 | 性质 | 校验要点 |
| --- | --- | --- |
| `search_products(filters, query, page)` | 只读 | 服务端限制字段、分页和 Top-K；无任意 SQL 或任意 URL |
| `get_product_specs(product_ids, sku_ids)` | 只读 | 返回规格、单位、来源与版本；非法/不存在 ID 显式报错 |
| `get_live_offer(sku_ids, user_context)` | 只读 | 一次计算现价、库存、促销资格、过期时间和报价快照；不得把模型口述的会员身份当真 |
| `get_cart(cart_id)` | 只读 | 每次按认证身份校验 cart 所属；返回最小必要字段 |
| `add_to_cart(cart_id, sku_id, qty, offer_snapshot_id, confirmation_token, idempotency_key)` | 写入 | 限量、鉴权、幂等、有效报价、价格/库存二次核验、事务和并发控制；变价返回冲突，不静默成功 |
| `create_sales_handoff(reason_code, consented_context)` | 写入 | 只交接已授权的最小上下文，去 PII，记录状态与审计 |

MCP 层声明 JSON Schema 输入/输出；业务服务不信任模型传来的 `user_id`、角色、会员等级或购物车所有权。认证主体来自服务端请求上下文。购物车句柄显式传递、每次鉴权；所有写入有审计事件和幂等键。模型可以建议调用，但无法自行创造折扣、改价、增加库存、读取他人购物车或跳过用户确认。真实支付和支付凭据完全不进入本项目。MCP 最新官方工具规范强调 Schema、输入验证、访问控制、限流和有状态工具的显式句柄：[Tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)。

## 8. 记忆与用户画像：服务售前而不污染事实

| 类型 | 内容与实现 | 生存规则 |
| --- | --- | --- |
| 当前会话工作记忆 | 最近轮次、已验证槽位、候选清单、澄清/Replan 状态；LangGraph `PostgresSaver` | 定期压缩只含意图与决策证据的摘要；checkpoint 与业务审计分离 |
| 结构化用户画像 | 经用户明确确认的偏好类目、常用尺寸、预算段等；业务表 `user_preference` | 用户可查看/修改/删除；有来源、时间、同意状态；敏感属性不从对话偷偷推断 |
| 柔性长期偏好 | “以后优先推荐浅色木质家具”“尽量简短解释”等；默认 `PostgresStore` 跨会话按用户 namespace 存储 | 只用于软排序和表达风格，可撤回/更正/TTL；用户当前明确条件始终优先 |
| 商品/价格/库存/促销 | PostgreSQL + 业务工具实时读取 | **绝不作为长期记忆**；过期快照不得继续用于推荐或加购 |

可保留与售后 v3 便于比较的 `LongTermMemory` 适配接口，但**项目运行时只启用 PostgresStore 一套在线长期偏好**。Mem0 OSS 是独立的可选离线实验：用相同多会话样本比较提取、去重、更正、召回、删除、延迟与成本；不把两套记忆结果拼接，也不为完成主项目强制部署 Mem0。偏好例子：“上次喜欢白色书桌”不能成为今天“必须买白色”的硬条件；“上次价格 1299”不能回答今天价格。

## 9. 可观测性、评测与上线门槛

### 9.1 Trace 与业务指标

OTel Span 覆盖 `route`、澄清、检索、过滤/排序、实时报价、促销引擎、MCP 写入、DB、模型调用；附匿名 `session_id`、`plan_revision`、`catalog_version`、`offer_version`、Prompt/模型/工具版本、错误码与延迟，不附原始 PII 或全量聊天。Jaeger 定位跨服务慢点/故障；Langfuse 可选显示模型决策、检索证据、成本和评测分数。应用指标另记录商品零结果率、澄清率、人工转接率、加购成功/冲突率；这些是**演示环境任务指标**，不是线上转化率。

### 9.2 三层版本化评测

1. **组件层：**商品清洗、单位归一化、促销组合/互斥、硬约束纯函数、购物车幂等和越权测试；检索用 ESCI 做独立多语排序基线，中文目录用人工标注 query–SKU 集评估 `Recall@10`、`nDCG@10`。
2. **多轮任务层：**120–200 条锁定 JSONL 旅程，覆盖模糊需求、条件变更、对比、指代、缺属性、零结果、促销不适用、报价过期、缺货、工具失败、人工转接。每条记录初始数据快照、必需/禁止工具、期望槽位、可接受商品集、理由证据与最终状态。按用户/商品族划分开发集与测试集，避免同款泄漏。
3. **安全和发布层：**提示注入、跨用户购物车、伪造会员资格、重复加购、陈旧价格、负库存、越权字段、敏感信息泄漏。P95 延迟、Token/成本、外部服务故障下的降级另做基线。LLM Judge 只辅助评估解释质量/自然度，业务事实由数据库断言，抽样人工复核 Judge 分数。

| 指标 | 初始门槛（需用基线再校准） |
| --- | --- |
| 硬约束、SKU 属性、价格/库存/促销事实错误 | 锁定安全集 **0 例**；任何失败阻断发布 |
| 跨用户访问、未确认加购、重复写入、非法折扣 | **0 例**；必须有自动化回归 |
| 中文检索 `Recall@10`、`nDCG@10` | 先与关键词-only 基线比较并报告实测值，不提前承诺虚构分数 |
| 多轮任务完成率、澄清准确率、无结果正确拒答率 | 发布前定义目标并按任务类型分层汇报；不以一个总分掩盖安全失败 |
| P95 响应、模型成本 | 在指定硬件/模型/数据档位下报告，超过预算则优先减少无效模型/工具调用 |

每次数据、Prompt、模型、检索、规则、记忆变更都跑对应回归；Trace 与 JSONL 案例可以相互定位。评测脚本可复现、测试集锁定，Langfuse 不成为唯一事实仓库。

## 10. 安全、隐私、可靠性

- 商品标题/描述、用户文本、文档检索结果都可能包含提示注入；作为数据进入模型，不允许改变系统提示、工具白名单或认证信息。没有“执行任意 SQL/HTTP 请求”的工具。
- 输入长度、模型输出 Schema、工具参数、JSON 体积、查询 Top-K、超时/重试次数均设上限。工具失败分级：检索失败可建议重试/转人工；实时报价失败时禁止生成具体成交承诺；购物车写失败应返回可恢复的状态而非假成功。
- 商品索引可以异步更新，价格、库存、促销资格必须同步权威查询。对热数据缓存设短 TTL 和版本校验；写入时始终再读/再算。
- 对用户画像与偏好采取最小化、目的限定和可删除机制；日志脱敏。演示用户全部匿名模拟；密钥走环境变量或密钥管理，不提交仓库。
- 评测和演示统一固定模拟时钟/事件脚本，让促销过期、缺货和价格变化能稳定复现。

## 11. 七周实施路线与验收

| 周 | 工作 | 可验收结果 |
| --- | --- | --- |
| 1 | 定义单商家业务、数据契约、许可证清单；ABO 子集导入/清洗；PostgreSQL 迁移与两档 Seed | 可查看真实来源字段与合成字段；导入质量报告；重复/缺属性/单位测试 |
| 2 | FastAPI/Next.js 基础交互；LangGraph 状态、结构化意图/槽位、澄清与持久化 | 单/多意图、缺槽位、指代不清、2 轮澄清上限、会话重启恢复 |
| 3 | FTS + pgvector 混合检索、硬过滤、排序、比较；建立中文相关性标注集 | 三类约束任务可稳定给出符合条件的 Top 3；零结果不编造；与关键词-only 基线比较 |
| 4 | MCP 商品/报价/促销/库存/购物车工具，确定性促销引擎，价格快照和加购确认 | 缺货、变价、促销不符、重复加购、跨用户访问端到端通过 |
| 5 | Replan、用户画像与 PostgresStore 柔性偏好、人工转接；OTel/Jaeger 与可选 Langfuse | 改条件/过期/工具失败均有可解释路径；偏好可更正与删除；跨服务 Trace 可查 |
| 6 | 120–200 条锁定多轮案例、安全/性能回归、固定演示脚本 | 关键安全门槛全过，任务指标与 P95/成本报告可复现 |
| 7 | 缺陷修复、文档/架构图/威胁模型、Docker Compose 一键运行；可选 Mem0 隔离对照 | 新人按 README 能跑快速档及 3 条演示；实验结论只写实测结果 |

时间紧时，优先保留“约束查找 → 比较 → 当前报价 → 确认加购 → 评测/Trace”的完整链路。先砍 Mem0、Langfuse 自托管、复杂促销类型和多品类扩展，不砍身份校验、硬约束、价格二次核验与安全测试。

## 12. 三条面试演示脚本与最终交付

1. **约束推荐：**“预算 2000 元以内，要放在 80 cm 宽的位置，最好浅色办公桌。”系统澄清必要尺寸含义，展示 3 个通过硬过滤的 SKU、取舍理由和来源；用户改成“必须带抽屉”，触发 Replan。
2. **实时变化：**用户比较两款办公椅并询问促销。演示其中一款库存变 0、另一款优惠不满足资格；Agent 不复述旧报价，解释原因并给合适替代/人工入口。
3. **安全加购：**用户确认某 SKU 与数量，报价在确认前变化；首次加购返回新价格需再次确认；重复提交同一幂等键不会重复加购，伪造他人 `cart_id` 被拒绝。

最终仓库交付独立的前后端与 MCP 服务、迁移/Seed/数据来源与许可说明、版本化 Prompt/Schema、商品属性规范、120–200 条评测旅程、自动化单元/集成/安全测试、可复现指标报告、Jaeger 示例 Trace、可选 Langfuse 配置、架构图、威胁模型和演示录屏/脚本。README 明确写明“真实商品元数据 + 合成商家业务状态 + 人工审核任务集”的混合来源，并区分**计划目标**和**已实测结果**。

## 参考依据

- [ABO 官方数据集、下载与许可](https://amazon-berkeley-objects.s3.amazonaws.com/index.html)
- [Amazon Science ESCI 官方仓库与标注定义](https://github.com/amazon-science/esci-data)
- [LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence) 与 [Memory](https://docs.langchain.com/oss/python/langgraph/add-memory)
- [MCP Tools 规范](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)
- [OpenRouter Structured Outputs](https://openrouter.ai/docs/guides/features/structured-outputs)
- [pgvector Hybrid Search](https://github.com/pgvector/pgvector#hybrid-search)
