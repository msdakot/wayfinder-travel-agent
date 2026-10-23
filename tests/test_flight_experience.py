import http.cookiejar
import json
from pathlib import Path
import secrets
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from travel_recovery.server import make_handler
from travel_recovery.store import Store


class FlightExperienceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.temp.name)/"state.db",airport_travel_minutes=45,airport_buffer_minutes=120)
        self.snap=self.store.create("London",demo=True)
        self.trip_id=self.snap["trip"]["id"]

    def tearDown(self):
        self.temp.cleanup()

    def test_not_checked_in_prompts_and_hides_boarding_pass(self):
        experience=self.snap["flight_experience"]
        self.assertEqual(experience["check_in"]["status"],"not_checked_in")
        self.assertTrue(experience["check_in"]["action_required"])
        self.assertIsNone(experience["boarding_pass"])
        leave=experience["leave_for_airport"]
        self.assertEqual(leave["recommended_at"],"2026-11-06T10:30:00-08:00")
        self.assertEqual(leave["travel_duration_minutes"],45)
        self.assertEqual(leave["airport_buffer_minutes"],120)
        self.assertIn("Fixed POC estimate",leave["assumption"])

    def test_demo_check_in_persists_and_gates_boarding_pass(self):
        revision=self.snap["trip"]["revision"]
        checked=self.store.set_check_in(self.trip_id,True)
        experience=checked["flight_experience"]
        self.assertEqual(checked["trip"]["revision"],revision+1)
        self.assertEqual(experience["check_in"]["status"],"checked_in")
        boarding=experience["boarding_pass"]
        self.assertEqual(boarding["flight"],"DEMO101")
        self.assertEqual(boarding["traveler"],"Alex Traveler")
        self.assertEqual(boarding["seat"],"12A")
        self.assertEqual(boarding["terminal"],"International Terminal")
        self.assertEqual(boarding["gate"],"A7")
        self.assertEqual(boarding["booking_reference"],"DEMO-FLIGHT")
        self.assertTrue(boarding["barcode"]["token"].startswith("DEMO-"))
        self.assertFalse(boarding["barcode"]["scannable"])
        self.assertIn("cannot be used",boarding["notice"])
        persisted=self.store.snapshot(self.trip_id)
        self.assertEqual(persisted["flight_experience"]["boarding_pass"],boarding)
        duplicate=self.store.set_check_in(self.trip_id,True)
        self.assertEqual(duplicate["trip"]["revision"],revision+1)
        reset=self.store.set_check_in(self.trip_id,False)
        self.assertIsNone(reset["flight_experience"]["boarding_pass"])

    def test_delay_exposes_consequence_and_recovery_state(self):
        delayed=self.store.replay(self.trip_id,minutes=120,key="flight-detail-delay")
        experience=delayed["flight_experience"]
        self.assertEqual(experience["delay"]["status"],"delayed")
        self.assertEqual(experience["delay"]["minutes"],120)
        self.assertEqual(experience["delay"]["source"],"simulated_replay")
        self.assertTrue(experience["affected"]["connection_missed"])
        self.assertEqual(experience["affected"]["recovery_status"],"options_ready")
        self.assertEqual(experience["affected"]["recommended_option"],"later-train")
        action=self.store.approve(delayed["trip"]["id"],delayed["plan"]["id"],"later-train")
        self.assertEqual(action["state"],"simulated_confirmed")
        refreshed=self.store.snapshot(self.trip_id)
        self.assertEqual(refreshed["flight_experience"]["affected"]["recovery_status"],"selected")

    def test_airport_timing_configuration_is_bounded(self):
        with self.assertRaisesRegex(ValueError,"Airport Travel"):
            Store(Path(self.temp.name)/"bad.db",airport_travel_minutes=-1)

    def test_check_in_http_contract_uses_existing_same_origin_protection(self):
        api_store=Store(Path(self.temp.name)/"api.db")
        with socket.socket() as available:
            available.bind(("127.0.0.1",0));port=available.getsockname()[1]
        token=secrets.token_urlsafe(24)
        server=ThreadingHTTPServer(("127.0.0.1",port),make_handler(api_store,token,port))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        origin=f"http://127.0.0.1:{port}"
        jar=http.cookiejar.CookieJar()
        browser=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        def post(path,payload,headers=None):
            request=urllib.request.Request(origin+path,data=json.dumps(payload).encode(),headers=headers or {"Content-Type":"application/json","Origin":origin},method="POST")
            with browser.open(request,timeout=5) as response:return json.load(response)
        try:
            browser.open(origin+"/",timeout=5).read()
            created=post("/api/trips",{"title":"Flight detail","demo":True})
            trip_id=created["trip"]["id"]
            checked=post(f"/api/trips/{trip_id}/check-in",{"checked_in":True})
            self.assertEqual(checked["flight_experience"]["check_in"]["status"],"checked_in")
            self.assertIsNotNone(checked["flight_experience"]["boarding_pass"])
            request=urllib.request.Request(origin+f"/api/trips/{trip_id}/check-in",data=b'{"checked_in":false}',headers={"Content-Type":"application/json","Origin":"https://attacker.invalid"},method="POST")
            with self.assertRaises(urllib.error.HTTPError) as rejected:
                browser.open(request,timeout=5)
            self.assertEqual(rejected.exception.code,403)
        finally:
            server.shutdown();server.server_close();thread.join(timeout=2)


if __name__=="__main__":
    unittest.main()
