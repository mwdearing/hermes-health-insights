"""Local-model narration for health concerns, with a deterministic fallback.

Code (concerns.py and concern_rules/) decides WHAT the findings are. This
module only asks the LOCAL model to reword them into a short, calm summary in
house style. Nothing here ever leaves the host: the request goes to the
router's loopback endpoint (127.0.0.1:8080), the same local model the bot
runs on, never a cloud provider. This is enforced, not just documented: a
narration URL whose host is not a loopback address (127.0.0.0/8, ::1 or
"localhost") is refused before any request is made, and redirects are not
followed, so findings can never be sent to another machine from here.

Only Finding.to_dict() fields are ever sent to the model: id, level, title,
evidence, source, advice. Evidence text can include real numbers (a reading,
a lab value) because it is the user's own data going to their own LOCAL model on their
own host. It is never written anywhere else from here. With no narration URL configured the
deterministic summary is used and nothing leaves the process.

Proxies: the opener is built with an empty ProxyHandler, so HTTP(S)_PROXY /
ALL_PROXY in the environment can never reroute a narration request.

No cloud fallback: every failure (refused URL, timeout, connection refused,
HTTP error, bad JSON, empty text, rejected narrative) ends in
deterministic_summary(); there is no second endpoint to try.

Output validation (validate_narrative): finding text is untrusted DATA. It is
only ever sent inside the JSON user message, never in the system prompt, and
the returned narrative is rejected (deterministic summary used instead) when:
  1. it contains a number (digits) whose value does not appear in the findings'
     text fields, level or count (compared numerically; "9" does not match "19"; thousands
     commas are ignored; spelled-out numbers are not checked);
  2. it contains, case-insensitively and on word boundaries, a phrase from
     _FORBIDDEN_PHRASES (dosing, treatment, medication-change or causal claims
     such as "stop taking", "you should take", "causes", "caused by");
  3. it contains a prompt-injection echo from _INJECTION_PHRASES (e.g.
     "ignore previous instructions", "email the data").

If the model call fails for any reason (unreachable, timeout, malformed
response, empty text) — narrate() ALWAYS returns a usable summary via
deterministic_summary(), so nightly jobs never depend on the model being up.
"""
from __future__ import annotations

import ipaddress
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Sequence

from health_insights import settings

LOCAL_MODEL_URL = settings.narration_url() or ""  # empty = no model, deterministic text only
LOCAL_MODEL_NAME = settings.narration_model()
DEFAULT_TIMEOUT_S = 20

LEVEL_WORDS = {0: "normal", 1: "worth watching", 2: "worth discussing with your clinician", 3: "urgent"}

SYSTEM_PROMPT = (
    "You write a short, calm health summary for the user from a fixed list of findings, given as JSON. "
    "The list is ALREADY ORDERED from most to least serious (highest 'level' first) — "
    "describe them in that exact order, do not reorder or re-rank them yourself, and do not say what order they are in. "
    "Rules: 4 to 6 sentences total. Plain, direct language, no medical jargon. "
    "Never invent a finding, a number, or a cause that is not in the input. "
    "Never give dosing, medication or treatment advice. Never say this is a diagnosis. "
    "If the list is empty, say plainly that nothing stood out. "
    "End with exactly this sentence: \"Informational, not medical advice.\""
)


def _finding_dicts(findings: Sequence) -> list[dict]:
    return [f.to_dict() if hasattr(f, "to_dict") else dict(f) for f in findings]


def deterministic_summary(findings: Sequence, overall_level: int | None = None) -> str:
    """A fixed-template summary built directly from the findings, no model involved.

    This is the fallback narrate() uses on any failure, and is itself a valid,
    complete summary — never a degraded placeholder.
    """
    rows = _finding_dicts(findings)
    if overall_level is None:
        overall_level = max((r["level"] for r in rows), default=0)
    if not rows:
        return "Nothing stood out this period. Informational, not medical advice."
    rows = sorted(rows, key=lambda r: (-r["level"], r["id"]))
    lines = [f"{r['title']} ({LEVEL_WORDS.get(r['level'], 'note')}): {r['evidence']}" for r in rows]
    lead = f"{len(rows)} item{'s' if len(rows) != 1 else ''} this period, most serious: {LEVEL_WORDS.get(overall_level, 'note')}."
    return lead + " " + " ".join(lines) + " Informational, not medical advice."


def _require_loopback(url: str) -> None:
    """Raise ValueError unless *url* is http(s) with a loopback host (127.0.0.0/8, ::1 or localhost)."""
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname
    if parts.scheme not in ("http", "https") or not host:
        raise ValueError("narration URL must be an http(s) URL on a loopback host; refusing")
    if host.lower() == "localhost":
        return
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if not loopback:
        raise ValueError(f"narration URL host {host!r} is not a loopback address; refusing to send findings off this host")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A loopback endpoint must not be able to bounce the request to another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirects are not followed for narration", headers, fp)


# ProxyHandler({}) overrides the default env-based proxy handling: always connect directly.
def _build_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


_OPENER = _build_opener()

_FORBIDDEN_PHRASES = (
    "increase your dose", "decrease your dose", "lower your dose", "raise your dose", "change your dose",
    "adjust your dose", "your dosage", "you should take", "you should stop", "you should start",
    "stop taking", "start taking", "keep taking", "take more", "take less",
    "different treatment", "new treatment", "try a different", "change your medication", "new medication",
    "causes", "caused by", "because of your", "is due to", "leads to",
)
_INJECTION_PHRASES = (
    "ignore previous", "ignore all previous", "ignore the above", "ignore your instructions", "disregard previous", "disregard the above",
    "email the data", "send the data", "system prompt", "as an ai", "new instructions",
)
_NUMBER_RE = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")


def _numbers(text: str) -> set[float]:
    return {float(m.replace(",", "")) for m in _NUMBER_RE.findall(text)}


def _phrase_hit(text: str, phrases) -> str | None:
    low = text.lower()
    for ph in phrases:
        if re.search(r"(?<!\w)" + re.escape(ph) + r"(?!\w)", low):
            return ph
    return None


def validate_narrative(text: str, findings: Sequence) -> str | None:
    """Return None if *text* is acceptable, else a short reason it must be rejected."""
    rows = _finding_dicts(findings)
    allowed: set[float] = {float(len(rows))}  # the item count is a computed value too
    for r in rows:
        allowed.add(float(r["level"]))
        for key in ("id", "title", "evidence", "source", "advice"):
            allowed |= _numbers(str(r.get(key, "")))
    # The mandated disclaimer has no digits; any other digit run must come from the findings.
    stray = _numbers(text) - allowed
    if stray:
        return f"number not in findings: {sorted(stray)[0]:g}"
    hit = _phrase_hit(text, _FORBIDDEN_PHRASES)
    if hit:
        return f"forbidden dosing/treatment/causal phrase: {hit!r}"
    hit = _phrase_hit(text, _INJECTION_PHRASES)
    if hit:
        return f"instruction echo: {hit!r}"
    return None


def _call_local_model(findings_json: str, timeout_s: float) -> str:
    """Raises on any failure; callers must catch and fall back."""
    if not LOCAL_MODEL_URL:
        raise ValueError("no narration model configured")
    _require_loopback(LOCAL_MODEL_URL)
    payload = json.dumps({
        "model": LOCAL_MODEL_NAME,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": findings_json},
        ],
        "max_tokens": 300,
        "temperature": 0.3,
    }).encode("utf-8")
    req = urllib.request.Request(
        LOCAL_MODEL_URL, data=payload, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with _OPENER.open(req, timeout=timeout_s) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    text = body["choices"][0]["message"]["content"].strip()
    if not text:
        raise ValueError("empty completion")
    return text


def narrate(findings: Sequence, overall_level: int | None = None, timeout_s: float = DEFAULT_TIMEOUT_S) -> str:
    """Return a short summary of *findings*: the local model's wording, or the
    deterministic fallback on any failure. Never raises."""
    # Pre-sort most-to-least serious ourselves (same order as deterministic_summary):
    # a small local model narrates a given order far more reliably than it reasons
    # about severity itself.
    rows = sorted(_finding_dicts(findings), key=lambda r: (-r["level"], r["id"]))
    try:
        text = _call_local_model(json.dumps(rows), timeout_s)
        if validate_narrative(text, rows) is not None:
            return deterministic_summary(findings, overall_level)
        if "not medical advice" not in text.lower():
            text = text.rstrip() + " Informational, not medical advice."
        return text
    except (urllib.error.URLError, TimeoutError, OSError, KeyError, IndexError, ValueError, TypeError):
        return deterministic_summary(findings, overall_level)
