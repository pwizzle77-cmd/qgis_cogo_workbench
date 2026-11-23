"""Turn a deed's words into COGO courses, and reject anything the deed did not say.

The offline parser runs first. A model is asked only about clauses that parser
could not read. Each model call must quote the deed, and every number it uses
must appear in that quote.
"""
import re

from .ai_client import AIError
from .engine import Course, bearing, traverse
from .legal import _angle

OCR_PROMPTS = {
    "deepseek-ocr": "Free OCR.",
    "default": ("Transcribe every word of this scanned page exactly as written, line by line. Keep "
                "bearings, degree/minute/second symbols and distances exactly. Do not summarize, "
                "explain or add anything."),
}

FIND_PROMPT = (
    "Below is the text of a deed. Copy out, word for word, ONLY the legal description of the land: "
    "from where it starts (e.g. 'Commence at' or 'Begin at') through the last call that returns to the "
    "point of beginning, including any 'containing ... acres' line. Do not change any character.\n\n"
    "DEED TEXT:\n%s")

CALL_SCHEMA = {
    "type": "object",
    "properties": {
        "calls": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "quote": {"type": "string"},
                    "kind": {"type": "string", "enum": ["line", "curve"]},
                    "bearing": {"type": "string"},
                    "distance": {"type": ["number", "null"]},
                    "radius": {"type": ["number", "null"]},
                    "arc_length": {"type": ["number", "null"]},
                    "chord_length": {"type": ["number", "null"]},
                    "delta": {"type": ["string", "null"]},
                    "side": {"type": ["string", "null"]},
                    "bearing_is": {"type": ["string", "null"]},
                },
                "required": ["quote", "kind", "bearing"],
            },
        }
    },
    "required": ["calls"],
}

CALLS_PROMPT = (
    "You read land surveying legal descriptions and rewrite them as metes and bounds. From the TEXT "
    "below, list each boundary course in order, one course per call. A sentence that contains several "
    "courses is several calls, not one. "
    "'thence run West along the South line of the quarter a distance of 208 feet' is ONE course: "
    "bearing West, distance 208. 'Along the south line' or 'parallel to the east line' names the line "
    "being followed. It is not another course. If the text really does contain two courses, return both.\n"
    "For each call give:\n"
    "- quote: the exact words of that one course copied from the text\n"
    "- kind: line or curve\n"
    "- bearing: the direction of travel as N 45°30'15\" E, or a cardinal N, S, E or W (for a curve, the "
    "chord bearing or tangent bearing that is given)\n"
    "- distance (the unit written in the text) for a line; for a curve radius, and arc_length or "
    "chord_length or delta, side (left/right) and bearing_is (chord/tangent)\n"
    "Only use numbers that appear in the text. If a value isn't stated, use null. Skip commencement "
    "ties (calls before the point of beginning).\n\nTEXT:\n%s")

_START = re.compile(r"\b(commenc(?:e|ing)\s+at|begin(?:ning)?\s+at)\b", re.I)
_END = re.compile(
    r"(point\s+of\s+beginning|P\.?\s*O\.?\s*B\.?)[^.;]*[.;]?(\s*containing\s+[\d.,]+\s+acres?[^.]*\.?)?",
    re.I)


def ocr_prompt(model):
    return OCR_PROMPTS.get((model or "").split(":")[0], OCR_PROMPTS["default"])


def find_description(text):
    """From the first 'Commence at' / 'Begin at' through the last point of beginning."""
    found = _START.search(text or "")
    if not found:
        return ""
    last = None
    for end in _END.finditer(text, found.end()):
        last = end
    if last is None:
        return ""
    return text[found.start():last.end()].strip()


def _norm(value):
    value = value.lower().translate(str.maketrans({
        "’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "−": "-", "º": "°", "˚": "°"}))
    return re.sub(r"[^a-z0-9°'\".]+", " ", value).strip()


def _numbers(value):
    return [float(item.replace(",", "")) for item in re.findall(r"\d[\d,]*\.?\d*", value)]


def quote_in_text(quote, text):
    normalized = _norm(quote or "")
    return bool(normalized) and normalized in _norm(text or "")


def _number_in(value, numbers):
    return any(abs(float(value) - item) <= 1e-9 for item in numbers)


def _plain(value):
    text = f"{float(value):.10f}".rstrip("0").rstrip(".")
    return text or "0"


def _direction(text):
    text = " ".join(str(text or "").split())
    try:
        bearing(text)
        return text
    except ValueError:
        pass
    match = re.fullmatch(r"([NS])\s*(.*?)\s*([EW])", text.strip(), re.I)
    if match:
        direction = match[1].upper() + _angle(match[2]) + match[3].upper()
        bearing(direction)
        return direction
    word = re.sub(r"[^A-Za-z]", "", text).upper()
    if word in {"N", "E", "S", "W", "NORTH", "SOUTH", "EAST", "WEST"}:
        return word[0]
    raise ValueError("bad bearing: %s" % text)


def check_call(call, text):
    """One model call as a workbench Course, or ValueError saying why it was rejected."""
    quote = call.get("quote") or ""
    if not quote_in_text(quote, text):
        raise ValueError("its quote isn't in the deed text")
    nums = _numbers(quote)
    try:
        direction = _direction(call.get("bearing", ""))
    except ValueError as error:
        raise ValueError(str(error)) from None
    for number in _numbers(call.get("bearing", "")):
        if not _number_in(number, nums):
            raise ValueError("bearing number %g isn't in its quote" % number)
    if call.get("kind") == "curve":
        radius = call.get("radius")
        if radius is None or not _number_in(radius, nums):
            raise ValueError("curve radius missing or not in its quote")
        side = (call.get("side") or "").lower()
        if side in ("r", "right"):
            turn = "R"
        elif side in ("l", "left"):
            turn = "L"
        else:
            raise ValueError("curve has no left/right")
        basis = "Tangent" if (call.get("bearing_is") or "").lower() == "tangent" else "Chord"
        if call.get("arc_length") is not None and _number_in(call["arc_length"], nums):
            measure, value = "Arc", _plain(call["arc_length"])
        elif call.get("chord_length") is not None and _number_in(call["chord_length"], nums):
            measure, value = "Chord", _plain(call["chord_length"])
        elif call.get("delta"):
            for number in _numbers(str(call["delta"])):
                if not _number_in(number, nums):
                    raise ValueError("delta number %g isn't in its quote" % number)
            measure, value = "Delta", _angle(str(call["delta"]))
        else:
            raise ValueError("curve needs an arc length, chord or delta from its quote")
        course = Course("Curve", direction, value, _plain(radius), measure, turn, basis)
    else:
        distance = call.get("distance")
        if distance is None or not _number_in(distance, nums):
            raise ValueError("distance missing or not in its quote")
        course = Course("Line", direction, _plain(distance))
    try:
        traverse((0, 0), [course])
    except ValueError as error:
        raise ValueError(str(error)) from None
    return course


def checked_rows(answer, source):
    """Model JSON into review rows. A bad call stays visible and is not loaded."""
    rows = []
    for call in (answer or {}).get("calls", []):
        if not isinstance(call, dict):
            continue
        try:
            course = check_call(call, source)
            rows.append({"phase": "Boundary", "source": call.get("quote", ""), "course": course,
                         "by": "AI", "note": ""})
        except (ValueError, TypeError, AIError) as error:
            rows.append({"phase": "Boundary", "source": call.get("quote", ""), "course": None,
                         "by": "AI", "note": "AI answer rejected: %s" % error})
    return rows
