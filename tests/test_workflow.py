import base64
import concurrent.futures
import json
from pathlib import Path
import tempfile
import unittest
from travel_recovery.store import Store, ROOT
from travel_recovery.answers import answer
from travel_recovery.documents import extract


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.temp.name)/"state.db")
        self.snap=self.store.create("London",demo=True)
        self.trip_id=self.snap["trip"]["id"]
    def tearDown(self):
        self.temp.cleanup()
    def test_document_to_answer_has_valid_citations(self):
        result=answer(self.snap,"What time does hotel check-in close?")
        self.assertIn("11:00 PM London time",result["text"])
        self.assertTrue(result["citations"])
        ids={d["id"] for d in self.snap["documents"]}
        self.assertTrue(all(c["id"] in ids for c in result["citations"]))
    def test_complete_recovery_and_idempotent_approval(self):
        snap=self.store.replay(self.trip_id)
        self.assertIn("$45",answer(snap,"What should I do now?")["text"])
        action=self.store.approve(self.trip_id,snap["plan"]["id"],"later-train")
        self.assertEqual(action["state"],"simulated_confirmed")
        again=self.store.approve(self.trip_id,snap["plan"]["id"],"later-train")
        self.assertEqual(action["id"],again["id"])
        self.assertEqual(len(self.store.snapshot(self.trip_id)["actions"]),1)
    def test_stale_plan_cannot_be_approved(self):
        snap=self.store.replay(self.trip_id)
        self.store.budget(self.trip_id,20)
        with self.assertRaises(ValueError):self.store.approve(self.trip_id,snap["plan"]["id"],"later-train")
    def test_duplicate_event_does_not_create_another_plan(self):
        self.store.replay(self.trip_id,key="test-key")
        self.assertEqual(self.store.replay(self.trip_id,key="test-key"),{"duplicate":True})
    def test_rejected_candidate_cannot_be_approved(self):
        snap=self.store.replay(self.trip_id)
        with self.assertRaises(ValueError):self.store.approve(self.trip_id,snap["plan"]["id"],"late-bus")
    def test_upload_dedup_and_limit_under_concurrency(self):
        doc=self.snap["documents"][0]
        content=(ROOT/"data/sample-documents"/doc["name"]).read_bytes()
        self.assertEqual(self.store.upload(self.trip_id,doc["name"],content)["id"],doc["id"])
        def upload(i):
            try:self.store.upload(self.trip_id,f"note-{i}.txt",f"Synthetic note {i}".encode());return True
            except ValueError:return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            results=list(pool.map(upload,range(15)))
        self.assertEqual(sum(results),9)
        self.assertEqual(self.store.snapshot(self.trip_id)["document_count"],15)
    def test_unreviewed_documents_are_not_used(self):
        self.store.upload(self.trip_id,"new.txt",b"DOCUMENT_TYPE: hotel\nCHECK_IN_DEADLINE: 2026-11-07T15:00:00+00:00\n")
        snap=self.store.snapshot(self.trip_id)
        self.assertEqual(snap["facts"]["hotel.check_in_deadline"]["value"],"2026-11-07T23:00:00+00:00")
        self.assertFalse(snap["conflicts"])
    def test_conflicting_reviewed_document_blocks_recovery(self):
        d=self.store.upload(self.trip_id,"new.txt",b"DOCUMENT_TYPE: hotel\nCHECK_IN_DEADLINE: 2026-11-07T15:00:00+00:00\n")
        self.store.review(self.trip_id,d["id"],d["kind"],d["fields"])
        with self.assertRaises(ValueError):self.store.replay(self.trip_id)
    def test_document_deletion_invalidates_answers_and_plans(self):
        r=answer(self.snap,"Summarize my trip")
        self.store.save_message(self.trip_id,"Summarize my trip",r,self.snap["trip"]["revision"])
        self.store.replay(self.trip_id)
        self.store.delete_document(self.trip_id,self.snap["documents"][0]["id"])
        snap=self.store.snapshot(self.trip_id)
        self.assertEqual(snap["messages"],[])
        self.assertIsNone(snap["plan"])
        self.assertEqual(snap["flight_statuses"],[])
        self.assertEqual(snap["notifications"],[])
        self.assertIsNone(snap["monitor"])
    def test_cross_itinerary_document_rejected(self):
        other=self.store.create("Other")
        doc=self.snap["documents"][0]
        with self.assertRaises(ValueError):self.store.review(other["trip"]["id"],doc["id"],doc["kind"],doc["fields"])
    def test_cancellation_is_not_misrepresented_as_delay_support(self):
        self.assertIn("not supported",answer(self.snap,"My flight is canceled. What now?")["text"])
    def test_scanned_and_invalid_files_fail_clearly(self):
        with self.assertRaises(ValueError):extract("broken.pdf",b"not a pdf")
        with self.assertRaises(ValueError):extract("image.png",b"image")
    def test_unknown_question_does_not_invent_an_answer(self):
        self.assertIn("couldn't find",answer(self.snap,"Zyxwvuts?")["text"])
    def test_missing_documents_dont_inherit_demo_facts(self):
        other=self.store.create("Empty")
        with self.assertRaises(ValueError):self.store.context(other)
    def test_inventory_failure_changes_recommendation(self):
        snap=self.store.replay(self.trip_id,unavailable=True)
        self.assertEqual(snap["plan"]["result"]["recommended_option"],"taxi")
    def test_new_event_invalidates_prior_plan(self):
        old=self.store.replay(self.trip_id)
        self.store.replay(self.trip_id,minutes=180)
        with self.assertRaises(ValueError):self.store.approve(self.trip_id,old["plan"]["id"],"later-train")
    def test_monitoring_requires_reviewed_flight_and_records_provider_mode(self):
        empty=self.store.create("Empty")
        with self.assertRaisesRegex(ValueError,"Review a flight"):
            self.store.set_monitoring(empty["trip"]["id"],True,"aviationstack")
        snap=self.store.set_monitoring(self.trip_id,True,"demo_replay")
        self.assertEqual(snap["monitor"]["enabled"],1)
        self.assertEqual(snap["monitor"]["provider"],"demo_replay")
    def test_disabled_monitor_ignores_incoming_status(self):
        self.store.set_monitoring(self.trip_id,False,"demo_replay")
        result=self.store.replay(self.trip_id,minutes=120,key="disabled-update")
        self.assertEqual(result,{"ignored":"monitoring_disabled"})
        snap=self.store.snapshot(self.trip_id)
        self.assertIsNone(snap["event"])
        self.assertIsNone(snap["plan"])
        self.assertEqual(snap["notifications"],[])
    def test_unchanged_status_with_new_provider_id_is_deduplicated(self):
        first=self.store.replay(self.trip_id,minutes=120,key="poll-1")
        duplicate=self.store.replay(self.trip_id,minutes=120,key="poll-2")
        self.assertEqual(duplicate,{"duplicate":True})
        snap=self.store.snapshot(self.trip_id)
        self.assertEqual(len(snap["flight_statuses"]),1)
        self.assertEqual(len(snap["notifications"]),1)
        self.assertEqual(snap["plan"]["id"],first["plan"]["id"])
    def test_out_of_order_provider_status_is_ignored(self):
        base={"provider":"demo_replay","flight_id":"DEMO101","simulation":True,"cause":"weather"}
        current=self.store.ingest_flight_status(self.trip_id,{**base,"provider_event_id":"newer","status":"delayed","estimated_arrival":"2026-11-07T20:00:00+00:00","observed_at":"2026-11-07T17:00:00+00:00"})
        ignored=self.store.ingest_flight_status(self.trip_id,{**base,"provider_event_id":"older","status":"on_time","estimated_arrival":"2026-11-07T18:00:00+00:00","observed_at":"2026-11-07T16:00:00+00:00"})
        self.assertEqual(ignored,{"ignored":"out_of_order"})
        snap=self.store.snapshot(self.trip_id)
        self.assertEqual(snap["event"]["provider_event_id"],"newer")
        self.assertEqual(len(snap["notifications"]),1)
        self.assertEqual(snap["plan"]["id"],current["plan"]["id"])
    def test_live_custom_delay_is_labeled_and_does_not_invent_recovery(self):
        custom=self.store.create("Uploaded itinerary")
        trip_id=custom["trip"]["id"]
        doc=self.store.upload(trip_id,"flight.txt",b"DOCUMENT_TYPE: flight\nFLIGHT_ID: AB123\nARRIVAL: 2026-11-07T18:00:00+00:00\n")
        self.store.review(trip_id,doc["id"],doc["kind"],doc["fields"])
        snap=self.store.ingest_flight_status(trip_id,{"provider":"aviationstack","provider_event_id":"provider-1","flight_id":"AB123","status":"delayed","estimated_arrival":"2026-11-07T20:00:00+00:00","cause":None,"observed_at":"2026-11-07T17:00:00+00:00","simulation":False})
        self.assertIsNone(snap["plan"])
        self.assertEqual(snap["status_processing"]["source_mode"],"live_provider")
        self.assertFalse(snap["status_processing"]["recovery_plan_created"])
        self.assertEqual(snap["notifications"][0]["source_mode"],"live_provider")
        self.assertIn("checking this delay against every connection",snap["notifications"][0]["detail"])

if __name__=="__main__":unittest.main()
