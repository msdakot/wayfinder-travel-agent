from pathlib import Path
import json
import tempfile
import unittest

from travel_recovery.store import Store
from travel_recovery.answers import answer


class PlaceExperienceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.temp.name)/"state.db")
        self.trip_id=self.store.create("London",demo=True)["trip"]["id"]

    def tearDown(self):
        self.temp.cleanup()

    def test_known_personal_places_include_map_and_route_choices(self):
        conference=self.store.capture_commitment(self.trip_id,{"text":"Conference on Nov 8 from 9 AM to 5 PM at ExCeL"})
        self.store.capture_commitment(self.trip_id,{"text":"Beer crawl on Nov 7 from 9:30 PM to 11:30 PM at Bermondsey"})
        places=self.store.snapshot(self.trip_id)["place_experiences"]
        self.assertEqual(len(places),2)
        guide=next(item for item in places if item["commitment_id"]==conference["id"])
        self.assertEqual(guide["destination"]["name"],"ExCeL London")
        self.assertEqual([mode["id"] for mode in guide["modes"]],["walk","transit","taxi"])
        self.assertEqual(sum(mode["recommended"] for mode in guide["modes"]),1)
        self.assertTrue(all(mode["leave_at"] for mode in guide["modes"]))
        self.assertFalse(guide["freshness"]["live"])

    def test_unknown_place_does_not_invent_coordinates(self):
        self.store.capture_commitment(self.trip_id,{"title":"Private dinner","start":"2026-11-08T19:00:00+00:00","end":"2026-11-08T20:00:00+00:00","location":"Friend's home","type":"dining","flexibility":"fixed","source":"chat"})
        self.assertEqual(self.store.snapshot(self.trip_id)["place_experiences"],[])

    def test_complete_demo_resolves_every_named_london_plan_including_london_eye(self):
        trip=self.store.create("Complete London trip",demo=True,full_demo=True)
        places=trip["place_experiences"]
        by_title={item["title"]:item for item in places}
        self.assertEqual(set(by_title),{
            "Dinner near The Marlow House","Conference — day 1","Beer crawl",
            "Conference — day 2","British Museum tickets","London Eye tickets",
            "Client meeting","Lunch with a friend","Flight home from Heathrow",
        })
        eye=by_title["London Eye tickets"]
        self.assertEqual(eye["destination"]["name"],"The London Eye")
        self.assertEqual(eye["resolution"]["matched"],"london eye")

    def test_chat_answers_place_and_route_from_unified_itinerary(self):
        self.store.capture_commitment(self.trip_id,{"text":"Beer crawl on Nov 8 from 8 PM to 10 PM at Bermondsey"})
        result=answer(self.store.snapshot(self.trip_id),"Where is the beer crawl and how do I get there?")
        self.assertIn("Bermondsey Beer Mile",result["text"])
        self.assertIn("recommended route",result["text"])
        self.assertIn("not live traffic",result["text"])

    def test_chat_resolves_short_location_follow_up(self):
        self.store.capture_commitment(self.trip_id,{"text":"Beer crawl on Nov 8 from 8 PM to 10 PM at Bermondsey"})
        snap=self.store.snapshot(self.trip_id)
        first=answer(snap,"Where is the beer crawl on Sunday?")
        self.store.save_message(self.trip_id,"Where is the beer crawl on Sunday?",first,snap["trip"]["revision"])
        result=answer(self.store.snapshot(self.trip_id),"Can you share the location?")
        self.assertIn("Bermondsey Beer Mile",result["text"])
        self.assertIn("Tap Beer crawl",result["text"])

    def test_chile_documents_map_public_stops_with_itinerary_origins_and_clp(self):
        chile=self.store.create("Chile 2025")
        trip_id=chile["trip"]["id"]
        root=Path(__file__).resolve().parents[1]/"data"/"chile-documents"
        reviewed=[]
        for name in ("02-flights-and-ground.txt","03-stays-and-atacama.txt","04-w-circuit.txt"):
            doc=self.store.upload(trip_id,name,(root/name).read_bytes())
            self.store.review(trip_id,doc["id"],doc["kind"],doc["fields"])
            reviewed.append(doc["id"])
        snap=self.store.snapshot(trip_id)
        by_key={guide["resolution"]["place_key"]:guide for guide in snap["place_experiences"]}

        required={
            "scl","cjc","pnt","backpackers_san_pedro","socaire_control",
            "piedras_rojas","yaganhouse","chumango","central","las_torres",
            "frances","paine_grande","grey",
        }
        self.assertTrue(required.issubset(by_key),required-set(by_key))
        calama_flight=next(guide for guide in snap["place_experiences"] if guide["source_item_key"]=="la378-scl-cjc")
        self.assertEqual(calama_flight["origin"]["name"],"Santiago International Airport")
        self.assertEqual(by_key["backpackers_san_pedro"]["origin"]["name"],"El Loa Airport")
        self.assertEqual(by_key["central"]["origin"]["name"],"YaganHouse")
        self.assertEqual(by_key["paine_grande"]["origin"]["name"],"Francés Mountain Hostel and Sector")
        self.assertTrue(all(mode["cost"]["currency"]=="CLP" for guide in by_key.values() if guide["destination"]["country"]=="CL" for mode in guide["modes"]))
        self.assertIn(by_key["socaire_control"]["source_document_id"],reviewed)
        self.assertEqual(by_key["socaire_control"]["destination"]["lat"],-23.5874448)
        self.assertEqual(by_key["socaire_control"]["destination"]["lng"],-67.89106072)

    def test_each_bounded_chile_destination_can_be_resolved_without_private_geocoding(self):
        items=[]
        destinations=(
            ("santiago","Santiago International Airport"),("calama","El Loa Airport, Calama"),
            ("san-pedro","Portal del Inca 486, San Pedro de Atacama, Chile"),
            ("socaire","Socaire control office, Socaire, Chile"),
            ("piedras","Piedras Rojas, Chile"),("yagan","O'Higgins 584, Puerto Natales, Chile"),
            ("chumango","Avenida Santiago Bueras 775, Puerto Natales, Chile"),
            ("central","Central Mountain Hostel, Torres del Paine"),
            ("torres","Las Torres Base, Torres del Paine"),("frances","Francés Mountain Hostel, Torres del Paine"),
            ("paine","Paine Grande Mountain Hostel, Torres del Paine"),
            ("grey","Grey Glacier lookout, Torres del Paine"),("pehoe","Lake Pehoé catamaran, Torres del Paine"),
            ("pnt","Teniente Julio Gallardo Airport, Puerto Natales"),
        )
        for index,(item_id,location) in enumerate(destinations):
            items.append({
                "id":item_id,"title":item_id,"start":f"2025-12-{index+1:02d}T12:00:00-03:00",
                "end":f"2025-12-{index+1:02d}T13:00:00-03:00","location":location,
                "type":"tour","flexibility":"fixed",
            })
        payload=("DOCUMENT_TYPE: itinerary\n"+"\n".join("ITEM: "+json.dumps(item) for item in items)+"\n").encode()
        trip_id=self.store.create("Bounded Chile map registry")["trip"]["id"]
        doc=self.store.upload(trip_id,"places.txt",payload)
        self.store.review(trip_id,doc["id"],doc["kind"],doc["fields"])
        places=self.store.snapshot(trip_id)["place_experiences"]
        self.assertEqual(len(places),len(destinations))
        self.assertEqual({place["resolution"]["place_key"] for place in places},{
            "scl","cjc","backpackers_san_pedro","socaire_control","piedras_rojas",
            "yaganhouse","chumango","central","las_torres","frances","paine_grande",
            "grey","pehoe","pnt",
        })

