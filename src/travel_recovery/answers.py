"""Grounded local answers and an optional managed Cortex Agent transport."""
import json
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from .cloud import configured as cloud_configured, sync_and_answer, verify as verify_cloud


def configuration():
    configured = cloud_configured()
    status = verify_cloud() if configured else {"configured": False, "verified": False, "provisioned": False}
    return {"answer_mode": "cortex_agent" if status.get("provisioned") else "local_grounded", "snowflake": "connected_and_provisioned" if status.get("provisioned") else "configured_not_verified" if configured else "not_configured", "flight_api": "configured_not_yet_verified" if os.environ.get("AVIATIONSTACK_API_KEY") else "not_configured", "monitoring":"automatic_while_app_open", "cloud_enabled":bool(status.get("provisioned")), "cloud_verified":bool(status.get("verified")), "cloud_role":status.get("role")}


def answer(snapshot, question):
    if not isinstance(question, str) or not question.strip() or len(question) > 2000:
        raise ValueError("Ask a question between 1 and 2,000 characters")
    q = question.lower().strip()
    facts = snapshot["facts"]
    route_text=str(facts.get("flight.route",{}).get("value","")).lower()
    destination_zone=ZoneInfo("America/Santiago" if "santiago" in route_text or "chile" in route_text else "Europe/London")
    destination_label="Santiago time" if str(destination_zone)=="America/Santiago" else "London time"
    citations = []
    def cite_doc(doc):
        if not any(c["id"] == doc["id"] for c in citations):
            page = doc["pages"][0]
            citations.append({"id":doc["id"],"name":doc["name"],"page":page["page"],"excerpt":page["text"][:360]})
    def val(key, fallback="not documented"):
        f = facts.get(key)
        if not f:
            return fallback
        doc = next(d for d in snapshot["documents"] if d["id"]==f["document_id"])
        cite_doc(doc)
        return f["value"]
    def when(value):
        try:
            local=datetime.fromisoformat(value).astimezone(destination_zone)
            return local.strftime("%b %-d at %-I:%M %p")+f" {destination_label}"
        except (ValueError,TypeError):
            return str(value)
    def commitment_summary():
        commitments=snapshot.get("commitments",[])
        if not commitments:
            return " You have not added any personal plans yet."
        affected=[]
        safe=[]
        uncertain=[]
        for item in commitments:
            impact=item.get("impact",{})
            status=impact.get("status")
            label=f"{item['title']} ({when(item['start'])})"
            if status in {"affected","at_risk"}:
                affected.append(label)
            elif status=="not_affected":
                safe.append(label)
            else:
                uncertain.append(label)
        first=commitments[:4]
        parts=[f" Your itinerary contains {len(commitments)} bookings and plans. First up: "+"; ".join(item['title'] for item in first)+"."]
        if affected:
            parts.append(" Needs attention: "+"; ".join(affected)+".")
        if safe and snapshot.get("event"):
            parts.append(f" The other {len(safe)} reviewed plans remain unchanged by this update.")
        if uncertain:
            parts.append(" Timing still needs review: "+"; ".join(uncertain)+".")
        return "".join(parts)
    def place_target():
        """Resolve a place from this question or the immediately preceding chat.

        The second pass makes short mobile follow-ups such as "share the location"
        useful without letting old, unrelated conversation override an explicit name.
        """
        commitments=snapshot.get("commitments",[])
        stop={"the","and","for","with","from","this","that","where","what","when","location","directions","share","show","there","here","please","could","would","should","about","tell","give","route","map"}
        def tokens(value):
            return set(re.findall(r"[a-z0-9]+",str(value).lower()))-stop
        def best(value):
            wanted=tokens(value)
            ranked=[]
            for item in commitments:
                searchable=tokens(item.get("title",""))|tokens(item.get("location",""))
                score=len(wanted & searchable)
                if score:
                    ranked.append((score,item))
            ranked.sort(key=lambda pair:pair[0],reverse=True)
            return ranked[0][1] if ranked else None
        match=best(q)
        if match:
            return match
        # Only borrow context for a location-style follow-up. Look at the most
        # recent user question first, followed by its grounded answer.
        for message in reversed(snapshot.get("messages",[])[-3:]):
            match=best(message.get("question","")) or best(message.get("answer",{}).get("text",""))
            if match:
                return match
        return None
    def place_answer():
        target=place_target()
        if not target:
            return "Which stop do you mean? You can ask for a place by name, such as ‘Where is the beer crawl?’"
        guide=next((item for item in snapshot.get("place_experiences",[]) if item["commitment_id"]==target["id"]),None)
        title=target["title"]
        if not guide:
            location=str(target.get("location") or "").strip()
            if location:
                return f"{title} is listed at {location}. I do not have a verified map point or route for it yet. Add the full address and I’ll include it in your itinerary guidance."
            return f"I have {title} in your itinerary, but no location is saved yet. Add the address in chat and I’ll use it for route guidance."
        destination=guide["destination"]
        recommended=next((mode for mode in guide["modes"] if mode["id"]==guide["recommended_mode"]),guide["modes"][0])
        cost=recommended["cost"]
        amount=cost.get("amount")
        currency=cost.get("currency","USD")
        cost_text="free" if amount==0 else "fare not yet verified" if amount is None else f"{currency} {amount}"
        leave_text=when(recommended["leave_at"]) if recommended.get("leave_at") else "not calculated"
        return (
            f"{title} is at {destination['name']}, {destination['address']}. "
            f"From {guide['origin']['name']}, the recommended route is {recommended['label'].lower()}: "
            f"leave by {leave_text}, allow about {recommended['duration_minutes']} minutes, and expect {cost_text}. "
            f"Tap {title} in your itinerary to see the map and compare walking, public transit, and taxi. "
            "Travel times are trip estimates, not live traffic."
        )
    docs = [d for d in snapshot["documents"] if d["state"]=="reviewed"]
    if not docs:
        text="I don't have reviewed trip documents yet. Upload a PDF or TXT document, review the extracted fields, and confirm it before asking about your itinerary."
    elif snapshot["conflicts"]:
        text="Some reviewed documents disagree: "+", ".join(snapshot["conflicts"])+". Please correct or remove the conflicting document before relying on a recovery recommendation."
    elif any(x in q for x in ("passport","visa","entry","expire","expiry")):
        expiry=val("passport.expiry_date")
        text=f"The passport expiry recorded for this trip is {expiry}. This is a manually reviewed document snapshot. An expiry date alone does not establish destination entry or visa eligibility. Those requirements are not connected in this POC."
    elif any(x in q for x in ("reward","point","mile","loyalty")):
        text=f"{val('rewards.program')}: {val('rewards.balance')} {val('rewards.unit')}, recorded as of {val('rewards.as_of')}. This is an uploaded snapshot, not a live balance. Redemption prices and availability are unverified, so these points have not been deducted from cash recovery costs."
    elif any(x in q for x in ("reimburs","insurance","claim","benefit","covered","coverage")):
        name=val("benefit.policy_name")
        threshold=val("benefit.minimum_delay_minutes")
        causes=val("benefit.covered_causes",[])
        text=f"The reviewed policy is {name}. Its recorded delay threshold is {threshold} minutes and its listed causes are {', '.join(causes) if isinstance(causes,list) else causes}. "
        plan=snapshot.get("plan")
        if plan and not plan["stale"]:
            status=plan["result"]["benefit_assessment"]["status"]
            text+={"demo_conditions_not_met":"The current scenario does not meet the recorded demo conditions. ","needs_more_information":"More information is required before assessing the current scenario. ","potentially_eligible_requires_review":"The current scenario may qualify for review, but reimbursement is not approved. "}.get(status,"")
        text+="Keep the original and revised itinerary, payment evidence, a carrier statement of the cause, itemized receipts, and details of other reimbursement. Replacement train and taxi expenses are not established as covered. The demo policy is fictional; no payment is guaranteed."
    elif any(x in q for x in ("delay","cancel","alternative","recover","miss","what should","what now","next step","disrupt")):
        val("flight.arrival"); val("connection.departure"); val("hotel.check_in_deadline")
        if "cancel" in q:
            text="Cancellation recovery is not supported by this build yet. A cancellation has no reliable arrival time, so I won't reuse the delay calculation. The demo supports delay scenarios only; a canceled trip needs manual assistance."
        elif snapshot.get("plan") and not snapshot["plan"]["stale"]:
            result=snapshot["plan"]["result"]
            text=f"In the current replay, the flight is {result['delay_minutes']} minutes late. "
            if not result["connection_missed"]:
                text+="Your original connection remains reachable under the reviewed transfer-buffer assumption. No replacement is needed."
            else:
                text+="The original train is no longer reachable. "
                options={o["id"]:o for o in result["options"]}
                choice=options.get(result["recommended_option"])
                if choice:
                    text+=f"The lowest-cost feasible option is {choice['mode']}, adding ${choice['cost']} USD and reaching the hotel at {when(choice['hotel_arrival'])}. "
                else:
                    text+="None of the supplied alternatives satisfy all checked constraints. Manual assistance or a new inventory search is needed. "
                for option in result["options"]:
                    if not option["feasible"]:
                        text+=f"{option['mode'].capitalize()} ({option['id']}): {'; '.join(option['rejection_reasons'])}. "
                text+="All prices and inventory here are simulated. Potential reimbursement is separate from the money you would pay now."
            text+=commitment_summary()
        else:
            scheduled=val('flight.arrival')
            connection=val('connection.departure',None)
            event=snapshot.get("event") or {}
            estimated=event.get("estimated_arrival") or event.get("arrival") or scheduled
            try: delay=max(0,round((datetime.fromisoformat(estimated)-datetime.fromisoformat(scheduled)).total_seconds()/60))
            except (TypeError,ValueError): delay=0
            affected=[item for item in snapshot.get("commitments",[]) if item.get("impact",{}).get("affected")]
            text=f"Flight {val('flight.flight_id')} is {delay} minutes late and is now expected at {when(estimated)}. "
            if connection:
                text+=f"Your onward connection departs {when(connection)}. "
            if affected:
                item=affected[0]
                text+=f"{item['title']} may be affected: {item['impact'].get('reason','confirm the connection before continuing')}. {str(item['impact'].get('suggested_action','Confirm the next step')).capitalize()}. "
            else:
                text+="No downstream booking currently needs a change. "
            text+="Wayfinder has not changed or purchased anything."
    elif any(x in q for x in ("hotel","check-in","check in","checkin")):
        text=f"Your hotel is {val('hotel.hotel_name')}. Its check-in deadline is {when(val('hotel.check_in_deadline'))}. An arrival after that deadline requires explicit hotel confirmation; late access is not assumed. Check-out is recorded as {when(val('hotel.check_out'))}."
    elif any(x in q for x in ("where","location","address","direction","get there","walk","transit","taxi","map")):
        text=place_answer()
    elif any(x in q for x in ("summar","itinerary","my trip","trip plan","flight","train","schedule","remind")):
        text=f"Your reviewed journey is {val('flight.route')}. Flight {val('flight.flight_id')} was scheduled to arrive {when(val('flight.arrival'))}. Allow the documented {val('flight.minimum_transfer_minutes')} minute transfer buffer. "
        connection=val('connection.departure',None)
        if connection:
            text+=f"Your onward connection departs {when(connection)}. "
        if snapshot.get("event"):
            status=snapshot['event'].get('status','updated').replace('_',' ')
            text+=f"The latest {status} update shows arrival at {when(snapshot['event']['arrival'])}. "
        text+=commitment_summary()
        plan=snapshot.get("plan")
        if plan and not plan["stale"] and plan["result"].get("connection_missed"):
            choice=next((o for o in plan["result"].get("options",[]) if o["id"]==plan["result"].get("recommended_option")),None)
            if choice:
                text+=f" Your original connection no longer works. The current recommendation is the {choice['mode']} for ${choice['cost']} extra, arriving at {when(choice['hotel_arrival'])}; choose a recovery option below to continue."
        text+=" Booking times come from your reviewed documents."
        if snapshot["trip"].get("scenario")=="heathrow-demo":
            text+=" Sample recovery prices and inventory are simulated."
    else:
        # Lexical retrieval returns excerpts, not invented paraphrases or a fake LLM answer.
        words=set(re.findall(r"[a-z]{3,}",q))-{"the","and","what","does","this","with","have","about","could","would","please"}
        ranked=[]
        for doc in docs:
            if doc["kind"]=="passport":
                continue
            for page in doc["pages"]:
                score=len(words & set(re.findall(r"[a-z]{3,}",page["text"].lower())))
                if score: ranked.append((score,doc,page))
        ranked.sort(key=lambda x:x[0],reverse=True)
        if ranked:
            text="The closest reviewed evidence is below. This local mode retrieves matching passages; it cannot reliably infer an answer beyond these records.\n\n"
            for _,doc,page in ranked[:2]:
                cite_doc(doc)
                lines=[line for line in page["text"].splitlines() if words & set(re.findall(r"[a-z]{3,}",line.lower()))]
                text+=doc["name"]+": "+" ".join(lines)[:550]+"\n\n"
        else:
            text="I couldn't find supporting evidence for that question in the reviewed documents. I can answer about the itinerary, hotel deadlines, a replayed delay, rewards, passport expiry, or the reimbursement checklist. Please add the relevant document for anything else."
    result={"text":text,"citations":citations,"mode":"local_grounded","label":"Local grounded answers · no LLM connected","revision":snapshot["trip"]["revision"]}
    return result


def cortex_answer(snapshot, question, grounded):
    """Call the scoped managed agent; never silently replace a cloud failure."""
    return sync_and_answer(snapshot, question, grounded)
