# Wayfinder — live demo runbook

Target time: **2 minutes 30 seconds**. Keep the app at phone width. Do not explain implementation while clicking; narrate the traveler’s outcome.

## Before the judges arrive

1. Run `./scripts/run.sh` from the repository root.
2. Open <http://127.0.0.1:8765> in Chrome.
3. Keep this folder ready in Finder: `demo/judge-kit/inputs/`.
4. Confirm the settings page says **Snowflake and Claude — Connected**.
5. Use a fresh custom itinerary for the Chile upload. Keep the London sample available as the recovery fallback.
6. Do not enable browser notifications during the pitch; the in-app alert is enough.

## 0:00–0:20 — Start with nothing

**Say:** “A traveler starts with the confirmations they already have. I’ll build the journey from five documents.”

- On **My itineraries**, choose **Add documents**.
- Name it **Chile · December 2025**.
- In the file picker, open `demo/judge-kit/inputs/`, select all five `.txt` files, and upload.

## 0:20–0:45 — One review creates the trip

**Say:** “Wayfinder extracted the flights, ground transport, stays, activities, and the W trek. The traveler approves these details once before the agent can use them.”

- Choose **Review and create itinerary**.
- In the review dialog, point to the five sources and click **Use these details**.
- Pause on the completed timeline.

Expected result: **5 reviewed documents**, **18 itinerary items**, and no duplicate or conflicting entries.

## 0:45–1:10 — Show connected context

**Say:** “Every plan is placed in time, tied back to its source, and mapped when Wayfinder has a known location.”

- Open **W Circuit day 2 · Las Torres Base hike**.
- Show the destination map, commute choices, and linked source document.
- Close the detail sheet.

## 1:10–1:35 — Let monitoring change the trip

**Say:** “The traveler never enters delay minutes. A provider update changes the monitored flight, and Wayfinder checks the rest of the journey.”

- Open the presenter control and trigger the labeled **71-minute provider-style update**.
- Pause on flight AA945 and the orange **LA378 connection at risk** state.
- Open notifications to show the new alert.

Expected result: arrival moves from **7:45 AM to 8:56 AM**, leaving only **37 minutes beyond the reviewed transfer buffer** for LA378.

## 1:35–1:55 — Ask the grounded question

**Say:** “The traveler asks one question instead of searching five apps.”

- Open **Ask**.
- Send: **What changed, what is at risk, and what should I do next?**
- Point to the concise answer and document citations.

## 1:55–2:30 — Show the approval boundary

**Say:** “For a broken connection, Wayfinder compares feasible options and waits for the traveler’s approval.”

- Return to **My itineraries** and open **A long weekend in London**.
- If needed, use **Presenter controls → Run automatic disruption**.
- Open **Recover**, choose the recommended **9:15 PM train**, and confirm.
- Pause on **Later train is now in your trip** and the rewritten itinerary.

**Close with:** “The alert became a grounded decision, and the decision became the new journey. The traveler approved once; Wayfinder updated everything else.”

## Recovery if the live agent is slow

- Keep narrating while the answer loads; the itinerary and delay analysis are already deterministic.
- If Snowflake is unavailable, say: “The live cloud answer is unavailable, so I’m showing the same grounded local fallback; the reviewed evidence and deterministic impact state are unchanged.”
- If a map provider is slow, show the saved destination and commute options, then continue.
- If the Chile trip already exists, create a new trip with the same five files; do not use a partially reviewed trip.
