
from datetime import date, timedelta
from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db

router = APIRouter(prefix="/api", tags=["portals"])


# ── Billing Portal ────────────────────────────────────────────────────────────

@router.get("/billing/claims")
async def get_billing_claims(
    status: str = Query(default="all", description="all | Pending | Approved | Rejected | UnderReview"),
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
):
    base = """
        SELECT
            bc.claim_id,
            bc.patient_id,
            p.full_name         AS patient_name,
            bc.cpt_code,
            bc.procedure_description,
            e.primary_diagnosis_icd10 AS icd10_code,
            e.diagnosis_description,
            bc.claim_amount,
            bc.claim_date,
            bc.payer_name,
            bc.claim_status,
            bc.has_coding_error,
            bc.error_type,
            bc.rejection_reason
        FROM billing_claims bc
        JOIN encounters e ON bc.encounter_id = e.encounter_id
        JOIN patients p   ON bc.patient_id   = p.patient_id
    """
    if status != "all":
        result = await db.execute(
            text(base + " WHERE bc.claim_status = :status ORDER BY bc.claim_date DESC LIMIT :limit"),
            {"status": status, "limit": limit},
        )
    else:
        result = await db.execute(
            text(base + " ORDER BY bc.claim_date DESC LIMIT :limit"),
            {"limit": limit},
        )
    rows = [dict(r._mapping) for r in result.fetchall()]

    # summary stats
    stats_result = await db.execute(text("""
        SELECT
            COUNT(*)                                            AS total_claims,
            SUM(CASE WHEN claim_status='Rejected'   THEN 1 ELSE 0 END) AS rejected,
            SUM(CASE WHEN has_coding_error=1        THEN 1 ELSE 0 END) AS coding_errors,
            SUM(CASE WHEN claim_status='Pending'    THEN 1 ELSE 0 END) AS pending,
            ROUND(AVG(claim_amount), 2)                        AS avg_claim_amount
        FROM billing_claims
    """))
    stats = dict(stats_result.fetchone()._mapping)
    return {"summary": stats, "claims": rows}


# ── Compliance Portal ─────────────────────────────────────────────────────────

@router.get("/compliance/findings")
async def get_compliance_findings(
    severity: str = Query(default="all", description="all | Critical | Major | Minor"),
    status: str = Query(default="all", description="all | Open | InReview | Resolved"),
    days: int = Query(default=90, description="Look-back window in days"),
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
):
    since = (date.today() - timedelta(days=days)).isoformat()
    conditions = ["af.audit_date >= :since"]
    params: dict = {"since": since, "limit": limit}

    if severity != "all":
        conditions.append("af.severity = :severity")
        params["severity"] = severity
    if status != "all":
        conditions.append("af.resolution_status = :status")
        params["status"] = status

    where = "WHERE " + " AND ".join(conditions)
    result = await db.execute(text(f"""
        SELECT
            af.finding_id,
            af.process_id,
            cp.process_type,
            cp.department,
            cp.staff_name,
            af.audit_date,
            af.violation_type,
            af.severity,
            af.regulation_body,
            af.policy_reference,
            af.finding_description,
            af.resolution_status,
            af.resolved_at
        FROM audit_findings af
        JOIN clinical_processes cp ON af.process_id = cp.process_id
        {where}
        ORDER BY
            CASE af.severity WHEN 'Critical' THEN 1 WHEN 'Major' THEN 2 ELSE 3 END,
            af.audit_date DESC
        LIMIT :limit
    """), params)
    rows = [dict(r._mapping) for r in result.fetchall()]

    stats_result = await db.execute(text("""
        SELECT
            COUNT(*)                                                    AS total_findings,
            SUM(CASE WHEN severity='Critical'          THEN 1 ELSE 0 END) AS critical,
            SUM(CASE WHEN severity='Major'             THEN 1 ELSE 0 END) AS major,
            SUM(CASE WHEN severity='Minor'             THEN 1 ELSE 0 END) AS minor,
            SUM(CASE WHEN resolution_status='Open'     THEN 1 ELSE 0 END) AS open_findings,
            SUM(CASE WHEN resolution_status='Resolved' THEN 1 ELSE 0 END) AS resolved
        FROM audit_findings
    """))
    stats = dict(stats_result.fetchone()._mapping)
    return {"summary": stats, "findings": rows}


# ── Pharmacy Portal ───────────────────────────────────────────────────────────

@router.get("/pharmacy/alerts")
async def get_pharmacy_alerts(
    alert_window_days: int = Query(default=60, description="Days ahead to check for expiry"),
    db: AsyncSession = Depends(get_db),
):
    today = date.today().isoformat()
    cutoff = (date.today() + timedelta(days=alert_window_days)).isoformat()

    expiry_result = await db.execute(text("""
        SELECT
            di.inventory_id,
            d.drug_id,
            d.drug_name,
            d.category,
            d.supplier,
            d.unit_cost,
            di.batch_number,
            di.expiry_date,
            CAST(julianday(di.expiry_date) - julianday(:today) AS INTEGER) AS days_until_expiry,
            di.current_stock,
            ROUND(di.current_stock * d.unit_cost, 2)                       AS stock_value_at_risk,
            CASE
                WHEN julianday(di.expiry_date) - julianday(:today) <= 0  THEN 'Expired'
                WHEN julianday(di.expiry_date) - julianday(:today) <= 30 THEN 'Critical'
                WHEN julianday(di.expiry_date) - julianday(:today) <= 60 THEN 'High'
                ELSE 'Medium'
            END AS urgency
        FROM drug_inventory di
        JOIN drugs d ON di.drug_id = d.drug_id
        WHERE di.expiry_date <= :cutoff
        ORDER BY di.expiry_date ASC
        LIMIT 100
    """), {"today": today, "cutoff": cutoff})
    expiry_rows = [dict(r._mapping) for r in expiry_result.fetchall()]

    reorder_result = await db.execute(text("""
        SELECT
            di.inventory_id,
            d.drug_id,
            d.drug_name,
            d.category,
            d.supplier,
            di.current_stock,
            di.reorder_threshold,
            di.reorder_threshold - di.current_stock                        AS shortfall,
            di.average_daily_consumption,
            di.supplier_lead_time_days,
            CASE
                WHEN di.current_stock = 0                       THEN 'Critical'
                WHEN di.current_stock < di.reorder_threshold / 2 THEN 'High'
                ELSE 'Medium'
            END AS urgency
        FROM drug_inventory di
        JOIN drugs d ON di.drug_id = d.drug_id
        WHERE di.current_stock < di.reorder_threshold
        ORDER BY (di.reorder_threshold - di.current_stock) DESC
        LIMIT 100
    """))
    reorder_rows = [dict(r._mapping) for r in reorder_result.fetchall()]

    stats_result = await db.execute(text("""
        SELECT
            COUNT(*)                                                           AS total_batches,
            SUM(CASE WHEN julianday(expiry_date)-julianday('now') <= 0  THEN 1 ELSE 0 END) AS expired,
            SUM(CASE WHEN julianday(expiry_date)-julianday('now') <= 30
                      AND julianday(expiry_date)-julianday('now') > 0   THEN 1 ELSE 0 END) AS expiring_30d,
            SUM(CASE WHEN current_stock < reorder_threshold             THEN 1 ELSE 0 END) AS below_threshold
        FROM drug_inventory
    """))
    stats = dict(stats_result.fetchone()._mapping)
    return {"summary": stats, "expiry_alerts": expiry_rows, "reorder_alerts": reorder_rows}


# ── Patient Portal ────────────────────────────────────────────────────────────

@router.get("/patient/complaints")
async def get_patient_complaints(
    breached_only: bool = Query(default=False),
    status: str = Query(default="all", description="all | Open | AutoResolved | EscalatedToStaff"),
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
):
    conditions = []
    params: dict = {"limit": limit}

    if breached_only:
        conditions.append("pc.sla_breached = 1")
    if status != "all":
        conditions.append("pc.resolution_status = :status")
        params["status"] = status

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    result = await db.execute(text(f"""
        SELECT
            pc.complaint_id,
            pc.patient_id,
            p.full_name             AS patient_name,
            p.patient_tier,
            a.appointment_category,
            a.scheduled_datetime,
            a.actual_start_datetime,
            a.wait_time_minutes,
            pc.sla_threshold_minutes,
            pc.sla_breached,
            pc.breach_severity,
            pc.compensation_eligible,
            pc.compensation_type,
            pc.resolution_status,
            pc.complaint_date,
            pc.complaint_text
        FROM patient_complaints pc
        JOIN patients p    ON pc.patient_id    = p.patient_id
        LEFT JOIN appointments a ON pc.appointment_id = a.appointment_id
        {where}
        ORDER BY
            CASE pc.breach_severity WHEN 'Critical' THEN 1 WHEN 'High' THEN 2 ELSE 3 END,
            pc.complaint_date DESC
        LIMIT :limit
    """), params)
    rows = [dict(r._mapping) for r in result.fetchall()]

    stats_result = await db.execute(text("""
        SELECT
            COUNT(*)                                                             AS total_complaints,
            SUM(CASE WHEN sla_breached=1                  THEN 1 ELSE 0 END)   AS sla_breached,
            SUM(CASE WHEN breach_severity='Critical'      THEN 1 ELSE 0 END)   AS critical_breaches,
            SUM(CASE WHEN compensation_eligible=1         THEN 1 ELSE 0 END)   AS compensation_eligible,
            SUM(CASE WHEN resolution_status='Open'        THEN 1 ELSE 0 END)   AS open_complaints,
            SUM(CASE WHEN resolution_status='AutoResolved' THEN 1 ELSE 0 END)  AS auto_resolved
        FROM patient_complaints
    """))
    stats = dict(stats_result.fetchone()._mapping)
    return {"summary": stats, "complaints": rows}


# ── Dispatch Portal ───────────────────────────────────────────────────────────

@router.get("/dispatch/routes")
async def get_dispatch_routes(
    sla_risk_only: bool = Query(default=False),
    category: str = Query(default="all", description="all | Emergency | Routine | Refrigerated"),
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
):
    conditions = []
    params: dict = {"limit": limit}

    if sla_risk_only:
        conditions.append("dr.sla_breached = 1")
    if category != "all":
        conditions.append("dr.delivery_category = :category")
        params["category"] = category

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    result = await db.execute(text(f"""
        SELECT
            dr.delivery_id,
            dr.delivery_category,
            v.vehicle_type,
            v.registration,
            r.origin,
            r.destination,
            r.distance_km,
            r.route_type,
            ROUND(r.estimated_duration_minutes * r.traffic_factor) AS adjusted_eta_minutes,
            dr.sla_window_minutes,
            dr.estimated_cost,
            dr.delivery_status,
            dr.sla_breached,
            dr.delay_reason,
            dr.scheduled_datetime,
            dr.actual_delivery_datetime,
            -- Cost vs SLA trade-off score (lower is better)
            ROUND(
                (r.estimated_duration_minutes * r.traffic_factor / dr.sla_window_minutes) * 0.6
                + (dr.estimated_cost / 10000.0) * 0.4
            , 3) AS risk_score,
            CASE
                WHEN r.estimated_duration_minutes * r.traffic_factor > dr.sla_window_minutes THEN 'Critical'
                WHEN r.estimated_duration_minutes * r.traffic_factor > dr.sla_window_minutes * 0.8 THEN 'High'
                WHEN r.estimated_duration_minutes * r.traffic_factor > dr.sla_window_minutes * 0.6 THEN 'Medium'
                ELSE 'Low'
            END AS sla_risk
        FROM delivery_records dr
        JOIN vehicles v ON dr.vehicle_id = v.vehicle_id
        JOIN routes r   ON dr.route_id   = r.route_id
        {where}
        ORDER BY risk_score DESC
        LIMIT :limit
    """), params)
    rows = [dict(r._mapping) for r in result.fetchall()]

    stats_result = await db.execute(text("""
        SELECT
            COUNT(*)                                                          AS total_deliveries,
            SUM(CASE WHEN sla_breached=1                   THEN 1 ELSE 0 END) AS sla_breached,
            SUM(CASE WHEN delivery_status='Delayed'        THEN 1 ELSE 0 END) AS delayed,
            SUM(CASE WHEN delivery_category='Emergency'    THEN 1 ELSE 0 END) AS emergency,
            ROUND(AVG(estimated_cost), 2)                                     AS avg_cost,
            SUM(CASE WHEN delivery_status='Delivered'
                AND sla_breached=0                         THEN 1 ELSE 0 END) AS on_time_delivered
        FROM delivery_records
    """))
    stats = dict(stats_result.fetchone()._mapping)
    return {"summary": stats, "routes": rows}


# ── Vehicle Availability (Dispatch enhancement) ───────────────────────────────

@router.get("/dispatch/vehicles")
async def get_vehicle_availability(
    vehicle_type: str = Query(default="all"),
    db: AsyncSession = Depends(get_db),
):
    params: dict = {}
    where = ""
    if vehicle_type != "all":
        where = "WHERE vehicle_type = :vehicle_type"
        params["vehicle_type"] = vehicle_type

    result = await db.execute(text(f"""
        SELECT
            v.vehicle_id,
            v.vehicle_type,
            v.registration,
            v.capacity_kg,
            v.available,
            v.base_location,
            COUNT(dr.delivery_id) AS deliveries_today
        FROM vehicles v
        LEFT JOIN delivery_records dr
            ON v.vehicle_id = dr.vehicle_id
            AND DATE(dr.scheduled_datetime) = DATE('now')
        {where}
        GROUP BY v.vehicle_id
        ORDER BY v.available DESC, deliveries_today ASC
    """), params)
    rows = [dict(r._mapping) for r in result.fetchall()]
    return {"vehicles": rows}