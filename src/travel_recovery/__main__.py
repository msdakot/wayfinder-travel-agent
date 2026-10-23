import argparse
import json
from pathlib import Path
from .engine import recover

parser = argparse.ArgumentParser(description="Replay a synthetic flight delay and validate recovery plans")
parser.add_argument("--trip", type=Path, default=Path(__file__).resolve().parents[2] / "data/demo_trip.json")
parser.add_argument("--arrival", default="2026-11-07T20:00:00+00:00")
parser.add_argument("--cause", default="weather")
args = parser.parse_args()
trip = json.loads(args.trip.read_text())
event = {"id": "replay-1", "type": "delay", "flight_id": trip["flight"]["id"], "arrival": args.arrival, "cause": args.cause}
print(json.dumps(recover(trip, event), indent=2))
