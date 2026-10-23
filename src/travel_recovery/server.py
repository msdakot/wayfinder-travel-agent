"""Local-only HTTP application. Run with the bundled Python for PDF support."""
import base64
import hashlib
import json
import os
import re
import secrets
import threading
import time
from collections import deque
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, urlencode
from urllib.request import Request, urlopen
from datetime import datetime, timezone, timedelta
from .store import Store, ROOT, now
from .answers import answer, cortex_answer, configuration


def load_env():
    p=ROOT/".env.local"
    if p.exists():
        for line in p.read_text().splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                k,v=line.split("=",1)
                if re.fullmatch(r"[A-Z][A-Z_0-9]*",k.strip()):
                    os.environ.setdefault(k.strip(),v.strip().strip('"').strip("'"))


def reminders(snap):
    result=[]
    for key,label,offset in (("flight.arrival","Expected arrival",0),("connection.departure","Onward train departure",0),("hotel.check_in_deadline","Hotel check-in closes",0),("hotel.check_out","Hotel check-out",0)):
        f=snap["facts"].get(key)
        if f:
            due=datetime.fromisoformat(f["value"])-timedelta(minutes=offset)
            result.append({"id":key,"title":label,"at":due.isoformat(),"due":due<=datetime.now(timezone.utc),"source":f["document_name"]})
    # Only expiry tracking; never imply a destination's passport validity rules.
    f=snap["facts"].get("passport.expiry_date")
    if f:
        result.append({"id":"passport.expiry_date","title":"Passport expires · expiry tracking only","at":f["value"]+"T00:00:00+00:00","due":f["value"]<=datetime.now(timezone.utc).date().isoformat(),"source":f["document_name"]})
    return result


def make_handler(store,token,port):
    allowed_hosts={f"127.0.0.1:{port}",f"localhost:{port}"}
    parse_slots=threading.BoundedSemaphore(3)
    route_times=deque()
    route_lock=threading.Lock()
    def allow_route_request():
        cutoff=time.monotonic()-60
        with route_lock:
            while route_times and route_times[0]<cutoff: route_times.popleft()
            if len(route_times)>=30:return False
            route_times.append(time.monotonic());return True
    class Handler(BaseHTTPRequestHandler):
        server_version="WayfinderPOC"
        def log_message(self,*args):
            pass
        def send(self,status,payload,ctype="application/json",cookie=False):
            if isinstance(payload,(dict,list)):
                payload=json.dumps(payload,ensure_ascii=False).encode()
            elif isinstance(payload,str): payload=payload.encode()
            self.send_response(status)
            self.send_header("Content-Type",ctype)
            self.send_header("Content-Length",str(len(payload)))
            self.send_header("Cache-Control","no-store")
            self.send_header("X-Content-Type-Options","nosniff")
            self.send_header("Referrer-Policy","strict-origin-when-cross-origin")
            self.send_header("Content-Security-Policy","default-src 'self'; script-src 'self' 'unsafe-eval' https://maps.googleapis.com https://maps.gstatic.com; style-src 'self' 'unsafe-inline'; img-src 'self' data: https://maps.googleapis.com https://maps.gstatic.com https://*.googleapis.com https://*.gstatic.com; connect-src 'self' https://maps.googleapis.com https://maps.gstatic.com https://*.googleapis.com https://*.gstatic.com; font-src 'self' data: https://fonts.gstatic.com; worker-src 'self' blob:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
            if cookie:
                self.send_header("Set-Cookie",f"wayfinder_session={token}; HttpOnly; SameSite=Strict; Path=/")
            self.end_headers()
            self.wfile.write(payload)
        def authorized(self,mutation=False):
            if self.headers.get("Host") not in allowed_hosts:
                self.send(403,{"error":"Unrecognized local host"});return False
            try:
                cookies=SimpleCookie(self.headers.get("Cookie",""))
                valid="wayfinder_session" in cookies and secrets.compare_digest(cookies["wayfinder_session"].value,token)
            except Exception: valid=False
            if not valid:
                self.send(401,{"error":"Open this app's home page to start a local session"});return False
            if mutation:
                origin=self.headers.get("Origin")
                if origin not in {"http://"+h for h in allowed_hosts}:
                    self.send(403,{"error":"Request must originate from the local app"});return False
            return True
        def do_GET(self):
            path=urlparse(self.path).path
            if self.headers.get("Host") not in allowed_hosts:
                return self.send(403,{"error":"Unrecognized local host"})
            static={"/":("index.html","text/html; charset=utf-8"),"/app.js":("app.js","text/javascript; charset=utf-8"),"/style.css":("style.css","text/css; charset=utf-8")}
            if path in static:
                file,ctype=static[path]
                return self.send(200,(ROOT/"web"/file).read_bytes(),ctype,cookie=path=="/")
            if not self.authorized(): return
            try:
                if path=="/api/health":
                    config=configuration()
                    config["google_routes"]="configured" if os.environ.get("GOOGLE_ROUTES_API_KEY") else "not_configured"
                    browser_maps_key=os.environ.get("GOOGLE_MAPS_BROWSER_KEY","")
                    config["google_maps_browser"]="configured" if re.fullmatch(r"AIza[0-9A-Za-z_-]{35}",browser_maps_key) else "not_configured"
                    return self.send(200,{"ok":True,"time":now(),"configuration":config,"document_support":["txt","text-based pdf"],"max_documents":15})
                if path=="/api/maps-config":
                    # Browser keys are intentionally public credentials. This key must be
                    # restricted in Google Cloud to this app's referrers and Maps JS only.
                    browser_maps_key=os.environ.get("GOOGLE_MAPS_BROWSER_KEY","")
                    if not re.fullmatch(r"AIza[0-9A-Za-z_-]{35}",browser_maps_key):
                        return self.send(200,{"enabled":False})
                    return self.send(200,{"enabled":True,"browser_api_key":browser_maps_key})
                if path=="/api/trips": return self.send(200,store.list_trips())
                match=re.fullmatch(r"/api/trips/([a-f0-9]+)",path)
                if match:
                    snap=store.snapshot(match[1]);snap["reminders"]=reminders(snap)
                    return self.send(200,snap)
                match=re.fullmatch(r"/api/samples/([a-zA-Z0-9_.-]+)",path)
                if match:
                    file=ROOT/"data/sample-documents"/match[1]
                    if file.is_file(): return self.send(200,file.read_bytes(),"text/plain; charset=utf-8")
                return self.send(404,{"error":"Not found"})
            except ValueError as e: return self.send(400,{"error":str(e)})
        def do_POST(self):
            if not self.authorized(True):return
            if self.headers.get("Content-Type","").split(";")[0]!="application/json":
                return self.send(415,{"error":"Use JSON requests"})
            try:
                length=int(self.headers.get("Content-Length","0"))
                if not 0<length<=14*1024*1024:
                    return self.send(413,{"error":"Request exceeds the upload limit"})
                data=json.loads(self.rfile.read(length))
                if not isinstance(data,dict): raise ValueError("Expected a JSON object")
                path=urlparse(self.path).path
                if path=="/api/trips":
                    title=data.get("title","Untitled itinerary")
                    if not isinstance(title,str) or not title.strip(): raise ValueError("Enter a trip title")
                    if set(data)-{"title","demo","full_demo"}: raise ValueError("Unexpected trip fields")
                    demo=data.get("demo") is True
                    full_demo=data.get("full_demo") is True
                    if full_demo and not demo: raise ValueError("A full demo must use the demo itinerary")
                    return self.send(201,store.create(title,demo=demo,full_demo=full_demo))
                match=re.fullmatch(r"/api/trips/([a-f0-9]+)/([a-z-]+)",path)
                if not match:return self.send(404,{"error":"Not found"})
                trip_id,operation=match.groups()
                if operation=="upload":
                    if not parse_slots.acquire(blocking=False):
                        return self.send(429,{"error":"Other documents are processing. Try again shortly"})
                    try:
                        name=data["name"]
                        if not isinstance(name,str): raise ValueError("Invalid filename")
                        content=base64.b64decode(data["content"],validate=True)
                        result=store.upload(trip_id,name,content)
                    finally:parse_slots.release()
                    return self.send(201,result)
                if operation=="review":
                    store.review(trip_id,data["document_id"],data["kind"],data["fields"])
                elif operation=="delete-document":store.delete_document(trip_id,data["document_id"])
                elif operation=="budget":store.budget(trip_id,data["amount"])
                elif operation=="replay":
                    if "unavailable" in data and type(data["unavailable"]) is not bool:raise ValueError("Invalid availability flag")
                    return self.send(200,store.replay(trip_id,data.get("minutes",120),data.get("cause","weather"),data.get("unavailable",False),data.get("event_key")))
                elif operation=="monitor":
                    snap=store.snapshot(trip_id)
                    provider="demo_replay" if snap["trip"]["scenario"]=="heathrow-demo" else "aviationstack"
                    return self.send(200,store.set_monitoring(trip_id,data.get("enabled",True),provider))
                elif operation=="check-in":
                    if set(data)-{"checked_in"}:
                        raise ValueError("Unexpected check-in fields")
                    return self.send(200,store.set_check_in(trip_id,data.get("checked_in",True)))
                elif operation=="commitment":
                    commitment_id=data.get("id")
                    if data.get("delete") is True:
                        if not isinstance(commitment_id,str):raise ValueError("Commitment id is required for deletion")
                        store.delete_commitment(trip_id,commitment_id)
                    elif commitment_id is not None:
                        if not isinstance(commitment_id,str):raise ValueError("Invalid commitment id")
                        fields={key:value for key,value in data.items() if key!="id"}
                        if not fields:raise ValueError("Provide at least one commitment field to update")
                        store.update_commitment(trip_id,commitment_id,fields)
                    else:
                        store.capture_commitment(trip_id,data)
                    return self.send(200,store.snapshot(trip_id))
                elif operation=="delete-commitment":
                    commitment_id=data.get("commitment_id") or data.get("id")
                    if not isinstance(commitment_id,str):raise ValueError("Commitment id is required for deletion")
                    store.delete_commitment(trip_id,commitment_id)
                    return self.send(200,store.snapshot(trip_id))
                elif operation=="approve":return self.send(200,store.approve(trip_id,data["plan_id"],data["option_id"]))
                elif operation=="ask":
                    snap=store.snapshot(trip_id)
                    question=data["question"]
                    result=answer(snap,question)
                    if data.get("mode")=="cortex":
                        if not configuration()["cloud_enabled"]:raise ValueError("Snowflake Cortex Agent is not connected yet. Choose local grounded mode or finish account setup")
                        result=cortex_answer(snap,question,result)
                    store.save_message(trip_id,question,result,snap["trip"]["revision"])
                    return self.send(200,result)
                elif operation=="check-flight":
                    return self.send(200,check_live_flight(store,trip_id))
                elif operation=="route":
                    if not allow_route_request():
                        return self.send(429,{"error":"Live directions are refreshing too quickly. Try again in a minute"})
                    return self.send(200,google_route(store,trip_id,data))
                else:return self.send(404,{"error":"Unknown operation"})
                return self.send(200,store.snapshot(trip_id))
            except (ValueError,KeyError,TypeError,OverflowError) as e:
                message=str(e) if isinstance(e,ValueError) else "Missing or invalid request fields"
                return self.send(400,{"error":message})
            except Exception:
                import traceback
                traceback.print_exc()
                return self.send(500,{"error":"An internal error occurred. Your existing trip data is preserved"})
    return Handler


def google_route(store,trip_id,data):
    """Compute a current route without exposing the Google credential or trusting a client destination."""
    key=os.environ.get("GOOGLE_ROUTES_API_KEY")
    if not key:
        raise ValueError("Live directions are not configured yet")
    commitment_id=data.get("commitment_id")
    mode=str(data.get("mode") or "transit").lower()
    if not isinstance(commitment_id,str) or mode not in {"walk","transit","drive"}:
        raise ValueError("Choose a valid itinerary stop and travel mode")
    origin=data.get("origin")
    if not isinstance(origin,dict):
        raise ValueError("Current location is required for live directions")
    lat,lng=origin.get("lat"),origin.get("lng")
    if type(lat) not in {int,float} or type(lng) not in {int,float} or not -90<=lat<=90 or not -180<=lng<=180:
        raise ValueError("Current location coordinates are invalid")
    snap=store.snapshot(trip_id)
    guide=next((item for item in snap.get("place_experiences",[]) if item["commitment_id"]==commitment_id),None)
    if not guide:
        raise ValueError("This itinerary stop does not have a verified map location")
    destination=guide["destination"]
    travel_mode={"walk":"WALK","transit":"TRANSIT","drive":"DRIVE"}[mode]
    payload={
        "origin":{"location":{"latLng":{"latitude":lat,"longitude":lng}}},
        "destination":{"location":{"latLng":{"latitude":destination["lat"],"longitude":destination["lng"]}}},
        "travelMode":travel_mode,"computeAlternativeRoutes":False,"languageCode":"en-US","units":"METRIC",
    }
    if mode=="drive":
        payload["routingPreference"]="TRAFFIC_AWARE"
    request=Request(
        "https://routes.googleapis.com/directions/v2:computeRoutes",
        data=json.dumps(payload,separators=(",",":")).encode(),method="POST",
        headers={"Content-Type":"application/json","X-Goog-Api-Key":key,"X-Goog-FieldMask":"routes.duration,routes.distanceMeters,routes.polyline.encodedPolyline"},
    )
    try:
        with urlopen(request,timeout=15) as response:
            provider=json.load(response)
    except Exception:
        raise ValueError("Google Maps could not calculate this route. Your saved itinerary was not changed") from None
    routes=provider.get("routes") if isinstance(provider,dict) else None
    if not isinstance(routes,list) or not routes:
        raise ValueError("Google Maps did not find a route for this travel mode")
    route=routes[0]
    if not isinstance(route,dict):
        raise ValueError("Google Maps returned an incomplete route")
    distance=route.get("distanceMeters")
    duration=route.get("duration")
    polyline=route.get("polyline")
    encoded=polyline.get("encodedPolyline") if isinstance(polyline,dict) else None
    match=re.fullmatch(r"(\d+(?:\.\d+)?)s",str(duration or ""))
    if type(distance) is not int or not 0<=distance<=2_000_000 or not match or not isinstance(encoded,str) or not 1<=len(encoded)<=200_000:
        raise ValueError("Google Maps returned an incomplete route")
    seconds=round(float(match.group(1)))
    if not 0<=seconds<=7*24*60*60:
        raise ValueError("Google Maps returned an incomplete route")
    return {
        "provider":"google_routes","source_mode":"live","checked_at":now(),"mode":mode,
        "origin":{"lat":lat,"lng":lng,"label":"Your current location"},
        "destination":{"lat":destination["lat"],"lng":destination["lng"],"name":destination["name"],"address":destination["address"]},
        "distance_meters":distance,"duration_seconds":seconds,"encoded_polyline":encoded,
        "traffic_aware":mode=="drive",
    }


def check_live_flight(store,trip_id):
    key=os.environ.get("AVIATIONSTACK_API_KEY")
    if not key:raise ValueError("Live flight API is not configured. Replay is available; it does not imply live status")
    snap=store.snapshot(trip_id)
    flight=snap["facts"].get("flight.flight_id",{}).get("value")
    if not flight or flight.startswith("DEMO"):
        raise ValueError("The synthetic DEMO flight cannot be checked live. Upload and review a real flight identifier")
    if not re.fullmatch(r"[A-Z0-9]{2,3}\d{1,4}",flight):raise ValueError("Unsupported flight identifier")
    # Fixed trusted host; never expose the URL or raw provider errors (it carries a key).
    url="https://api.aviationstack.com/v1/flights?"+urlencode({"access_key":key,"flight_iata":flight,"limit":5})
    try:
        with urlopen(url,timeout=15) as r:data=json.load(r)
    except Exception:raise ValueError("Flight provider request failed; existing trip facts were not changed") from None
    if data.get("error"):raise ValueError("Flight provider rejected the request. Check plan access and credentials")
    rows=data.get("data",[])
    results=[{"date":r.get("flight_date"),"status":r.get("flight_status"),"flight":r.get("flight",{}).get("iata"),"departure":r.get("departure",{}).get("airport"),"arrival":r.get("arrival",{}).get("airport"),"scheduled":r.get("arrival",{}).get("scheduled"),"estimated":r.get("arrival",{}).get("estimated"),"delay":r.get("arrival",{}).get("delay")} for r in rows]
    checked_at=now()
    processing={"recorded":False,"reason":"No provider row matched the reviewed flight"}
    candidates=[r for r in results if (r.get("flight") or "").upper()==flight]
    if len(candidates)==1:
        selected=candidates[0]
        status=(selected.get("status") or "unknown").lower()
        if status not in {"scheduled","on_time","active","delayed","landed","cancelled","diverted","incident","unknown"}:
            status="unknown"
        stable={k:selected.get(k) for k in ("date","status","flight","scheduled","estimated","delay")}
        provider_event_id=hashlib.sha256(json.dumps(stable,sort_keys=True,separators=(",", ":")).encode()).hexdigest()
        outcome=store.ingest_flight_status(trip_id,{
            "provider":"aviationstack","provider_event_id":provider_event_id,"flight_id":flight,
            "status":status,"estimated_arrival":selected.get("estimated") or selected.get("scheduled"),
            "cause":None,"observed_at":checked_at,"simulation":False,
        })
        processing={"recorded":not outcome.get("duplicate") and not outcome.get("ignored"),**outcome} if set(outcome).issubset({"duplicate","ignored"}) else outcome["status_processing"]
    elif len(candidates)>1:
        processing={"recorded":False,"reason":"Provider returned multiple matching services; confirm the operating date and airports"}
    return {"checked_at":checked_at,"provider":"aviationstack","source_mode":"live_provider","results":results,"processing":processing,"note":"Read-only provider status. Meaningful transitions are stored and deduplicated. Custom-itinerary recovery remains manual; no booking is changed."}


def main():
    load_env()
    port=int(os.environ.get("TRAVEL_PORT","8765"))
    store=Store(Path(os.environ.get("TRAVEL_DATABASE",str(ROOT/"runtime/travel.db"))))
    server=ThreadingHTTPServer(("127.0.0.1",port),make_handler(store,secrets.token_urlsafe(32),port))
    server.daemon_threads=True
    print(f"Wayfinder running at http://127.0.0.1:{port} — local single-traveler POC",flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()

if __name__=="__main__":main()
