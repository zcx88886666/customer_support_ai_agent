"""Render the repository's editable, dependency-free architecture SVGs.

Run from any directory with ``python docs/architecture/render_diagrams.py``.
The diagrams describe implemented boundaries; update this file with the code.
"""

from __future__ import annotations

from html import escape
from pathlib import Path


HERE = Path(__file__).resolve().parent


def text(x: int, y: int, value: str, css: str = "body", anchor: str = "start") -> str:
    return f'<text x="{x}" y="{y}" class="{css}" text-anchor="{anchor}">{escape(value)}</text>'


def card(
    x: int, y: int, width: int, height: int, title: str, lines: tuple[str, ...],
    *, fill: str = "#ffffff", border: str = "#cbd5e1", accent: str = "#2563eb",
    title_size: str = "card-title", body_size: str = "body",
) -> str:
    parts = [
        f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="18" fill="{fill}" stroke="{border}" stroke-width="2"/>',
        f'<rect x="{x}" y="{y}" width="7" height="{height}" rx="3" fill="{accent}"/>',
        text(x + 24, y + 38, title, title_size),
    ]
    parts.extend(text(x + 24, y + 71 + index * 25, line, body_size) for index, line in enumerate(lines))
    return "\n".join(parts)


def arrow(points: str, *, color: str = "#64748b", dashed: bool = False, width: int = 3) -> str:
    dash = ' stroke-dasharray="9 7"' if dashed else ""
    marker = {"#dc5a4c": "arrow-red", "#059669": "arrow-green", "#0f766e": "arrow-teal"}.get(color, "arrow")
    return (f'<path d="{points}" fill="none" stroke="{color}" stroke-width="{width}"'
            f' stroke-linecap="round" stroke-linejoin="round"{dash} marker-end="url(#{marker})"/>')


def svg(width: int, height: int, title: str, description: str, content: list[str]) -> str:
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title description">
<title id="title">{escape(title)}</title>
<desc id="description">{escape(description)}</desc>
<defs>
  <marker id="arrow" markerWidth="12" markerHeight="12" refX="10" refY="6" orient="auto" markerUnits="userSpaceOnUse"><path d="M2 2 L10 6 L2 10" fill="none" stroke="#64748b" stroke-width="2"/></marker>
  <marker id="arrow-red" markerWidth="12" markerHeight="12" refX="10" refY="6" orient="auto" markerUnits="userSpaceOnUse"><path d="M2 2 L10 6 L2 10" fill="none" stroke="#dc5a4c" stroke-width="2"/></marker>
  <marker id="arrow-green" markerWidth="12" markerHeight="12" refX="10" refY="6" orient="auto" markerUnits="userSpaceOnUse"><path d="M2 2 L10 6 L2 10" fill="none" stroke="#059669" stroke-width="2"/></marker>
  <marker id="arrow-teal" markerWidth="12" markerHeight="12" refX="10" refY="6" orient="auto" markerUnits="userSpaceOnUse"><path d="M2 2 L10 6 L2 10" fill="none" stroke="#0f766e" stroke-width="2"/></marker>
  <style>
    text {{ font-family: "Ubuntu Sans", "DejaVu Sans", Arial, sans-serif; fill: #15243b; }}
    .heading {{ font-size: 38px; font-weight: 750; letter-spacing: -1px; }}
    .subtitle {{ font-size: 18px; fill: #52637b; }}
    .section {{ font-size: 17px; font-weight: 750; fill: #52637b; letter-spacing: 1.8px; }}
    .card-title {{ font-size: 22px; font-weight: 750; }}
    .small-title {{ font-size: 19px; font-weight: 750; }}
    .body {{ font-size: 17px; fill: #334155; }}
    .small {{ font-size: 15px; fill: #475569; }}
    .label {{ font-size: 15px; font-weight: 700; fill: #52637b; }}
    .white {{ fill: #ffffff; }}
  </style>
</defs>
<rect width="{width}" height="{height}" fill="#f7f9fc"/>
{chr(10).join(content)}
</svg>
'''


def architecture() -> str:
    parts = [
        text(50, 63, "ResolveAI · System architecture", "heading"),
        text(50, 95, "Implemented local stack · request path, read boundaries, storage, and optional services", "subtitle"),
        text(50, 135, "ENTRY AND IDENTITY", "section"),
        text(50, 365, "APPLICATION AND AGENT", "section"),
        text(50, 645, "READ SERVICES AND ASYNCHRONOUS WORK", "section"),
        text(50, 876, "SYSTEM OF RECORD AND OBSERVABILITY", "section"),
        # Draw connectors behind cards.
        arrow("M295 228 H335"),
        arrow("M580 228 H620"),
        arrow("M1060 228 H1020", color="#7c3aed"),
        arrow("M725 310 V390"),
        arrow("M940 310 V390"),
        arrow("M405 605 V665"),
        arrow("M1245 605 V665"),
        arrow("M405 835 V905"),
        arrow("M1245 835 V858 H570 V905"),
        arrow("M760 405 V345 H1488 V310", color="#0f766e", dashed=True),
        text(1360, 341, "optional LLM", "label"),
        card(50, 160, 245, 150, "People", ("Customer · Support", "Warehouse · Supervisor"), accent="#2563eb"),
        card(335, 160, 245, 150, "Web UI", ("Next.js browser", "chat · tickets · approvals"), accent="#2563eb"),
        card(620, 160, 400, 150, "FastAPI", ("OIDC, role and object authorization", "chat/stream, returns, tickets, approvals"), accent="#1d4ed8"),
        card(1060, 160, 245, 150, "Keycloak", ("OIDC sign-in", "JWT roles and identity"), fill="#faf5ff", border="#d8b4fe", accent="#7c3aed"),
        card(1345, 160, 285, 150, "OpenRouter", ("Optional model calls", "bounded by request budgets"), fill="#effcf9", border="#99f6e4", accent="#0f766e"),
        card(50, 390, 710, 215, "LangGraph agent orchestration", ("Coordinator verifies order context and merges evidence.",), fill="#eff6ff", border="#bfdbfe", accent="#2563eb"),
        card(74, 481, 202, 101, "Coordinator", ("route · clarify · merge",), fill="#ffffff", border="#bfdbfe", accent="#2563eb", title_size="small-title", body_size="small"),
        card(299, 481, 202, 101, "PolicyAgent", ("read-only policy",), fill="#ffffff", border="#bfdbfe", accent="#2563eb", title_size="small-title", body_size="small"),
        card(524, 481, 212, 101, "OrderLogistics", ("read-only order",), fill="#ffffff", border="#bfdbfe", accent="#2563eb", title_size="small-title", body_size="small"),
        card(860, 390, 770, 215, "Business command boundary", (
            "Domain rules create returns, tickets, receipts, proposals, and decisions.",
            "SQL ownership, eligibility, approval, balance, and idempotency checks",
            "are authoritative; model output cannot approve or issue a refund.",
        ), fill="#fffaf0", border="#fcd34d", accent="#d97706"),
        card(50, 665, 710, 170, "Read-only evidence", ("Authenticated reads return source IDs, versions, and evidence gaps.",), fill="#ffffff", border="#cbd5e1", accent="#2563eb"),
        card(74, 749, 316, 82, "Commerce MCP", ("owned order + shipments",), fill="#f8fafc", border="#cbd5e1", accent="#2563eb", title_size="small-title", body_size="small"),
        card(415, 749, 321, 82, "Policy retrieval", ("published bundle + index",), fill="#f8fafc", border="#cbd5e1", accent="#2563eb", title_size="small-title", body_size="small"),
        card(860, 665, 770, 170, "Asynchronous work", ("Redis queues tasks; PostgreSQL remains the business authority.",), fill="#ffffff", border="#cbd5e1", accent="#d97706"),
        card(884, 749, 278, 82, "Redis broker", ("namespaced JSON jobs",), fill="#f8fafc", border="#cbd5e1", accent="#d97706", title_size="small-title", body_size="small"),
        card(1187, 749, 419, 82, "Celery workers + Beat", ("refunds · deadlines · index · bulk eval",), fill="#f8fafc", border="#cbd5e1", accent="#d97706", title_size="small-title", body_size="small"),
        card(50, 905, 520, 140, "PostgreSQL + pgvector", ("Orders, policy, tickets, audits, refund ledger", "LangGraph checkpoints and guarded preferences"), fill="#eafaf5", border="#86efac", accent="#059669"),
        card(605, 905, 235, 140, "Git Prompt catalog", ("hash-locked release", "loaded locally"), fill="#ffffff", border="#cbd5e1", accent="#475569"),
        card(875, 905, 350, 140, "OTel → Collector → Jaeger", ("local request and worker traces", "SQL audit stays separate"), fill="#ffffff", border="#cbd5e1", accent="#475569"),
        card(1260, 905, 370, 140, "Langfuse Cloud · optional", ("selected masked agent traces", "Prompt mirror + evaluation views"), fill="#ffffff", border="#cbd5e1", accent="#475569"),
        text(50, 1082, "Solid arrows: main request or data path     Dashed arrow: optional model dependency     All refunds are simulated.", "subtitle"),
    ]
    return svg(1680, 1100, "ResolveAI implemented system architecture", "Users reach the Next.js web UI and FastAPI. Keycloak supplies OIDC identity. FastAPI calls a LangGraph coordinator and protected domain services. The agent uses read-only Commerce MCP and policy retrieval. PostgreSQL stores business truth; Redis and Celery run controlled background jobs. OpenRouter and Langfuse are optional external services.", parts)


def workflow() -> str:
    parts = [
        text(50, 63, "Return to simulated refund · workflow", "heading"),
        text(50, 96, "Each write crosses a server-side gate; human decisions and SQL facts control the money path.", "subtitle"),
        text(50, 137, "ACTOR", "section"),
        text(360, 137, "MAIN PATH", "section"),
        text(1190, 137, "EXCEPTION / HUMAN PATH", "section"),
    ]
    stages = [
        (160, "1  Customer asks or submits", ("Chat may read facts; a return requires explicit confirmation.",), "#eff6ff", "#2563eb"),
        (280, "2  Identity and owned order", ("API checks role, customer ownership, item, quantity, and reason.",), "#eff6ff", "#2563eb"),
        (400, "3  Deterministic eligibility", ("Delivery, policy window, exclusions, and paid order status.",), "#fffaf0", "#d97706"),
        (520, "4  Return request created", ("Confirmed + eligible; idempotency key protects retries.",), "#eafaf5", "#059669"),
        (640, "5  Warehouse records receipt", ("Actual quantity and receipt time are checked.",), "#fffaf0", "#d97706"),
        (760, "6  Warehouse inspection", ("A passing inspection is required for a proposal.",), "#fffaf0", "#d97706"),
        (880, "7  Server calculates proposal", ("Amount and source versions come from PostgreSQL.",), "#fffaf0", "#d97706"),
        (1000, "8  Supervisor approves or rejects", ("Protected role action; approval is stored in SQL.",), "#faf5ff", "#7c3aed"),
        (1120, "9  Worker issues simulated refund", ("Recheck approval, versions, and balance; one ledger + audit.",), "#eafaf5", "#059669"),
    ]
    for index, (y, _, _, _, _) in enumerate(stages[:-1]):
        parts.append(arrow(f"M740 {y + 90} V{stages[index + 1][0]}", color="#64748b"))
    parts.extend([
        arrow("M1120 445 H1190", color="#dc5a4c"),
        arrow("M1120 685 H1190", color="#dc5a4c"),
        arrow("M1120 805 H1190", color="#dc5a4c"),
        arrow("M1120 1045 H1190", color="#dc5a4c"),
        arrow("M740 1210 V1248", color="#059669"),
    ])
    for y, title, lines, fill, accent in stages:
        parts.append(card(360, y, 760, 90, title, lines, fill=fill, border=accent, accent=accent))
    parts.extend([
        card(1190, 400, 430, 120, "Policy exception or no delivery", ("One ticket; no return or refund.", "Bad quantity / foreign order → error."), fill="#fff1f0", border="#f6b6ae", accent="#dc5a4c", title_size="small-title"),
        card(1190, 640, 430, 100, "Quantity mismatch", ("Exception ticket; no normal receipt/refund.",), fill="#fff1f0", border="#f6b6ae", accent="#dc5a4c", title_size="small-title"),
        card(1190, 760, 430, 100, "Inspection failed", ("Linked ticket; no approved refund path.",), fill="#fff1f0", border="#f6b6ae", accent="#dc5a4c", title_size="small-title"),
        card(1190, 1000, 430, 100, "Rejected or stale proposal", ("No refund; a new proposal needs approval.",), fill="#fff1f0", border="#f6b6ae", accent="#dc5a4c", title_size="small-title"),
        card(360, 1248, 760, 88, "Customer sees the final status", ("The ledger is simulated; no payment provider is called.",), fill="#eafaf5", border="#86efac", accent="#059669", title_size="small-title", body_size="small"),
        card(50, 165, 250, 115, "Customer", ("chat or return form",), fill="#ffffff", border="#cbd5e1", accent="#2563eb", title_size="small-title"),
        card(50, 650, 250, 115, "Warehouse", ("receipt + inspection",), fill="#ffffff", border="#cbd5e1", accent="#d97706", title_size="small-title"),
        card(50, 1005, 250, 115, "Supervisor", ("human approval",), fill="#ffffff", border="#cbd5e1", accent="#7c3aed", title_size="small-title"),
        card(50, 1135, 250, 115, "Controlled worker", ("idempotent issuance",), fill="#ffffff", border="#cbd5e1", accent="#059669", title_size="small-title"),
        text(50, 1358, "Blue: request/evidence    Amber: rule or transaction gate    Purple: human approval    Green: recorded state    Coral: ticket or stop", "subtitle"),
    ])
    return svg(1680, 1380, "ResolveAI return to simulated refund workflow", "The customer asks or submits a confirmed return. The API verifies ownership and deterministic eligibility. Ineligible requests create support tickets. Warehouse receipt and inspection must pass. The server calculates a version-bound refund proposal. A supervisor approves it. Only a controlled worker rechecks database facts and writes one simulated refund ledger and audit event. Quantity, inspection, and proposal exceptions stop or create tickets.", parts)


def main() -> None:
    (HERE / "system-overview.svg").write_text(architecture(), encoding="utf-8")
    (HERE / "return-workflow.svg").write_text(workflow(), encoding="utf-8")


if __name__ == "__main__":
    main()
