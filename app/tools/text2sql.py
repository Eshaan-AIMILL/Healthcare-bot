import json
import re
from typing import Any

from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.utils.prompts import TEXT2SQL_SYSTEM, TEXT2SQL_USER
from app.utils.logger import logger

_BLOCKED_SQL_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|TRUNCATE|PRAGMA|ATTACH|DETACH|VACUUM)\b",
    re.IGNORECASE,
)
_NAMED_BIND_RE = re.compile(r"(?<!:):([A-Za-z_][A-Za-z0-9_]*)")


# Minimal schema description injected into the system prompt.
_DB_SCHEMA = """
Tables and key columns:

patients(patient_id, full_name, date_of_birth, gender, insurance_provider, patient_tier)
encounters(encounter_id, patient_id, encounter_date, department, attending_physician,
           primary_diagnosis_icd10, secondary_diagnosis_icd10, diagnosis_description)
billing_claims(claim_id, encounter_id, patient_id, cpt_code, procedure_description,
               claim_amount, claim_date, payer_name, claim_status, rejection_reason,
               has_coding_error, error_type)
clinical_processes(process_id, process_type, department, staff_id, staff_name,
                   procedure_date, patient_consent_obtained, documentation_complete,
                   protocol_followed, incident_reported)
audit_findings(finding_id, process_id, auditor_id, audit_date, violation_type,
               severity, regulation_body, policy_reference, finding_description,
               resolution_status)
drugs(drug_id, drug_name, generic_name, category, unit, unit_cost, supplier)
drug_inventory(inventory_id, drug_id, batch_number, expiry_date, current_stock,
               reorder_threshold, max_stock_level, average_daily_consumption,
               supplier_lead_time_days)
appointments(appointment_id, patient_id, doctor_name, department, appointment_category,
             scheduled_datetime, actual_start_datetime, wait_time_minutes, appointment_status)
patient_complaints(complaint_id, patient_id, appointment_id, complaint_date, complaint_text,
                   sla_threshold_minutes, sla_breached, breach_severity,
                   compensation_eligible, compensation_type, resolution_status)
vehicles(vehicle_id, vehicle_type, registration, capacity_kg, available, base_location)
routes(route_id, origin, destination, distance_km, estimated_duration_minutes,
       traffic_factor, route_type)
delivery_records(delivery_id, vehicle_id, route_id, delivery_category,
                 scheduled_datetime, actual_delivery_datetime, sla_window_minutes,
                 estimated_cost, delivery_status, sla_breached, delay_reason)
"""


def _build_llm() -> ChatOllama:
    return ChatOllama(
        model=settings.ollama_model,
        base_url=settings.ollama_base_url,
        temperature=0.0,
        format="json",
    )


def _load_json_object(content: str) -> dict[str, Any]:

    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, flags=re.DOTALL)
        if not match:
            raise
        payload = json.loads(match.group(0))

    if not isinstance(payload, dict):
        raise ValueError("Text2SQL response must be a JSON object")
    return payload


def _clean_sql(sql_query: str) -> str:
    sql_query = sql_query.strip()
    sql_query = re.sub(r";+\s*$", "", sql_query)
    # SQLite has no DUAL table; some local models occasionally emit Oracle-style SELECTs.
    sql_query = re.sub(r"\s+FROM\s+dual\b", "", sql_query, flags=re.IGNORECASE)
    return sql_query.strip()


def _is_safe_select(sql_query: str) -> bool:
    sql_upper = sql_query.lstrip().upper()
    if not (sql_upper.startswith("SELECT") or sql_upper.startswith("WITH")):
        return False
    if ";" in sql_query:
        return False
    return not _BLOCKED_SQL_RE.search(sql_query)


def _replace_qmark_placeholders(
    sql_query: str, values: list[Any]
) -> tuple[str, dict[str, Any]]:
    qmark_count = sql_query.count("?")
    if qmark_count != len(values):
        raise ValueError(
            f"Text2SQL produced {qmark_count} placeholders but {len(values)} params"
        )

    bind_params = {f"param_{index}": value for index, value in enumerate(values)}

    def replace_qmark(_: re.Match[str]) -> str:
        replace_qmark.index += 1
        return f":param_{replace_qmark.index - 1}"

    replace_qmark.index = 0
    return re.sub(r"\?", replace_qmark, sql_query), bind_params


def _extract_bind_names(sql_query: str) -> list[str]:
    bind_names: list[str] = []
    for match in _NAMED_BIND_RE.finditer(sql_query):
        name = match.group(1)
        if name not in bind_names:
            bind_names.append(name)
    return bind_names


def _coerce_params_for_sqlalchemy(
    sql_query: str, params: Any
) -> tuple[str, dict[str, Any]]:
    
    if params in (None, [], ()):
        return sql_query, {}

    qmark_count = sql_query.count("?")
    bind_names = _extract_bind_names(sql_query)

    if isinstance(params, dict):
        bind_params = dict(params)
        if qmark_count:
            return _replace_qmark_placeholders(sql_query, list(bind_params.values()))

        if bind_names:
            missing = [name for name in bind_names if name not in bind_params]
            if missing:
                logger.warning(
                    "Text2SQL omitted named bind values for {}; using placeholder names as fallbacks.",
                    missing,
                )
                for name in missing:
                    bind_params[name] = name

            return sql_query, {name: bind_params[name] for name in bind_names}

        if not bind_params:
            return sql_query, {}

        logger.warning(
            "Text2SQL returned params for a query with no bind placeholders; ignoring params."
        )
        return sql_query, {}

    if not isinstance(params, (list, tuple)):
        raise ValueError("Text2SQL params must be an object or array")

    if len(params) == 1 and isinstance(params[0], dict):
        return _coerce_params_for_sqlalchemy(sql_query, dict(params[0]))

    if all(isinstance(item, (list, tuple)) for item in params):
        if len(params) != 1:
            raise ValueError("SELECT queries cannot use executemany parameter batches")
        params = list(params[0])

    values = list(params)
    if qmark_count:
        return _replace_qmark_placeholders(sql_query, values)

    if bind_names:
        if len(bind_names) != len(values):
            raise ValueError(
                f"Text2SQL produced {len(bind_names)} named binds but {len(values)} params"
            )
        return sql_query, dict(zip(bind_names, values))

    logger.warning(
        "Text2SQL returned params for a query with no bind placeholders; ignoring params."
    )
    return sql_query, {}


def _deterministic_sql(question: str) -> tuple[str, dict[str, Any], str] | None:

    normalized = question.lower()
    normalized_dash = normalized.replace("-", " ")
    normalized_icd = normalized.replace("icd-10", "icd10")

    if normalized.startswith("### task:"):
        return None

    if (
        ("risk dashboard" in normalized or ("dashboard" in normalized and "critical" in normalized))
        and "billing" in normalized
        and "compliance" in normalized
        and "pharmacy" in normalized
        and "patient" in normalized
        and ("dispatch" in normalized or "delivery" in normalized)
    ):
        return (
            """
WITH critical_billing AS (
    SELECT 'billing' AS risk_domain,
           bc.claim_id AS record_id,
           COALESCE(bc.error_type, 'CodingError') AS risk_type,
           'Critical' AS severity,
           bc.claim_status AS status,
           bc.claim_date AS event_date,
           bc.payer_name AS owner,
           COALESCE(bc.rejection_reason, 'Claim rejected with coding issue.') AS details
    FROM billing_claims AS bc
    WHERE bc.claim_status = 'Rejected'
      AND (COALESCE(bc.has_coding_error, 0) = 1 OR bc.error_type IS NOT NULL)
),
critical_compliance AS (
    SELECT 'compliance' AS risk_domain,
           af.finding_id AS record_id,
           af.violation_type AS risk_type,
           af.severity AS severity,
           af.resolution_status AS status,
           af.audit_date AS event_date,
           cp.department AS owner,
           af.finding_description AS details
    FROM audit_findings AS af
    JOIN clinical_processes AS cp ON cp.process_id = af.process_id
    WHERE af.severity = 'Critical'
),
critical_pharmacy AS (
    SELECT 'pharmacy' AS risk_domain,
           di.inventory_id AS record_id,
           CASE
               WHEN di.expiry_date < date('now') THEN 'ExpiredBatch'
               ELSE 'ReorderCritical'
           END AS risk_type,
           'Critical' AS severity,
           CASE
               WHEN di.expiry_date < date('now') THEN 'Expired'
               ELSE 'BelowReorderThreshold'
           END AS status,
           CASE
               WHEN di.expiry_date < date('now') THEN di.expiry_date
               ELSE date('now')
           END AS event_date,
           d.supplier AS owner,
           d.drug_name || ' (' || di.batch_number || ')' AS details
    FROM drug_inventory AS di
    JOIN drugs AS d ON d.drug_id = di.drug_id
    WHERE di.expiry_date < date('now')
       OR (
           di.current_stock < di.reorder_threshold
           AND COALESCE(di.average_daily_consumption, 0) > 0
           AND (di.current_stock / di.average_daily_consumption) <= COALESCE(di.supplier_lead_time_days, 0)
       )
),
critical_patient AS (
    SELECT 'patient' AS risk_domain,
           pc.complaint_id AS record_id,
           COALESCE(pc.breach_severity, 'Critical') AS risk_type,
           'Critical' AS severity,
           pc.resolution_status AS status,
           pc.complaint_date AS event_date,
           COALESCE(ap.department, 'Unknown') AS owner,
           pc.complaint_text AS details
    FROM patient_complaints AS pc
    LEFT JOIN appointments AS ap ON ap.appointment_id = pc.appointment_id
    WHERE pc.breach_severity = 'Critical'
       OR (
           COALESCE(pc.sla_breached, 0) = 1
           AND ap.wait_time_minutes > COALESCE(pc.sla_threshold_minutes, 0) * 2
       )
),
critical_dispatch AS (
    SELECT 'dispatch' AS risk_domain,
           dr.delivery_id AS record_id,
           'EmergencySLABreach' AS risk_type,
           'Critical' AS severity,
           dr.delivery_status AS status,
           date(dr.scheduled_datetime) AS event_date,
           COALESCE(r.origin, 'Unknown') || ' -> ' || COALESCE(r.destination, 'Unknown') AS owner,
           COALESCE(dr.delay_reason, 'Emergency dispatch breached SLA window.') AS details
    FROM delivery_records AS dr
    LEFT JOIN routes AS r ON r.route_id = dr.route_id
    WHERE dr.delivery_category = 'Emergency'
      AND COALESCE(dr.sla_breached, 0) = 1
),
all_risks AS (
    SELECT * FROM critical_billing
    UNION ALL
    SELECT * FROM critical_compliance
    UNION ALL
    SELECT * FROM critical_pharmacy
    UNION ALL
    SELECT * FROM critical_patient
    UNION ALL
    SELECT * FROM critical_dispatch
),
ranked AS (
    SELECT risk_domain,
           record_id,
           risk_type,
           severity,
           status,
           event_date,
           owner,
           details,
           COUNT(*) OVER (PARTITION BY risk_domain) AS domain_critical_count,
           ROW_NUMBER() OVER (
               PARTITION BY risk_domain
               ORDER BY date(event_date) DESC, record_id
           ) AS domain_rank
    FROM all_risks
)
SELECT risk_domain,
       record_id,
       risk_type,
       severity,
       status,
       event_date,
       owner,
       details,
       domain_critical_count,
       domain_rank
FROM ranked
WHERE domain_rank <= 5
ORDER BY risk_domain, domain_rank
""",
            {},
            "Cross-domain critical risk dashboard across billing, compliance, pharmacy, patient, and dispatch.",
        )

    if (
        "claim" in normalized
        and ("coding error" in normalized_dash or "coding errors" in normalized_dash)
    ):
        return (
            """
SELECT bc.claim_id,
       bc.patient_id,
       bc.encounter_id,
       bc.claim_date,
       bc.payer_name,
       bc.claim_status,
       bc.cpt_code,
       e.primary_diagnosis_icd10 AS icd10_code,
       bc.error_type,
       bc.rejection_reason,
       CASE
           WHEN bc.claim_status = 'Rejected' THEN 'Critical'
           WHEN bc.error_type IN ('Upcoding', 'Unbundling') THEN 'High'
           WHEN bc.error_type IS NOT NULL OR COALESCE(bc.has_coding_error, 0) = 1 THEN 'Medium'
           ELSE 'Low'
       END AS rejection_risk,
       CASE
           WHEN bc.claim_status = 'Rejected' THEN 0.95
           WHEN bc.error_type IN ('Upcoding', 'Unbundling') THEN 0.75
           WHEN bc.error_type IS NOT NULL OR COALESCE(bc.has_coding_error, 0) = 1 THEN 0.55
           ELSE 0.20
       END AS rejection_risk_score
FROM billing_claims AS bc
LEFT JOIN encounters AS e ON e.encounter_id = bc.encounter_id
WHERE COALESCE(bc.has_coding_error, 0) = 1
   OR bc.error_type IS NOT NULL
ORDER BY
    CASE
        WHEN bc.claim_status = 'Rejected' THEN 0
        WHEN bc.error_type IN ('Upcoding', 'Unbundling') THEN 1
        ELSE 2
    END,
    bc.claim_date DESC
LIMIT 50
""",
            {},
            "Claims with coding errors and derived rejection risk classification.",
        )

    if (
        "payer" in normalized
        and "rejection" in normalized
        and ("error type" in normalized_dash or "error types" in normalized_dash)
    ):
        return (
            """
WITH payer_stats AS (
    SELECT payer_name,
           COUNT(*) AS total_claims,
           SUM(CASE WHEN claim_status = 'Rejected' THEN 1 ELSE 0 END) AS rejected_claims,
           ROUND(
               100.0 * SUM(CASE WHEN claim_status = 'Rejected' THEN 1 ELSE 0 END)
               / NULLIF(COUNT(*), 0),
               2
           ) AS rejection_rate_pct
    FROM billing_claims
    GROUP BY payer_name
),
error_breakdown AS (
    SELECT payer_name,
           error_type,
           COUNT(*) AS error_count
    FROM billing_claims
    WHERE claim_status = 'Rejected'
      AND error_type IS NOT NULL
    GROUP BY payer_name, error_type
),
ranked_errors AS (
    SELECT payer_name,
           error_type,
           error_count,
           ROW_NUMBER() OVER (
               PARTITION BY payer_name
               ORDER BY error_count DESC, error_type
           ) AS rank_in_payer
    FROM error_breakdown
)
SELECT ps.payer_name,
       ps.total_claims,
       ps.rejected_claims,
       ps.rejection_rate_pct,
       re.error_type AS top_error_type,
       re.error_count AS top_error_count
FROM payer_stats AS ps
LEFT JOIN ranked_errors AS re
       ON re.payer_name = ps.payer_name
      AND re.rank_in_payer = 1
ORDER BY ps.rejection_rate_pct DESC, ps.total_claims DESC
LIMIT 50
""",
            {},
            "Payer-level rejection rates with most common rejected-claim coding error type.",
        )

    if (
        "claim" in normalized
        and "rejection" in normalized
        and "icd10" in normalized_icd
    ):
        return (
            """
WITH monthly_claims AS (
    SELECT bc.claim_id, bc.claim_status, e.primary_diagnosis_icd10 AS icd10_code
    FROM billing_claims AS bc
    JOIN encounters AS e ON e.encounter_id = bc.encounter_id
    WHERE bc.claim_date >= date('now', 'start of month')
      AND bc.claim_date < date('now', 'start of month', '+1 month')
),
overall AS (
    SELECT COUNT(*) AS overall_total_claims,
           SUM(CASE WHEN claim_status = 'Rejected' THEN 1 ELSE 0 END) AS overall_rejected_claims
    FROM monthly_claims
),
icd10 AS (
    SELECT icd10_code,
           COUNT(*) AS total_claims,
           SUM(CASE WHEN claim_status = 'Rejected' THEN 1 ELSE 0 END) AS rejected_claims,
           ROUND(
               100.0 * SUM(CASE WHEN claim_status = 'Rejected' THEN 1 ELSE 0 END)
               / NULLIF(COUNT(*), 0),
               2
           ) AS rejection_rate_pct
    FROM monthly_claims
    GROUP BY icd10_code
)
SELECT o.overall_total_claims,
       o.overall_rejected_claims,
       ROUND(
           100.0 * o.overall_rejected_claims / NULLIF(o.overall_total_claims, 0),
           2
       ) AS overall_rejection_rate_pct,
       i.icd10_code,
       i.total_claims,
       i.rejected_claims,
       i.rejection_rate_pct,
       ROUND(
           100.0 * i.rejected_claims / NULLIF(o.overall_rejected_claims, 0),
           2
       ) AS share_of_rejections_pct
FROM icd10 AS i
CROSS JOIN overall AS o
WHERE i.rejected_claims > 0
ORDER BY i.rejected_claims DESC, i.rejection_rate_pct DESC
LIMIT 10
""",
            {},
            "Monthly claim rejection rate and ICD-10 rejection drivers.",
        )

    if (
        "appointment" in normalized
        and "complaint" in normalized
        and "sla" in normalized
        and "week" in normalized
        and ("average delay" in normalized or "avg delay" in normalized)
    ):
        return (
            """
SELECT COUNT(*) AS breached_complaint_count,
       ROUND(
           AVG(
               CASE
                   WHEN pc.sla_breached = 1
                   THEN ap.wait_time_minutes - COALESCE(pc.sla_threshold_minutes, 0)
                   ELSE NULL
               END
           ),
           2
       ) AS avg_delay_minutes_over_sla,
       ROUND(
           AVG(
               CASE
                   WHEN pc.sla_breached = 1 THEN ap.wait_time_minutes
                   ELSE NULL
               END
           ),
           2
       ) AS avg_wait_time_minutes,
       SUM(CASE WHEN pc.breach_severity = 'Critical' THEN 1 ELSE 0 END) AS critical_breaches
FROM patient_complaints AS pc
LEFT JOIN appointments AS ap ON ap.appointment_id = pc.appointment_id
WHERE pc.complaint_date >= date('now', '-7 days')
  AND pc.complaint_date <= date('now')
  AND COALESCE(pc.sla_breached, 0) = 1
""",
            {},
            "Weekly SLA-breached appointment complaints with average delay.",
        )

    if (
        "department" in normalized
        and "sla" in normalized
        and "specialist" in normalized
        and ("worst" in normalized or "lowest" in normalized)
    ):
        return (
            """
WITH specialist AS (
    SELECT department,
           wait_time_minutes,
           CASE
               WHEN wait_time_minutes <= 45 THEN 1
               ELSE 0
           END AS sla_met
    FROM appointments
    WHERE appointment_category = 'Specialist'
      AND scheduled_datetime >= datetime('now', '-90 days')
),
dept_stats AS (
    SELECT department,
           COUNT(*) AS total_appointments,
           SUM(CASE WHEN sla_met = 1 THEN 1 ELSE 0 END) AS within_sla_count,
           SUM(CASE WHEN sla_met = 0 THEN 1 ELSE 0 END) AS breached_count,
           ROUND(
               100.0 * SUM(CASE WHEN sla_met = 1 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0),
               2
           ) AS sla_compliance_rate_pct,
           ROUND(
               AVG(CASE WHEN sla_met = 0 THEN wait_time_minutes - 45 ELSE NULL END),
               2
           ) AS avg_delay_over_sla_minutes
    FROM specialist
    GROUP BY department
)
SELECT department,
       total_appointments,
       within_sla_count,
       breached_count,
       sla_compliance_rate_pct,
       avg_delay_over_sla_minutes
FROM dept_stats
ORDER BY sla_compliance_rate_pct ASC, breached_count DESC
LIMIT 50
""",
            {},
            "Department-level specialist appointment SLA compliance ranking.",
        )

    if (
        "process" in normalized
        and "patient consent" in normalized
        and ("not obtained" in normalized or "missing" in normalized)
    ):
        return (
            """
SELECT cp.process_id,
       cp.process_type,
       cp.department,
       cp.procedure_date,
       cp.patient_consent_obtained,
       af.finding_id,
       af.violation_type,
       COALESCE(af.severity, 'None') AS severity,
       COALESCE(af.regulation_body, 'None') AS regulation_body,
       COALESCE(af.resolution_status, 'Open') AS resolution_status
FROM clinical_processes AS cp
LEFT JOIN audit_findings AS af ON af.process_id = cp.process_id
WHERE COALESCE(cp.patient_consent_obtained, 1) = 0
ORDER BY
    CASE COALESCE(af.severity, 'None')
        WHEN 'Critical' THEN 0
        WHEN 'Major' THEN 1
        WHEN 'Minor' THEN 2
        ELSE 3
    END,
    cp.procedure_date DESC
LIMIT 50
""",
            {},
            "Clinical processes without patient consent, enriched with audit severity when available.",
        )

    if (
        "jci" in normalized
        and "violation" in normalized
        and "open" in normalized
        and ("department" in normalized or "responsible" in normalized)
    ):
        return (
            """
WITH open_jci AS (
    SELECT cp.department,
           af.finding_id
    FROM audit_findings AS af
    JOIN clinical_processes AS cp ON cp.process_id = af.process_id
    WHERE af.regulation_body = 'JCI'
      AND af.resolution_status IN ('Open', 'InReview')
),
totals AS (
    SELECT COUNT(*) AS total_open_jci_violations
    FROM open_jci
)
SELECT t.total_open_jci_violations,
       o.department,
       COUNT(*) AS department_open_count
FROM totals AS t
LEFT JOIN open_jci AS o ON 1 = 1
GROUP BY t.total_open_jci_violations, o.department
ORDER BY department_open_count DESC, o.department
LIMIT 50
""",
            {},
            "Open JCI violations with department ownership split.",
        )

    if (
        ("on-time" in normalized or "on time" in normalized)
        and "emergency" in normalized
        and ("dispatch" in normalized or "delivery" in normalized)
    ):
        return (
            """
WITH emergency_deliveries AS (
    SELECT dr.delivery_id,
           dr.route_id,
           r.origin,
           r.destination,
           dr.scheduled_datetime,
           dr.actual_delivery_datetime,
           dr.sla_window_minutes,
           dr.delivery_status,
           dr.sla_breached
    FROM delivery_records AS dr
    LEFT JOIN routes AS r ON r.route_id = dr.route_id
    WHERE dr.delivery_category = 'Emergency'
      AND dr.scheduled_datetime >= datetime('now', 'start of month')
      AND dr.scheduled_datetime < datetime('now', 'start of month', '+1 month')
),
scored AS (
    SELECT *,
           CASE
               WHEN actual_delivery_datetime IS NOT NULL
                    AND COALESCE(sla_breached, 0) = 0 THEN 1
               ELSE 0
           END AS on_time_flag
    FROM emergency_deliveries
),
overall AS (
    SELECT COUNT(*) AS overall_emergency_dispatches,
           SUM(on_time_flag) AS on_time_dispatches,
           SUM(CASE WHEN COALESCE(sla_breached, 0) = 1 THEN 1 ELSE 0 END) AS late_dispatches
    FROM scored
),
route_stats AS (
    SELECT route_id,
           origin,
           destination,
           COUNT(*) AS route_dispatches,
           SUM(on_time_flag) AS route_on_time_dispatches,
           ROUND(100.0 * SUM(on_time_flag) / NULLIF(COUNT(*), 0), 2) AS route_on_time_rate_pct
    FROM scored
    GROUP BY route_id, origin, destination
)
SELECT o.overall_emergency_dispatches,
       o.on_time_dispatches,
       o.late_dispatches,
       ROUND(
           100.0 * o.on_time_dispatches / NULLIF(o.overall_emergency_dispatches, 0),
           2
       ) AS overall_on_time_rate_pct,
       rs.route_id,
       rs.origin,
       rs.destination,
       rs.route_dispatches,
       rs.route_on_time_dispatches,
       rs.route_on_time_rate_pct
FROM route_stats AS rs
CROSS JOIN overall AS o
ORDER BY rs.route_on_time_rate_pct ASC, rs.route_dispatches DESC
LIMIT 10
""",
            {},
            "Monthly emergency dispatch on-time delivery rate and route performance.",
        )

    if (
        ("delayed" in normalized or "delay" in normalized)
        and (
            "delivery" in normalized
            or "deliveries" in normalized
            or "dispatch" in normalized
            or "dispatches" in normalized
        )
        and "week" in normalized
    ):
        return (
            """
SELECT dr.delivery_id,
       dr.route_id,
       r.origin,
       r.destination,
       dr.delivery_category,
       dr.scheduled_datetime,
       dr.actual_delivery_datetime,
       dr.delivery_status,
       dr.sla_window_minutes,
       CAST(
           (julianday(dr.actual_delivery_datetime) - julianday(dr.scheduled_datetime))
           * 24 * 60 AS INTEGER
       ) AS actual_duration_minutes,
       CAST(
           (julianday(dr.actual_delivery_datetime) - julianday(dr.scheduled_datetime))
           * 24 * 60 AS INTEGER
       ) - dr.sla_window_minutes AS delay_minutes,
       dr.delay_reason
FROM delivery_records AS dr
LEFT JOIN routes AS r ON r.route_id = dr.route_id
WHERE dr.scheduled_datetime >= datetime('now', '-7 days')
  AND dr.scheduled_datetime <= datetime('now')
  AND (dr.delivery_status = 'Delayed' OR COALESCE(dr.sla_breached, 0) = 1)
ORDER BY delay_minutes DESC, dr.scheduled_datetime DESC
LIMIT 50
""",
            {},
            "Delayed or SLA-breached deliveries from the last 7 days.",
        )

    if (
        "critical" in normalized
        and "hipaa" in normalized
        and ("violation" in normalized or "finding" in normalized)
    ):
        return (
            """
SELECT af.finding_id,
       af.violation_type,
       af.severity,
       af.regulation_body,
       af.audit_date,
       af.resolution_status,
       af.policy_reference,
       af.finding_description,
       cp.process_id,
       cp.process_type,
       cp.department,
       cp.staff_name
FROM audit_findings AS af
JOIN clinical_processes AS cp ON cp.process_id = af.process_id
WHERE af.severity = 'Critical'
  AND af.regulation_body = 'HIPAA'
  AND af.audit_date >= date('now', '-30 days')
ORDER BY af.audit_date DESC, af.finding_id
LIMIT 50
""",
            {},
            "Critical HIPAA audit findings from the last 30 days with resolution status.",
        )

    if (
        ("drug" in normalized or "pharmacy" in normalized)
        and ("expiring" in normalized or "expiry" in normalized or "expire" in normalized)
        and ("cost" in normalized or "exposure" in normalized or "60 days" in normalized)
    ):
        return (
            """
WITH expiring AS (
    SELECT d.drug_id,
           d.drug_name,
           d.generic_name,
           d.category,
           d.unit_cost,
           di.batch_number,
           di.expiry_date,
           di.current_stock,
           di.reorder_threshold,
           di.average_daily_consumption,
           CAST(julianday(di.expiry_date) - julianday(date('now')) AS INTEGER) AS days_until_expiry,
           ROUND(di.current_stock * COALESCE(d.unit_cost, 0), 2) AS cost_exposure
    FROM drug_inventory AS di
    JOIN drugs AS d ON d.drug_id = di.drug_id
    WHERE di.expiry_date >= date('now')
      AND di.expiry_date < date('now', '+60 days')
),
overall AS (
    SELECT COUNT(*) AS expiring_batch_count,
           ROUND(COALESCE(SUM(cost_exposure), 0), 2) AS total_cost_exposure
    FROM expiring
)
SELECT overall.expiring_batch_count,
       overall.total_cost_exposure,
       expiring.drug_id,
       expiring.drug_name,
       expiring.batch_number,
       expiring.expiry_date,
       expiring.days_until_expiry,
       expiring.current_stock,
       expiring.unit_cost,
       expiring.cost_exposure,
       expiring.reorder_threshold,
       expiring.average_daily_consumption
FROM overall
LEFT JOIN expiring ON 1 = 1
ORDER BY expiring.expiry_date ASC, expiring.cost_exposure DESC
LIMIT 20
""",
            {},
            "Drug batches expiring in the next 60 days and total inventory cost exposure.",
        )

    if (
        ("drug" in normalized or "pharmacy" in normalized)
        and (
            ("reorder" in normalized and "threshold" in normalized)
            or "stockout" in normalized
            or "stock out" in normalized
            or "low stock" in normalized
        )
    ):
        return (
            """
WITH below_reorder AS (
    SELECT d.drug_id,
           d.drug_name,
           d.generic_name,
           d.category,
           d.unit,
           d.unit_cost,
           d.supplier,
           di.inventory_id,
           di.batch_number,
           di.current_stock,
           di.reorder_threshold,
           di.average_daily_consumption,
           di.supplier_lead_time_days,
           di.reorder_threshold - di.current_stock AS reorder_gap,
           CASE
               WHEN di.average_daily_consumption > 0
               THEN ROUND(di.current_stock / di.average_daily_consumption, 1)
               ELSE NULL
           END AS days_until_stockout,
           CASE
               WHEN di.average_daily_consumption > 0
                    AND (di.current_stock / di.average_daily_consumption) <= di.supplier_lead_time_days
               THEN 'Critical'
               WHEN di.average_daily_consumption > 0
                    AND (di.current_stock / di.average_daily_consumption) <= di.supplier_lead_time_days + 3
               THEN 'High'
               ELSE 'Medium'
           END AS reorder_urgency
    FROM drug_inventory AS di
    JOIN drugs AS d ON d.drug_id = di.drug_id
    WHERE di.current_stock < di.reorder_threshold
),
overall AS (
    SELECT COUNT(*) AS below_reorder_count,
           SUM(CASE WHEN reorder_urgency = 'Critical' THEN 1 ELSE 0 END) AS critical_reorder_count,
           SUM(reorder_gap) AS total_reorder_gap_units
    FROM below_reorder
)
SELECT overall.below_reorder_count,
       overall.critical_reorder_count,
       overall.total_reorder_gap_units,
       below_reorder.drug_id,
       below_reorder.drug_name,
       below_reorder.batch_number,
       below_reorder.current_stock,
       below_reorder.reorder_threshold,
       below_reorder.reorder_gap,
       below_reorder.average_daily_consumption,
       below_reorder.days_until_stockout,
       below_reorder.supplier_lead_time_days,
       below_reorder.reorder_urgency,
       below_reorder.supplier
FROM overall
LEFT JOIN below_reorder ON 1 = 1
ORDER BY
    CASE below_reorder.reorder_urgency
        WHEN 'Critical' THEN 0
        WHEN 'High' THEN 1
        ELSE 2
    END,
    below_reorder.days_until_stockout ASC,
    below_reorder.reorder_gap DESC
LIMIT 20
""",
            {},
            "Drug inventory batches below reorder threshold with days until stockout.",
        )

    return None


async def _execute_select(
    sql_query: str,
    params: Any,
    db: AsyncSession,
    question: str,
    explanation: str,
) -> list[dict[str, Any]]:
    sql_query = _clean_sql(sql_query)
    if not _is_safe_select(sql_query):
        logger.warning(f"Text2SQL returned non-SELECT query - blocked: {sql_query!r}")
        return []

    sql_query, bind_params = _coerce_params_for_sqlalchemy(sql_query, params)
    logger.debug(f"Text2SQL generated: {sql_query!r} | params={bind_params} | {explanation}")

    result = await db.execute(text(sql_query), bind_params)
    rows = [dict(row) for row in result.mappings().all()]
    logger.info(f"Text2SQL returned {len(rows)} rows for: {question!r}")
    return rows


async def run_text2sql(question: str, db: AsyncSession) -> list[dict[str, Any]]:
    
    try:
        if question.lower().startswith("### task:"):
            logger.info("Skipping Text2SQL for metadata prompt.")
            return []

        deterministic = _deterministic_sql(question)
        if deterministic:
            sql_query, params, explanation = deterministic
            return await _execute_select(sql_query, params, db, question, explanation)

        llm = _build_llm()
        system_prompt = TEXT2SQL_SYSTEM.format(schema=_DB_SCHEMA)
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=TEXT2SQL_USER.format(question=question)),
        ]

        response = await llm.ainvoke(messages)
        payload = _load_json_object(response.content)
        sql_query: str = payload.get("sql", "")
        params: Any = payload.get("params", {})
        explanation: str = payload.get("explanation", "")

        return await _execute_select(sql_query, params, db, question, explanation)

    except json.JSONDecodeError as exc:
        logger.error(f"Text2SQL: LLM returned non-JSON: {exc}")
        return []
    except ValueError as exc:
        logger.error(f"Text2SQL returned invalid payload: {exc}")
        return []
    except Exception as exc:
        logger.error(f"Text2SQL execution failed: {exc}")
        return []
