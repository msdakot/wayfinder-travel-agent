import json
from pathlib import Path
import tempfile
import unittest

from travel_recovery.documents import extract
from travel_recovery.store import Store


def itinerary_text(items):
    lines=["DOCUMENT_TYPE: itinerary"]
    lines.extend("ITEM: "+json.dumps(item,separators=(",",":")) for item in items)
    return ("\n".join(lines)+"\n").encode()


def item(item_id,title,start,end,location,item_type,flexibility="fixed"):
    return {
        "id":item_id,"title":title,"start":start,"end":end,
        "location":location,"type":item_type,"flexibility":flexibility,
    }


class ItineraryDocumentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.temp.name)/"state.db")
        self.trip_id=self.store.create("Chile")['trip']['id']
        self.transport=item("la-scl","Flight LA to Santiago","2026-12-02T14:00:00-08:00","2026-12-03T10:30:00-03:00","LAX to SCL","transport")
        self.stay=item("santiago-stay","Santiago hotel","2026-12-03T15:00:00-03:00","2026-12-06T11:00:00-03:00","Lastarria, Santiago","lodging")

    def tearDown(self):
        self.temp.cleanup()

    def upload_and_review(self,name,items):
        doc=self.store.upload(self.trip_id,name,itinerary_text(items))
        self.store.review(self.trip_id,doc["id"],doc["kind"],doc["fields"])
        return doc

    def test_extracts_bounded_repeated_json_items(self):
        parsed=extract("chile.txt",itinerary_text([self.transport,self.stay]))
        self.assertEqual(parsed["kind"],"itinerary")
        self.assertEqual([row["id"] for row in parsed["fields"]["items"]],["la-scl","santiago-stay"])
        self.assertEqual(parsed["fields"]["items"][0]["start"],"2026-12-02T22:00:00+00:00")
        with self.assertRaisesRegex(ValueError,"valid JSON"):
            extract("bad.txt",b"DOCUMENT_TYPE: itinerary\nITEM: {bad}\n")

    def test_multiple_documents_create_unified_trip_without_fact_conflicts(self):
        first=self.upload_and_review("transport-and-stay.txt",[self.transport,self.stay])
        rental=item("rental","Rental car","2026-12-06T11:30:00-03:00","2026-12-10T18:00:00-03:00","Santiago Airport","transport","flexible")
        hike=item("hike","Torres del Paine hike","2026-12-08T08:00:00-03:00","2026-12-08T17:00:00-03:00","Torres del Paine","tour")
        second=self.upload_and_review("rental-and-activities.txt",[rental,hike])
        snap=self.store.snapshot(self.trip_id)
        self.assertEqual(len(snap["commitments"]),4)
        self.assertEqual({c["type"] for c in snap["commitments"]},{"transport","lodging","tour"})
        self.assertTrue(all(c["source"]=="document" for c in snap["commitments"]))
        self.assertEqual({c["source_document_id"] for c in snap["commitments"]},{first["id"],second["id"]})
        self.assertEqual(snap["conflicts"],[])
        self.assertNotIn("itinerary.items",snap["facts"])

    def test_rereview_upserts_stable_item_ids_and_removes_obsolete_items(self):
        doc=self.upload_and_review("chile.txt",[self.transport,self.stay])
        before={c["source_item_key"]:c["id"] for c in self.store.snapshot(self.trip_id)["commitments"]}
        changed={**self.transport,"title":"Flight to Santiago — LA departure"}
        self.store.review(self.trip_id,doc["id"],"itinerary",{"items":[changed]})
        snap=self.store.snapshot(self.trip_id)
        self.assertEqual(len(snap["commitments"]),1)
        self.assertEqual(snap["commitments"][0]["id"],before["la-scl"])
        self.assertEqual(snap["commitments"][0]["title"],changed["title"])
        revision=snap["trip"]["revision"]
        updated=snap["commitments"][0]["updated"]
        self.store.review(self.trip_id,doc["id"],"itinerary",{"items":[changed]})
        repeated=self.store.snapshot(self.trip_id)
        self.assertEqual(len(repeated["commitments"]),1)
        self.assertEqual(repeated["trip"]["revision"],revision)
        self.assertEqual(repeated["commitments"][0]["updated"],updated)

    def test_invalid_rereview_is_atomic_and_document_delete_is_scoped(self):
        doc=self.upload_and_review("chile.txt",[self.transport,self.stay])
        manual_item=item("unused","Dinner","2026-12-04T19:00:00-03:00","2026-12-04T21:00:00-03:00","Santiago","dining")
        manual_item.pop("id")
        manual=self.store.add_commitment(self.trip_id,manual_item|{"source":"manual"})
        prior=self.store.snapshot(self.trip_id)
        invalid={**self.transport,"end":self.transport["start"]}
        with self.assertRaisesRegex(ValueError,"end must be after"):
            self.store.review(self.trip_id,doc["id"],"itinerary",{"items":[invalid]})
        after=self.store.snapshot(self.trip_id)
        self.assertEqual([(c["id"],c["title"]) for c in after["commitments"]],[(c["id"],c["title"]) for c in prior["commitments"]])
        self.assertEqual(next(d for d in after["documents"] if d["id"]==doc["id"])["fields"],next(d for d in prior["documents"] if d["id"]==doc["id"])["fields"])
        self.store.delete_document(self.trip_id,doc["id"])
        remaining=self.store.snapshot(self.trip_id)["commitments"]
        self.assertEqual([c["id"] for c in remaining],[manual["id"]])


if __name__=="__main__":
    unittest.main()
