"""
Dispatch Engine — Production-Grade Core Logic

All dispatch business rules live here, completely separate from the LLM agent.
The agent calls these functions to get deterministic, rule-based results BEFORE
passing to the LLM for natural language explanation.

Capabilities:
  1. Vehicle compatibility check   — type + capacity enforcement
  2. Time-of-day traffic schedule  — realistic peak/off-peak ETAs
  3. Predictive SLA breach engine  — detects breach BEFORE it happens
  4. Multi-stop route sequencer    — priority-ordered stops for one vehicle
  5. Driver fatigue / shift limit  — max deliveries per vehicle per day
  6. Three-way trade-off scorer    — cheapest vs fastest vs safest-SLA
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.logger import logger

# ── Constants ─────────────────────────────────────────────────────────────────

SLA_WINDOWS: dict[str, int] = {
    "Emergency":   30,
    "Refrigerated": 60,
    "Routine":     120,
}

# Vehicle types that are compatible with each delivery category
VEHICLE_COMPATIBILITY: dict[str, list[str]] = {
    "Emergency":   ["Ambulance", "Van"],
    "Refrigerated":["Refrigerated"],
    "Routine":     ["Van", "Motorcycle", "Ambulance", "Refrigerated"],
}

# Minimum vehicle capacity (kg) required per delivery category
MIN_CAPACITY_KG: dict[str, float] = {
    "Emergency":    200.0,
    "Refrigerated": 200.0,
    "Routine":       50.0,
}

# Maximum deliveries per vehicle per shift (8-hour shift)
MAX_DELIVERIES_PER_SHIFT = 4

# Traffic factor schedule by hour (24h) and route type
# format: { route_type: { hour_start: factor } }
TRAFFIC_SCHEDULE: dict[str, dict[int, float]] = {
    "Urban": {
        0:  0.85,   # 00:00–05:59  low traffic
        6:  1.20,   # 06:00–07:59  early morning pickup
        8:  1.80,   # 08:00–10:59  peak morning
        11: 1.10,   # 11:00–16:59  midday normal
        17: 1.75,   # 17:00–19:59  evening peak
        20: 1.10,   # 20:00–21:59  post-peak
        22: 0.85,   # 22:00–23:59  night
    },
    "Highway": {
        0:  0.90,
        6:  1.10,
        8:  1.30,
        11: 1.00,
        17: 1.25,
        20: 1.00,
        22: 0.90,
    },
    "Rural": {
        0:  0.95,
        6:  1.00,
        8:  1.05,
        11: 1.00,
        17: 1.05,
        20: 1.00,
        22: 0.95,
    },
}


def get_traffic_factor(route_type: str, dispatch_hour: int) -> float:
    """
    Return the traffic multiplier for a given route type and hour of day.
    Uses the closest lower-bound hour in the schedule.
    """
    schedule = TRAFFIC_SCHEDULE.get(route_type, TRAFFIC_SCHEDULE["Urban"])
    applicable_hours = sorted(h for h in schedule if h <= dispatch_hour)
    if not applicable_hours:
        return 1.0
    return schedule[applicable_hours[-1]]


def compute_adjusted_eta(
    base_duration_minutes: int,
    route_type: str,
    dispatch_hour: int,
) -> int:
    """Return realistic ETA in minutes accounting for time-of-day traffic."""
    factor = get_traffic_factor(route_type, dispatch_hour)
    return round(base_duration_minutes * factor)


# ── 1. Vehicle Compatibility Check ───────────────────────────────────────────

def check_vehicle_compatibility(
    vehicle_type: str,
    capacity_kg: float,
    delivery_category: str,
    payload_kg: float = 0.0,
) -> dict[str, Any]:
    """
    Returns a dict with:
      compatible: bool
      reason: str explaining why not compatible (empty if ok)
    """
    allowed_types = VEHICLE_COMPATIBILITY.get(delivery_category, [])
    min_cap = MIN_CAPACITY_KG.get(delivery_category, 0.0)

    if vehicle_type not in allowed_types:
        return {
            "compatible": False,
            "reason": (
                f"Vehicle type '{vehicle_type}' is not permitted for "
                f"'{delivery_category}' deliveries. "
                f"Allowed types: {allowed_types}"
            ),
        }

    if capacity_kg < min_cap:
        return {
            "compatible": False,
            "reason": (
                f"Vehicle capacity {capacity_kg}kg is below minimum "
                f"{min_cap}kg required for '{delivery_category}' deliveries."
            ),
        }

    if payload_kg > 0 and payload_kg > capacity_kg:
        return {
            "compatible": False,
            "reason": (
                f"Payload {payload_kg}kg exceeds vehicle capacity {capacity_kg}kg."
            ),
        }

    return {"compatible": True, "reason": ""}


# ── 2. Predictive SLA Breach Engine ──────────────────────────────────────────

def predict_sla_breach(
    scheduled_datetime: datetime,
    current_datetime: datetime,
    base_duration_minutes: int,
    route_type: str,
    sla_window_minutes: int,
    delivery_status: str,
) -> dict[str, Any]:
    """
    For in-transit deliveries, predict whether the SLA will be breached
    BEFORE the delivery completes.

    Returns:
      will_breach:         bool — predicted breach
      minutes_remaining:   int  — minutes left in SLA window
      predicted_arrival:   datetime
      breach_margin_minutes: int — negative means over SLA
      confidence:          str  — High | Medium | Low
    """
    if delivery_status not in ("InTransit", "Scheduled"):
        return {
            "will_breach": False,
            "minutes_remaining": None,
            "predicted_arrival": None,
            "breach_margin_minutes": None,
            "confidence": "N/A",
            "reason": f"Prediction not applicable for status '{delivery_status}'",
        }

    dispatch_hour = current_datetime.hour
    adjusted_eta  = compute_adjusted_eta(base_duration_minutes, route_type, dispatch_hour)

    # Time elapsed since scheduled start
    elapsed_minutes = max(
        0, (current_datetime - scheduled_datetime).total_seconds() / 60
    )
    remaining_travel = max(0, adjusted_eta - elapsed_minutes)
    predicted_arrival = current_datetime + timedelta(minutes=remaining_travel)

    # SLA deadline from scheduled time
    sla_deadline = scheduled_datetime + timedelta(minutes=sla_window_minutes)
    minutes_to_deadline = (sla_deadline - current_datetime).total_seconds() / 60
    breach_margin = round(minutes_to_deadline - remaining_travel)

    will_breach = breach_margin < 0
    confidence  = (
        "High"   if abs(breach_margin) > 10 else
        "Medium" if abs(breach_margin) > 3  else
        "Low"
    )

    return {
        "will_breach":            will_breach,
        "minutes_remaining":      round(minutes_to_deadline),
        "predicted_arrival":      predicted_arrival.isoformat(),
        "breach_margin_minutes":  breach_margin,
        "adjusted_eta_minutes":   adjusted_eta,
        "confidence":             confidence,
        "reason": (
            f"ETA {adjusted_eta}min, SLA deadline in {round(minutes_to_deadline)}min. "
            f"Margin: {breach_margin:+d}min"
        ),
    }


# ── 3. Multi-Stop Route Sequencer ─────────────────────────────────────────────

PRIORITY_ORDER: dict[str, int] = {
    "Emergency":   1,
    "Refrigerated": 2,
    "Routine":     3,
}


def sequence_multi_stop(
    stops: list[dict[str, Any]],
    vehicle_type: str,
    vehicle_capacity_kg: float,
) -> dict[str, Any]:
    """
    Given a list of pending deliveries for one vehicle, return an optimal
    stop sequence with cumulative ETA and SLA risk per stop.

    Each stop dict must have:
      delivery_id, delivery_category, destination, base_duration_minutes,
      route_type, sla_window_minutes, payload_kg (optional)

    Returns:
      sequence:       ordered list of stops
      total_eta_minutes
      sla_violations: list of stops that will breach SLA
      warnings:       capacity / compatibility issues
    """
    warnings: list[str] = []

    # Filter incompatible stops
    valid_stops = []
    for stop in stops:
        cat = stop.get("delivery_category", "Routine")
        compat = check_vehicle_compatibility(
            vehicle_type,
            vehicle_capacity_kg,
            cat,
            stop.get("payload_kg", 0.0),
        )
        if compat["compatible"]:
            valid_stops.append(stop)
        else:
            warnings.append(
                f"Stop {stop.get('delivery_id')} skipped: {compat['reason']}"
            )

    # Sort by priority then SLA window (tightest first within same priority)
    sorted_stops = sorted(
        valid_stops,
        key=lambda s: (
            PRIORITY_ORDER.get(s.get("delivery_category", "Routine"), 99),
            s.get("sla_window_minutes", 120),
        ),
    )

    now = datetime.now()
    cumulative_minutes = 0
    sequenced: list[dict] = []
    sla_violations: list[str] = []

    for i, stop in enumerate(sorted_stops):
        dispatch_hour = (now + timedelta(minutes=cumulative_minutes)).hour
        eta = compute_adjusted_eta(
            stop.get("base_duration_minutes", 30),
            stop.get("route_type", "Urban"),
            dispatch_hour,
        )
        cumulative_minutes += eta
        sla_window = stop.get("sla_window_minutes", 120)
        will_breach = cumulative_minutes > sla_window

        if will_breach:
            sla_violations.append(stop.get("delivery_id", f"stop_{i}"))

        sequenced.append({
            **stop,
            "sequence_position":      i + 1,
            "cumulative_eta_minutes": cumulative_minutes,
            "leg_eta_minutes":        eta,
            "will_breach_sla":        will_breach,
            "sla_margin_minutes":     sla_window - cumulative_minutes,
        })

    return {
        "sequence":            sequenced,
        "total_eta_minutes":   cumulative_minutes,
        "stop_count":          len(sequenced),
        "sla_violations":      sla_violations,
        "warnings":            warnings,
    }


# ── 4. Driver Fatigue / Shift Limit Check ─────────────────────────────────────

def check_driver_fatigue(deliveries_today: int) -> dict[str, Any]:
    """
    Returns fatigue status and whether the vehicle should be reassigned.
    """
    if deliveries_today >= MAX_DELIVERIES_PER_SHIFT:
        return {
            "fatigued":         True,
            "deliveries_today": deliveries_today,
            "max_per_shift":    MAX_DELIVERIES_PER_SHIFT,
            "recommendation":   "Reassign to a rested vehicle. Driver has reached shift limit.",
        }
    remaining = MAX_DELIVERIES_PER_SHIFT - deliveries_today
    return {
        "fatigued":          False,
        "deliveries_today":  deliveries_today,
        "max_per_shift":     MAX_DELIVERIES_PER_SHIFT,
        "remaining_capacity": remaining,
        "recommendation":    f"Vehicle can take {remaining} more delivery/deliveries this shift.",
    }


# ── 5. Three-Way Trade-Off Scorer ─────────────────────────────────────────────

def score_route_options(
    routes: list[dict[str, Any]],
    delivery_category: str,
    dispatch_hour: int,
) -> dict[str, Any]:
    """
    Given multiple candidate routes, score each as:
      - Cheapest    (lowest cost)
      - Fastest     (lowest adjusted ETA)
      - Safest SLA  (largest SLA margin)

    Returns the three recommended options with justification.
    For Emergency, always recommend Fastest as primary.
    """
    if not routes:
        return {"error": "No routes provided"}

    sla_window = SLA_WINDOWS.get(delivery_category, 120)

    scored = []
    for r in routes:
        base_dur  = r.get("estimated_duration_minutes", 30)
        route_type = r.get("route_type", "Urban")
        adj_eta   = compute_adjusted_eta(base_dur, route_type, dispatch_hour)
        cost      = r.get("estimated_cost", 0.0)
        sla_margin = sla_window - adj_eta

        scored.append({
            **r,
            "adjusted_eta_minutes": adj_eta,
            "sla_margin_minutes":   sla_margin,
            "will_breach_sla":      sla_margin < 0,
            "cost":                 cost,
        })

    cheapest  = min(scored, key=lambda x: x["cost"])
    fastest   = min(scored, key=lambda x: x["adjusted_eta_minutes"])
    safest    = max(scored, key=lambda x: x["sla_margin_minutes"])

    # Primary recommendation depends on delivery category
    if delivery_category == "Emergency":
        primary = fastest
        primary_reason = "Emergency deliveries always prioritise speed over cost."
    elif delivery_category == "Refrigerated":
        primary = safest
        primary_reason = "Refrigerated deliveries prioritise SLA compliance to protect cargo integrity."
    else:
        primary = cheapest
        primary_reason = "Routine deliveries optimise for cost efficiency."

    return {
        "delivery_category":   delivery_category,
        "sla_window_minutes":  sla_window,
        "primary_recommendation": {
            "option":   primary,
            "reason":   primary_reason,
        },
        "options": {
            "cheapest": {
                "route":  cheapest,
                "reason": f"Lowest cost at ₹{cheapest['cost']:,.0f}",
            },
            "fastest": {
                "route":  fastest,
                "reason": f"Shortest ETA at {fastest['adjusted_eta_minutes']}min",
            },
            "safest_sla": {
                "route":  safest,
                "reason": (
                    f"Largest SLA margin at {safest['sla_margin_minutes']}min"
                    if safest["sla_margin_minutes"] >= 0
                    else f"All routes breach SLA — least late by {abs(safest['sla_margin_minutes'])}min"
                ),
            },
        },
        "all_routes_scored": scored,
    }


# ── 6. Full Dispatch Assessment (orchestrates all checks) ─────────────────────

async def run_dispatch_assessment(
    delivery_id: str,
    db: AsyncSession,
) -> dict[str, Any]:
    """
    Run a complete production-grade dispatch assessment for one delivery.
    Fetches all needed data from DB and runs every check.
    Returns a comprehensive assessment dict that the LLM agent can explain.
    """
    result = await db.execute(text("""
        SELECT
            dr.delivery_id,
            dr.delivery_category,
            dr.scheduled_datetime,
            dr.sla_window_minutes,
            dr.estimated_cost,
            dr.delivery_status,
            dr.sla_breached,
            dr.delay_reason,
            v.vehicle_id,
            v.vehicle_type,
            v.capacity_kg,
            v.available,
            v.base_location,
            r.route_id,
            r.origin,
            r.destination,
            r.distance_km,
            r.estimated_duration_minutes,
            r.route_type,
            r.traffic_factor,
            COUNT(dr2.delivery_id) AS deliveries_today
        FROM delivery_records dr
        JOIN vehicles v ON dr.vehicle_id = v.vehicle_id
        JOIN routes r   ON dr.route_id   = r.route_id
        LEFT JOIN delivery_records dr2
            ON dr2.vehicle_id = v.vehicle_id
            AND DATE(dr2.scheduled_datetime) = DATE('now')
        WHERE dr.delivery_id = :delivery_id
        GROUP BY dr.delivery_id
    """), {"delivery_id": delivery_id})

    row = result.mappings().fetchone()
    if not row:
        return {"error": f"Delivery {delivery_id} not found"}

    d = dict(row)
    now = datetime.now()
    scheduled = datetime.fromisoformat(str(d["scheduled_datetime"]))

    # 1. Vehicle compatibility
    compatibility = check_vehicle_compatibility(
        d["vehicle_type"], d["capacity_kg"], d["delivery_category"]
    )

    # 2. Time-of-day ETA
    dispatch_hour = scheduled.hour
    adjusted_eta  = compute_adjusted_eta(
        d["estimated_duration_minutes"], d["route_type"], dispatch_hour
    )

    # 3. Predictive SLA breach
    prediction = predict_sla_breach(
        scheduled, now,
        d["estimated_duration_minutes"],
        d["route_type"],
        d["sla_window_minutes"],
        d["delivery_status"],
    )

    # 4. Driver fatigue
    fatigue = check_driver_fatigue(d["deliveries_today"])

    # 5. Alternative routes for trade-off scoring
    alt_routes_result = await db.execute(text("""
        SELECT route_id, origin, destination, distance_km,
               estimated_duration_minutes, route_type, estimated_cost
        FROM routes
        WHERE origin = :origin AND destination = :destination
        ORDER BY estimated_duration_minutes ASC
        LIMIT 5
    """), {"origin": d["origin"], "destination": d["destination"]})

    alt_route_rows = [
        {**dict(r._mapping), "estimated_cost": d["estimated_cost"]}
        for r in alt_routes_result.fetchall()
    ]
    trade_off = score_route_options(alt_route_rows, d["delivery_category"], dispatch_hour)

    assessment = {
        "delivery_id":      delivery_id,
        "delivery_category": d["delivery_category"],
        "origin":           d["origin"],
        "destination":      d["destination"],
        "delivery_status":  d["delivery_status"],
        "vehicle": {
            "vehicle_id":    d["vehicle_id"],
            "vehicle_type":  d["vehicle_type"],
            "capacity_kg":   d["capacity_kg"],
            "base_location": d["base_location"],
        },
        "compatibility_check": compatibility,
        "time_of_day_eta": {
            "dispatch_hour":        dispatch_hour,
            "adjusted_eta_minutes": adjusted_eta,
            "traffic_factor_used":  get_traffic_factor(d["route_type"], dispatch_hour),
            "route_type":           d["route_type"],
        },
        "sla_prediction":   prediction,
        "fatigue_check":    fatigue,
        "route_trade_off":  trade_off,
        "flags": {
            "vehicle_incompatible":  not compatibility["compatible"],
            "sla_breach_predicted":  prediction.get("will_breach", False),
            "driver_fatigued":       fatigue["fatigued"],
            "currently_delayed":     d["delivery_status"] == "Delayed",
        },
    }

    # Overall risk
    flag_count = sum(1 for v in assessment["flags"].values() if v)
    assessment["overall_risk"] = (
        "Critical" if flag_count >= 3 else
        "High"     if flag_count == 2 else
        "Medium"   if flag_count == 1 else
        "Low"
    )

    logger.info(
        f"Dispatch assessment: {delivery_id} | "
        f"risk={assessment['overall_risk']} | "
        f"flags={assessment['flags']}"
    )
    return assessment