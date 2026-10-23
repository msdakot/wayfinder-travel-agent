#!/usr/bin/env python3
"""Verify the exact document bundle and delay state used in the judge demo."""
from pathlib import Path
from tempfile import TemporaryDirectory

from travel_recovery.store import Store


ROOT = Path(__file__).resolve().parents[1]
INPUTS = ROOT / "demo" / "judge-kit" / "inputs"


def main():
    files = sorted(INPUTS.glob("*.txt"))
    if len(files) != 5:
        raise SystemExit(f"Expected 5 judge inputs, found {len(files)}")

    with TemporaryDirectory() as temp:
        store = Store(Path(temp) / "judge-demo.db")
        trip_id = store.create("Chile · December 2025")["trip"]["id"]
        for path in files:
            document = store.upload(trip_id, path.name, path.read_bytes())
            store.review(
                trip_id,
                document["id"],
                document["kind"],
                document["fields"],
            )

        itinerary = store.snapshot(trip_id)
        assert itinerary["document_count"] == 5
        assert all(document["state"] == "reviewed" for document in itinerary["documents"])
        assert len(itinerary["commitments"]) == 18
        assert itinerary["conflicts"] == []
        assert itinerary["facts"]["flight.flight_id"]["value"] == "AA945"

        delayed = store.replay(trip_id, minutes=71, cause="weather", key="judge-demo-delay")
        assert delayed["event"]["status"] == "delayed"
        assert delayed["flight_experience"]["delay"]["minutes"] == 71
        affected = delayed["flight_experience"]["affected"]["commitments"]
        assert len(affected) == 1
        assert affected[0]["title"] == "LA378 · Santiago to Calama"
        assert affected[0]["status"] == "at_risk"

    print("Judge demo verified: 5 documents → 18 items → AA945 +71 min → LA378 at risk")


if __name__ == "__main__":
    main()
