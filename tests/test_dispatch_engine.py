from datetime import datetime, timedelta
import pytest

from app.agents.dispatch_engine import (
    check_vehicle_compatibility,
    check_driver_fatigue,
    compute_adjusted_eta,
    get_traffic_factor,
    predict_sla_breach,
    sequence_multi_stop,
    score_route_options,
    TRAFFIC_SCHEDULE,
    SLA_WINDOWS,
    VEHICLE_COMPATIBILITY,
    MAX_DELIVERIES_PER_SHIFT,
)



class TestVehicleCompatibility:

    def test_ambulance_ok_for_emergency(self):
        r = check_vehicle_compatibility("Ambulance", 500, "Emergency")
        assert r["compatible"] is True

    def test_motorcycle_blocked_for_emergency(self):
        r = check_vehicle_compatibility("Motorcycle", 50, "Emergency")
        assert r["compatible"] is False
        assert "not permitted" in r["reason"]

    def test_refrigerated_only_for_refrigerated(self):
        r = check_vehicle_compatibility("Van", 500, "Refrigerated")
        assert r["compatible"] is False

        r2 = check_vehicle_compatibility("Refrigerated", 500, "Refrigerated")
        assert r2["compatible"] is True

    def test_capacity_too_low_blocked(self):
        # Emergency needs 200kg min, van with 100kg should fail
        r = check_vehicle_compatibility("Van", 100, "Emergency")
        assert r["compatible"] is False
        assert "capacity" in r["reason"].lower()

    def test_payload_exceeds_capacity_blocked(self):
        r = check_vehicle_compatibility("Van", 300, "Routine", payload_kg=400)
        assert r["compatible"] is False
        assert "Payload" in r["reason"]

    def test_motorcycle_ok_for_routine(self):
        r = check_vehicle_compatibility("Motorcycle", 50, "Routine")
        assert r["compatible"] is True

    def test_all_vehicle_types_covered(self):
        for category, types in VEHICLE_COMPATIBILITY.items():
            for vtype in types:
                r = check_vehicle_compatibility(vtype, 1000, category)
                assert r["compatible"] is True, (
                    f"{vtype} should be compatible with {category}"
                )



class TestTrafficFactor:

    def test_urban_peak_morning(self):
        factor = get_traffic_factor("Urban", 9)
        assert factor >= 1.7, "Urban at 9am should be peak traffic"

    def test_urban_night_low(self):
        factor = get_traffic_factor("Urban", 2)
        assert factor < 1.0, "Urban at 2am should be below baseline"

    def test_highway_lower_than_urban_peak(self):
        urban   = get_traffic_factor("Urban",   9)
        highway = get_traffic_factor("Highway", 9)
        assert highway < urban, "Highway peak should be lower than Urban peak"

    def test_rural_mostly_flat(self):
        factors = [get_traffic_factor("Rural", h) for h in range(24)]
        assert max(factors) - min(factors) < 0.2, (
            "Rural traffic should not vary much"
        )

    def test_unknown_route_type_returns_1(self):
        factor = get_traffic_factor("Unknown", 12)
        assert factor == 1.0

    def test_all_hours_covered(self):
        for hour in range(24):
            f = get_traffic_factor("Urban", hour)
            assert 0.5 < f < 3.0, f"Unexpected factor {f} for hour {hour}"



class TestAdjustedETA:

    def test_peak_eta_longer_than_off_peak(self):
        off_peak = compute_adjusted_eta(30, "Urban", 3)   # 3am
        peak     = compute_adjusted_eta(30, "Urban", 9)   # 9am
        assert peak > off_peak

    def test_base_duration_scales(self):
        eta_30 = compute_adjusted_eta(30, "Highway", 12)
        eta_60 = compute_adjusted_eta(60, "Highway", 12)
        assert eta_60 == pytest.approx(eta_30 * 2, abs=2)

    def test_returns_integer(self):
        eta = compute_adjusted_eta(25, "Urban", 8)
        assert isinstance(eta, int)



class TestSLAPrediction:

    def _make_prediction(
        self,
        base_duration=20,
        elapsed_minutes=5,
        sla_window=30,
        route_type="Urban",
        dispatch_hour=9,
        status="InTransit",
    ):
        now       = datetime.now().replace(hour=dispatch_hour, minute=0, second=0)
        scheduled = now - timedelta(minutes=elapsed_minutes)
        return predict_sla_breach(
            scheduled_datetime=scheduled,
            current_datetime=now,
            base_duration_minutes=base_duration,
            route_type=route_type,
            sla_window_minutes=sla_window,
            delivery_status=status,
        )

    def test_safe_delivery_no_breach(self):
        # 10min base, 60min SLA — should be safe
        r = self._make_prediction(base_duration=10, sla_window=60, dispatch_hour=3)
        assert r["will_breach"] is False
        assert r["breach_margin_minutes"] > 0

    def test_certain_breach_detected(self):
        # 60min base at peak, only 30min SLA
        r = self._make_prediction(
            base_duration=60, sla_window=30, dispatch_hour=9, elapsed_minutes=2
        )
        assert r["will_breach"] is True
        assert r["breach_margin_minutes"] < 0

    def test_prediction_skipped_for_delivered(self):
        r = self._make_prediction(status="Delivered")
        assert r["will_breach"] is False
        assert "not applicable" in r["reason"]

    def test_high_confidence_when_large_margin(self):
        r = self._make_prediction(base_duration=5, sla_window=120, dispatch_hour=3)
        assert r["confidence"] == "High"

    def test_low_confidence_when_tight_margin(self):
        # ETA right on the SLA edge
        r = self._make_prediction(base_duration=28, sla_window=30, dispatch_hour=3)
        assert r["confidence"] in ("Low", "Medium")

    def test_predicted_arrival_is_future(self):
        r = self._make_prediction()
        if r["predicted_arrival"]:
            arrival = datetime.fromisoformat(r["predicted_arrival"])
            assert arrival > datetime.now() - timedelta(seconds=5)



class TestDriverFatigue:

    def test_zero_deliveries_not_fatigued(self):
        r = check_driver_fatigue(0)
        assert r["fatigued"] is False
        assert r["remaining_capacity"] == MAX_DELIVERIES_PER_SHIFT

    def test_at_limit_fatigued(self):
        r = check_driver_fatigue(MAX_DELIVERIES_PER_SHIFT)
        assert r["fatigued"] is True
        assert "Reassign" in r["recommendation"]

    def test_one_below_limit_not_fatigued(self):
        r = check_driver_fatigue(MAX_DELIVERIES_PER_SHIFT - 1)
        assert r["fatigued"] is False
        assert r["remaining_capacity"] == 1

    def test_over_limit_still_fatigued(self):
        r = check_driver_fatigue(MAX_DELIVERIES_PER_SHIFT + 5)
        assert r["fatigued"] is True



class TestMultiStopSequencer:

    def _make_stops(self):
        return [
            {
                "delivery_id":           "DEL001",
                "delivery_category":     "Routine",
                "destination":           "Karol Bagh Depot",
                "base_duration_minutes": 40,
                "route_type":            "Urban",
                "sla_window_minutes":    120,
                "payload_kg":            50,
            },
            {
                "delivery_id":           "DEL002",
                "delivery_category":     "Emergency",
                "destination":           "AIIMS Delhi",
                "base_duration_minutes": 20,
                "route_type":            "Highway",
                "sla_window_minutes":    30,
                "payload_kg":            100,
            },
            {
                "delivery_id":           "DEL003",
                "delivery_category":     "Refrigerated",
                "destination":           "Max Hospital Saket",
                "base_duration_minutes": 25,
                "route_type":            "Urban",
                "sla_window_minutes":    60,
                "payload_kg":            200,
            },
        ]

    def test_emergency_scheduled_first(self):
        result = sequence_multi_stop(
            self._make_stops(), "Ambulance", 500
        )
        seq = result["sequence"]
        assert seq[0]["delivery_id"] == "DEL002", (
            "Emergency should always be first in sequence"
        )

    def test_incompatible_stops_filtered(self):
        stops = self._make_stops()
        # Motorcycle can't do Refrigerated
        result = sequence_multi_stop(stops, "Motorcycle", 50)
        ids = [s["delivery_id"] for s in result["sequence"]]
        assert "DEL003" not in ids, "Refrigerated stop should be filtered for Motorcycle"
        assert len(result["warnings"]) > 0

    def test_cumulative_eta_increases(self):
        result = sequence_multi_stop(self._make_stops(), "Ambulance", 500)
        etas = [s["cumulative_eta_minutes"] for s in result["sequence"]]
        assert etas == sorted(etas), "Cumulative ETA must always increase"

    def test_sla_violations_detected(self):
        # Short SLA on a long stop — should trigger violation
        stops = [{
            "delivery_id":           "DEL_SLOW",
            "delivery_category":     "Emergency",
            "destination":           "Rohini",
            "base_duration_minutes": 60,
            "route_type":            "Urban",
            "sla_window_minutes":    15,
            "payload_kg":            50,
        }]
        result = sequence_multi_stop(stops, "Ambulance", 500)
        assert "DEL_SLOW" in result["sla_violations"]

    def test_total_eta_equals_last_cumulative(self):
        result = sequence_multi_stop(self._make_stops(), "Ambulance", 500)
        if result["sequence"]:
            last_eta = result["sequence"][-1]["cumulative_eta_minutes"]
            assert result["total_eta_minutes"] == last_eta


# ── 7. Three-Way Trade-Off ────────────────────────────────────────────────────

class TestTradeOffScorer:

    def _routes(self):
        return [
            {
                "route_id": "RTE001", "origin": "A", "destination": "B",
                "estimated_duration_minutes": 20, "route_type": "Highway",
                "estimated_cost": 5000, "distance_km": 15,
            },
            {
                "route_id": "RTE002", "origin": "A", "destination": "B",
                "estimated_duration_minutes": 35, "route_type": "Urban",
                "estimated_cost": 1200, "distance_km": 12,
            },
            {
                "route_id": "RTE003", "origin": "A", "destination": "B",
                "estimated_duration_minutes": 28, "route_type": "Rural",
                "estimated_cost": 2000, "distance_km": 18,
            },
        ]

    def test_emergency_picks_fastest(self):
        result = score_route_options(self._routes(), "Emergency", 12)
        fastest_id = result["options"]["fastest"]["route"]["route_id"]
        primary_id = result["primary_recommendation"]["option"]["route_id"]
        assert primary_id == fastest_id

    def test_routine_picks_cheapest(self):
        result = score_route_options(self._routes(), "Routine", 12)
        cheapest_id = result["options"]["cheapest"]["route"]["route_id"]
        primary_id  = result["primary_recommendation"]["option"]["route_id"]
        assert primary_id == cheapest_id

    def test_refrigerated_picks_safest_sla(self):
        result = score_route_options(self._routes(), "Refrigerated", 12)
        safest_id = result["options"]["safest_sla"]["route"]["route_id"]
        primary_id = result["primary_recommendation"]["option"]["route_id"]
        assert primary_id == safest_id

    def test_all_three_options_present(self):
        result = score_route_options(self._routes(), "Routine", 12)
        assert "cheapest"    in result["options"]
        assert "fastest"     in result["options"]
        assert "safest_sla"  in result["options"]

    def test_empty_routes_returns_error(self):
        result = score_route_options([], "Emergency", 9)
        assert "error" in result

    def test_sla_window_correct_per_category(self):
        for cat, window in SLA_WINDOWS.items():
            result = score_route_options(self._routes(), cat, 12)
            assert result["sla_window_minutes"] == window