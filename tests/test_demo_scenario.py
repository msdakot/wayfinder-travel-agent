import json
import http.cookiejar
from pathlib import Path
import secrets
import socket
import tempfile
import threading
import unittest
import urllib.request

from travel_recovery.answers import answer
from travel_recovery.server import make_handler
from travel_recovery.store import ROOT, Store
from http.server import ThreadingHTTPServer


class DeterministicDemoScenarioTests(unittest.TestCase):
    """Proves the exact on-time -> delayed storyline used in the demo."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "state.db")
        self.snapshot = self.store.create("A long weekend in London", demo=True)
        self.trip_id = self.snapshot["trip"]["id"]
        self.fixture = json.loads((ROOT / "data/demo_status_sequence.json").read_text())

    def tearDown(self):
        self.temp.cleanup()

    def test_on_time_then_delayed_recalculates_and_drives_notifications(self):
        self.assertIsNone(self.snapshot["event"])
        self.assertIsNone(self.snapshot["plan"])

        observed = []
        for update in self.fixture["updates"]:
            snapshot = self.store.replay(
                self.trip_id,
                minutes=update["delay_minutes"],
                cause=update["cause"],
                key=update["event_key"],
            )
            expected = update["expected"]
            if update["delay_minutes"] == 0:
                self.assertIsNone(snapshot["plan"])
                self.assertEqual(snapshot["event"]["status"], "on_time")
                connection_missed = False
            else:
                result = snapshot["plan"]["result"]
                self.assertEqual(result["delay_minutes"], update["delay_minutes"])
                self.assertEqual(result["status"], expected["status"])
                self.assertEqual(result["connection_missed"], expected["connection_missed"])
                self.assertEqual(result["recommended_option"], expected["recommended_option"])
                self.assertEqual(len(result["options"]), expected["option_count"])
                connection_missed = result["connection_missed"]

            self.assertEqual(snapshot["notifications"][0]["title"], expected["persisted_notification_title"])
            self.assertEqual(snapshot["notifications"][0]["source_mode"], "simulated_replay")

            # These two values are the state inputs used by web/app.js to render
            # the in-app notification title, unread dot, and recovery link.
            notification_title = (
                "Delay affects your onward journey"
                if connection_missed
                else "Flight status changed"
            )
            self.assertEqual(notification_title, expected["notification_title"])
            self.assertEqual(bool(connection_missed), expected["notification_urgent"])
            observed.append(snapshot)

        on_time, delayed = observed
        self.assertIsNone(on_time["plan"])
        self.assertEqual(on_time["event"]["status"], "on_time")

        result = delayed["plan"]["result"]
        feasible = [option["id"] for option in result["options"] if option["feasible"]]
        rejected = [option["id"] for option in result["options"] if not option["feasible"]]
        self.assertEqual(feasible, self.fixture["updates"][1]["expected"]["feasible_options"])
        self.assertEqual(rejected, self.fixture["updates"][1]["expected"]["rejected_options"])
        self.assertIn("check-in deadline", result["options"][2]["rejection_reasons"][0])

        # Both provider-style updates remain in the audit log while the latest
        # event and plan are the delayed state shown by the UI.
        transition_logs = [item for item in delayed["logs"] if item["kind"] == "flight_status_transition"]
        self.assertEqual(len(transition_logs), 2)
        self.assertEqual(delayed["event"]["provider_event_id"], "demo-status-delayed-120")
        self.assertEqual(delayed["plan"]["event_id"], delayed["event"]["id"])

    def test_delayed_answer_uses_recalculated_plan_and_reviewed_sources(self):
        delayed = self.store.replay(
            self.trip_id,
            minutes=120,
            cause="weather",
            key="demo-answer-delayed-120",
        )
        response = answer(delayed, "What should I do now that the flight is delayed?")
        self.assertIn("original train is no longer reachable", response["text"])
        self.assertIn("$45 USD", response["text"])
        self.assertGreaterEqual(len(response["citations"]), 3)
        reviewed_ids = {
            document["id"]
            for document in delayed["documents"]
            if document["state"] == "reviewed"
        }
        self.assertTrue({item["id"] for item in response["citations"]} <= reviewed_ids)

    def test_replaying_same_status_update_is_idempotent(self):
        update = self.fixture["updates"][1]
        self.store.replay(
            self.trip_id,
            minutes=update["delay_minutes"],
            cause=update["cause"],
            key=update["event_key"],
        )
        duplicate = self.store.replay(
            self.trip_id,
            minutes=update["delay_minutes"],
            cause=update["cause"],
            key=update["event_key"],
        )
        self.assertEqual(duplicate, {"duplicate": True})
        snapshot = self.store.snapshot(self.trip_id)
        transition_logs = [item for item in snapshot["logs"] if item["kind"] == "flight_status_transition"]
        self.assertEqual(len(transition_logs), 1)
        self.assertEqual(len(snapshot["notifications"]), 1)

    def test_browser_api_sequence_exposes_latest_recalculated_state(self):
        """Exercises the same JSON boundary used by the browser demo."""
        api_store = Store(Path(self.temp.name) / "api-state.db")
        with socket.socket() as available:
            available.bind(("127.0.0.1", 0))
            port = available.getsockname()[1]
        server = ThreadingHTTPServer(
            ("127.0.0.1", port),
            make_handler(api_store, secrets.token_urlsafe(24), port),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        jar = http.cookiejar.CookieJar()
        browser = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        origin = f"http://127.0.0.1:{port}"

        def post(path, payload):
            request = urllib.request.Request(
                origin + path,
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json", "Origin": origin},
                method="POST",
            )
            with browser.open(request, timeout=5) as response:
                return json.load(response)

        try:
            browser.open(origin + "/", timeout=5).read()  # establishes local session cookie
            created = post("/api/trips", {"title": "API status sequence", "demo": True})
            trip_id = created["trip"]["id"]
            on_time = post(
                f"/api/trips/{trip_id}/replay",
                {"minutes": 0, "cause": "weather", "event_key": "api-on-time"},
            )
            self.assertIsNone(on_time["plan"])
            self.assertEqual(on_time["event"]["status"], "on_time")
            self.assertEqual(on_time["notifications"][0]["title"], "Flight on time")

            delayed = post(
                f"/api/trips/{trip_id}/replay",
                {"minutes": 120, "cause": "weather", "event_key": "api-delayed-120"},
            )
            self.assertTrue(delayed["plan"]["result"]["connection_missed"])
            self.assertEqual(delayed["plan"]["result"]["recommended_option"], "later-train")
            with browser.open(origin + f"/api/trips/{trip_id}", timeout=5) as response:
                refreshed = json.load(response)
            self.assertEqual(refreshed["event"]["provider_event_id"], "api-delayed-120")
            self.assertEqual(refreshed["plan"]["event_id"], refreshed["event"]["id"])
            self.assertEqual(refreshed["plan"]["result"]["recommended_option"], "later-train")
            self.assertEqual(refreshed["notifications"][0]["title"], "Flight delayed")
            self.assertEqual(len(refreshed["notifications"]), 2)
            self.assertGreaterEqual(len(refreshed["reminders"]), 4)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_browser_api_approval_rewrites_active_itinerary_projection(self):
        """Proves the option chosen in the phone UI becomes the active journey."""
        api_store = Store(Path(self.temp.name) / "approval-api-state.db")
        with socket.socket() as available:
            available.bind(("127.0.0.1", 0))
            port = available.getsockname()[1]
        server = ThreadingHTTPServer(
            ("127.0.0.1", port),
            make_handler(api_store, secrets.token_urlsafe(24), port),
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        jar = http.cookiejar.CookieJar()
        browser = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        origin = f"http://127.0.0.1:{port}"

        def post(path, payload):
            request = urllib.request.Request(
                origin + path,
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json", "Origin": origin},
                method="POST",
            )
            with browser.open(request, timeout=5) as response:
                return json.load(response)

        try:
            browser.open(origin + "/", timeout=5).read()
            created = post("/api/trips", {"title": "Approval demo", "demo": True})
            trip_id = created["trip"]["id"]
            delayed = post(
                f"/api/trips/{trip_id}/replay",
                {"minutes": 120, "cause": "weather", "event_key": "approval-delay"},
            )
            post(
                f"/api/trips/{trip_id}/approve",
                {"plan_id": delayed["plan"]["id"], "option_id": "later-train"},
            )
            with browser.open(origin + f"/api/trips/{trip_id}", timeout=5) as response:
                refreshed = json.load(response)

            self.assertEqual(len(refreshed["actions"]), 1)
            self.assertEqual(refreshed["actions"][0]["option_id"], "later-train")
            train = refreshed["train_experience"]
            self.assertEqual(train["original_connection"]["state"], "missed")
            self.assertEqual(train["status"]["state"], "replacement_selected")
            self.assertEqual(train["recovery_connection"]["departure_at"], "2026-11-07T21:15:00+00:00")
            self.assertEqual(train["recovery_connection"]["hotel_arrival"], "2026-11-07T22:30:00+00:00")
            self.assertEqual(train["recovery_connection"]["cost"], 45)
            self.assertTrue(train["recovery_connection"]["selected"])
            self.assertEqual(refreshed["flight_experience"]["affected"]["recovery_status"], "selected")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
