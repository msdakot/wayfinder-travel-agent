"""Bounded document extraction with explicit human review."""
import io
import json
import re

MAX_BYTES = 10 * 1024 * 1024
MAX_TEXT = 100_000
TYPES = {"flight", "connection", "hotel", "benefit", "passport", "rewards", "notes", "itinerary"}
FIELDS = {
    "flight": {"flight_id", "route", "departure", "arrival", "boarding_time", "terminal", "gate", "seat", "minimum_transfer_minutes", "booking_reference"},
    "connection": {"departure", "hotel_arrival", "route", "booking_reference"},
    "hotel": {"hotel_name", "check_in_deadline", "check_out", "booking_reference"},
    "benefit": {"policy_name", "minimum_delay_minutes", "covered_causes", "booking_paid_with_eligible_card"},
    "passport": {"holder", "expiry_date", "passport_masked"},
    "rewards": {"program", "balance", "unit", "as_of"}, "notes": set(),
    "itinerary": {"items"},
}

ITINERARY_TYPES = {"meeting", "tour", "dining", "transport", "lodging", "event", "personal", "other"}
ITINERARY_FLEXIBILITY = {"fixed", "flexible", "optional"}
MAX_ITINERARY_ITEMS = 50


def extract(filename, content):
    if not content or len(content) > MAX_BYTES:
        raise ValueError("Choose a nonempty document smaller than 10 MB")
    pages = []
    if filename.lower().endswith(".pdf"):
        if not content.startswith(b"%PDF-"):
            raise ValueError("File does not have a valid PDF signature")
        from pypdf import PdfReader
        try:
            reader = PdfReader(io.BytesIO(content))
            if reader.is_encrypted:
                raise ValueError("Encrypted PDFs are not supported")
            if len(reader.pages) > 30:
                raise ValueError("PDFs must have at most 30 pages")
            pages = [{"page": i + 1, "text": page.extract_text() or ""} for i, page in enumerate(reader.pages)]
        except ValueError:
            raise
        except Exception:
            raise ValueError("Could not read this PDF. Try a text-based PDF or TXT file") from None
    elif filename.lower().endswith(".txt"):
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ValueError("TXT documents must use UTF-8 encoding") from None
        if "\x00" in text:
            raise ValueError("Binary data is not a supported text document")
        pages = [{"page": 1, "text": text}]
    else:
        raise ValueError("This build supports TXT and text-based PDF documents. Image OCR is not connected")
    text = "\n".join(p["text"] for p in pages)
    if len(text) > MAX_TEXT:
        raise ValueError("Extracted text exceeds the 100,000 character limit")
    if not text.strip():
        raise ValueError("No readable text found. Scanned PDFs require OCR, which is not connected")
    pairs = [(k.lower(), v.strip()) for k, v in re.findall(r"^([A-Z][A-Z_]+):\s*(.+)$", text, re.M)]
    raw = dict(pairs)
    kind = raw.get("document_type", "notes").lower()
    if kind not in TYPES:
        kind = "notes"
    if kind == "itinerary":
        item_lines = [value for key, value in pairs if key == "item"]
        if not item_lines:
            raise ValueError("Itinerary documents need at least one ITEM JSON line")
        if len(item_lines) > MAX_ITINERARY_ITEMS:
            raise ValueError(f"Itinerary documents can contain at most {MAX_ITINERARY_ITEMS} items")
        items = []
        for line in item_lines:
            try:
                item = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                raise ValueError("Each ITEM must be one valid JSON object on a single line") from None
            items.append(item)
        fields = {"items": items}
    else:
        fields = {k: v for k, v in raw.items() if k in FIELDS[kind]}
    return {"kind": kind, "fields": validate_fields(kind, fields), "pages": pages, "text": text}


def validate_fields(kind, fields):
    from datetime import date
    from .engine import timestamp
    if kind not in TYPES or not isinstance(fields, dict):
        raise ValueError("Invalid document classification or fields")
    if set(fields) - FIELDS[kind]:
        raise ValueError("Unexpected fields for this document type")
    if kind == "itinerary":
        items = fields.get("items")
        if not isinstance(items, list) or not 1 <= len(items) <= MAX_ITINERARY_ITEMS:
            raise ValueError(f"Itinerary items must be a list with 1 to {MAX_ITINERARY_ITEMS} entries")
        result = [validate_itinerary_item(item) for item in items]
        keys = [item["id"] for item in result]
        if len(set(keys)) != len(keys):
            raise ValueError("Itinerary ITEM ids must be unique within a document")
        return {"items": result}
    result = {}
    for key, value in fields.items():
        if value is None or value == "":
            continue
        if key in {"arrival", "departure", "boarding_time", "hotel_arrival", "check_in_deadline", "check_out"}:
            timestamp(value)
        elif key in {"expiry_date", "as_of"}:
            date.fromisoformat(value)
        elif key in {"minimum_transfer_minutes", "minimum_delay_minutes", "balance"}:
            if isinstance(value, bool) or str(value) != str(int(value)) or not 0 <= int(value) <= 100_000_000:
                raise ValueError(f"{key} must be a nonnegative integer")
            value = int(value)
            if key == "minimum_transfer_minutes" and value > 1440:
                raise ValueError("Transfer buffer cannot exceed 1440 minutes")
        elif key == "booking_paid_with_eligible_card":
            if value in ("true", "false", "unknown"):
                value = {"true": True, "false": False, "unknown": None}[value]
            elif value is not None and type(value) is not bool:
                raise ValueError("Payment eligibility must be true, false, or unknown")
        elif key == "covered_causes":
            value = [x.strip().lower() for x in value.split(",")] if isinstance(value, str) else value
            if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
                raise ValueError("Covered causes must be a list of text values")
        elif not isinstance(value, str) or len(value) > 300:
            raise ValueError(f"{key} must be short text")
        result[key] = value
    return result


def validate_itinerary_item(item):
    """Validate one deliberately small repeated itinerary record."""
    from datetime import timezone
    from .engine import timestamp
    if not isinstance(item, dict):
        raise ValueError("Each itinerary ITEM must be a JSON object")
    allowed = {"id", "title", "start", "end", "location", "type", "flexibility"}
    if set(item) - allowed:
        raise ValueError("Unexpected itinerary ITEM fields: " + ", ".join(sorted(set(item) - allowed)))
    missing = allowed - {"id"} - set(item)
    if missing:
        raise ValueError("Missing itinerary ITEM fields: " + ", ".join(sorted(missing)))
    result = {}
    item_id = item.get("id")
    if item_id is None:
        canonical = json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        import hashlib
        item_id = hashlib.sha256(canonical.encode()).hexdigest()[:24]
    if not isinstance(item_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", item_id):
        raise ValueError("Itinerary ITEM id must use 1 to 64 letters, numbers, dots, underscores, or hyphens")
    result["id"] = item_id
    for key, limit in (("title", 120), ("location", 200)):
        value = item.get(key)
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
            raise ValueError(f"Itinerary ITEM {key} must be 1 to {limit} characters")
        result[key] = " ".join(value.split())
    if item.get("type") not in ITINERARY_TYPES:
        raise ValueError("Unsupported itinerary ITEM type")
    if item.get("flexibility") not in ITINERARY_FLEXIBILITY:
        raise ValueError("Itinerary ITEM flexibility must be fixed, flexible, or optional")
    result["type"] = item["type"]
    result["flexibility"] = item["flexibility"]
    start = timestamp(item.get("start"))
    end = timestamp(item.get("end"))
    if end <= start:
        raise ValueError("Itinerary ITEM end must be after its start")
    result["start"] = start.astimezone(timezone.utc).isoformat()
    result["end"] = end.astimezone(timezone.utc).isoformat()
    return result
