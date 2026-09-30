# ResolveAI 项目上下文（团队交接）

> 更新：2026-09-28。本文提炼**已确认的设计决策**，不是聊天记录、Codex 自动记忆，也不表示功能已经实现。实际完成情况见 [STATUS.md](./STATUS.md)。

## 项目目标与阅读顺序

ResolveAI 是面向 AI 应用工程师作品集的**单企业电商售后智能客服**：以可本地运行、可复现数据与评估、可审计的受控业务流程展示工程能力。售前方案 [CommerceGuide](../plans/commerceguide-presales-v1.md) 独立，不纳入本项目。详细设计以 [ResolveAI v6](../plans/resolveai-v6.md) 为准；[v1–v5](../plans/) 保留决策演进，冲突时不覆盖 v6。新成员先读本页和 [STATUS.md](./STATUS.md)，实施具体模块时再读 v6 对应章节。

## 已确认的设计决策

| 主题 | 决策与原因 | v6 章节 |
|---|---|---|
| 业务边界 | 已认证客户咨询、查本人订单/物流、退货申请、仓库入库质检、主管审批、**幂等模拟退款**构成主链；不接真实支付、私有平台流水或多租户 SaaS。 | §1、§4、§12 |
| 退货与退款 | 合格已签收实物商品从签收次日起有七个自然日申请窗口；客户确认后可创建申请。退款必须等实际退货入库、质检通过、服务端提案及主管批准；金额、权限、状态由领域服务校验，不由模型决定。 | §1、§4 |
| 智能体边界 | LangGraph 主图 `Coordinator` 加两个**只读**子图 `PolicyAgent`、`OrderLogisticsAgent`；仅复合问题需要时并行，结构化证据合并，失败或冲突安全降级。缺槽位/指代不清先澄清，改口或状态变化触发受限 Replan。 | §3、§5 |
| 身份与政策 | Keycloak OIDC 四角色：customer、support、warehouse、supervisor；服务端逐对象授权。政策文本、规则、索引及生效时间组成不可变 `policy_bundle`；RAG 证据不能替代资金规则。 | §4、§6 |
| 主数据 | 唯一主链是固定 seed、因果一致、可重放的**合成业务世界**；公开真实数据只在许可允许时校准分布或做隔离导入，不拼接不存在的订单—售后关系。25 个演示场景、约 10 万单日常档、按需 100 万单规模档均为建设目标。 | §8 |
| 应用记忆 | `PostgresSaver` 保存父图线程/审批状态，PostgreSQL 保存经确认的结构化画像；PostgresStore 与 Mem0 OSS 用隔离故事集 A/B，未过安全门槛可关闭。订单、余额、物流、审批始终实时查询业务库。**这不是 Codex 项目记忆。** | §7 |
| Prompt 与模型 | 本地 Git 的 `prompts/catalog/` 是 Prompt 唯一可编辑来源，release manifest 锁定 hash；Langfuse Cloud 只接收单向镜像。OpenRouter 模型按任务和精确版本配置；Cloud UI 修改不改变运行时 Prompt。 | §6 |
| 评估与观测 | 本地 JSONL 金标、隔离 Runner、数据库终态/安全断言及 JSON/HTML 报告决定发布；Langfuse Cloud 展示脱敏 Agent Trace、Experiment、Score、Dashboard。OTel → Collector → Jaeger 保留本地系统 Trace，业务审计在 PostgreSQL；Cloud 断连不阻断本地评测。 | §9–§10 |

## 不能误写成已验证的内容

上述技术栈、数据量、评测集大小、门槛、工期与 Langfuse 配额均属于 v6 计划或当时的服务条款，**不是当前实测结果**。实际实现与测试进度只看 [STATUS.md](./STATUS.md)、代码和评测报告；公开数据的许可、外部服务价格/限额及候选模型需在使用前重新核验。单 Agent 与多 Agent 的收益必须做相同 fixture 的配对实验，不能预设“多 Agent 更好”。

## 维护与 Ubuntu 交接

- 用户确认设计变更时，先更新本页的决策摘要及日期，并创建新版计划或在当前计划中留下明确的版本化变更；不要悄悄改写历史计划。
- 只有文件、命令结果或报告可验证时，才在 [STATUS.md](./STATUS.md) 标为完成；把待办、假设、已完成分开。
- 项目内只保留团队可共享的决策和必要证据，不复制完整 Codex 会话、认证文件、密钥、真实个人数据或未脱敏 Trace。迁到 Ubuntu 后以相对路径阅读这些文档；个人会话记录不等于团队知识库。
