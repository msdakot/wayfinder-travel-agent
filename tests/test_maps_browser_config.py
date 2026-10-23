import http.cookiejar
import json
import os
from pathlib import Path
import secrets
import socket
import tempfile
import threading
import unittest
import urllib.request
from unittest.mock import patch

from travel_recovery.server import make_handler
from travel_recovery.store import Store


class MapsBrowserConfigTests(unittest.TestCase):
    def test_health_exposes_only_the_browser_key_and_allows_google_map_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)/"state.db")
            with socket.socket() as available:
                available.bind(("127.0.0.1",0));port=available.getsockname()[1]
            server=__import__("http.server").server.ThreadingHTTPServer(
                ("127.0.0.1",port),make_handler(store,secrets.token_urlsafe(24),port)
            )
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            jar=http.cookiejar.CookieJar()
            browser=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
            origin=f"http://127.0.0.1:{port}"
            try:
                browser_key="AIza"+"a"*35
                with patch.dict(os.environ,{"GOOGLE_ROUTES_API_KEY":"server-secret","GOOGLE_MAPS_BROWSER_KEY":browser_key}):
                    with browser.open(origin+"/",timeout=5) as response:
                        csp=response.headers["Content-Security-Policy"]
                        referrer_policy=response.headers["Referrer-Policy"]
                        response.read()
                    with browser.open(origin+"/api/health",timeout=5) as response:
                        health=json.load(response)
                    with browser.open(origin+"/api/maps-config",timeout=5) as response:
                        maps_config=json.load(response)
                config=health["configuration"]
                self.assertNotIn("google_maps_browser_key",config)
                self.assertEqual(config["google_maps_browser"],"configured")
                self.assertEqual(config["google_routes"],"configured")
                self.assertNotIn("server-secret",json.dumps(health))
                self.assertEqual(maps_config,{"enabled":True,"browser_api_key":browser_key})
                self.assertNotIn("server-secret",json.dumps(maps_config))
                self.assertIn("https://maps.googleapis.com",csp)
                self.assertIn("https://maps.gstatic.com",csp)
                self.assertEqual("strict-origin-when-cross-origin",referrer_policy)
            finally:
                server.shutdown();server.server_close();thread.join(timeout=2)


if __name__=="__main__":
    unittest.main()
