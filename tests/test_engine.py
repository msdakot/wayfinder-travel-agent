import copy
import json
from pathlib import Path
import unittest
from travel_recovery.engine import recover

class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.trip = json.loads((Path(__file__).resolve().parents[1] / "data/demo_trip.json").read_text())
        self.event = {"type": "delay", "flight_id": "DEMO101", "arrival": "2026-11-07T20:00:00+00:00", "cause": "weather"}

    def test_delay_rejects_cheaper_option_that_misses_hotel_deadline(self):
        result = recover(self.trip, self.event)
        self.assertTrue(result["connection_missed"])
        self.assertEqual(result["recommended_option"], "later-train")
        self.assertFalse(next(o for o in result["options"] if o["id"] == "late-bus")["feasible"])

    def test_inventory_change_selects_remaining_feasible_option(self):
        self.trip["alternatives"][0]["available"] = False
        self.assertEqual(recover(self.trip, self.event)["recommended_option"], "taxi")

    def test_no_feasible_option_escalates(self):
        self.trip["preferences"]["maximum_additional_spend"] = 10
        self.assertEqual(recover(self.trip, self.event)["status"], "needs_manual_assistance")

    def test_small_delay_does_not_propose_unnecessary_changes(self):
        self.event["arrival"] = "2026-11-07T18:10:00+00:00"
        self.assertEqual(recover(self.trip, self.event)["options"], [])

    def test_offsets_compare_as_instants(self):
        self.event["arrival"] = "2026-11-07T15:00:00-05:00"
        self.assertEqual(recover(self.trip, self.event)["delay_minutes"], 120)

    def test_unknown_cause_never_claims_eligibility(self):
        self.event.update(arrival="2026-11-07T21:00:00+00:00", cause=None)
        self.assertEqual(recover(self.trip, self.event)["benefit_assessment"]["status"], "needs_more_information")

    def test_wrong_flight_rejected(self):
        self.event["flight_id"] = "OTHER"
        with self.assertRaises(ValueError):
            recover(self.trip, self.event)

    def test_does_not_mutate_trip(self):
        original = copy.deepcopy(self.trip)
        recover(self.trip, self.event)
        self.assertEqual(self.trip, original)

if __name__ == "__main__":
    unittest.main()
