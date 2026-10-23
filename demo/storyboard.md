# Wayfinder — 60-second demo storyboard

Target runtime: **58–60 seconds**. Record as a phone-width screen capture. Use short dissolves between the creation, itinerary, disruption, and assistant scenes. Keep the pointer still while speaking, then make one deliberate interaction per sentence.

| Time | Screen and interaction | Voiceover | Recording note |
|---|---|---|---|
| 0:00–0:06 | Start on **Manage itineraries**. Hold for one second, then tap **Add documents**. | “No more frantically searching and managing your trip. Wayfinder turns scattered confirmations into one journey and keeps it clear as plans change.” | Begin with no modal open. Use a clean title card only if it does not consume more than one second. |
| 0:06–0:15 | Enter **Chile — December 2025**. Select the prepared flight, connection, stays/Atacama, and W Trek documents. Cut from the upload state to the completed itinerary rather than waiting through every review step. | “I’ll create my Chile trip by adding flight, stay, transfer, tour, and W Trek documents. Wayfinder builds one timeline and keeps each original confirmation one tap away.” | Show the real product workflow, but compress repetitive upload/review actions with a clean dissolve. Never show local file paths, email, passport, payment, API keys, or browser developer tools. |
| 0:15–0:27 | Slowly scroll the unified timeline. Tap **W Circuit** and briefly reveal its attached confirmation. Close it, tap an Atacama or Patagonia stop, then show the destination map and commute choices. If location permission is already granted, tap **Use my current location** once. | “Every stop is connected in time and place. I can open an activity, see it on Google Maps, compare commute options, and—with permission—route from my current location.” | The destination map is live Google Maps. A current-location route is live only after browser permission; otherwise leave the clearly labeled itinerary estimate visible. |
| 0:27–0:34 | Open AA945. Hold on its original **On time** state and the onward LA378 connection. | “AA945 is on time, so my connection to LA378 looks comfortable.” | Establish the baseline before any disruption. Do not start from the delayed state. |
| 0:34–0:43 | Trigger the presenter/demo status update. Show the visible **Demo** or simulated-provider label, then the **71 min delay** and LA378 changing to **At risk**. Briefly scroll to one downstream impact. | “Now a demo status update reports a 71-minute delay. Wayfinder marks the connection at risk and carries the change through my itinerary.” | Do not call this a live airline update. The 71-minute replay narrows the connection; it does not prove LA378 is missed. |
| 0:43–0:55 | Open **Ask about this trip**. Send: **What changed, and what should I do?** Show the concise answer, affected-plan guidance, and source chips. Tap one source chip only if the response arrives early enough. | “I ask, ‘What changed, and what should I do?’ Claude uses my Snowflake-backed trip context to give me a concise, source-linked answer and the decisions that matter.” | Pre-warm Snowflake before recording so the answer fits the take. Keep the Snowflake/Claude technical detail in the narration; the traveler-facing screen should remain simple. |
| 0:55–1:00 | Return to the updated timeline or hold on the actionable assistant answer. Fade to the Wayfinder wordmark. | “Wayfinder keeps the whole trip together, so I spend less time searching and more time traveling.” | End on a stable screen with no toast or loading indicator. |

## Clean-take checklist

- Reset the Chile itinerary to its original on-time state before every take.
- Keep the flight update visibly labeled as a demo/simulated provider event.
- Confirm all uploaded documents are reviewed and all public destinations resolve before recording.
- Pre-open the Chile itinerary, pre-warm the Snowflake/Claude question path, then return to Manage itineraries.
- Grant location permission before recording only if showing a current-location Google route; otherwise show the labeled estimate.
- Use roughly 150 spoken words per minute, a short pause after the opening sentence, and no filler words. The script is 152 words.
- Record at phone width with notifications, bookmarks, personal account details, and developer tools hidden.
- Use hard cuts only to compress upload/review waiting; use 150–250 ms dissolves for other scene changes.
- Keep background music low enough that every product term and time change is clear.
