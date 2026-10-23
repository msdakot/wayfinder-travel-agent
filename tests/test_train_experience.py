from pathlib import Path
import tempfile
import unittest

from travel_recovery.store import Store


class TrainExperienceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.temp.name)/"state.db")
        self.snap=self.store.create("London",demo=True)
        self.trip_id=self.snap["trip"]["id"]

    def tearDown(self):
        self.temp.cleanup()

    def test_reviewed_ticket_and_station_guidance_are_exposed(self):
        train=self.snap["train_experience"]
        self.assertEqual(train["status"]["state"],"scheduled")
        self.assertFalse(train["freshness"]["live"])
        self.assertIn("no live rail feed",train["freshness"]["label"])
        self.assertTrue(train["ticket"]["booked"])
        self.assertEqual(train["ticket"]["passenger"],"Alex Traveler")
        self.assertEqual(train["ticket"]["booking_reference"],"DEMO-TRAIN")
        self.assertEqual(train["ticket"]["valid_for"],"2026-11-07T19:00:00+00:00")
        station=train["station_departure"]
        self.assertEqual(station["recommended_leave_at"],"2026-11-07T18:15:00+00:00")
        self.assertEqual(station["travel_minutes"],15)
        self.assertIn("Fixed POC estimate",station["assumption"])

    def test_last_mile_options_are_structured_and_budget_checked(self):
        options=self.snap["train_experience"]["last_mile"]
        self.assertEqual([option["id"] for option in options],["walk","bus","taxi"])
        self.assertEqual(sum(bool(option["recommended"]) for option in options),1)
        self.assertTrue(all(option["feasibility"]=="feasible" for option in options))
        self.store.budget(self.trip_id,10)
        options=self.store.snapshot(self.trip_id)["train_experience"]["last_mile"]
        taxi=next(option for option in options if option["id"]=="taxi")
        self.assertEqual(taxi["feasibility"],"blocked")
        self.assertIn("spend limit",taxi["reasons"][0])

    def test_missed_train_blocks_last_mile_until_recovery_selected(self):
        delayed=self.store.replay(self.trip_id,minutes=120,key="train-detail-delay")
        train=delayed["train_experience"]
        self.assertEqual(train["status"]["state"],"missed")
        self.assertEqual(train["original_connection"]["state"],"missed")
        self.assertEqual(train["original_connection"]["departure_at"],"2026-11-07T19:00:00+00:00")
        self.assertIsNone(train["recovery_connection"])
        self.assertTrue(all(option["feasibility"]=="blocked" for option in train["last_mile"]))
        self.store.approve(self.trip_id,delayed["plan"]["id"],"later-train")
        train=self.store.snapshot(self.trip_id)["train_experience"]
        self.assertEqual(train["status"]["state"],"replacement_selected")
        self.assertEqual(train["original_connection"]["state"],"missed")
        self.assertEqual(train["recovery_connection"]["mode"],"train")
        self.assertEqual(train["recovery_connection"]["departure_at"],"2026-11-07T21:15:00+00:00")
        self.assertEqual(train["station_departure"]["departure_at"],"2026-11-07T21:15:00+00:00")
        self.assertTrue(any(option["feasibility"]=="feasible" for option in train["last_mile"]))

    def test_without_reviewed_connection_train_experience_is_absent(self):
        empty=self.store.create("Empty",demo=False)
        self.assertIsNone(empty["train_experience"])


if __name__=="__main__":
    unittest.main()
