import http.cookiejar
import json
from pathlib import Path
import secrets
import socket
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

from travel_recovery.server import make_handler
from travel_recovery.store import Store


class CommitmentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.temp.name)/"state.db")
        self.snap=self.store.create("London",demo=True)
        self.trip_id=self.snap["trip"]["id"]

    def tearDown(self):
        self.temp.cleanup()

    def structured(self,**overrides):
        value={"title":"Dinner","start":"2026-11-07T21:30:00+00:00","end":"2026-11-07T23:30:00+00:00","location":"Bermondsey","type":"dining","flexibility":"flexible","source":"voice"}
        value.update(overrides)
        return value

    def test_structured_crud_is_scoped_and_duplicate_add_is_idempotent(self):
        revision=self.snap["trip"]["revision"]
        item=self.store.capture_commitment(self.trip_id,self.structured())
        after_add=self.store.snapshot(self.trip_id)
        self.assertEqual(after_add["trip"]["revision"],revision+1)
        duplicate=self.store.capture_commitment(self.trip_id,self.structured(title="  dinner ",location=" bermondsey "))
        self.assertEqual(duplicate["id"],item["id"])
        self.assertEqual(self.store.snapshot(self.trip_id)["trip"]["revision"],revision+1)
        updated=self.store.update_commitment(self.trip_id,item["id"],{"flexibility":"fixed"})
        self.assertEqual(updated["flexibility"],"fixed")
        self.assertEqual(self.store.snapshot(self.trip_id)["trip"]["revision"],revision+2)
        other=self.store.create("Other")
        with self.assertRaisesRegex(ValueError,"not found"):
            self.store.update_commitment(other["trip"]["id"],item["id"],{"title":"Wrong trip"})
        self.assertTrue(self.store.delete_commitment(self.trip_id,item["id"])["deleted"])
        self.assertEqual(self.store.snapshot(self.trip_id)["commitments"],[])

    def test_full_demo_contains_complete_london_schedule(self):
        full=self.store.create("Complete London demo",demo=True,full_demo=True)
        self.assertEqual(len(full["commitments"]),9)
        self.assertEqual(full["commitments"][0]["title"],"Dinner near The Marlow House")
        self.assertEqual(full["commitments"][-1]["title"],"Flight home from Heathrow")

    def test_bounded_text_capture_parses_demo_phrases_and_rejects_ambiguity(self):
        beer=self.store.capture_commitment(self.trip_id,{"text":"Beer crawl on Nov 7 from 9:30 PM to 11:30 PM at Bermondsey"})
        conference=self.store.capture_commitment(self.trip_id,{"text":"Conference on November 8 from 9 AM to 5 PM at ExCeL"})
        self.assertEqual((beer["type"],beer["start"],beer["source"]),("dining","2026-11-07T21:30:00+00:00","chat"))
        self.assertEqual((conference["type"],conference["end"]),("event","2026-11-08T17:00:00+00:00"))
        with self.assertRaisesRegex(ValueError,"explicit time"):
            self.store.capture_commitment(self.trip_id,{"text":"Dinner sometime Friday in London"})
        with self.assertRaisesRegex(ValueError,"daylight-saving"):
            self.store.capture_commitment(self.trip_id,{"text":"Meeting on Oct 25 from 1:30 AM to 2:30 AM at Office"})
        custom=self.store.create("Custom")
        with self.assertRaisesRegex(ValueError,"structured timestamps"):
            self.store.capture_commitment(custom["trip"]["id"],{"text":"Meeting on Nov 8 from 9 AM to 10 AM at Office"})

    def test_frontend_text_examples_use_bounded_defaults(self):
        dinner=self.store.capture_commitment(self.trip_id,{"text":"Dinner at Dishoom on Nov 7 at 8:30 PM, flexible by 30 minutes"})
        museum=self.store.capture_commitment(self.trip_id,{"text":"British Museum on Nov 8 from 10 AM to noon"})
        meeting=self.store.capture_commitment(self.trip_id,{"text":"Client meeting at 4 PM on Nov 9 at King’s Cross"})
        self.assertEqual((dinner["location"],dinner["start"],dinner["end"],dinner["flexibility"]),("Dishoom","2026-11-07T20:30:00+00:00","2026-11-07T21:30:00+00:00","flexible"))
        self.assertEqual((museum["location"],museum["kind"],museum["end"]),("British Museum","tour","2026-11-08T12:00:00+00:00"))
        self.assertEqual((meeting["location"],meeting["kind"],meeting["end"]),("King’s Cross","meeting","2026-11-09T17:00:00+00:00"))

    def test_delay_marks_affected_and_at_risk_commitments_deterministically(self):
        self.store.capture_commitment(self.trip_id,self.structured(title="Early meeting",start="2026-11-07T20:00:00+00:00",end="2026-11-07T21:00:00+00:00",type="meeting",flexibility="fixed"))
        self.store.capture_commitment(self.trip_id,self.structured(title="Beer crawl"))
        self.store.capture_commitment(self.trip_id,self.structured(title="Conference",start="2026-11-08T09:00:00+00:00",end="2026-11-08T17:00:00+00:00",location="ExCeL",type="event",flexibility="fixed"))
        snap=self.store.replay(self.trip_id,minutes=120,key="commitment-delay")
        impacts={item["title"]:item["impact"] for item in snap["commitments"]}
        self.assertEqual(impacts["Early meeting"]["status"],"affected")
        self.assertEqual(impacts["Beer crawl"]["status"],"at_risk")
        self.assertEqual(impacts["Beer crawl"]["available_at"],"2026-11-07T22:30:00+00:00")
        self.assertEqual(impacts["Conference"]["status"],"not_affected")
        self.assertEqual(len(snap["snowflake_safe_context"]["commitments"]),3)
        self.assertNotIn("created",snap["snowflake_safe_context"]["commitments"][0])

    def test_commitment_ending_before_destination_arrival_is_not_affected(self):
        self.store.capture_commitment(self.trip_id,self.structured(title="Airport breakfast",start="2026-11-07T15:00:00+00:00",end="2026-11-07T16:00:00+00:00",location="SFO",type="dining"))
        snap=self.store.replay(self.trip_id,minutes=120,key="pre-arrival")
        impact=snap["commitments"][0]["impact"]
        self.assertEqual(impact["status"],"not_affected")
        self.assertIn("before the scheduled destination arrival",impact["reason"])

    def test_commitment_added_after_delay_uses_latest_recovery_timing(self):
        self.store.replay(self.trip_id,minutes=120,key="first-delay")
        item=self.store.capture_commitment(self.trip_id,self.structured(title="Beer crawl"))
        self.assertEqual(item["impact"]["status"],"at_risk")
        snap=self.store.snapshot(self.trip_id)
        self.assertFalse(snap["plan"]["stale"])
        self.assertTrue(snap["plan"]["result"]["connection_missed"])

    def test_major_disruption_without_timing_is_unknown_not_safe(self):
        self.store.capture_commitment(self.trip_id,self.structured())
        snap=self.store.ingest_flight_status(self.trip_id,{"provider":"demo_replay","provider_event_id":"cancelled","flight_id":"DEMO101","status":"cancelled","estimated_arrival":None,"cause":None,"observed_at":"2026-11-07T17:00:00+00:00","simulation":True})
        self.assertEqual(snap["commitments"][0]["impact"]["status"],"timing_unknown")
        self.assertIsNone(snap["commitments"][0]["impact"]["affected"])

    def test_http_commitment_contract_returns_refreshed_snapshot(self):
        api_store=Store(Path(self.temp.name)/"api.db")
        with socket.socket() as available:
            available.bind(("127.0.0.1",0));port=available.getsockname()[1]
        server=ThreadingHTTPServer(("127.0.0.1",port),make_handler(api_store,secrets.token_urlsafe(24),port))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        jar=http.cookiejar.CookieJar();browser=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar));origin=f"http://127.0.0.1:{port}"
        def post(path,payload):
            req=urllib.request.Request(origin+path,data=json.dumps(payload).encode(),headers={"Content-Type":"application/json","Origin":origin},method="POST")
            with browser.open(req,timeout=5) as response:return json.load(response)
        try:
            browser.open(origin+"/",timeout=5).read()
            created=post("/api/trips",{"title":"API commitments","demo":True});trip_id=created["trip"]["id"]
            added=post(f"/api/trips/{trip_id}/commitment",{"text":"Conference on Nov 8 from 9 AM to 5 PM at ExCeL"})
            self.assertEqual(len(added["commitments"]),1)
            item=added["commitments"][0]
            updated=post(f"/api/trips/{trip_id}/commitment",{"id":item["id"],"flexibility":"optional"})
            self.assertEqual(updated["commitments"][0]["flexibility"],"optional")
            deleted=post(f"/api/trips/{trip_id}/delete-commitment",{"commitment_id":item["id"]})
            self.assertEqual(deleted["commitments"],[])
        finally:
            server.shutdown();server.server_close();thread.join(timeout=2)


if __name__=="__main__":
    unittest.main()
