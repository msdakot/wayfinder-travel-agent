-- Synthetic seed data only. Select the isolated hackathon database/schema first.

CREATE TABLE IF NOT EXISTS POC_TRIP_CONTEXT (ITINERARY_ID VARCHAR, CONTEXT VARIANT);

CREATE TABLE IF NOT EXISTS POC_POLICY_CHUNKS (DOCUMENT_ID VARCHAR, ITINERARY_ID VARCHAR, DOCUMENT_NAME VARCHAR, PAGE_NUMBER NUMBER, CHUNK_TEXT VARCHAR);

DELETE FROM POC_TRIP_CONTEXT WHERE ITINERARY_ID = 'demo-trip';

INSERT INTO POC_TRIP_CONTEXT SELECT 'demo-trip', PARSE_JSON('{"id": "demo-trip", "label": "Synthetic airport-to-hotel journey", "simulation": true, "flight": {"id": "DEMO101", "arrival": "2026-11-07T18:00:00+00:00"}, "connection": {"departure": "2026-11-07T19:00:00+00:00", "minimum_transfer_minutes": 45}, "hotel": {"check_in_deadline": "2026-11-07T23:00:00+00:00"}, "preferences": {"maximum_additional_spend": 120, "currency": "USD"}, "alternatives": [{"id": "later-train", "mode": "train", "departure": "2026-11-07T21:15:00+00:00", "hotel_arrival": "2026-11-07T22:30:00+00:00", "cost": 45, "available": true}, {"id": "taxi", "mode": "taxi", "departure": "2026-11-07T20:50:00+00:00", "hotel_arrival": "2026-11-07T21:45:00+00:00", "cost": 110, "available": true}, {"id": "late-bus", "mode": "bus", "departure": "2026-11-07T21:30:00+00:00", "hotel_arrival": "2026-11-07T23:45:00+00:00", "cost": 20, "available": true}], "benefit": {"source": "Fictional demo policy, clause 1 \u2014 not an actual card benefit", "minimum_delay_minutes": 180, "covered_causes": ["weather", "equipment"], "booking_paid_with_eligible_card": true, "required_evidence": ["Original and revised itinerary", "Booking payment evidence", "Carrier delay reason", "Itemized expense receipts"], "note": "Expense eligibility and administrator approval remain unverified; no alternative is represented as reimbursable."}}');

DELETE FROM POC_POLICY_CHUNKS WHERE ITINERARY_ID = 'demo-trip';

INSERT INTO POC_POLICY_CHUNKS SELECT '01-flight', 'demo-trip', '01-flight.txt', 1, 'WAYFINDER SYNTHETIC DEMO - NOT A REAL BOOKING
DOCUMENT_TYPE: flight
FLIGHT_ID: DEMO101
ROUTE: San Francisco to London Heathrow
ARRIVAL: 2026-11-07T18:00:00+00:00
MINIMUM_TRANSFER_MINUTES: 45
BOOKING_REFERENCE: DEMO-FLIGHT
Flight DEMO101 arrives at Heathrow at 18:00 on November 7, 2026.
Allow 45 minutes after landing before onward departure in this demonstration.
This fixed buffer is a scenario assumption, not airport guidance.
';

INSERT INTO POC_POLICY_CHUNKS SELECT '02-train', 'demo-trip', '02-train.txt', 1, 'WAYFINDER SYNTHETIC DEMO - NOT A REAL BOOKING
DOCUMENT_TYPE: connection
DEPARTURE: 2026-11-07T19:00:00+00:00
HOTEL_ARRIVAL: 2026-11-07T20:00:00+00:00
ROUTE: Heathrow to London hotel via Paddington
BOOKING_REFERENCE: DEMO-TRAIN
Your train leaves at 19:00. This fictional ticket is valid only for that departure.
Switching to a later departure requires a replacement ticket in this demo.
';

INSERT INTO POC_POLICY_CHUNKS SELECT '03-hotel', 'demo-trip', '03-hotel.txt', 1, 'WAYFINDER SYNTHETIC DEMO - NOT A REAL BOOKING
DOCUMENT_TYPE: hotel
HOTEL_NAME: The Marlow House
CHECK_IN_DEADLINE: 2026-11-07T23:00:00+00:00
CHECK_OUT: 2026-11-10T11:00:00+00:00
BOOKING_REFERENCE: DEMO-HOTEL
The staffed check-in desk closes at 23:00 on November 7, 2026.
Arrivals after 23:00 require explicit hotel confirmation; late access is not assumed.
This hotel and its policies are fictional.
';

INSERT INTO POC_POLICY_CHUNKS SELECT '04-card-benefit', 'demo-trip', '04-card-benefit.txt', 1, 'WAYFINDER SYNTHETIC DEMO - NOT ACTUAL INSURANCE OR CARD TERMS
DOCUMENT_TYPE: benefit
POLICY_NAME: Atlas Demo Card - Fictional Trip Delay Policy
MINIMUM_DELAY_MINUTES: 180
COVERED_CAUSES: weather,equipment
BOOKING_PAID_WITH_ELIGIBLE_CARD: true
Clause 1. For this synthetic example, a recorded arrival delay of at least 180 minutes,
caused by weather or equipment, and an eligible payment may trigger benefit review.
Clause 2. Reasonable meals and lodging may be considered after other reimbursements.
Replacement train fares and taxis are NOT established as covered by this policy.
Clause 3. Retain original and revised itineraries, payment evidence, a carrier delay
statement, itemized receipts and evidence of any other reimbursement.
No payment or eligibility is guaranteed. The benefit administrator must review a claim.
';

INSERT INTO POC_POLICY_CHUNKS SELECT '06-rewards', 'demo-trip', '06-rewards.txt', 1, 'WAYFINDER SYNTHETIC DEMO - NOT A LIVE LOYALTY ACCOUNT
DOCUMENT_TYPE: rewards
PROGRAM: Atlas Demo Rewards
BALANCE: 42000
UNIT: points
AS_OF: 2026-10-23
These are 42,000 manually recorded demo points. Redemption pricing and availability
are unknown. Do not subtract points from a cash fare without a verified redemption.
';
