"""Snowflake-backed trip context and Cortex Agent calls for the hackathon POC."""
import json
import os
import re
import threading
import time
import tomllib
from pathlib import Path

from .store import ROOT

DB = "DEFAULT_DATABASE"
SCHEMA = "WAYFINDER_POC"
AGENT = "TRAVEL_RECOVERY_AGENT"
_sync_lock = threading.Lock()


def _connection_values():
    path = Path(os.environ.get("SNOWFLAKE_CONNECTIONS_FILE", ROOT / "runtime/connections.toml"))
    name = os.environ.get("SNOWFLAKE_CONNECTION_NAME", "hackathon")
    if not path.is_file():
        raise ValueError("Snowflake connection file is missing")
    values = tomllib.loads(path.read_text()).get(name)
    if not values:
        raise ValueError(f"Snowflake connection {name!r} is missing")
    return values


def configured():
    try:
        _connection_values()
        import snowflake.connector  # noqa: F401
        return True
    except (ValueError, ImportError, OSError, tomllib.TOMLDecodeError):
        return False


def connect():
    try:
        import snowflake.connector
    except ImportError:
        raise ValueError("Install the Snowflake connector in the app environment") from None
    values = dict(_connection_values())
    values.setdefault("enable_server_session_keep_alive_auto_detection", False)
    return snowflake.connector.connect(**values, login_timeout=20, network_timeout=120)


def verify():
    """Cheap health check. It does not invoke a model or mutate cloud state."""
    if not configured():
        return {"configured": False, "verified": False}
    try:
        with connect() as conn:
            cur = conn.cursor()
            identity = cur.execute("SELECT CURRENT_ACCOUNT(), CURRENT_ROLE()").fetchone()
            cur.execute(f"SHOW TABLES LIKE 'RUNTIME_%' IN SCHEMA {DB}.{SCHEMA}")
            tables = {row[1] for row in cur.fetchall()}
            cur.execute(f"SHOW SEMANTIC VIEWS LIKE 'TRIP_SEMANTIC' IN SCHEMA {DB}.{SCHEMA}")
            semantic = bool(cur.fetchall())
            cur.execute(f"SHOW CORTEX SEARCH SERVICES LIKE 'POLICY_SEARCH' IN SCHEMA {DB}.{SCHEMA}")
            search = bool(cur.fetchall())
            cur.execute(f"SHOW AGENTS LIKE '{AGENT}' IN SCHEMA {DB}.{SCHEMA}")
            agent = bool(cur.fetchall())
        ready = {"RUNTIME_TRIPS", "RUNTIME_DOCUMENTS"}.issubset(tables) and semantic and search and agent
        return {"configured": True, "verified": True, "account": identity[0], "role": identity[1], "provisioned": ready}
    except Exception:
        return {"configured": True, "verified": False, "provisioned": False}


def _text_blocks(value):
    data = json.loads(value) if isinstance(value, str) else value
    content = data.get("content", []) if isinstance(data, dict) else []
    return data, [block.get("text", "") for block in content if block.get("type") == "text"]


def _extract_json(text):
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start >= 0 and end > start:
            return json.loads(cleaned[start:end + 1])
        raise


def cloud_safe_document(doc):
    """Conservative POC guard against syncing identity-document text."""
    text = "\n".join(page.get("text", "") for page in doc.get("pages", [])).upper()
    sensitive_markers = ("PASSPORT NUMBER", "DOCUMENT_TYPE: PASSPORT", "DATE OF BIRTH", "NATIONALITY:")
    return doc.get("kind") != "passport" and not any(marker in text for marker in sensitive_markers)


def _search_ids(cur):
    request = {"query": "WAYFINDER SYNTHETIC DEMO booking policy hotel train flight", "columns": ["DOCUMENT_ID"], "limit": 15}
    row = cur.execute(
        "SELECT SNOWFLAKE.CORTEX.SEARCH_PREVIEW(%s, %s)",
        (f"{DB}.{SCHEMA}.POLICY_SEARCH", json.dumps(request)),
    ).fetchone()
    data = json.loads(row[0]) if isinstance(row[0], str) else row[0]
    return {item.get("DOCUMENT_ID") or item.get("document_id") for item in data.get("results", [])}


def _sync_and_answer(snapshot, question, grounded):
    if snapshot["trip"]["scenario"] != "heathrow-demo":
        raise ValueError("Cloud mode is scoped to the synthetic Heathrow demo in this POC")
    reviewed = [d for d in snapshot["documents"] if d["state"] == "reviewed" and cloud_safe_document(d)]
    if not reviewed:
        raise ValueError("Review at least one non-identity trip document before using cloud mode")
    trip_id = snapshot["trip"]["id"]
    facts = {
        key: item["value"] for key, item in snapshot["facts"].items()
        if not key.startswith("passport.")
    }
    commitments = snapshot.get("snowflake_safe_context", {}).get("commitments", [])
    # Commitments and impact labels are deterministic application output. They
    # travel with the structured itinerary so the agent can explain them, but
    # the model is never allowed to reclassify an affected commitment as safe.
    facts["traveler_commitments"] = commitments
    recovery = snapshot.get("plan", {}).get("result") if snapshot.get("plan") and not snapshot["plan"]["stale"] else None
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(f"USE DATABASE {DB}")
        cur.execute(f"USE SCHEMA {SCHEMA}")
        # This is an explicitly single-traveler POC schema. Keep only the active
        # itinerary so Search cannot retrieve evidence from another local trip.
        cur.execute("TRUNCATE TABLE RUNTIME_DOCUMENTS")
        rows = []
        for doc in reviewed:
            for page in doc["pages"]:
                rows.append((trip_id, doc["id"], doc["name"], doc["kind"], page["page"], page["text"]))
        cur.executemany(
            "INSERT INTO RUNTIME_DOCUMENTS(ITINERARY_ID,DOCUMENT_ID,DOCUMENT_NAME,DOCUMENT_KIND,PAGE_NUMBER,CHUNK_TEXT) "
            "VALUES(%s,%s,%s,%s,%s,%s)", rows
        )
        cur.execute("TRUNCATE TABLE RUNTIME_TRIPS")
        cur.execute(
            "INSERT INTO RUNTIME_TRIPS(ITINERARY_ID,TITLE,REVISION,FACTS,RECOVERY) "
            "SELECT %s,%s,%s,PARSE_JSON(%s),PARSE_JSON(%s)",
            (trip_id, snapshot["trip"]["title"], snapshot["trip"]["revision"], json.dumps(facts), json.dumps(recovery))
        )
        expected_ids = {d["id"] for d in reviewed}
        deadline = time.monotonic() + 75
        while True:
            if expected_ids.issubset(_search_ids(cur)):
                break
            if time.monotonic() >= deadline:
                raise ValueError("Snowflake Search has not indexed the current trip yet; retry shortly")
            time.sleep(2)
        prompt = {
            "question": question,
            "itinerary_id": trip_id,
            "validated_recovery_answer": grounded["text"],
            "traveler_commitments_and_impacts": commitments,
            "allowed_document_ids": [d["id"] for d in reviewed],
            "response_contract": {"text": "concise traveler answer under 120 words", "document_ids": ["allowed source ids"]},
        }
        instruction = (
            "Return ONLY one JSON object with keys text and document_ids; do not use markdown fences or headings outside JSON. "
            "Write for a traveler checking a phone: lead with the answer, use at most 120 words, use short sentences, and omit implementation details. "
            "The validated_recovery_answer is authoritative for feasibility, recommendation, benefit status, and commitment impact. "
            "Never change an affected/safe classification from traveler_commitments_and_impacts. Explain affected personal plans and suggested actions when relevant. "
            "Use Analyst and Search to ground the explanation. INPUT: " + json.dumps(prompt)
        )
        request = {
            "stream": False,
            "messages": [{"role": "user", "content": [{"type": "text", "text": instruction}]}],
        }
        row = cur.execute(
            "SELECT SNOWFLAKE.CORTEX.DATA_AGENT_RUN(%s, %s)",
            (f"{DB}.{SCHEMA}.{AGENT}", json.dumps(request)),
        ).fetchone()
    raw, blocks = _text_blocks(row[0])
    if raw.get("code") or raw.get("message"):
        raise ValueError("Cortex Agent execution failed; answer withheld")
    if raw.get("warnings"):
        raise ValueError("Cortex Agent returned a warning; answer withheld")
    if not blocks:
        raise ValueError("Cortex Agent returned no answer text")
    tools_used = [
        block.get("tool_use", {}).get("name") for block in raw.get("content", [])
        if block.get("type") == "tool_use" and block.get("tool_use", {}).get("name")
    ]
    try:
        obj = _extract_json(blocks[-1])
        ids = obj["document_ids"]
        if not isinstance(obj["text"], str) or not obj["text"].strip() or not isinstance(ids, list):
            raise ValueError
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        # Agent releases can occasionally honor the content instructions but omit
        # the JSON envelope. In that case, citations come only from the already
        # validated local answer; the model cannot introduce a new source ID.
        prose = blocks[-1].strip()
        if not prose or len(prose) > 8000 or not grounded.get("citations") or not tools_used:
            raise ValueError("Cortex returned an unverified response format; answer withheld") from None
        obj = {"text": prose, "document_ids": [c["id"] for c in grounded["citations"]]}
        ids = obj["document_ids"]
    allowed = {d["id"]: d for d in reviewed}
    aliases = {}
    for doc in reviewed:
        for alias in (doc["name"], Path(doc["name"]).stem):
            if alias in aliases and aliases[alias] != doc["id"]:
                aliases[alias] = None
            else:
                aliases[alias] = doc["id"]
    normalized = [item if item in allowed else aliases.get(item) for item in ids]
    if any(item is None for item in normalized):
        raise ValueError("Cortex cited a document outside this itinerary; answer withheld")
    ids = normalized
    citations = []
    for item in dict.fromkeys(ids):
        doc = allowed[item]
        citations.append({"id": item, "name": doc["name"], "page": doc["pages"][0]["page"], "excerpt": doc["pages"][0]["text"][:360]})
    return {
        "text": obj["text"].strip(), "citations": citations, "mode": "cortex",
        "label": "Claude via Snowflake Cortex Agent", "revision": snapshot["trip"]["revision"],
        "run_id": raw.get("metadata", {}).get("run_id"),
        "tools_used": list(dict.fromkeys(tools_used)),
    }


def sync_and_answer(snapshot, question, grounded):
    """Serialize the single-traveler cloud workspace and answer from reviewed evidence."""
    if any(term in question.lower() for term in ("passport", "visa", "entry requirement", "identity document")):
        raise ValueError("Passport and identity-document questions stay in local mode in this POC")
    with _sync_lock:
        return _sync_and_answer(snapshot, question, grounded)
