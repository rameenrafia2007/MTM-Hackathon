"""Areas: the constraint registry, the dependency map, and the only model call
that is not "reading" (translating an unknown constraint into registry terms).

Without this module an update would recompute everything, and a rule of a kind
nobody anticipated would be silently ignored.
"""
from __future__ import annotations

import json
import re

from .lang import clocks_in, derivable, parse_clock, parse_window

PLAN_AREAS = ("venue", "schedule", "food", "judging", "budget")
ORDER = ("judging", "venue", "food", "schedule", "budget")           # compute order
DOWNSTREAM = {"judging": ("venue", "food", "schedule", "budget"),    # if X changes, these are redone
              "venue": ("food", "budget"), "food": ("budget",), "schedule": (), "budget": ()}

# metric -> (area, vendor_ok, hint shown to the reader/translator)
REGISTRY = {
    "power_backup": ("venue", False, "venue needs generator (op=require)"),
    "dietary_share": ("food", False, "subj=diet, val=min % of meals"),
    "fixed_event": ("schedule", False, "subj=name, val=HH:MM, dur=minutes"),
    "category_cap": ("budget", False, "subj=category/item, val=max rupees"),
    "tax_pct": ("budget", True, "subj=venue|food|all, val=%"),
    "service_charge_pct": ("budget", True, "subj=venue|food|all, val=%"),
    "unavailable": ("venue", True, "area=venue|food, subj=hall/tier"),
    "contingency_pct": ("budget", False, "val=% of budget reserved"),
    "cost_per_head_cap": ("budget", False, "subj=food|total, val=max rupees"),
    "overtime_rate": ("venue", True, "val=rupees/extra hour, dur=minutes included"),
    "min_buffer": ("schedule", False, "val=minutes between blocks"),
    "judge_unavailable": ("judging", False, "val=HH:MM-HH:MM"),
    "judge_available_from": ("judging", False, "val=HH:MM"),
    "judge_available_until": ("judging", False, "val=HH:MM"),
    "delivery_fee": ("food", True, "subj=food|venue, val=rupees"),
    "deposit_pct": ("budget", True, "subj=venue|food, val=%"),
    "food_buffer_pct": ("food", False, "val=% extra meals"),
}
CLOCK_METRICS = {"fixed_event", "judge_available_from", "judge_available_until"}
WINDOW_METRICS = {"judge_unavailable"}

SCALAR_ATTRS = ("total_budget", "student_count", "team_size", "staff_count", "duration_minutes",
                "break_minutes", "start_time", "judges_confirmed", "judge_fee", "minutes_per_team",
                "max_teams_per_judge")
KEYED_ATTRS = ("venue_option", "unit_price", "committed_spend", "requested_spend", "ordered_quantity")
ALL_ATTRS = SCALAR_ATTRS + KEYED_ATTRS + ("availability_note", "other_constraint")

_SC_AREAS = {
    "student_count": ("judging", "venue", "food", "schedule", "budget"),
    "team_size": ("judging", "venue", "food", "schedule", "budget"),
    "max_teams_per_judge": ("judging", "venue", "food", "schedule", "budget"),
    "staff_count": ("venue", "food", "budget"),
    "duration_minutes": ("schedule", "venue", "budget"),
    "break_minutes": ("schedule",), "start_time": ("schedule",),
    "total_budget": ("budget",), "judges_confirmed": ("judging",),
    "judge_fee": ("judging", "budget"), "minutes_per_team": ("judging", "schedule"),
}
_CAT = {"venue": ("venue", "budget"), "hall": ("venue", "budget"), "food": ("food", "budget"),
        "catering": ("food", "budget"), "judging": ("judging", "budget"), "honorarium": ("judging", "budget"),
        "all": ("venue", "food", "budget")}
_METRIC_AREAS = {"power_backup": ("venue", "budget"), "dietary_share": ("food", "budget"),
                 "fixed_event": ("schedule",), "min_buffer": ("schedule",),
                 "judge_unavailable": ("schedule",), "judge_available_from": ("schedule",),
                 "judge_available_until": ("schedule",), "contingency_pct": ("budget",),
                 "cost_per_head_cap": ("food", "budget"), "overtime_rate": ("venue", "budget"),
                 "food_buffer_pct": ("food", "budget"), "deposit_pct": ("budget",)}


def cat_of(subj: str) -> str:
    """Normalise a category word to venue|food|judging|all or the item text itself."""
    s = (subj or "").lower().strip()
    if not s or s in ("all", "everything", "total"):
        return "all"
    for k, c in (("venue", "venue"), ("hall", "venue"), ("room", "venue"), ("food", "food"),
                 ("cater", "food"), ("meal", "food"), ("lunch", "food"), ("judg", "judging"),
                 ("honor", "judging")):
        if k in s:
            return c
    return s


def areas_for_key(key: tuple) -> tuple:
    """Fact key -> directly affected areas (the dependency map)."""
    kind = key[0]
    if kind == "sc":
        return _SC_AREAS.get(key[1], ("budget",))
    if kind == "venue":
        return ("venue", "budget")
    if kind in ("price", "order"):
        return ("food", "budget")
    if kind in ("committed", "requested"):
        return ("budget",)
    if kind == "cons":
        metric, subj = key[1], key[2]
        if metric in ("category_cap", "tax_pct", "service_charge_pct", "delivery_fee", "deposit_pct",
                      "unavailable"):
            c = cat_of(subj)
            if metric == "unavailable":
                return ("venue", "budget") if c in ("venue", "all", "") else ("food", "budget")
            return _CAT.get(c, ("budget",))
        return _METRIC_AREAS.get(metric, ("budget",))
    return ("budget",)


def glossary() -> str:
    return "; ".join(f"{m}({h})" for m, (_, _, h) in REGISTRY.items())


# ------------------------------------------------------------- translation
def validate_translation(items: list, originals: dict) -> list:
    """Code re-validation of the model's translation: metric must be in the registry and
    numbers/clock times must be readable from the original constraint text."""
    ok = []
    for it in items:
        i = it.get("i")
        metric = it.get("metric")
        if i not in originals or metric not in REGISTRY:
            continue
        text = originals[i]["text"]
        val = it.get("val")
        if metric in CLOCK_METRICS and not isinstance(val, (int, float)):
            c = parse_clock(val)
            if c is not None and c not in clocks_in(text):
                continue
        elif metric in WINDOW_METRICS:
            w = parse_window(val) if isinstance(val, str) else None
            cl = clocks_in(text)
            if not w or w[0] not in cl or w[1] not in cl:
                continue
        elif isinstance(val, (int, float)) and not derivable(val, text):
            continue
        dur = it.get("dur")
        if isinstance(dur, (int, float)) and not derivable(dur, text):
            continue
        ok.append({"i": i, "metric": metric, "subj": str(it.get("subj") or ""), "val": val, "dur": dur,
                   "op": it.get("op") or "eq", "area": REGISTRY[metric][0], "src": originals[i]["src"],
                   "text": text, "via": "translation", "tier": originals[i].get("tier", "")})
    return ok


TRANSLATE_SYSTEM = (
    "Map each stated event constraint to ONE registry metric. You only translate wording; never "
    "calculate or plan. JSON only: {\"c\":[{\"i\":n,\"metric\":name,\"subj\":\"\",\"op\":\"min|max|eq|require|forbid\","
    "\"val\":number|\"HH:MM\",\"dur\":minutes}]}. Omit dur if none. If no metric fits, omit the item. "
    "Numbers as digits (lakh=100000, k=1000); clock times 24h. Metrics: ")


def translate(area: str, items: list, llm, trace, deadline) -> list:
    """ONE model call tagged with `area` for all unmapped constraints of that area."""
    originals = {i + 1: it for i, it in enumerate(items)}
    user = "\n".join(f"{i}: {it['text']}" for i, it in originals.items())

    def parse(txt: str):
        s = txt.strip()
        s = re.sub(r"^```(?:json)?|```$", "", s, flags=re.M).strip()
        a, b = s.find("{"), s.rfind("}")
        data = json.loads(s[a:b + 1])
        if not isinstance(data.get("c"), list):
            raise ValueError("missing 'c' list")
        return data["c"]

    res = llm.call(area, TRANSLATE_SYSTEM + glossary(), user, trace=trace, deadline=deadline,
                   validate=parse, max_tokens=600)
    out = validate_translation(res.parsed, originals)
    trace.code(f"translation of {len(items)} unmapped {area} constraint(s) -> {len(out)} registry constraint(s) "
               "after code re-validation")
    return out
