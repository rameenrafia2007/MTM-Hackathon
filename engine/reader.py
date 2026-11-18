"""Reader: the ONE batched model call (tag "reading") plus the code grounding check.

The model only reads and classifies. It returns compact JSON claims, each with a
verbatim evidence span. Code then drops any claim whose evidence is not in the
message or whose number cannot be read from that evidence.
"""
from __future__ import annotations

import json
import re

from .areas import ALL_ATTRS, KEYED_ATTRS, CLOCK_METRICS, PLAN_AREAS, REGISTRY, WINDOW_METRICS, glossary
from .lang import clocks_in, derivable, norm_text, numbers_in, parse_clock, parse_window
from .splitter import Msg, msg_hash

KINDS = ("fact", "quote", "request", "rule", "correction", "notice", "other_constraint")

SYSTEM = (
    "Extract claims from event-planning messages (English / Roman Urdu). Read and classify only; never "
    "calculate, plan or decide. Compact JSON only:\n"
    '{"msgs":[{"m":1,"sender":"","date":""}],'
    '"claims":[{"m":1,"attr":"","subj":"","val":0,"ev":""}]}\n'
    "msgs: one per message; sender/date from the header or text (\"X here:\"), \"\" if absent. Add "
    "\"rel\":\"n\" and \"why\" (<=5 words) ONLY for a message with nothing that affects the plan "
    "(chat, thanks, a trivial delay); it then has no claims. All other messages: leave rel out.\n"
    "Extract EVERY claim of EVERY message, even an old value that a later message replaces.\n"
    "attr: " + " ".join(a for a in ALL_ATTRS if a != "other_constraint") + " other_constraint\n"
    "ev = the exact words around the number, 2-6 words (like 'Rs 90,000' or '40 students'), never a bare number; "
    "join several spans with \" | \".\n"
    "Numbers as digits, rupees without commas (lakh=100000, k/hazaar=1000, paanch=5); durations in minutes "
    "(4-hour=240); clock times HH:MM 24h.\n"
    "break_minutes = length of a prayer/other break. venue_option: subj=hall name, val=price, cap=seats (always), "
    "feat=[features like generator] (repeat shared features in each hall; a hall feature is NOT an other_constraint). "
    "unit_price: subj=one-word tier, val=rupees per head. committed_spend: already ordered/paid/approved "
    "(subj=item). requested_spend: asked or quoted, not approved. ordered_quantity: subj=item, val=count.\n"
    "Add \"kind\":\"correction\" when a message corrects an earlier value, \"kind\":\"request\" when it asks for "
    "something. Relative change (\"5 more\"): val=delta and \"d\":1.\n"
    "Only for a rule the event must satisfy: attr=other_constraint, metric=<a metric below, else short snake_case>, "
    "area=venue|schedule|food|judging|budget, op=min|max|eq|require|forbid, subj, val, text.\n"
    "Metrics: " + glossary() + "\nOmit anything unclear. Never invent."
)
BLOB_EXTRA = ("\nThe input is ONE text that may hold several messages: split it, number them from 1 and add "
              "\"t\":exact message text to each msgs entry.")

ATTR_ALIAS = {
    "budget": "total_budget", "total": "total_budget", "students": "student_count",
    "participants": "student_count", "num_students": "student_count", "participant_count": "student_count",
    "members_per_team": "team_size", "team_members": "team_size", "staff": "staff_count",
    "staff_on_duty": "staff_count", "duration": "duration_minutes", "judges": "judges_confirmed",
    "judge_count": "judges_confirmed", "honorarium": "judge_fee", "judge_honorarium": "judge_fee",
    "fee_per_judge": "judge_fee", "minutes_per_judge_team": "minutes_per_team", "max_teams": "max_teams_per_judge",
    "price_per_head": "unit_price", "lunch_price": "unit_price", "meal_price": "unit_price",
    "hall": "venue_option", "venue": "venue_option", "prayer_break": "break_minutes", "break": "break_minutes",
    "spent": "committed_spend", "already_ordered": "committed_spend", "ordered_spend": "committed_spend",
    "order_quantity": "ordered_quantity", "quantity": "ordered_quantity", "requested": "requested_spend",
    "request": "requested_spend",
}
AREA_OF_ATTR = {"duration_minutes": "schedule", "break_minutes": "schedule", "start_time": "schedule",
                "availability_note": "schedule", "venue_option": "venue", "student_count": "venue",
                "staff_count": "venue", "unit_price": "food", "ordered_quantity": "food", "team_size": "judging",
                "judges_confirmed": "judging", "judge_fee": "judging", "minutes_per_team": "judging",
                "max_teams_per_judge": "judging", "total_budget": "budget", "committed_spend": "budget",
                "requested_spend": "budget"}
AREA_ALIAS = {"catering": "food", "meals": "food", "hall": "venue", "judges": "judging", "finance": "budget",
              "time": "schedule", "timing": "schedule", "money": "budget", "cost": "budget"}


# ------------------------------------------------------------- prompt
def build_prompt(msgs: list[Msg], blob: bool = False) -> tuple[str, str]:
    parts = []
    for m in msgs:
        meta = ""
        if m.sender or m.date:
            meta = f" (from: {m.sender or '?'}; date: {m.date or '?'})"
        parts.append(f"[{m.id}]{meta} {m.text}")
    return SYSTEM + (BLOB_EXTRA if blob else ""), "\n\n".join(parts)


# ------------------------------------------------------------- parsing
def _loads_lenient(txt: str):
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt.strip(), flags=re.M).strip()
    a = s.find("{")
    if a < 0:
        raise ValueError("no JSON object in output")
    s = s[a:]
    b = s.rfind("}")
    try:
        return json.loads(s[:b + 1])
    except ValueError as first:
        # truncated output: cut back to the last complete claim and close the structure
        for end in range(b, 0, -1):
            if s[end] == "}":
                for tail in ("]}", "]}]}", "}]}"):
                    try:
                        return json.loads(s[:end + 1] + tail)
                    except ValueError:
                        continue
        raise ValueError(f"invalid JSON: {first}")


def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        n = sorted(numbers_in(v))
        if re.search(r"\b(lakh|lakhs|lac|lacs|hazaar|hazar|thousand|million|crore)\b|\d\s*k\b", v.lower()) and n:
            return n[-1]
        if len(n) == 1 and re.fullmatch(r"[\sA-Za-z.:]*[\d,.]+\s*[A-Za-z%/ ]*", v):
            return n[0]
    return None


_CAP = re.compile(r"(\d[\d,]*)\s*(?:seats?|seaters?|people|persons|guests|capacity|pax)\b|"
                  r"\b(?:seats?|holds?|capacity(?: of)?)\s*(\d[\d,]*)", re.I)


def _cap_from_ev(ev):
    m = _CAP.search(str(ev or ""))
    if not m:
        return None
    try:
        return int((m.group(1) or m.group(2)).replace(",", ""))
    except ValueError:
        return None


def normalize_claim(c: dict) -> dict | None:
    if not isinstance(c, dict):
        return None
    try:
        m = int(c.get("m"))
    except (TypeError, ValueError):
        return None
    attr = str(c.get("attr") or "").strip().lower().replace(" ", "_")
    attr = ATTR_ALIAS.get(attr, attr)
    kind = str(c.get("kind") or "fact").strip().lower()
    if kind == "constraint":
        kind = "other_constraint"
    if kind not in KINDS:
        kind = "fact"
    area = str(c.get("area") or "").strip().lower()
    area = AREA_ALIAS.get(area, area)
    if area not in PLAN_AREAS:
        area = ""
    if not attr:
        return None
    if not area:
        area = AREA_OF_ATTR.get(attr, "")
    if attr not in ALL_ATTRS:            # unknown attr = a rule the registry may or may not know
        c = dict(c, metric=attr)
        attr, kind = "other_constraint", "other_constraint"
    val = c.get("val", c.get("value"))
    if val is None and c.get("price") is not None:
        val = c.get("price")
    if attr == "other_constraint":
        metric = str(c.get("metric") or "").strip().lower().replace(" ", "_")
        subj_key = str(c.get("subj") or "").strip().lower().replace(" ", "_")
        if metric not in REGISTRY and subj_key in REGISTRY:     # small models put the metric in subj
            metric, c = subj_key, dict(c, subj="")
        metric = ATTR_ALIAS.get(metric, metric)
        for key in (metric, subj_key):                          # a plain fact mislabelled as a constraint
            mapped = ATTR_ALIAS.get(key, key)
            if mapped in ALL_ATTRS and mapped != "other_constraint" and mapped not in KEYED_ATTRS:
                attr, metric, kind = mapped, "", "fact"
                c = dict(c, subj="")
                area = AREA_OF_ATTR.get(attr, area)
                break
        if attr == "other_constraint" and metric in REGISTRY:
            if str(c.get("subj") or "").strip().lower().replace(" ", "_") == metric:
                c = dict(c, subj="")
            if metric in ("power_backup", "unavailable"):        # yes/no rules carry no number
                val = None
                c = dict(c, op=c.get("op") or ("require" if metric == "power_backup" else "forbid"))
            area = area or REGISTRY[metric][0]
        if attr == "other_constraint":
            if metric in REGISTRY and metric in CLOCK_METRICS | WINDOW_METRICS:
                pass
            elif isinstance(val, str):
                nv = _num(val)
                val = nv if nv is not None else val
    if attr != "other_constraint":
        metric = ""
        if attr == "start_time":
            pass
        elif isinstance(val, str):
            nv = _num(val)
            val = nv if nv is not None else val
    feat = c.get("feat") or c.get("features") or []
    if isinstance(feat, str):
        feat = [f.strip() for f in re.split(r"[,;]", feat) if f.strip()]
    return {
        "m": m, "kind": kind, "area": area, "attr": attr, "metric": metric,
        "subj": str(c.get("subj") or "").strip(), "val": val, "unit": str(c.get("unit") or ""),
        "ev": str(c.get("ev") or "").strip(), "op": str(c.get("op") or "").strip().lower(),
        "cap": _num(c.get("cap", c.get("capacity"))) or (_cap_from_ev(c.get("ev")) if attr == "venue_option" else None),
        "feat": [str(f) for f in feat],
        "dur": _num(c.get("dur")), "d": 1 if c.get("d") in (1, True, "1") else 0,
        "text": str(c.get("text") or ""),
    }


def parse_output(txt: str, ids: list[int], blob: bool = False) -> dict:
    data = _loads_lenient(txt)
    if not isinstance(data, dict) or not isinstance(data.get("claims"), list):
        raise ValueError("output has no 'claims' list")
    claims = [n for n in (normalize_claim(c) for c in data["claims"]) if n]
    msgs = data.get("msgs") if isinstance(data.get("msgs"), list) else []
    meta = {}
    for it in msgs:
        try:
            meta[int(it.get("m"))] = {"sender": str(it.get("sender") or ""), "date": str(it.get("date") or ""),
                                      "rel": "n" if str(it.get("rel", "y")).lower().startswith("n") else "y",
                                      "why": str(it.get("why") or ""), "t": it.get("t")}
        except (TypeError, ValueError, AttributeError):
            continue
    if not blob:
        bad = [c["m"] for c in claims if c["m"] not in ids]
        if bad and len(bad) == len(claims):
            raise ValueError(f"claims refer to unknown message ids {sorted(set(bad))}")
    return {"claims": claims, "meta": meta}


# ------------------------------------------------------------- grounding
def _source(m: Msg) -> str:
    return norm_text(" ".join(x for x in (m.text, m.sender or "", m.date or "") if x))


def ground_claim(c: dict, msg: Msg) -> str | None:
    """Return None if grounded, else the reason the claim is dropped."""
    ev = c["ev"]
    if not ev:
        return "no evidence span"
    src = _source(msg)
    for seg in [s for s in ev.split("|") if s.strip()]:
        if norm_text(seg).strip(" .") not in src:
            return f"evidence not found in message: {seg.strip()[:50]!r}"
    val = c["val"]
    attr, metric = c["attr"], c["metric"]
    clock_like = attr == "start_time" or (attr == "other_constraint" and metric in CLOCK_METRICS | WINDOW_METRICS)
    if clock_like and isinstance(val, str):
        cl = clocks_in(ev)
        if metric in WINDOW_METRICS:
            w = parse_window(val)
            if not w or w[0] not in cl or w[1] not in cl:
                return f"time window {val!r} not readable from evidence"
        else:
            t = parse_clock(val)
            if t is None or t not in cl:
                return f"clock time {val!r} not readable from evidence"
    elif isinstance(val, (int, float)) and not isinstance(val, bool):
        if val == 0 and re.search(r"\b(free|nil|zero|no charge|muft|complimentary)\b", ev, re.I):
            pass
        elif not derivable(val, ev):
            return f"value {val} not derivable from evidence"
    for k in ("cap", "dur"):
        if isinstance(c[k], (int, float)) and not derivable(c[k], ev):
            return f"{k} {c[k]} not derivable from evidence"
    return None


def ground(claims: list[dict], msgs: list[Msg], trace) -> list[dict]:
    by_id = {m.id: m for m in msgs}
    kept = []
    for c in claims:
        msg = by_id.get(c["m"])
        why = "unknown message id" if msg is None else ground_claim(c, msg)
        if why:
            trace.code(f"dropped claim msg {c['m']} {c['attr']}"
                       + (f"[{c['subj']}]" if c["subj"] else "") + f": {why}")
        else:
            kept.append(c)
    return kept


# ------------------------------------------------------------- the call
def read(msgs: list[Msg], llm, trace, deadline, blob: bool = False):
    """One batched model call. Returns (claims, meta, msgs) -- msgs may be re-split from a blob."""
    system, user = build_prompt(msgs, blob)
    ids = [m.id for m in msgs]
    max_tokens = min(4096, 1100 + 160 * len(msgs))
    res = llm.call("reading", system, user, trace=trace, deadline=deadline,
                   validate=lambda t: parse_output(t, ids, blob), max_tokens=max_tokens)
    claims, meta = res.parsed["claims"], res.parsed["meta"]
    if blob:
        whole = norm_text(msgs[0].text)
        split = []
        for i in sorted(meta):
            t = meta[i].get("t")
            if isinstance(t, str) and t.strip() and norm_text(t) in whole:
                split.append(Msg(len(split) + 1, t.strip(), msg_hash(t), meta[i]["sender"] or None,
                                 meta[i]["date"] or None))
        if len(split) >= 2:
            remap = {i: k + 1 for k, i in enumerate(sorted(meta)) if isinstance(meta[i].get("t"), str)
                     and norm_text(meta[i]["t"]) in whole}
            claims = [dict(c, m=remap[c["m"]]) for c in claims if c["m"] in remap]
            meta = {remap[i]: v for i, v in meta.items() if i in remap}
            trace.code(f"blob input: reader split it into {len(split)} messages")
            msgs = split
        else:
            claims = [dict(c, m=1) for c in claims]
            trace.code("blob input: reader could not split it; treating the whole file as message 1")
    claims = ground(claims, msgs, trace)
    claims = _hall_features(claims, msgs, trace)
    _fix_relevance(claims, meta, trace)
    return claims, meta, msgs


_REQUIRE = re.compile(r"\b(must|need|needs|require[sd]?|should|compulsory|mandatory|chahiye|chahie|zaroori|"
                      r"lazmi)\b", re.I)


def _hall_features(claims: list[dict], msgs: list[Msg], trace) -> list[dict]:
    """A 'power_backup' claim worded as what a hall HAS (not what the event NEEDS) is a hall feature."""
    out = []
    for c in claims:
        if c["attr"] == "other_constraint" and c["metric"] == "power_backup" and not _REQUIRE.search(c["ev"]):
            halls = [h for h in claims if h["m"] == c["m"] and h["attr"] == "venue_option"]
            text = next((m.text for m in msgs if m.id == c["m"]), "")
            sent = next((s for s in re.split(r"(?<=[.!?])\s+", text) if norm_text(c["ev"]).strip(" .") in norm_text(s)), c["ev"])
            hit = [h for h in halls if re.search(r"\b" + re.escape(h["subj"].split()[-1]) + r"\b", sent)]
            for h in hit:
                if "generator" not in h["feat"]:
                    h["feat"] = h["feat"] + ["generator"]
            trace.code(f"msg {c['m']}: 'power backup' is stated as a hall feature, not a requirement; "
                       f"attached to {len(hit)} hall(s)")
            continue
        out.append(c)
    return out


def _fix_relevance(claims: list[dict], meta: dict, trace) -> None:
    """Small models sometimes mark every message irrelevant while still extracting claims from it."""
    with_claims = {c["m"] for c in claims}
    flagged = [i for i, v in meta.items() if v.get("rel") == "n" and i in with_claims]
    if flagged and len(flagged) * 2 > len(meta):
        for i in flagged:
            meta[i]["rel"] = "y"
        trace.code(f"reader marked {len(flagged)} of {len(meta)} messages irrelevant yet extracted claims from them; "
                   "the irrelevant flag is ignored for those messages")