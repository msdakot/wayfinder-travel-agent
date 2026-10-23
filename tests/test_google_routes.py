import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from travel_recovery.server import google_route
from travel_recovery.store import Store


class GoogleRoutesTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.temp.name)/"state.db")
        self.trip_id=self.store.create("London",demo=True)["trip"]["id"]
        self.commitment=self.store.capture_commitment(
            self.trip_id,{"text":"Beer crawl on Nov 8 from 8 PM to 10 PM at Bermondsey"}
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_route_uses_current_origin_and_server_owned_destination(self):
        captured={}
        response={"routes":[{"duration":"1234s","distanceMeters":9876,"polyline":{"encodedPolyline":"abc123"}}]}

        def fake_urlopen(request,timeout):
            captured["url"]=request.full_url
            captured["headers"]={k.lower():v for k,v in request.header_items()}
            captured["body"]=json.loads(request.data)
            captured["timeout"]=timeout
            return io.BytesIO(json.dumps(response).encode())

        with patch.dict(os.environ,{"GOOGLE_ROUTES_API_KEY":"test-key"}), patch("travel_recovery.server.urlopen",fake_urlopen):
            result=google_route(self.store,self.trip_id,{"commitment_id":self.commitment["id"],"mode":"drive","origin":{"lat":51.50,"lng":-0.12},"destination":{"lat":0,"lng":0}})

        self.assertEqual(result["distance_meters"],9876)
        self.assertEqual(result["duration_seconds"],1234)
        self.assertTrue(result["traffic_aware"])
        self.assertEqual(captured["body"]["destination"]["location"]["latLng"]["latitude"],51.4971)
        self.assertEqual(captured["body"]["routingPreference"],"TRAFFIC_AWARE")
        self.assertNotIn("test-key",json.dumps(result))

    def test_route_requires_credentials_and_valid_current_coordinates(self):
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaisesRegex(ValueError,"not configured"):
                google_route(self.store,self.trip_id,{"commitment_id":self.commitment["id"],"mode":"walk","origin":{"lat":51.5,"lng":-.1}})
        with patch.dict(os.environ,{"GOOGLE_ROUTES_API_KEY":"test-key"}):
            with self.assertRaisesRegex(ValueError,"coordinates"):
                google_route(self.store,self.trip_id,{"commitment_id":self.commitment["id"],"mode":"walk","origin":{"lat":100,"lng":-.1}})

    def test_route_rejects_malformed_or_unbounded_provider_results(self):
        cases=(
            {"routes":["not-an-object"]},
            {"routes":[{"duration":"60s","distanceMeters":10,"polyline":{}}]},
            {"routes":[{"duration":"700000s","distanceMeters":10,"polyline":{"encodedPolyline":"abc"}}]},
            {"routes":[{"duration":"60s","distanceMeters":2_000_001,"polyline":{"encodedPolyline":"abc"}}]},
        )
        for response in cases:
            with self.subTest(response=response):
                def fake_urlopen(request,timeout):
                    return io.BytesIO(json.dumps(response).encode())
                with patch.dict(os.environ,{"GOOGLE_ROUTES_API_KEY":"test-key"}), patch("travel_recovery.server.urlopen",fake_urlopen):
                    with self.assertRaisesRegex(ValueError,"incomplete route"):
                        google_route(self.store,self.trip_id,{"commitment_id":self.commitment["id"],"mode":"walk","origin":{"lat":51.5,"lng":-.1}})

