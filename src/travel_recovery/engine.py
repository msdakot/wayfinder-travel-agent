"""Deterministic checks. No LLM, live inventory, or real benefit decisions."""
from datetime import datetime, timedelta
from math import isfinite


def timestamp(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("Trip timestamps must include a UTC offset")
    return result


def recover(trip, event):
    if trip.get("simulation") is not True:
        raise ValueError("This validator currently requires a labeled synthetic itinerary")
    if trip["preferences"].get("currency") != "USD":
        raise ValueError("This POC validates USD prices only")
    budget = trip["preferences"]["maximum_additional_spend"]
    if isinstance(budget, bool) or not isinstance(budget, (float, int)) or not isfinite(budget) or budget < 0:
        raise ValueError("Budget must be a nonnegative finite amount")
    buffer = trip["connection"]["minimum_transfer_minutes"]
    if isinstance(buffer, bool) or not isinstance(buffer, int) or not 0 <= buffer <= 1440:
        raise ValueError("Transfer buffer must be an integer between 0 and 1440")
    if event.get("flight_id") != trip["flight"]["id"]:
        raise ValueError("Event does not match the trip flight")
    if event.get("type") != "delay":
        raise ValueError("This POC supports delay events only")
    arrival = timestamp(event["arrival"])
    original = timestamp(trip["flight"]["arrival"])
    delay = max(0, int((arrival - original).total_seconds() / 60))
    ready = arrival + timedelta(minutes=trip["connection"]["minimum_transfer_minutes"])
    missed = ready > timestamp(trip["connection"]["departure"])
    options = []
    seen = set()
    if missed:
        for option in trip["alternatives"]:
            reasons = []
            if option["id"] in seen:
                raise ValueError("Duplicate alternative identifier")
            seen.add(option["id"])
            available = option.get("available")
            if available is not None and type(available) is not bool:
                raise ValueError("Availability must be true, false, or unknown")
            cost = option["cost"]
            if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not isfinite(cost) or cost < 0:
                raise ValueError("Alternative price must be nonnegative and finite")
            if option.get("currency", "USD") != "USD":
                raise ValueError("Mixed currencies require a verified FX conversion")
            if timestamp(option["hotel_arrival"]) < timestamp(option["departure"]):
                raise ValueError("Hotel arrival cannot precede departure")
            if available is False:
                reasons.append("Unavailable in simulated inventory")
            if available is None:
                reasons.append("Availability is unknown; confirmation required")
            if timestamp(option["departure"]) < ready:
                reasons.append("Insufficient transfer time")
            if timestamp(option["hotel_arrival"]) > timestamp(trip["hotel"]["check_in_deadline"]):
                reasons.append("Arrives after hotel check-in deadline")
            if option["cost"] > trip["preferences"]["maximum_additional_spend"]:
                reasons.append("Exceeds additional spending limit")
            options.append({**option, "feasible": not reasons, "rejection_reasons": reasons})
    valid = sorted((o for o in options if o["feasible"]), key=lambda o: (o["cost"], timestamp(o["hotel_arrival"])))
    policy = trip["benefit"]
    if policy["booking_paid_with_eligible_card"] is not None and type(policy["booking_paid_with_eligible_card"]) is not bool:
        raise ValueError("Payment qualification must be true, false, or unknown")
    if delay < policy["minimum_delay_minutes"] or policy["booking_paid_with_eligible_card"] is False:
        benefit_status = "demo_conditions_not_met"
    elif event.get("cause") is None or policy["booking_paid_with_eligible_card"] is None:
        benefit_status = "needs_more_information"
    elif event["cause"] not in policy["covered_causes"]:
        benefit_status = "demo_conditions_not_met"
    else:
        benefit_status = "potentially_eligible_requires_review"
    return {
        "simulation": True,
        "trip_id": trip["id"],
        "event_id": event.get("id"),
        "delay_minutes": delay,
        "connection_missed": missed,
        "ready_for_transfer": ready.isoformat(),
        "status": "recovery_options_found" if valid else ("needs_manual_assistance" if missed else "original_connection_feasible"),
        "ranking": "Lowest additional cost among feasible options; earliest arrival breaks ties",
        "recommended_option": valid[0]["id"] if valid else None,
        "options": options,
        "benefit_assessment": {"status": benefit_status, "source": policy["source"], "note": policy["note"], "required_evidence": policy["required_evidence"]},
        "execution": "No bookings have been changed. All availability and prices are simulated."
    }
