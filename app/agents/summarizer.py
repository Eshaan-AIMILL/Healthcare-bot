import json
from app.core.state import AgentState
from app.utils.prompts import SUMMARIZER_SYSTEM, SUMMARIZER_USER
from app.utils.logger import logger
from app.config import settings
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage


def _build_llm() -> ChatOllama:
    return ChatOllama(
        model=settings.ollama_model,
        base_url=settings.ollama_base_url,
        temperature=0.1,
    )


def _format_pct(value: object) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.2f}%"


def _format_count(value: object) -> str:
    if value is None:
        return "0"
    return str(int(value))


def _format_money(value: object) -> str:
    if value is None:
        return "0.00"
    return f"{float(value):,.2f}"


def _format_days(value: object) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.1f}"


def _billing_rejection_summary(sql_rows: list[dict]) -> str | None:
    if not sql_rows or "overall_rejection_rate_pct" not in sql_rows[0]:
        return None

    first = sql_rows[0]
    total_claims = _format_count(first.get("overall_total_claims"))
    rejected_claims = _format_count(first.get("overall_rejected_claims"))
    rejection_rate = _format_pct(first.get("overall_rejection_rate_pct"))

    rows = [row for row in sql_rows if row.get("icd10_code")]
    table_rows = [
        "| ICD-10 code | Rejected claims | Total claims | Code rejection rate | Share of rejections |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for row in rows[:5]:
        table_rows.append(
            "| {icd10} | {rejected} | {total} | {rate} | {share} |".format(
                icd10=row.get("icd10_code", "n/a"),
                rejected=_format_count(row.get("rejected_claims")),
                total=_format_count(row.get("total_claims")),
                rate=_format_pct(row.get("rejection_rate_pct")),
                share=_format_pct(row.get("share_of_rejections_pct")),
            )
        )

    if not rows:
        driver_text = "No ICD-10 code has rejected claims in the current month."
    else:
        driver_text = "\n".join(table_rows)

    return (
        f"Overall claim rejection rate this month is {rejection_rate} "
        f"({rejected_claims} rejected out of {total_claims} claims).\n\n"
        f"Top ICD-10 rejection drivers by rejected claim count:\n\n{driver_text}\n\n"
        "Recommended next action: review coding and payer-denial notes for the top ICD-10 "
        "drivers before the next claims submission cycle."
    )


def _billing_payer_rejection_summary(sql_rows: list[dict]) -> str | None:
    if not sql_rows or not {"payer_name", "rejection_rate_pct", "top_error_type"}.issubset(sql_rows[0]):
        return None

    top_rows = [row for row in sql_rows if row.get("payer_name")]
    if not top_rows:
        return "No payer-level claim records are available for rejection rate analysis."

    worst = top_rows[0]
    table_rows = [
        "| Payer | Rejection rate | Rejected claims | Total claims | Most common error type |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for row in top_rows[:8]:
        table_rows.append(
            "| {payer} | {rate} | {rejected} | {total} | {error_type} |".format(
                payer=row.get("payer_name", "n/a"),
                rate=_format_pct(row.get("rejection_rate_pct")),
                rejected=_format_count(row.get("rejected_claims")),
                total=_format_count(row.get("total_claims")),
                error_type=row.get("top_error_type") or "n/a",
            )
        )

    return (
        f"The highest rejection rate is currently with {worst.get('payer_name', 'n/a')} "
        f"at {_format_pct(worst.get('rejection_rate_pct'))}. "
        f"The most frequent rejected-claim coding issue there is "
        f"{worst.get('top_error_type') or 'n/a'}.\n\n"
        f"{chr(10).join(table_rows)}\n\n"
        "Recommended next action: prioritize payer-specific coding review playbooks for the top "
        "rejection-rate payers."
    )


def _critical_risk_dashboard_summary(sql_rows: list[dict]) -> str | None:
    if not sql_rows or not {"risk_domain", "domain_critical_count", "domain_rank"}.issubset(sql_rows[0]):
        return None

    rows = [row for row in sql_rows if row.get("risk_domain")]
    if not rows:
        return "No critical items were found across the configured risk domains."

    domain_counts: dict[str, int] = {}
    for row in rows:
        domain = str(row.get("risk_domain", "")).strip()
        if not domain:
            continue
        domain_counts[domain] = int(row.get("domain_critical_count") or 0)

    count_lines = []
    for domain in ("billing", "compliance", "pharmacy", "patient", "dispatch"):
        count_lines.append(f"- {domain.title()}: {domain_counts.get(domain, 0)} critical items")

    table_rows = [
        "| Domain | Record ID | Risk type | Status | Owner |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in rows[:15]:
        table_rows.append(
            "| {domain} | {record_id} | {risk_type} | {status} | {owner} |".format(
                domain=row.get("risk_domain", "n/a"),
                record_id=row.get("record_id", "n/a"),
                risk_type=row.get("risk_type", "n/a"),
                status=row.get("status", "n/a"),
                owner=row.get("owner", "n/a"),
            )
        )

    highest_domain = max(domain_counts, key=domain_counts.get) if domain_counts else "n/a"

    return (
        f"Critical-risk dashboard generated across all five domains. The largest critical backlog is "
        f"in {highest_domain.title()} ({domain_counts.get(highest_domain, 0)} items).\n\n"
        f"{chr(10).join(count_lines)}\n\n"
        f"Sample critical records by domain:\n\n{chr(10).join(table_rows)}\n\n"
        "Recommended next action: assign immediate owners to the highest-backlog domain and clear "
        "open critical items in order of newest event date."
    )


def _compliance_findings_summary(sql_rows: list[dict]) -> str | None:
    if not sql_rows or not {"finding_id", "severity", "regulation_body"}.issubset(sql_rows[0]):
        return None

    rows = [row for row in sql_rows if row.get("finding_id")]
    if not rows:
        return "No matching critical HIPAA findings were found in the last 30 days."

    open_rows = [
        row for row in rows
        if str(row.get("resolution_status", "")).lower() in {"open", "inreview", "in review"}
    ]
    table_rows = [
        "| Finding ID | Violation type | Audit date | Status | Department | Process |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows[:10]:
        table_rows.append(
            "| {finding} | {violation} | {date} | {status} | {department} | {process} |".format(
                finding=row.get("finding_id", "n/a"),
                violation=row.get("violation_type", "n/a"),
                date=row.get("audit_date", "n/a"),
                status=row.get("resolution_status", "n/a"),
                department=row.get("department", "n/a"),
                process=row.get("process_type", "n/a"),
            )
        )

    return (
        f"Found {len(rows)} Critical HIPAA findings in the last 30 days; "
        f"{len(open_rows)} are still open or in review.\n\n"
        f"{chr(10).join(table_rows)}\n\n"
        "Recommended next action: assign owners for the open or in-review findings and "
        "verify remediation evidence before the next audit cycle."
    )


def _compliance_consent_missing_summary(sql_rows: list[dict]) -> str | None:
    if not sql_rows or not {"process_id", "patient_consent_obtained", "severity"}.issubset(sql_rows[0]):
        return None

    rows = [row for row in sql_rows if row.get("process_id")]
    if not rows:
        return "No clinical processes with missing patient consent were found."

    critical_count = sum(1 for row in rows if str(row.get("severity", "")).lower() == "critical")
    major_count = sum(1 for row in rows if str(row.get("severity", "")).lower() == "major")

    table_rows = [
        "| Process ID | Department | Procedure date | Severity | Regulation body | Status |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows[:10]:
        table_rows.append(
            "| {process_id} | {department} | {procedure_date} | {severity} | {regulation} | {status} |".format(
                process_id=row.get("process_id", "n/a"),
                department=row.get("department", "n/a"),
                procedure_date=row.get("procedure_date", "n/a"),
                severity=row.get("severity", "None"),
                regulation=row.get("regulation_body", "None"),
                status=row.get("resolution_status", "Open"),
            )
        )

    return (
        f"Found {len(rows)} processes where patient consent was not obtained. "
        f"{critical_count} are tagged Critical and {major_count} are Major.\n\n"
        f"{chr(10).join(table_rows)}\n\n"
        "Recommended next action: escalate Critical and Major consent gaps for immediate remediation "
        "and documentation closure."
    )


def _compliance_open_jci_summary(sql_rows: list[dict]) -> str | None:
    if not sql_rows or not {"total_open_jci_violations", "department_open_count"}.issubset(sql_rows[0]):
        return None

    total_open = int(sql_rows[0].get("total_open_jci_violations") or 0)
    rows = [row for row in sql_rows if row.get("department")]

    if not rows:
        return f"There are {total_open} open JCI violations, but no department split is available."

    table_rows = [
        "| Department | Open JCI violations |",
        "| --- | ---: |",
    ]
    for row in rows[:10]:
        table_rows.append(
            "| {department} | {count} |".format(
                department=row.get("department", "n/a"),
                count=_format_count(row.get("department_open_count")),
            )
        )

    top_department = rows[0].get("department", "n/a")
    top_count = _format_count(rows[0].get("department_open_count"))

    return (
        f"There are currently {total_open} open JCI violations. "
        f"{top_department} has the highest open count ({top_count}).\n\n"
        f"{chr(10).join(table_rows)}\n\n"
        "Recommended next action: assign accountable owners in the highest-burden departments and "
        "track closure weekly."
    )


def _pharmacy_reorder_summary(sql_rows: list[dict]) -> str | None:
    if not sql_rows or "below_reorder_count" not in sql_rows[0]:
        return None

    first = sql_rows[0]
    below_count = _format_count(first.get("below_reorder_count"))
    critical_count = _format_count(first.get("critical_reorder_count"))
    total_gap = _format_count(first.get("total_reorder_gap_units"))
    rows = [row for row in sql_rows if row.get("drug_id")]

    if not rows:
        return (
            "No drug batches are currently below their reorder threshold.\n\n"
            "Recommended next action: continue routine inventory monitoring."
        )

    table_rows = [
        "| Drug | Batch | Stock | Threshold | Gap | Days to stockout | Lead time | Urgency |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in rows[:10]:
        table_rows.append(
            "| {drug} | {batch} | {stock} | {threshold} | {gap} | {days} | {lead} | {urgency} |".format(
                drug=row.get("drug_name", "n/a"),
                batch=row.get("batch_number", "n/a"),
                stock=_format_count(row.get("current_stock")),
                threshold=_format_count(row.get("reorder_threshold")),
                gap=_format_count(row.get("reorder_gap")),
                days=_format_days(row.get("days_until_stockout")),
                lead=_format_count(row.get("supplier_lead_time_days")),
                urgency=row.get("reorder_urgency", "n/a"),
            )
        )

    return (
        f"{below_count} drug batches are below reorder threshold; {critical_count} are critical "
        f"because projected stockout is within supplier lead time. Total reorder gap is "
        f"{total_gap} units.\n\n"
        f"Highest-priority reorder risks:\n\n{chr(10).join(table_rows)}\n\n"
        "Recommended next action: place or expedite purchase orders for the Critical items first."
    )


def _pharmacy_expiry_summary(sql_rows: list[dict]) -> str | None:
    if not sql_rows or "total_cost_exposure" not in sql_rows[0]:
        return None

    first = sql_rows[0]
    batch_count = _format_count(first.get("expiring_batch_count"))
    total_exposure = _format_money(first.get("total_cost_exposure"))
    rows = [row for row in sql_rows if row.get("drug_id")]

    if not rows:
        return (
            "No drug batches are expiring within the next 60 days.\n\n"
            "Recommended next action: continue routine inventory monitoring."
        )

    table_rows = [
        "| Drug | Batch | Expiry date | Days left | Stock | Cost exposure |",
        "| --- | --- | --- | ---: | ---: | ---: |",
    ]
    for row in rows[:10]:
        table_rows.append(
            "| {drug} | {batch} | {expiry} | {days} | {stock} | {exposure} |".format(
                drug=row.get("drug_name", "n/a"),
                batch=row.get("batch_number", "n/a"),
                expiry=row.get("expiry_date", "n/a"),
                days=_format_count(row.get("days_until_expiry")),
                stock=_format_count(row.get("current_stock")),
                exposure=_format_money(row.get("cost_exposure")),
            )
        )

    return (
        f"{batch_count} drug batches are expiring within the next 60 days, "
        f"with total inventory cost exposure of {total_exposure}.\n\n"
        f"Highest-priority expiring batches by expiry date and exposure:\n\n"
        f"{chr(10).join(table_rows)}\n\n"
        "Recommended next action: prioritize use, transfer, or supplier return for the "
        "earliest-expiring high-exposure batches."
    )


def _patient_weekly_sla_summary(sql_rows: list[dict]) -> str | None:
    if not sql_rows or "breached_complaint_count" not in sql_rows[0]:
        return None

    first = sql_rows[0]
    breached_count = _format_count(first.get("breached_complaint_count"))
    avg_over_sla = _format_days(first.get("avg_delay_minutes_over_sla"))
    avg_wait = _format_days(first.get("avg_wait_time_minutes"))
    critical_breaches = _format_count(first.get("critical_breaches"))

    return (
        f"In the last 7 days, {breached_count} appointment complaints breached SLA. "
        f"Average delay beyond SLA was {avg_over_sla} minutes, with average breached wait time "
        f"of {avg_wait} minutes. {critical_breaches} breaches were classified as Critical.\n\n"
        "Recommended next action: prioritize immediate callback and recovery actions for Critical "
        "SLA-breach complaints."
    )


def _patient_department_sla_summary(sql_rows: list[dict]) -> str | None:
    if not sql_rows or not {"department", "sla_compliance_rate_pct"}.issubset(sql_rows[0]):
        return None

    rows = [row for row in sql_rows if row.get("department")]
    if not rows:
        return "No specialist appointment SLA compliance records were found."

    worst = rows[0]
    table_rows = [
        "| Department | SLA compliance | Breached | Total specialist appointments | Avg delay over SLA (min) |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for row in rows[:10]:
        table_rows.append(
            "| {department} | {compliance} | {breached} | {total} | {delay} |".format(
                department=row.get("department", "n/a"),
                compliance=_format_pct(row.get("sla_compliance_rate_pct")),
                breached=_format_count(row.get("breached_count")),
                total=_format_count(row.get("total_appointments")),
                delay=_format_days(row.get("avg_delay_over_sla_minutes")),
            )
        )

    return (
        f"The weakest specialist SLA compliance is in {worst.get('department', 'n/a')} "
        f"at {_format_pct(worst.get('sla_compliance_rate_pct'))}.\n\n"
        f"{chr(10).join(table_rows)}\n\n"
        "Recommended next action: review appointment scheduling and staffing in the lowest-"
        "compliance departments first."
    )


def _dispatch_on_time_summary(sql_rows: list[dict]) -> str | None:
    if not sql_rows or "overall_on_time_rate_pct" not in sql_rows[0]:
        return None

    first = sql_rows[0]
    total_dispatches = _format_count(first.get("overall_emergency_dispatches"))
    on_time = _format_count(first.get("on_time_dispatches"))
    late = _format_count(first.get("late_dispatches"))
    on_time_rate = _format_pct(first.get("overall_on_time_rate_pct"))

    rows = [row for row in sql_rows if row.get("route_id")]
    table_rows = [
        "| Route | Origin | Destination | Dispatches | On time | Route on-time rate |",
        "| --- | --- | --- | ---: | ---: | ---: |",
    ]
    for row in rows[:5]:
        table_rows.append(
            "| {route} | {origin} | {destination} | {dispatches} | {on_time} | {rate} |".format(
                route=row.get("route_id", "n/a"),
                origin=row.get("origin", "n/a"),
                destination=row.get("destination", "n/a"),
                dispatches=_format_count(row.get("route_dispatches")),
                on_time=_format_count(row.get("route_on_time_dispatches")),
                rate=_format_pct(row.get("route_on_time_rate_pct")),
            )
        )

    route_text = "\n".join(table_rows) if rows else "No route-level dispatch rows were found."

    return (
        f"Emergency medical dispatch on-time delivery rate this month is {on_time_rate} "
        f"({on_time} on time out of {total_dispatches} dispatches; {late} late).\n\n"
        f"Lowest-performing routes by on-time rate:\n\n{route_text}\n\n"
        "Recommended next action: investigate staffing, traffic, and handoff delays on the "
        "lowest-performing emergency routes."
    )


def _dispatch_delayed_deliveries_summary(sql_rows: list[dict]) -> str | None:
    if not sql_rows or not {"delivery_id", "route_id", "delay_minutes"}.issubset(sql_rows[0]):
        return None

    rows = [row for row in sql_rows if row.get("delivery_id")]
    if not rows:
        return "No delayed deliveries were found in the selected week."

    table_rows = [
        "| Delivery ID | Route | Category | Delay (min) | Origin | Destination |",
        "| --- | --- | --- | ---: | --- | --- |",
    ]
    for row in rows[:10]:
        table_rows.append(
            "| {delivery} | {route} | {category} | {delay} | {origin} | {destination} |".format(
                delivery=row.get("delivery_id", "n/a"),
                route=row.get("route_id", "n/a"),
                category=row.get("delivery_category", "n/a"),
                delay=_format_count(row.get("delay_minutes")),
                origin=row.get("origin", "n/a"),
                destination=row.get("destination", "n/a"),
            )
        )

    return (
        f"Found {len(rows)} delayed or SLA-breached deliveries in the last 7 days.\n\n"
        f"{chr(10).join(table_rows)}\n\n"
        "Recommended next action: reroute high-delay corridors and prioritize available emergency "
        "fleet capacity for those routes."
    )


def _deterministic_summary(state: AgentState) -> str | None:
    return (
        _billing_rejection_summary(state.sql_rows)
        or _billing_payer_rejection_summary(state.sql_rows)
        or _critical_risk_dashboard_summary(state.sql_rows)
        or _compliance_findings_summary(state.sql_rows)
        or _compliance_consent_missing_summary(state.sql_rows)
        or _compliance_open_jci_summary(state.sql_rows)
        or _pharmacy_reorder_summary(state.sql_rows)
        or _pharmacy_expiry_summary(state.sql_rows)
        or _patient_weekly_sla_summary(state.sql_rows)
        or _patient_department_sla_summary(state.sql_rows)
        or _dispatch_on_time_summary(state.sql_rows)
        or _dispatch_delayed_deliveries_summary(state.sql_rows)
    )


async def summarizer_node(state: AgentState) -> AgentState:
    if state.error and not state.agent_result:
        state.final_response = (
            "I encountered an issue processing your request. "
            f"Error: {state.error}. Please try rephrasing your question."
        )
        return state

    direct_response = _deterministic_summary(state)
    if direct_response:
        state.final_response = direct_response
        logger.info(f"Summarizer produced {len(state.final_response)} char deterministic response.")
        return state

    llm = _build_llm()
    messages = [
        SystemMessage(content=SUMMARIZER_SYSTEM),
        HumanMessage(
            content=SUMMARIZER_USER.format(
                intent=state.intent,
                agent_result=json.dumps(state.agent_result, default=str, indent=2),
                sql_rows=json.dumps(state.sql_rows[:15], default=str, indent=2),
                query=state.query,
            )
        ),
    ]

    try:
        response = await llm.ainvoke(messages)
        state.final_response = response.content.strip()
        logger.info(f"Summarizer produced {len(state.final_response)} char response.")
    except Exception as exc:
        logger.error(f"Summarizer failed: {exc}")
        state.final_response = (
            "The analysis completed but the response formatter encountered an error. "
            f"Raw result: {json.dumps(state.agent_result, default=str)}"
        )

    return state
