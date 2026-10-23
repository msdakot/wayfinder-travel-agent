"""SQLite-backed single-traveler POC. All mutations are transaction-scoped."""
import copy
import hashlib
import json
import math
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from .documents import extract, validate_fields
from .engine import recover, timestamp

ROOT = Path(__file__).resolve().parents[2]

DEMO_COMMITMENTS = (
    {"title":"Dinner near The Marlow House","start":"2026-11-07T21:00:00+00:00","end":"2026-11-07T22:00:00+00:00","location":"Marylebone","type":"dining","flexibility":"flexible","source":"traveler"},
    {"title":"Conference — day 1","start":"2026-11-08T09:00:00+00:00","end":"2026-11-08T17:00:00+00:00","location":"ExCeL London","type":"event","flexibility":"fixed","source":"traveler"},
    {"title":"Beer crawl","start":"2026-11-08T20:00:00+00:00","end":"2026-11-08T23:00:00+00:00","location":"Bermondsey","type":"dining","flexibility":"fixed","source":"traveler"},
    {"title":"Conference — day 2","start":"2026-11-09T09:00:00+00:00","end":"2026-11-09T15:00:00+00:00","location":"ExCeL London","type":"event","flexibility":"fixed","source":"traveler"},
    {"title":"British Museum tickets","start":"2026-11-09T16:00:00+00:00","end":"2026-11-09T17:30:00+00:00","location":"British Museum","type":"tour","flexibility":"fixed","source":"traveler"},
    {"title":"London Eye tickets","start":"2026-11-09T19:00:00+00:00","end":"2026-11-09T20:00:00+00:00","location":"South Bank","type":"tour","flexibility":"fixed","source":"traveler"},
    {"title":"Client meeting","start":"2026-11-10T10:00:00+00:00","end":"2026-11-10T11:00:00+00:00","location":"King’s Cross","type":"meeting","flexibility":"fixed","source":"traveler"},
    {"title":"Lunch with a friend","start":"2026-11-10T12:30:00+00:00","end":"2026-11-10T14:00:00+00:00","location":"Covent Garden","type":"dining","flexibility":"flexible","source":"traveler"},
    {"title":"Flight home from Heathrow","start":"2026-11-10T18:00:00+00:00","end":"2026-11-10T19:00:00+00:00","location":"London Heathrow","type":"transport","flexibility":"fixed","source":"traveler"},
)


def uid():
    return uuid.uuid4().hex


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path, airport_travel_minutes=45, airport_buffer_minutes=120):
        for label,value in (("airport travel",airport_travel_minutes),("airport buffer",airport_buffer_minutes)):
            if type(value) is not int or not 0 <= value <= 360:
                raise ValueError(f"{label.title()} must be whole minutes from 0 to 360")
        self.path = str(path)
        self.airport_travel_minutes = airport_travel_minutes
        self.airport_buffer_minutes = airport_buffer_minutes
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS trips(id TEXT PRIMARY KEY,title TEXT NOT NULL,scenario TEXT,revision INTEGER NOT NULL DEFAULT 1,budget INTEGER NOT NULL DEFAULT 120,created TEXT);
                CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY,trip_id TEXT NOT NULL REFERENCES trips(id),name TEXT,hash TEXT,state TEXT,kind TEXT,fields TEXT,pages TEXT,created TEXT,UNIQUE(trip_id,hash));
                CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,trip_id TEXT NOT NULL REFERENCES trips(id),payload TEXT,created TEXT);
                CREATE TABLE IF NOT EXISTS plans(id TEXT PRIMARY KEY,trip_id TEXT NOT NULL REFERENCES trips(id),revision INTEGER,event_id TEXT,result TEXT,created TEXT,expires TEXT);
                CREATE TABLE IF NOT EXISTS actions(id TEXT PRIMARY KEY,trip_id TEXT NOT NULL REFERENCES trips(id),plan_id TEXT,option_id TEXT,state TEXT,result TEXT,created TEXT,UNIQUE(plan_id,option_id));
                CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY,trip_id TEXT NOT NULL REFERENCES trips(id),question TEXT,answer TEXT,created TEXT);
                CREATE TABLE IF NOT EXISTS logs(id TEXT PRIMARY KEY,trip_id TEXT,kind TEXT,detail TEXT,created TEXT);
                CREATE TABLE IF NOT EXISTS monitors(trip_id TEXT PRIMARY KEY REFERENCES trips(id),enabled INTEGER NOT NULL,provider TEXT NOT NULL,last_checked TEXT,updated TEXT);
                CREATE TABLE IF NOT EXISTS flight_statuses(id TEXT PRIMARY KEY,trip_id TEXT NOT NULL REFERENCES trips(id),provider TEXT NOT NULL,provider_event_id TEXT NOT NULL,flight_id TEXT NOT NULL,status TEXT NOT NULL,estimated_arrival TEXT,cause TEXT,observed_at TEXT NOT NULL,received_at TEXT NOT NULL,simulation INTEGER NOT NULL,fingerprint TEXT NOT NULL,UNIQUE(trip_id,provider,provider_event_id));
                CREATE TABLE IF NOT EXISTS notifications(id TEXT PRIMARY KEY,trip_id TEXT NOT NULL REFERENCES trips(id),status_id TEXT NOT NULL REFERENCES flight_statuses(id),kind TEXT NOT NULL,title TEXT NOT NULL,detail TEXT NOT NULL,source_mode TEXT NOT NULL,created TEXT NOT NULL,UNIQUE(trip_id,status_id,kind));
                CREATE TABLE IF NOT EXISTS commitments(id TEXT PRIMARY KEY,trip_id TEXT NOT NULL REFERENCES trips(id),title TEXT NOT NULL,start_at TEXT NOT NULL,end_at TEXT NOT NULL,location TEXT NOT NULL,activity_type TEXT NOT NULL,flexibility TEXT NOT NULL,source TEXT NOT NULL,created TEXT NOT NULL,updated TEXT NOT NULL,source_document_id TEXT REFERENCES documents(id),source_item_key TEXT);
                CREATE TABLE IF NOT EXISTS flight_checkins(trip_id TEXT NOT NULL REFERENCES trips(id),flight_id TEXT NOT NULL,checked_in INTEGER NOT NULL,checked_in_at TEXT,updated TEXT NOT NULL,PRIMARY KEY(trip_id,flight_id));
            ''')
            commitment_columns={row[1] for row in db.execute("PRAGMA table_info(commitments)")}
            if "source_document_id" not in commitment_columns:
                db.execute("ALTER TABLE commitments ADD COLUMN source_document_id TEXT")
            if "source_item_key" not in commitment_columns:
                db.execute("ALTER TABLE commitments ADD COLUMN source_item_key TEXT")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS commitments_document_item ON commitments(source_document_id,source_item_key) WHERE source_document_id IS NOT NULL")
        try:
            Path(self.path).chmod(0o600)
        except OSError:
            pass

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _trip(self, db, trip_id):
        row = db.execute("SELECT * FROM trips WHERE id=?", (trip_id,)).fetchone()
        if not row:
            raise ValueError("Itinerary not found")
        return dict(row)

    def log(self, db, trip_id, kind, detail):
        db.execute("INSERT INTO logs VALUES(?,?,?,?,?)", (uid(), trip_id, kind, detail, now()))

    def list_trips(self):
        with self.connection() as db:
            return [dict(r) for r in db.execute("SELECT * FROM trips ORDER BY created DESC")]

    def create(self, title="Untitled itinerary", demo=False, full_demo=False):
        trip_id = uid()
        with self.connection() as db:
            db.execute("INSERT INTO trips(id,title,scenario,created) VALUES(?,?,?,?)", (trip_id, title[:100], "heathrow-demo" if demo else "custom", now()))
        if demo:
            for file in sorted((ROOT / "data/sample-documents").glob("*.txt")):
                doc = self.upload(trip_id, file.name, file.read_bytes())
                self.review(trip_id, doc["id"], doc["kind"], doc["fields"])
            if full_demo:
                for commitment in DEMO_COMMITMENTS:
                    self.add_commitment(trip_id,commitment)
        return self.snapshot(trip_id)

    def upload(self, trip_id, filename, content):
        # Parsing is bounded; a transaction serializes the final slot allocation.
        parsed = extract(filename, content)
        digest = hashlib.sha256(content).hexdigest()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._trip(db, trip_id)
            existing = db.execute("SELECT * FROM documents WHERE trip_id=? AND hash=?", (trip_id, digest)).fetchone()
            if existing:
                return self.doc(dict(existing))
            count = db.execute("SELECT count(*) FROM documents WHERE trip_id=?", (trip_id,)).fetchone()[0]
            if count >= 15:
                raise ValueError("This itinerary already has 15 documents. Remove one before uploading another")
            doc_id = uid()
            db.execute("INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?)", (doc_id,trip_id,Path(filename).name[:160],digest,"needs_review",parsed["kind"],json.dumps(parsed["fields"]),json.dumps(parsed["pages"]),now()))
            db.execute("UPDATE trips SET revision=revision+1 WHERE id=?", (trip_id,))
            self.log(db,trip_id,"document_uploaded",Path(filename).name[:160])
            return self.doc(dict(db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()))

    @staticmethod
    def doc(row):
        return {**row,"fields":json.loads(row["fields"]),"pages":json.loads(row["pages"])}

    def review(self, trip_id, doc_id, kind, fields):
        fields = validate_fields(kind, fields)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._trip(db,trip_id)
            row = db.execute("SELECT * FROM documents WHERE id=? AND trip_id=?",(doc_id,trip_id)).fetchone()
            if not row:
                raise ValueError("Document not found in this itinerary")
            document_changed=row["state"]!="reviewed" or row["kind"]!=kind or json.loads(row["fields"])!=fields
            if kind == "itinerary":
                commitments_changed=self._upsert_itinerary_commitments(db,trip_id,doc_id,fields["items"])
            else:
                # Reclassifying an itinerary document removes only commitments
                # generated from that document; manual and other document items stay.
                commitments_changed=bool(db.execute("DELETE FROM commitments WHERE trip_id=? AND source_document_id=?",(trip_id,doc_id)).rowcount)
            if document_changed:
                db.execute("UPDATE documents SET kind=?,fields=?,state='reviewed' WHERE id=?",(kind,json.dumps(fields),doc_id))
            if document_changed or commitments_changed:
                db.execute("UPDATE trips SET revision=revision+1 WHERE id=?",(trip_id,))
                self.log(db,trip_id,"document_reviewed",row["name"])

    def _upsert_itinerary_commitments(self,db,trip_id,doc_id,items):
        """Replace one reviewed document's bounded items atomically, preserving stable ids."""
        other_count=db.execute(
            "SELECT count(*) FROM commitments WHERE trip_id=? AND (source_document_id IS NULL OR source_document_id<>?)",
            (trip_id,doc_id),
        ).fetchone()[0]
        if other_count+len(items)>100:
            raise ValueError("This itinerary would exceed the 100 commitment limit")
        existing={row["source_item_key"]:dict(row) for row in db.execute(
            "SELECT * FROM commitments WHERE trip_id=? AND source_document_id=?",(trip_id,doc_id)
        )}
        keep=[]
        changed=now()
        mutated=False
        for item in items:
            key=item["id"]
            keep.append(key)
            prior=existing.get(key)
            if prior:
                expected=(item["title"],item["start"],item["end"],item["location"],item["type"],item["flexibility"],"document")
                actual=tuple(prior[field] for field in ("title","start_at","end_at","location","activity_type","flexibility","source"))
                if actual!=expected:
                    db.execute(
                        "UPDATE commitments SET title=?,start_at=?,end_at=?,location=?,activity_type=?,flexibility=?,source='document',updated=? WHERE id=?",
                        (item["title"],item["start"],item["end"],item["location"],item["type"],item["flexibility"],changed,prior["id"]),
                    )
                    mutated=True
            else:
                created=changed
                db.execute(
                    "INSERT INTO commitments(id,trip_id,title,start_at,end_at,location,activity_type,flexibility,source,created,updated,source_document_id,source_item_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (uid(),trip_id,item["title"],item["start"],item["end"],item["location"],item["type"],item["flexibility"],"document",created,created,doc_id,key),
                )
                mutated=True
        placeholders=",".join("?" for _ in keep)
        removed=db.execute(
            f"DELETE FROM commitments WHERE trip_id=? AND source_document_id=? AND source_item_key NOT IN ({placeholders})",
            (trip_id,doc_id,*keep),
        ).rowcount
        return mutated or bool(removed)

    def delete_document(self, trip_id, doc_id):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._trip(db,trip_id)
            row=db.execute("SELECT id FROM documents WHERE id=? AND trip_id=?",(doc_id,trip_id)).fetchone()
            if not row:
                raise ValueError("Document not found")
            db.execute("DELETE FROM commitments WHERE trip_id=? AND source_document_id=?",(trip_id,doc_id))
            db.execute("DELETE FROM documents WHERE id=? AND trip_id=?",(doc_id,trip_id))
            db.execute("UPDATE trips SET revision=revision+1 WHERE id=?",(trip_id,))
            # Answers/plans contain quoted evidence: invalidate and remove them too.
            db.execute("DELETE FROM messages WHERE trip_id=?",(trip_id,))
            db.execute("DELETE FROM plans WHERE trip_id=?",(trip_id,))
            db.execute("DELETE FROM events WHERE trip_id=?",(trip_id,))
            db.execute("DELETE FROM notifications WHERE trip_id=?",(trip_id,))
            db.execute("DELETE FROM flight_statuses WHERE trip_id=?",(trip_id,))
            db.execute("DELETE FROM monitors WHERE trip_id=?",(trip_id,))
            db.execute("DELETE FROM flight_checkins WHERE trip_id=?",(trip_id,))
            self.log(db,trip_id,"document_deleted","Document removed; plans, events and answers invalidated")

    def snapshot(self, trip_id):
        with self.connection() as db:
            trip = self._trip(db,trip_id)
            docs = [self.doc(dict(r)) for r in db.execute("SELECT * FROM documents WHERE trip_id=? ORDER BY created",(trip_id,))]
            event_row = db.execute("SELECT * FROM events WHERE trip_id=? ORDER BY created DESC LIMIT 1",(trip_id,)).fetchone()
            plan_row = db.execute("SELECT * FROM plans WHERE trip_id=? ORDER BY created DESC LIMIT 1",(trip_id,)).fetchone()
            messages = [{**dict(r),"answer":json.loads(r["answer"])} for r in db.execute("SELECT * FROM messages WHERE trip_id=? ORDER BY created",(trip_id,))]
            actions = [{**dict(r),"result":json.loads(r["result"])} for r in db.execute("SELECT * FROM actions WHERE trip_id=? ORDER BY created DESC",(trip_id,))]
            logs = [dict(r) for r in db.execute("SELECT * FROM logs WHERE trip_id=? ORDER BY created DESC LIMIT 20",(trip_id,))]
            monitor_row = db.execute("SELECT * FROM monitors WHERE trip_id=?",(trip_id,)).fetchone()
            status_rows = [dict(r) for r in db.execute("SELECT * FROM flight_statuses WHERE trip_id=? ORDER BY observed_at DESC, received_at DESC LIMIT 20",(trip_id,))]
            notification_rows = [dict(r) for r in db.execute("SELECT * FROM notifications WHERE trip_id=? ORDER BY created DESC LIMIT 20",(trip_id,))]
            commitment_rows = [dict(r) for r in db.execute("SELECT * FROM commitments WHERE trip_id=? ORDER BY start_at,id",(trip_id,))]
            checkin_rows = [dict(r) for r in db.execute("SELECT * FROM flight_checkins WHERE trip_id=?",(trip_id,))]
        approved = [d for d in docs if d["state"]=="reviewed"]
        facts, conflicts = {}, []
        for d in approved:
            if d["kind"]=="itinerary":
                continue
            for key,value in d["fields"].items():
                name = d["kind"]+"."+key
                if name in facts and facts[name]["value"] != value:
                    conflicts.append(name)
                facts[name]={"value":value,"document_id":d["id"],"document_name":d["name"]}
        event = json.loads(event_row["payload"]) if event_row else None
        plan = {**dict(plan_row),"result":json.loads(plan_row["result"])} if plan_row else None
        if plan:
            plan["stale"] = plan["revision"] != trip["revision"] or plan["expires"] < now()
        commitments = [self.commitment(dict(row)) for row in commitment_rows]
        commitments = self.analyze_commitment_impacts(commitments, event, plan, facts)
        safe_commitments = [
            {key: item[key] for key in ("id","title","start","end","location","type","flexibility","source","impact")}
            for item in commitments
        ]
        flight_id=facts.get("flight.flight_id",{}).get("value")
        checkin=next((row for row in checkin_rows if row["flight_id"]==flight_id),None)
        flight_experience=self.flight_experience(trip,facts,event,plan,actions,commitments,checkin)
        train_experience=self.train_experience(trip,facts,event,plan,actions)
        place_experiences=self.place_experiences(commitments)
        return {"trip":trip,"documents":docs,"facts":facts,"conflicts":sorted(set(conflicts)),"event":event,"plan":plan,"messages":messages,"actions":actions,"logs":logs,"monitor":dict(monitor_row) if monitor_row else None,"flight_statuses":status_rows,"notifications":notification_rows,"commitments":commitments,"flight_experience":flight_experience,"train_experience":train_experience,"place_experiences":place_experiences,"snowflake_safe_context":{"itinerary_id":trip["id"],"commitments":safe_commitments},"document_count":len(docs)}

    @staticmethod
    def _fact_value(facts,key,default=None):
        return facts.get(key,{}).get("value",default)

    def place_experiences(self,commitments):
        """Return map guidance only for public places in the bounded demo registry.

        A name or address must match the registry; unknown/private locations never
        receive guessed coordinates. Chile origins come from an explicit route
        source when it is known, otherwise from the preceding mapped itinerary
        stop. The browser can replace these estimates with a current Google route.
        """
        places={
            # London sample.
            "excel":{"name":"ExCeL London","address":"Royal Victoria Dock, London E16 1XL","lat":51.5080,"lng":0.0296,"country":"GB"},
            "bermondsey":{"name":"Bermondsey Beer Mile","address":"Bermondsey, London SE1","lat":51.4971,"lng":-0.0810,"country":"GB"},
            "british_museum":{"name":"The British Museum","address":"Great Russell Street, London WC1B 3DG","lat":51.5194,"lng":-0.1270,"country":"GB"},
            "london_eye":{"name":"The London Eye","address":"Riverside Building, County Hall, London SE1 7PB","lat":51.5033,"lng":-0.1196,"country":"GB"},
            "kings_cross":{"name":"King’s Cross","address":"Euston Road, London N1 9AL","lat":51.5308,"lng":-0.1238,"country":"GB"},
            "covent_garden":{"name":"Covent Garden","address":"Covent Garden, London WC2E","lat":51.5117,"lng":-0.1240,"country":"GB"},
            "marylebone":{"name":"Marylebone","address":"Marylebone, London W1","lat":51.5220,"lng":-0.1510,"country":"GB"},
            "heathrow":{"name":"London Heathrow Airport","address":"Hounslow TW6","lat":51.4700,"lng":-0.4543,"country":"GB","kind":"airport"},
            # Public airports used by the reviewed Chile itinerary.
            "sfo":{"name":"San Francisco International Airport","address":"San Francisco, CA 94128","lat":37.6213,"lng":-122.3790,"country":"US","kind":"airport"},
            "dfw":{"name":"Dallas Fort Worth International Airport","address":"DFW Airport, TX 75261","lat":32.8998,"lng":-97.0403,"country":"US","kind":"airport"},
            "scl":{"name":"Santiago International Airport","address":"Armando Cortínez Ote. 1704, Pudahuel, Chile","lat":-33.3892733,"lng":-70.7900043,"country":"CL","kind":"airport"},
            "cjc":{"name":"El Loa Airport","address":"Acceso Aeródromo El Loa, Calama, Chile","lat":-22.4996754,"lng":-68.8960387,"country":"CL","kind":"airport"},
            "pnt":{"name":"Teniente Julio Gallardo Airport","address":"Ruta Y-300, Puerto Natales, Chile","lat":-51.6701370,"lng":-72.5260252,"country":"CL","kind":"airport"},
            # Addresses and destinations present in the reviewed confirmations.
            "backpackers_san_pedro":{"name":"Backpackers San Pedro Hostel","address":"Pasaje Portal del Inca 486, San Pedro de Atacama, Chile","lat":-22.9076669,"lng":-68.1969128,"country":"CL"},
            "socaire_control":{"name":"Socaire control office","address":"Socaire, Antofagasta, Chile","lat":-23.5874448,"lng":-67.89106072,"country":"CL"},
            "piedras_rojas":{"name":"Piedras Rojas and Salar de Aguas Calientes Sur","address":"Ruta 23 CH, Antofagasta, Chile","lat":-23.9213249,"lng":-67.6964003,"country":"CL","kind":"tour"},
            "yaganhouse":{"name":"YaganHouse","address":"O’Higgins 584, Puerto Natales, Chile","lat":-51.72927094,"lng":-72.50266266,"country":"CL"},
            "chumango":{"name":"Hostel Chuman-Go","address":"Avenida Santiago Bueras 775, Puerto Natales, Chile","lat":-51.7352138,"lng":-72.4957299,"country":"CL"},
            "central":{"name":"Central Mountain Hostel","address":"Central Sector, Torres del Paine National Park, Chile","lat":-50.9666937,"lng":-72.8700867,"country":"CL","kind":"trail"},
            "las_torres":{"name":"Mirador Base Las Torres","address":"Torres del Paine National Park, Chile","lat":-50.9426680,"lng":-72.9496630,"country":"CL","kind":"trail"},
            "frances":{"name":"Francés Mountain Hostel and Sector","address":"Francés Sector, Torres del Paine National Park, Chile","lat":-51.0260641,"lng":-73.0272705,"country":"CL","kind":"trail"},
            "paine_grande":{"name":"Paine Grande Mountain Hostel","address":"Paine Grande Sector, Torres del Paine National Park, Chile","lat":-51.0726521,"lng":-73.0937822,"country":"CL","kind":"trail"},
            "grey":{"name":"Grey Glacier lookout","address":"Grey Sector, Torres del Paine National Park, Chile","lat":-51.0021735,"lng":-73.1826253,"country":"CL","kind":"trail"},
            "pehoe":{"name":"Lake Pehoé catamaran landing","address":"Lake Pehoé, Torres del Paine National Park, Chile","lat":-51.0922343,"lng":-73.0327537,"country":"CL","kind":"trail"},
        }
        aliases={
            "excel":["excel london","excel"],"bermondsey":["bermondsey beer mile","bermondsey"],
            "british_museum":["british museum"],"london_eye":["london eye"],
            "kings_cross":["king’s cross","king's cross","kings cross"],"covent_garden":["covent garden"],
            "marylebone":["marylebone"],"heathrow":["london heathrow","heathrow airport","heathrow"],
            "sfo":["san francisco international airport","sfo airport"],
            "dfw":["dallas fort worth international airport","dfw airport"],
            "scl":["santiago international airport","arturo merino benítez airport","comodoro arturo merino benítez"],
            "cjc":["el loa airport","aeropuerto el loa","calama airport"],
            "pnt":["teniente julio gallardo airport","puerto natales airport"],
            "backpackers_san_pedro":["backpackers san pedro","portal del inca 486","pasaje portal del inca 486"],
            "socaire_control":["socaire control office","socaire check-in","socaire tour check-in"],
            "piedras_rojas":["salar de aguas calientes sur","piedras rojas","lagunas miscanti","laguna miscanti","miñiques","miniques"],
            "yaganhouse":["yaganhouse","yagán house","yagan house","o'higgins 584","o’higgins 584"],
            "chumango":["hostel chuman-go","hostel chumango","santiago bueras 775"],
            "central":["central mountain hostel","central sector","camping central"],
            "las_torres":["las torres base","base las torres","mirador base torres"],
            "frances":["francés mountain hostel","frances mountain hostel","francés sector","frances sector","francés valley","frances valley"],
            "paine_grande":["paine grande mountain hostel","paine grande sector","paine grande"],
            "grey":["grey glacier lookout","grey glacier","mirador grey"],
            "pehoe":["pehoé catamaran","pehoe catamaran","pehoé lake","pehoe lake","lake pehoé","lake pehoe"],
        }
        alias_index=sorted(
            ((alias.lower(),key) for key,names in aliases.items() for alias in names),
            key=lambda pair:len(pair[0]),reverse=True,
        )

        def resolve(text):
            normalized=str(text or "").lower()
            match=next(((alias,key) for alias,key in alias_index if alias in normalized),None)
            if not match:
                return None,None
            return match[1],match[0]

        london_origin={"name":"The Marlow House","address":"Marylebone, London","lat":51.5216,"lng":-0.1580,"country":"GB"}
        previous=None
        results=[]
        for item in commitments:
            title=str(item.get("title") or "").strip()
            location=str(item.get("location") or "").strip()
            query=f"{title} {location}".strip().lower()
            route_parts=re.split(r"\s+to\s+",location,flags=re.IGNORECASE)
            destination_text=route_parts[-1] if len(route_parts)>1 else query
            key,matched=resolve(destination_text)
            if not key and len(route_parts)==1:
                key,matched=resolve(query)
            if not key:
                continue
            destination=copy.deepcopy(places[key])
            country=destination["country"]
            source_key,_=resolve(route_parts[0]) if len(route_parts)>1 else (None,None)
            if country=="GB":
                origin=copy.deepcopy(london_origin)
            elif source_key:
                origin=copy.deepcopy(places[source_key])
            elif previous and previous.get("country")==country:
                origin=copy.deepcopy(previous)
            elif country=="CL":
                # A public arrival point is safer than inventing a hotel/current
                # location when the first Chile item has no resolvable predecessor.
                fallback="pnt" if destination["lat"] < -45 else "cjc" if destination["lat"] > -30 else "scl"
                origin=copy.deepcopy(places[fallback])
            else:
                origin=copy.deepcopy(destination)

            lat1,lat2=map(math.radians,(origin["lat"],destination["lat"]))
            dlat=lat2-lat1
            dlng=math.radians(destination["lng"]-origin["lng"])
            hav=math.sin(dlat/2)**2+math.cos(lat1)*math.cos(lat2)*math.sin(dlng/2)**2
            distance=6371*2*math.atan2(math.sqrt(hav),math.sqrt(max(0,1-hav)))
            start=timestamp(item["start"]) if item.get("start") else None
            arrive=start.isoformat() if start else None
            currency={"GB":"GBP","CL":"CLP","US":"USD"}.get(country,"USD")

            if destination.get("kind")=="trail":
                duration=max(10,round(distance/3.5*60))
                mode_specs=(("walk","Hike",duration,0),)
                recommended="walk"
            elif distance>100:
                # Long itinerary legs are already booked transport. Avoid
                # presenting an absurd taxi/walking estimate across a country.
                booked_minutes=None
                if item.get("start") and item.get("end"):
                    booked_minutes=max(1,round((timestamp(item["end"])-timestamp(item["start"])).total_seconds()/60))
                duration=booked_minutes or max(30,round(distance/650*60+60))
                mode_specs=(("transit","Booked transport",duration,None),)
                recommended="transit"
            else:
                durations={
                    "walk":max(8,round(distance/4.5*60*1.2)),
                    "transit":max(12,round(distance/20*60+10)),
                    "taxi":max(8,round(distance/25*60+6)),
                }
                recommended="walk" if distance<=2.2 else "transit"
                if country=="GB":
                    costs={"walk":0,"transit":3,"taxi":max(12,round(distance*3+5))}
                else:
                    # The confirmations do not establish local fares.
                    costs={"walk":0,"transit":None,"taxi":None}
                available_modes=[]
                if country=="GB" or distance<=8:
                    available_modes.append(("walk","Walk"))
                available_modes.append(("transit","Public transit"))
                available_modes.append(("taxi","Taxi"))
                mode_specs=tuple((mode,label,durations[mode],costs[mode]) for mode,label in available_modes)

            modes=[]
            for mode,label,duration,cost in mode_specs:
                leave=(start-timedelta(minutes=duration+10)).isoformat() if start else None
                modes.append({"id":mode,"label":label,"duration_minutes":duration,"cost":{"amount":cost,"currency":currency},"recommended":mode==recommended,"leave_at":leave,"arrive_by":arrive})
            results.append({
                "commitment_id":item["id"],"title":item["title"],"start":item.get("start"),
                "source_document_id":item.get("source_document_id"),"source_item_key":item.get("source_item_key"),
                "origin":origin,"destination":destination,"distance_km":round(distance,1),
                "resolution":{"source":"bounded_public_place_registry","query":query,"matched":matched,"place_key":key},
                "recommended_mode":recommended,"modes":modes,
                "freshness":{"live":False,"label":"Trip estimate · use your location for a live Google route"},
                "assumption":"The destination is matched from the reviewed itinerary. Route times are estimates until you request live directions from your current location.",
            })
            previous=destination
        return results

    def flight_experience(self,trip,facts,event,plan,actions,commitments,checkin):
        """Build the traveler-facing state for one reviewed flight.

        This does not call or imply an airline check-in. The fixed journey-to-airport
        inputs are deliberately exposed so a production travel-time service can replace
        them without changing the UI contract.
        """
        flight_id=self._fact_value(facts,"flight.flight_id")
        if not flight_id:
            return None
        boarding_at=self._fact_value(facts,"flight.boarding_time")
        leave_for_airport=None
        if boarding_at:
            leave_at=timestamp(boarding_at)-timedelta(minutes=self.airport_travel_minutes+self.airport_buffer_minutes)
            leave_for_airport={
                "recommended_at":leave_at.isoformat(),
                "boarding_at":timestamp(boarding_at).isoformat(),
                "travel_duration_minutes":self.airport_travel_minutes,
                "airport_buffer_minutes":self.airport_buffer_minutes,
                "basis":f"Boarding time minus {self.airport_travel_minutes} minutes to the airport and a {self.airport_buffer_minutes}-minute airport buffer",
                "assumption":"Fixed POC estimate. Live traffic, origin location, security lines, and airline guidance are not connected.",
            }
        checked_in=bool(checkin and checkin["checked_in"])
        check_in={
            "status":"checked_in" if checked_in else "not_checked_in",
            "checked_in":checked_in,
            "checked_in_at":checkin["checked_in_at"] if checked_in else None,
            "action_required":not checked_in,
            "message":"Demo check-in confirmed. No airline record was changed." if checked_in else "Check in to reveal your demo boarding pass. This does not contact the airline.",
            "simulation":True,
        }
        boarding_pass=None
        if checked_in:
            reference=str(self._fact_value(facts,"flight.booking_reference","DEMO"))
            token=hashlib.sha256(f"wayfinder-demo:{trip['id']}:{flight_id}:{reference}".encode()).hexdigest()[:20].upper()
            boarding_pass={
                "flight":flight_id,
                "traveler":self._fact_value(facts,"passport.holder","Traveler"),
                "seat":self._fact_value(facts,"flight.seat","Not assigned"),
                "boarding_time":boarding_at,
                "terminal":self._fact_value(facts,"flight.terminal","Not assigned"),
                "gate":self._fact_value(facts,"flight.gate","Not assigned"),
                "booking_reference":reference,
                "barcode":{"token":f"DEMO-{token}","format":"demo_token","scannable":False},
                "notice":"Demo boarding pass only. It cannot be used for travel.",
            }
        scheduled_arrival=self._fact_value(facts,"flight.arrival")
        estimated_arrival=(event or {}).get("estimated_arrival") or (event or {}).get("arrival") or scheduled_arrival
        delay_minutes=0
        if scheduled_arrival and estimated_arrival:
            delay_minutes=max(0,round((timestamp(estimated_arrival)-timestamp(scheduled_arrival)).total_seconds()/60))
        status=(event or {}).get("status") or "scheduled"
        delay={
            "status":status,
            "minutes":delay_minutes,
            "scheduled_arrival":scheduled_arrival,
            "estimated_arrival":estimated_arrival,
            "cause":(event or {}).get("cause"),
            "source":"simulated_replay" if (event or {}).get("simulation") else "live_provider" if event else "reviewed_itinerary",
        }
        result=plan.get("result") if plan else None
        selected=next((action for action in actions if plan and action["plan_id"]==plan["id"]),None)
        impacted=[{
            "id":item["id"],"title":item["title"],"status":item["impact"]["status"],
            "suggested_action":item["impact"].get("suggested_action"),
        } for item in commitments if item.get("impact",{}).get("affected") is not False]
        if selected:
            recovery_status="selected"
        elif result and result.get("connection_missed"):
            recovery_status="options_ready"
        elif status in {"cancelled","diverted","incident"}:
            recovery_status="manual_review"
        elif status=="delayed" and not result:
            recovery_status="unavailable"
        else:
            recovery_status="not_needed"
        connection_missed=result.get("connection_missed") if result else None
        if selected:
            summary="A recovery option has been selected. Follow the updated itinerary."
        elif connection_missed:
            summary="The delay breaks the reviewed onward connection. Recovery options are ready."
        elif impacted:
            summary=f"{len(impacted)} itinerary item{'s are' if len(impacted)!=1 else ' is'} affected or needs review."
        elif delay_minutes:
            summary="The flight is delayed, but no downstream conflict is currently identified."
        else:
            summary="No downstream itinerary conflict is currently identified."
        affected={
            "connection_missed":connection_missed,
            "commitments":impacted,
            "recovery_status":recovery_status,
            "recommended_option":result.get("recommended_option") if result and recovery_status=="options_ready" else None,
            "summary":summary,
        }
        return {
            "flight_id":flight_id,
            "check_in":check_in,
            "boarding_pass":boarding_pass,
            "leave_for_airport":leave_for_airport,
            "delay":delay,
            "affected":affected,
        }

    def train_experience(self,trip,facts,event,plan,actions):
        """Build a traveler-facing view of the reviewed onward train.

        Rail status and ground timings are deliberately labeled. This POC has no
        live rail, traffic, maps, or ticketing integration; the flight-derived
        connection state is deterministic and the journey estimates are fixed.
        """
        departure=self._fact_value(facts,"connection.departure")
        route=self._fact_value(facts,"connection.route")
        if not departure and not route:
            return None
        booking_reference=self._fact_value(facts,"connection.booking_reference")
        source=facts.get("connection.departure") or facts.get("connection.route") or {}
        passenger=self._fact_value(facts,"passport.holder","Traveler")
        result=plan.get("result") if plan and not plan.get("stale") else None
        selected=next((action for action in actions if plan and action["plan_id"]==plan["id"]),None)
        selected_option=(selected or {}).get("result",{}).get("option") if selected else None

        state="scheduled"
        label="Scheduled"
        detail="The booked train is shown from the reviewed itinerary."
        status_source="reviewed_itinerary"
        active_departure=departure
        if result and result.get("connection_missed"):
            state="missed"
            label="Connection missed"
            detail="The latest flight timing no longer leaves enough time to reach this train."
            status_source="derived_from_flight_recovery"
        elif event and event.get("status")=="delayed":
            state="connection_at_risk"
            label="Connection still reachable"
            detail="The flight changed, but the reviewed transfer buffer still fits."
            status_source="derived_from_flight_recovery"
        if selected_option:
            status_source="selected_recovery_option"
            if selected_option.get("mode")=="train":
                state="replacement_selected"
                label="Replacement train selected"
                detail="The selected recovery train is now the active demo plan."
                active_departure=selected_option.get("departure") or departure
            else:
                state="alternate_transport_selected"
                label=f"{str(selected_option.get('mode','Alternative')).title()} selected"
                detail="The selected recovery option replaces the original train in this demo."

        original_connection_missed=bool((result and result.get("connection_missed")) or selected_option)
        original_connection={
            "departure_at":departure,
            "state":"missed" if original_connection_missed else ("at_risk" if event and event.get("status")=="delayed" else "scheduled"),
            "label":"Missed" if original_connection_missed else ("At risk" if event and event.get("status")=="delayed" else "Scheduled"),
            "reason":"The flight now arrives after this train leaves." if original_connection_missed else None,
        }
        recovery_connection=None
        if selected_option:
            recovery_connection={
                "mode":selected_option.get("mode"),
                "departure_at":selected_option.get("departure"),
                "hotel_arrival":selected_option.get("hotel_arrival"),
                "cost":selected_option.get("cost"),
                "selected":True,
            }

        transfer_minutes=45
        leave_at=(timestamp(active_departure)-timedelta(minutes=transfer_minutes)).isoformat() if active_departure else None
        station_departure={
            "station":"Heathrow Terminals 2 & 3 rail station",
            "location":"Heathrow Airport, London",
            "departure_at":active_departure,
            "recommended_leave_at":leave_at,
            "travel_minutes":15,
            "mode":"walk",
            "from":"Heathrow arrivals",
            "basis":"Train departure minus a fixed 15-minute terminal-to-station walk and 30-minute navigation and baggage buffer.",
            "assumption":"Fixed POC estimate. Live walking time, terminal assignment, baggage delivery, border queues, and station access are not connected.",
        }
        ticket={
            "booked":bool(booking_reference),
            "passenger":passenger,
            "booking_reference":booking_reference,
            "coach":"Not assigned",
            "seat":"Not assigned",
            "valid_for":departure,
            "source_document":source.get("document_name"),
            "notice":"Reviewed demo ticket details only. This is not a scannable rail ticket and no rail booking has been changed.",
        }

        station_arrival=(timestamp(active_departure)+timedelta(minutes=35)) if active_departure else None
        deadline=self._fact_value(facts,"hotel.check_in_deadline")
        hotel=self._fact_value(facts,"hotel.hotel_name","The Marlow House")
        modes=[
            ("walk","Walk",22,0,"Door-to-door walking route from Paddington; use only with manageable luggage."),
            ("bus","Bus",18,3,"Local bus estimate including a short walk from the stop."),
            ("taxi","Taxi",12,24,"Direct taxi estimate from the station rank."),
        ]
        last_mile=[]
        original_missed=state=="missed"
        train_active=state not in {"missed","alternate_transport_selected"}
        for option_id,mode,duration,cost,description in modes:
            arrival_at=(station_arrival+timedelta(minutes=duration)) if station_arrival else None
            reasons=[]
            if not train_active:
                reasons.append("Choose a viable recovery option before planning from Paddington.")
            if arrival_at and deadline and arrival_at>timestamp(deadline):
                reasons.append("Estimated arrival is after the reviewed hotel check-in deadline.")
            if cost>trip["budget"]:
                reasons.append("Estimated cost exceeds the trip's additional-spend limit.")
            feasibility="feasible" if not reasons else "blocked"
            last_mile.append({
                "id":option_id,"mode":mode,"duration_minutes":duration,
                "cost":{"amount":cost,"currency":"USD"},
                "feasibility":feasibility,"reasons":reasons,"detail":description,
                "recommended":option_id=="bus" and feasibility=="feasible",
                "arrival_at":arrival_at.isoformat() if arrival_at else None,
            })
        return {
            "route":route,
            "status":{"state":state,"label":label,"detail":detail,"source":status_source},
            "original_connection":original_connection,
            "recovery_connection":recovery_connection,
            "freshness":{"live":False,"last_checked":None,"label":"Reviewed itinerary · no live rail feed connected"},
            "ticket":ticket,
            "station_departure":station_departure,
            "destination":{"station":"London Paddington","name":hotel,"address":"Address not present in reviewed documents"},
            "last_mile":last_mile,
            "assumptions":[
                "Train details come from the reviewed itinerary, not a live rail provider.",
                "Station walk and last-mile durations and prices are fixed demo estimates.",
                "USD estimates are used to match the demo trip budget; no currency conversion is performed.",
            ],
        }

    def set_check_in(self,trip_id,checked_in=True):
        if type(checked_in) is not bool:
            raise ValueError("Checked-in state must be true or false")
        snap=self.snapshot(trip_id)
        flight_id=self._fact_value(snap["facts"],"flight.flight_id")
        if not flight_id:
            raise ValueError("Review a flight booking before checking in")
        changed_at=now()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._trip(db,trip_id)
            current=db.execute("SELECT checked_in FROM flight_checkins WHERE trip_id=? AND flight_id=?",(trip_id,flight_id)).fetchone()
            if current is not None and bool(current["checked_in"])==checked_in:
                return self.snapshot(trip_id)
            checked_in_at=changed_at if checked_in else None
            db.execute(
                "INSERT INTO flight_checkins(trip_id,flight_id,checked_in,checked_in_at,updated) VALUES(?,?,?,?,?) "
                "ON CONFLICT(trip_id,flight_id) DO UPDATE SET checked_in=excluded.checked_in,checked_in_at=excluded.checked_in_at,updated=excluded.updated",
                (trip_id,flight_id,int(checked_in),checked_in_at,changed_at),
            )
            db.execute("UPDATE trips SET revision=revision+1 WHERE id=?",(trip_id,))
            # Check-in changes presentation state, not recovery feasibility. Keep the
            # latest still-valid recovery plan bound to the new revision.
            db.execute(
                "UPDATE plans SET revision=(SELECT revision FROM trips WHERE id=?) "
                "WHERE id=(SELECT id FROM plans WHERE trip_id=? ORDER BY created DESC LIMIT 1) AND expires>=?",
                (trip_id,trip_id,now()),
            )
            self.log(db,trip_id,"demo_check_in_changed","checked in" if checked_in else "not checked in")
        return self.snapshot(trip_id)

    @staticmethod
    def commitment(row):
        item = {
            "id":row["id"], "trip_id":row["trip_id"], "title":row["title"],
            "start":row["start_at"], "end":row["end_at"], "location":row["location"],
            "type":row["activity_type"], "kind":row["activity_type"], "flexibility":row["flexibility"], "source":row["source"],
            "created":row["created"], "updated":row["updated"],
        }
        if "source_document_id" in row.keys() and row["source_document_id"]:
            item["source_document_id"] = row["source_document_id"]
            item["source_item_key"] = row["source_item_key"]
        return item

    @staticmethod
    def validate_commitment(data, existing=None):
        if not isinstance(data, dict):
            raise ValueError("Commitment must be an object")
        allowed={"title","start","end","location","type","flexibility","source"}
        if set(data)-allowed:
            raise ValueError("Unexpected commitment fields: "+", ".join(sorted(set(data)-allowed)))
        merged={key:existing.get(key) if existing else None for key in allowed}
        merged.update(data)
        for key,limit in (("title",120),("location",200),("source",200)):
            value=merged.get(key)
            if not isinstance(value,str) or not value.strip() or len(value.strip())>limit:
                raise ValueError(f"Commitment {key} must be 1 to {limit} characters")
            merged[key]=" ".join(value.split())
        if merged.get("type") not in {"meeting","tour","dining","transport","lodging","event","personal","other"}:
            raise ValueError("Unsupported commitment type")
        if merged.get("flexibility") not in {"fixed","flexible","optional"}:
            raise ValueError("Flexibility must be fixed, flexible, or optional")
        start=timestamp(merged.get("start"))
        end=timestamp(merged.get("end"))
        if end <= start:
            raise ValueError("Commitment end must be after its start")
        merged["start"]=start.astimezone(timezone.utc).isoformat()
        merged["end"]=end.astimezone(timezone.utc).isoformat()
        return merged

    def parse_commitment_text(self, trip_id, text):
        """Parse one deliberately narrow London-demo phrase; reject all ambiguity."""
        if not isinstance(text,str) or not 1<=len(text.strip())<=500:
            raise ValueError("Commitment text must be 1 to 500 characters")
        snap=self.snapshot(trip_id)
        if snap["trip"]["scenario"]!="heathrow-demo":
            raise ValueError("Natural-language commitment capture is scoped to the London sample; use structured timestamps with UTC offsets")
        date=r"[A-Za-z]{3,9}\s+\d{1,2}(?:,?\s+\d{4})?"
        time=r"(?:\d{1,2}(?::\d{2})?\s*(?:AM|PM)|noon|midnight)"
        patterns=[
            (rf"^(?P<title>.+?)\s+on\s+(?P<date>{date})\s+from\s+(?P<start>{time})\s+to\s+(?P<end>{time})\s+at\s+(?P<location>.+?)\s*$",False,None),
            (rf"^(?P<title>.+?)\s+on\s+(?P<date>{date})\s+from\s+(?P<start>{time})\s+to\s+(?P<end>{time})\s*$",False,"title"),
            (rf"^(?P<title>.+?)\s+at\s+(?P<location>.+?)\s+on\s+(?P<date>{date})\s+at\s+(?P<start>{time}),?\s+flexible\s+by\s+(?P<flex_minutes>\d{{1,3}})\s+minutes?\s*$",True,None),
            (rf"^(?P<title>.+?)\s+at\s+(?P<start>{time})\s+on\s+(?P<date>{date})\s+at\s+(?P<location>.+?)\s*$",True,None),
        ]
        parts=None;single_time=False;location_default=None
        for pattern,is_single,default_location in patterns:
            match=re.fullmatch(pattern,text.strip(),re.I)
            if match:
                parts=match.groupdict();single_time=is_single;location_default=default_location;break
        if parts is None:
            raise ValueError("Use an activity, date, explicit time or time range, and location")
        if location_default=="title":
            parts["location"]=parts["title"]
        if re.search(r"\d{4}",parts["date"]):
            date_text=parts["date"].replace(",","")
        else:
            arrival=snap["facts"].get("flight.arrival",{}).get("value")
            if not arrival:
                raise ValueError("Include a four-digit year because the itinerary year is unknown")
            date_text=f"{parts['date']} {timestamp(arrival).year}"
        def parse_local(time_text):
            normalized=re.sub(r"\s+"," ",time_text.strip().upper())
            normalized={"NOON":"12 PM","MIDNIGHT":"12 AM"}.get(normalized,normalized)
            suffix="%I:%M %p" if ":" in normalized else "%I %p"
            for month in ("%b","%B"):
                try:
                    naive=datetime.strptime(f"{date_text} {normalized}",f"{month} %d %Y {suffix}")
                except ValueError:
                    continue
                zone=ZoneInfo("Europe/London")
                first=naive.replace(tzinfo=zone,fold=0)
                second=naive.replace(tzinfo=zone,fold=1)
                if first.utcoffset()!=second.utcoffset():
                    raise ValueError("Commitment time is ambiguous at a daylight-saving transition; use structured timestamps with an offset")
                if first.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)!=naive:
                    raise ValueError("Commitment time does not exist at a daylight-saving transition; use structured timestamps with an offset")
                return first
            raise ValueError("Commitment date or time is invalid or ambiguous")
        start=parse_local(parts["start"])
        end=start+timedelta(hours=1) if single_time else parse_local(parts["end"])
        if end<=start:
            raise ValueError("Commitment end must be after its start; include separate dates for overnight activities using structured fields")
        title=" ".join(parts["title"].split())
        lower=title.lower()
        if any(word in lower for word in ("dinner","lunch","breakfast","restaurant","beer crawl")):
            activity_type="dining"
        elif "conference" in lower or "event" in lower:
            activity_type="event"
        elif "meeting" in lower:
            activity_type="meeting"
        elif "tour" in lower or "museum" in lower:
            activity_type="tour"
        elif any(word in lower for word in ("train","flight","transfer")):
            activity_type="transport"
        else:
            activity_type="other"
        flexibility="flexible" if parts.get("flex_minutes") else "fixed"
        return {"title":title,"start":start.isoformat(),"end":end.isoformat(),"location":parts["location"],"type":activity_type,"flexibility":flexibility,"source":"chat"}

    def capture_commitment(self, trip_id, data):
        if not isinstance(data,dict):
            raise ValueError("Commitment must be an object")
        if "text" in data:
            if set(data)!={"text"}:
                raise ValueError("Send either text or structured commitment fields, not both")
            data=self.parse_commitment_text(trip_id,data["text"])
        return self.add_commitment(trip_id,data)

    def add_commitment(self, trip_id, data):
        item=self.validate_commitment(data)
        commitment_id=uid()
        created=now()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._trip(db,trip_id)
            existing=db.execute(
                "SELECT * FROM commitments WHERE trip_id=? AND lower(title)=lower(?) AND start_at=? AND end_at=? AND lower(location)=lower(?)",
                (trip_id,item["title"],item["start"],item["end"],item["location"]),
            ).fetchone()
            if existing:
                existing_id=existing["id"]
            else:
                existing_id=None
            if existing_id is None:
                if db.execute("SELECT count(*) FROM commitments WHERE trip_id=?",(trip_id,)).fetchone()[0]>=100:
                    raise ValueError("This itinerary already has 100 commitments")
                db.execute(
                    "INSERT INTO commitments(id,trip_id,title,start_at,end_at,location,activity_type,flexibility,source,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (commitment_id,trip_id,item["title"],item["start"],item["end"],item["location"],item["type"],item["flexibility"],item["source"],created,created),
                )
                db.execute("UPDATE trips SET revision=revision+1 WHERE id=?",(trip_id,))
                # Personal plans enrich the disruption context but do not change
                # transport inventory or feasibility. Keep the latest recovery
                # proposal bound to the new itinerary revision so it remains
                # actionable while commitment impacts are recalculated.
                db.execute(
                    "UPDATE plans SET revision=(SELECT revision FROM trips WHERE id=?) "
                    "WHERE id=(SELECT id FROM plans WHERE trip_id=? ORDER BY created DESC LIMIT 1) AND expires>=?",
                    (trip_id,trip_id,now()),
                )
                self.log(db,trip_id,"commitment_added",item["title"])
        resolved_id=existing_id or commitment_id
        return next(item for item in self.snapshot(trip_id)["commitments"] if item["id"]==resolved_id)

    def update_commitment(self, trip_id, commitment_id, data):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._trip(db,trip_id)
            row=db.execute("SELECT * FROM commitments WHERE id=? AND trip_id=?",(commitment_id,trip_id)).fetchone()
            if not row:
                raise ValueError("Commitment not found in this itinerary")
            current=self.commitment(dict(row))
            item=self.validate_commitment(data,current)
            updated=now()
            db.execute(
                "UPDATE commitments SET title=?,start_at=?,end_at=?,location=?,activity_type=?,flexibility=?,source=?,updated=? WHERE id=? AND trip_id=?",
                (item["title"],item["start"],item["end"],item["location"],item["type"],item["flexibility"],item["source"],updated,commitment_id,trip_id),
            )
            db.execute("UPDATE trips SET revision=revision+1 WHERE id=?",(trip_id,))
            db.execute(
                "UPDATE plans SET revision=(SELECT revision FROM trips WHERE id=?) "
                "WHERE id=(SELECT id FROM plans WHERE trip_id=? ORDER BY created DESC LIMIT 1) AND expires>=?",
                (trip_id,trip_id,now()),
            )
            self.log(db,trip_id,"commitment_updated",item["title"])
        return next(item for item in self.snapshot(trip_id)["commitments"] if item["id"]==commitment_id)

    def delete_commitment(self, trip_id, commitment_id):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._trip(db,trip_id)
            row=db.execute("SELECT title FROM commitments WHERE id=? AND trip_id=?",(commitment_id,trip_id)).fetchone()
            if not row:
                raise ValueError("Commitment not found in this itinerary")
            db.execute("DELETE FROM commitments WHERE id=? AND trip_id=?",(commitment_id,trip_id))
            db.execute("UPDATE trips SET revision=revision+1 WHERE id=?",(trip_id,))
            db.execute(
                "UPDATE plans SET revision=(SELECT revision FROM trips WHERE id=?) "
                "WHERE id=(SELECT id FROM plans WHERE trip_id=? ORDER BY created DESC LIMIT 1) AND expires>=?",
                (trip_id,trip_id,now()),
            )
            self.log(db,trip_id,"commitment_deleted",row["title"])
        return {"deleted":True,"id":commitment_id}

    @staticmethod
    def analyze_commitment_impacts(commitments, event, plan, facts):
        """Conservatively compare destination commitments with known traveler availability."""
        available_at=None
        basis="No current disruption to assess"
        timing_unknown=bool(event and event.get("status") in {"cancelled","diverted","incident"})
        if event and event.get("status") in {"delayed","active"}:
            if plan:
                result=plan["result"]
                choice=next((option for option in result.get("options",[]) if option["id"]==result.get("recommended_option") and option.get("feasible")),None)
                if choice:
                    available_at=timestamp(choice["hotel_arrival"])
                    basis="Recommended recovery arrival"
                elif not result.get("connection_missed"):
                    available_at=timestamp(result["ready_for_transfer"])
                    basis="Ready after flight transfer buffer"
            event_arrival=event.get("estimated_arrival")
            if available_at is None and event_arrival:
                arrival=timestamp(event_arrival)
                buffer_fact=facts.get("flight.minimum_transfer_minutes",{}).get("value",0)
                buffer_minutes=buffer_fact if type(buffer_fact) is int and 0<=buffer_fact<=1440 else 0
                available_at=arrival+timedelta(minutes=buffer_minutes)
                basis="Latest arrival plus reviewed transfer buffer"
            if available_at is None:
                timing_unknown=True
        output=[]
        original_arrival=timestamp(facts["flight.arrival"]["value"]) if "flight.arrival" in facts else None
        for item in commitments:
            enriched=copy.deepcopy(item)
            start,end=timestamp(item["start"]),timestamp(item["end"])
            if original_arrival and end<=original_arrival:
                impact={"status":"not_affected","affected":False,"available_at":available_at.isoformat() if available_at else None,"reason":"Commitment ends before the scheduled destination arrival"}
            elif timing_unknown:
                impact={"status":"timing_unknown","affected":None,"available_at":None,"reason":"The latest disruption has no reliable arrival time; review this commitment manually","suggested_action":"review manually"}
            elif available_at is None:
                impact={"status":"not_affected","affected":False,"available_at":None,"reason":basis}
            else:
                if end <= available_at:
                    status="affected"
                    reason=f"Traveler is not expected to be available until {available_at.isoformat()}, after this commitment ends"
                elif start < available_at:
                    status="at_risk"
                    reason=f"Traveler is not expected to be available until {available_at.isoformat()}, after this commitment starts"
                elif start < available_at + timedelta(minutes=45):
                    status="at_risk"
                    reason=f"Only {max(0, round((start-available_at).total_seconds()/60))} minutes remain after the reviewed transfer buffer"
                else:
                    status="not_affected"
                    reason=f"Commitment starts after the current {basis.lower()}"
                impact={"status":status,"affected":status in {"affected","at_risk"},"available_at":available_at.isoformat(),"reason":reason}
                impact["assumption"]="The commitment is treated as a destination activity; no travel-time or geocoding service is connected"
                if impact["affected"]:
                    impact["suggested_action"]=("confirm connection with airline" if item["type"]=="transport" else {"fixed":"contact organizer","flexible":"reschedule","optional":"consider dropping"}[item["flexibility"]])
            enriched["impact"]=impact
            output.append(enriched)
        return output

    def context(self, snapshot):
        f = snapshot["facts"]
        def value(key, required=True, default=None):
            if key not in f:
                if required: raise ValueError("Missing reviewed trip field: "+key)
                return default
            return f[key]["value"]
        if snapshot["conflicts"]:
            raise ValueError("Resolve conflicting document fields first: "+", ".join(snapshot["conflicts"]))
        base = json.loads((ROOT/"data/demo_trip.json").read_text())
        base.update(id=snapshot["trip"]["id"],simulation=True)
        base["flight"]={"id":value("flight.flight_id"),"arrival":value("flight.arrival")}
        base["connection"]={"departure":value("connection.departure"),"minimum_transfer_minutes":value("flight.minimum_transfer_minutes")}
        base["hotel"]={"check_in_deadline":value("hotel.check_in_deadline")}
        base["preferences"]["maximum_additional_spend"]=snapshot["trip"]["budget"]
        if snapshot["trip"]["scenario"] != "heathrow-demo":
            base["alternatives"]=[]
        # An absent policy never inherits the fixture's eligibility assumptions.
        base["benefit"].update(source=value("benefit.policy_name",False,"No reviewed benefit policy"),minimum_delay_minutes=value("benefit.minimum_delay_minutes",False,0),covered_causes=value("benefit.covered_causes",False,[]),booking_paid_with_eligible_card=value("benefit.booking_paid_with_eligible_card",False,None))
        return base

    def set_monitoring(self, trip_id, enabled=True, provider="demo_replay"):
        if type(enabled) is not bool:
            raise ValueError("Monitoring enabled must be true or false")
        if provider not in {"demo_replay", "aviationstack"}:
            raise ValueError("Unsupported flight-status provider")
        snap = self.snapshot(trip_id)
        if "flight.flight_id" not in snap["facts"] or "flight.arrival" not in snap["facts"]:
            raise ValueError("Review a flight identifier and scheduled arrival before enabling monitoring")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._trip(db, trip_id)
            db.execute(
                "INSERT INTO monitors(trip_id,enabled,provider,last_checked,updated) VALUES(?,?,?,?,?) "
                "ON CONFLICT(trip_id) DO UPDATE SET enabled=excluded.enabled,provider=excluded.provider,updated=excluded.updated",
                (trip_id, int(enabled), provider, None, now()),
            )
            self.log(db, trip_id, "monitoring_updated", f"{provider}: {'enabled' if enabled else 'disabled'}")
        return self.snapshot(trip_id)

    def ingest_flight_status(self, trip_id, update, unavailable=False):
        """Record one provider transition and create at most one alert and plan.

        Provider observations are evidence, not instructions. Exact provider retries and
        unchanged consecutive states are ignored; older observations never overwrite a
        newer status. Recovery remains limited to the labeled synthetic fixture.
        """
        if not isinstance(update, dict):
            raise ValueError("Flight status update must be an object")
        provider = update.get("provider")
        if provider not in {"demo_replay", "aviationstack"}:
            raise ValueError("Unsupported flight-status provider")
        simulation = update.get("simulation")
        if type(simulation) is not bool or simulation != (provider == "demo_replay"):
            raise ValueError("Provider mode and simulation label do not match")
        provider_event_id = update.get("provider_event_id")
        if not isinstance(provider_event_id, str) or not 1 <= len(provider_event_id) <= 200:
            raise ValueError("Provider event identifier is required")
        status = update.get("status")
        if status not in {"scheduled", "on_time", "active", "delayed", "landed", "cancelled", "diverted", "incident", "unknown"}:
            raise ValueError("Unsupported flight status")
        observed_at = update.get("observed_at") or now()
        from .engine import timestamp
        observed_at = timestamp(observed_at).astimezone(timezone.utc).isoformat()
        estimated_arrival = update.get("estimated_arrival")
        if estimated_arrival is not None:
            timestamp(estimated_arrival)
        cause = update.get("cause")
        if cause not in {None, "weather", "equipment", "other"}:
            raise ValueError("Unsupported disruption cause")
        snap = self.snapshot(trip_id)
        if snap.get("monitor") and not snap["monitor"]["enabled"]:
            return {"ignored": "monitoring_disabled"}
        flight_id = snap["facts"].get("flight.flight_id", {}).get("value")
        scheduled_arrival = snap["facts"].get("flight.arrival", {}).get("value")
        if not flight_id or not scheduled_arrival:
            raise ValueError("Review a flight identifier and scheduled arrival before monitoring")
        if update.get("flight_id") != flight_id:
            raise ValueError("Flight status does not match this itinerary")
        fingerprint = hashlib.sha256(json.dumps(
            {"flight_id": flight_id, "status": status, "estimated_arrival": estimated_arrival, "cause": cause},
            sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()
        recovery = None
        if status == "delayed" and estimated_arrival and snap["trip"]["scenario"] == "heathrow-demo":
            trip = self.context(snap)
            if unavailable:
                trip["alternatives"][0]["available"] = False
            recovery = recover(trip, {
                "id": provider_event_id, "type": "delay", "flight_id": flight_id,
                "arrival": estimated_arrival, "cause": cause, "simulation": simulation,
            })
        status_id = uid()
        received_at = now()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            trip_row = self._trip(db, trip_id)
            duplicate = db.execute(
                "SELECT 1 FROM flight_statuses WHERE trip_id=? AND provider=? AND provider_event_id=?",
                (trip_id, provider, provider_event_id),
            ).fetchone()
            if duplicate:
                return {"duplicate": True}
            latest = db.execute(
                "SELECT * FROM flight_statuses WHERE trip_id=? ORDER BY observed_at DESC,received_at DESC LIMIT 1",
                (trip_id,),
            ).fetchone()
            if latest and timestamp(observed_at) <= timestamp(latest["observed_at"]):
                return {"ignored": "out_of_order"}
            if latest and latest["fingerprint"] == fingerprint:
                db.execute("UPDATE monitors SET last_checked=?,updated=? WHERE trip_id=?", (received_at, received_at, trip_id))
                return {"duplicate": True}
            if trip_row["revision"] != snap["trip"]["revision"]:
                raise ValueError("Itinerary changed during status processing; retry")
            revision = trip_row["revision"] + 1
            event_id = uid()
            event = {
                "id": event_id, "type": "delay" if status == "delayed" else "flight_status",
                "flight_id": flight_id, "status": status, "arrival": estimated_arrival or scheduled_arrival,
                "estimated_arrival": estimated_arrival,
                "cause": cause, "simulation": simulation, "provider": provider,
                "provider_event_id": provider_event_id, "observed_at": observed_at,
            }
            db.execute(
                "INSERT INTO flight_statuses VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (status_id, trip_id, provider, provider_event_id, flight_id, status, estimated_arrival, cause,
                 observed_at, received_at, int(simulation), fingerprint),
            )
            db.execute(
                "INSERT INTO monitors(trip_id,enabled,provider,last_checked,updated) VALUES(?,?,?,?,?) "
                "ON CONFLICT(trip_id) DO UPDATE SET enabled=1,provider=excluded.provider,last_checked=excluded.last_checked,updated=excluded.updated",
                (trip_id, 1, provider, received_at, received_at),
            )
            db.execute("UPDATE trips SET revision=? WHERE id=?", (revision, trip_id))
            db.execute("INSERT INTO events VALUES(?,?,?,?)", (event_id, trip_id, json.dumps(event), received_at))
            if recovery is not None:
                plan_id = uid()
                db.execute(
                    "INSERT INTO plans VALUES(?,?,?,?,?,?,?)",
                    (plan_id, trip_id, revision, event_id, json.dumps(recovery), received_at,
                     (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()),
                )
            source_mode = "simulated_replay" if simulation else "live_provider"
            title = f"Flight {status.replace('_', ' ')}"
            if recovery and recovery["connection_missed"]:
                detail = "The status change breaks the reviewed connection. Validated recovery options are ready."
            elif recovery:
                detail = "The status change does not break the reviewed connection under the current transfer assumption."
            elif status in {"cancelled", "diverted", "incident"}:
                detail = "The provider reported a major disruption. Automated recovery is not supported; manual assistance is required."
            elif status == "delayed":
                detail = "Wayfinder is checking this delay against every connection, booking, and personal plan in your itinerary."
            else:
                detail = "The latest airline update is now reflected in your itinerary."
            db.execute(
                "INSERT INTO notifications VALUES(?,?,?,?,?,?,?,?)",
                (uid(), trip_id, status_id, "flight_status", title, detail, source_mode, received_at),
            )
            self.log(db, trip_id, "flight_status_transition", f"{source_mode}: {status}")
        result = self.snapshot(trip_id)
        result["status_processing"] = {
            "recorded": True, "notification_created": True,
            "recovery_plan_created": recovery is not None,
            "source_mode": "simulated_replay" if simulation else "live_provider",
        }
        return result

    def replay(self, trip_id, minutes=120, cause="weather", unavailable=False, key=None):
        if type(minutes) is not int or not 0 <= minutes <= 1440:
            raise ValueError("Delay must be an integer from 0 to 1440 minutes")
        if cause not in {"weather","equipment","unknown","other"}:
            raise ValueError("Unsupported disruption cause")
        snap = self.snapshot(trip_id)
        # Replay is a labeled provider-style observation. It may be used for any
        # reviewed itinerary, but synthetic recovery inventory remains confined
        # to the Heathrow fixture inside ingest_flight_status.
        from .engine import timestamp
        arrival = (timestamp(snap["facts"]["flight.arrival"]["value"]) + timedelta(minutes=minutes)).isoformat()
        return self.ingest_flight_status(trip_id, {
            "provider": "demo_replay", "provider_event_id": key or uid(),
            "flight_id": snap["facts"]["flight.flight_id"]["value"],
            "status": "delayed" if minutes else "on_time", "estimated_arrival": arrival,
            "cause": None if cause == "unknown" else cause, "observed_at": now(), "simulation": True,
        }, unavailable=unavailable)

    def budget(self,trip_id,amount):
        if type(amount) is not int or not 0 <= amount <= 100000:
            raise ValueError("Budget must be whole USD from 0 to 100,000")
        with self.connection() as db:
            self._trip(db,trip_id)
            db.execute("UPDATE trips SET budget=?,revision=revision+1 WHERE id=?",(amount,trip_id))

    def approve(self,trip_id,plan_id,option_id):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            trip=self._trip(db,trip_id)
            existing=db.execute("SELECT * FROM actions WHERE plan_id=? AND option_id=? AND trip_id=?",(plan_id,option_id,trip_id)).fetchone()
            if existing:
                return {**dict(existing),"result":json.loads(existing["result"]),"duplicate":True}
            p=db.execute("SELECT * FROM plans WHERE id=? AND trip_id=?",(plan_id,trip_id)).fetchone()
            if not p or p["revision"] != trip["revision"] or p["expires"] < now():
                raise ValueError("Plan expired or itinerary changed. Replay recovery to get a current plan")
            result=json.loads(p["result"])
            option=next((x for x in result["options"] if x["id"]==option_id and x["feasible"]),None)
            if not option:
                raise ValueError("Option is not validated as feasible")
            if db.execute("SELECT 1 FROM actions WHERE plan_id=?",(plan_id,)).fetchone():
                raise ValueError("A recovery option has already been selected for this plan")
            action={"id":uid(),"trip_id":trip_id,"plan_id":plan_id,"option_id":option_id,"state":"simulated_confirmed","result":{"confirmation":"SIM-"+uid()[:8].upper(),"option":option,"note":"Simulation only. No ticket purchased, reservation canceled, or money charged."},"created":now()}
            db.execute("INSERT INTO actions VALUES(?,?,?,?,?,?,?)",(action["id"],trip_id,plan_id,option_id,action["state"],json.dumps(action["result"]),action["created"]))
            db.execute("UPDATE trips SET revision=revision+1 WHERE id=?",(trip_id,))
            self.log(db,trip_id,"simulated_action_confirmed",option_id)
            return action

    def save_message(self,trip_id,question,answer,revision):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if self._trip(db,trip_id)["revision"] != revision:
                raise ValueError("Trip changed while answering. Please ask again")
            db.execute("INSERT INTO messages(id,trip_id,question,answer,created) VALUES(?,?,?,?,?)",(uid(),trip_id,question,json.dumps(answer),now()))
            self.log(db,trip_id,"question_answered",answer["mode"])
