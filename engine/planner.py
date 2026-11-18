"""Planner (pure code): turns resolved facts into the five-area plan.

No model is used here. Judges, headcount, hall, food tier, schedule blocks and the
budget are all plain arithmetic and selection, so they are exact and cost 0 tokens.
Constraints of the registry (power backup, vegetarian share, tax, caps...) are
applied by the handlers below. Without it nothing could be recomputed for an update.
"""
from __future__ import annotations

import copy
import math
import os

from .areas import DOWNSTREAM, ORDER, cat_of
from .lang import fmt_clock, money, parse_clock, parse_window, same_subj
from .resolver import Facts, core_counts

POLICY = {"setup": 10, "closing": 15, "meal": 30, "gap": 5, "min_build": 10}
FLOORS = {"setup": 5, "closing": 5, "meal": 15}
PRAYER_WORDS = ("prayer", "namaz", "salah", "salat", "zuhr", "dhuhr", "asr", "jumma", "juma", "jumu")


def rnd(x: float) -> int:
    return int(math.floor(x + 0.5))


def _pct(f: Facts, metrics, cats) -> float:
    tot = 0.0
    for m in metrics:
        for c in f.mapped(m):
            if cat_of(c["subj"]) in cats and isinstance(c.get("val"), (int, float)):
                tot += c["val"]
    return tot


def _src(slot, field="v"):
    own = (slot or {}).get("own", {}).get(field)
    return [own[0]] if own else []


def _committed_total(f: Facts) -> int:
    return sum(s["v"] for s in f.committed.values() if isinstance(s.get("v"), (int, float)))


def contingency_amount(f: Facts) -> int:
    tb = f.v("total_budget")
    pct = sum(c["val"] for c in f.mapped("contingency_pct") if isinstance(c.get("val"), (int, float)))
    return rnd(pct / 100 * tb) if tb and pct else 0


def effective_budget(f: Facts, core: dict | None) -> float | None:
    tb = f.v("total_budget")
    if tb is None:
        return None
    eff = tb
    if core and "headcount" in core:
        for c in f.mapped("cost_per_head_cap"):
            if cat_of(c["subj"]) == "all" and isinstance(c.get("val"), (int, float)):
                eff = min(eff, c["val"] * core["headcount"])
    return eff


# ================================================================== judging
def plan_judging(f: Facts, plan: dict) -> dict:
    core = core_counts(f)
    out: dict = {"errors": [], "src": {}}
    miss = [a for a in ("student_count", "team_size", "max_teams_per_judge", "minutes_per_team", "judge_fee",
                        "judges_confirmed") if f.v(a) is None]
    if miss:
        out["errors"].append("missing facts: " + ", ".join(miss))
    if core is None:
        return out
    if "error" in core:
        out["errors"].append(core["error"])
        return out
    teams, needed, mt = core["teams"], core["judges_needed"], f.v("max_teams_per_judge")
    mpt, fee, conf = f.v("minutes_per_team"), f.v("judge_fee"), f.v("judges_confirmed")
    base, extra = divmod(teams, needed)
    table, nxt = [], 1
    for j in range(needed):
        n = base + (1 if j < extra else 0)
        table.append({"judge": j + 1, "teams": list(range(nxt, nxt + n))})
        nxt += n
    rounds = max(len(t["teams"]) for t in table)
    out.update({
        "students": core["students"], "team_size": core["team_size"], "teams": teams,
        "judges_confirmed": conf, "judges_needed": needed, "judges_used": needed,
        "max_teams_per_judge": mt, "minutes_per_team": mpt, "rounds": rounds, "fee": fee,
        "honorarium": needed * fee if fee is not None else None, "table": table,
        "window_minutes": rounds * mpt if mpt else None,
    })
    if conf is not None and conf < needed:
        out["errors"].append(f"not enough judges: {needed} needed for {teams} teams (max {mt} each), "
                             f"only {conf} confirmed")
    out["src"] = {"students": f.src("student_count"), "team_size": f.src("team_size"),
                  "teams": f.src("student_count") + f.src("team_size"),
                  "judges_confirmed": f.src("judges_confirmed"), "fee": f.src("judge_fee"),
                  "honorarium": f.src("judge_fee"), "max_teams_per_judge": f.src("max_teams_per_judge"),
                  "judges_needed": f.src("max_teams_per_judge") + f.src("student_count") + f.src("team_size"),
                  "judges_used": f.src("max_teams_per_judge"), "minutes_per_team": f.src("minutes_per_team")}
    return out


# ================================================================== venue
def venue_cost(f: Facts, opt: dict, duration) -> dict:
    hours = math.ceil(duration / 60) if duration else None
    price = opt.get("price") or 0
    base = price * (hours or 0) if opt.get("unit") == "hour" else price
    ot = 0
    for c in f.mapped("overtime_rate"):
        if isinstance(c.get("val"), (int, float)) and isinstance(c.get("dur"), (int, float)) and hours:
            ot += rnd(c["val"] * max(0, hours - c["dur"] / 60))
    pct = _pct(f, ("tax_pct", "service_charge_pct"), ("venue", "all"))
    tax = rnd((base + ot) * pct / 100)
    fee = sum(c["val"] for c in f.mapped("delivery_fee")
              if cat_of(c["subj"]) == "venue" and isinstance(c.get("val"), (int, float)))
    return {"hours": hours, "base": base, "overtime": ot, "tax": tax, "fee": fee, "total": base + ot + tax + fee}


def plan_venue(f: Facts, plan: dict) -> dict:
    core = core_counts(f)
    out: dict = {"errors": [], "src": {}}
    if core is None or "error" in core:
        out["errors"].append("cannot size the venue: headcount is unknown")
        return out
    hc, D = core["headcount"], f.v("duration_minutes")
    need_power = any(c.get("op") != "forbid" for c in f.mapped("power_backup"))
    forbid_power = any(c.get("op") == "forbid" for c in f.mapped("power_backup"))
    unavailable = [c for c in f.mapped("unavailable") if c.get("area") != "food"]
    caps = [c["val"] for c in f.mapped("category_cap")
            if cat_of(c["subj"]) == "venue" and isinstance(c.get("val"), (int, float))]
    alts, ok = [], []
    for k, s in f.venues.items():
        why = []
        if not isinstance(s.get("price"), (int, float)) or not isinstance(s.get("capacity"), (int, float)):
            why.append("quote incomplete (price or capacity missing)")
            alts.append({"name": s["name"], "capacity": s.get("capacity"), "price": s.get("price"), "ok": False,
                         "reasons": why})
            continue
        cost = venue_cost(f, s, D)
        feats = s.get("features", [])
        if s["capacity"] < hc:
            why.append(f"capacity {int(s['capacity'])} < headcount {hc}")
        if need_power and "power_backup" not in feats:
            why.append("no power backup (required)")
        if forbid_power and "power_backup" in feats:
            why.append("has power backup (excluded by a rule)")
        if any(same_subj(c["subj"], s["name"]) for c in unavailable):
            why.append("unavailable")
        if caps and cost["total"] > min(caps):
            why.append(f"cost {money(cost['total'])} exceeds venue cap {money(min(caps))}")
        rec = {"name": s["name"], "capacity": int(s["capacity"]), "price": s["price"], "unit": s.get("unit", "flat"),
               "features": feats, "total": cost["total"], "ok": not why, "reasons": why, "cost": cost, "slot": s}
        alts.append(rec)
        if not why:
            ok.append(rec)
    out["alternatives"] = [{k: v for k, v in a.items() if k not in ("slot", "cost")} for a in alts]
    out["required"] = (["power backup"] if need_power else []) + [f"capacity >= {hc}"]
    if not ok:
        out["errors"].append(f"no hall fits: headcount {hc}" + (", power backup required" if need_power else "")
                             + "; " + "; ".join(f"{a['name']}: {', '.join(a['reasons'])}" for a in alts))
        return out
    best = min(ok, key=lambda r: (r["total"], r["capacity"], r["name"]))
    s, cost = best["slot"], best["cost"]
    out.update({"name": best["name"], "capacity": best["capacity"], "price": best["price"], "unit": best["unit"],
                "features": best["features"], "hours": cost["hours"], "base": cost["base"],
                "overtime": cost["overtime"], "tax": cost["tax"], "fee": cost["fee"], "total": cost["total"],
                "seats_spare": best["capacity"] - hc, "headcount": hc})
    out["src"] = {"name": _src(s, "price"), "price": _src(s, "price"), "capacity": _src(s, "capacity"),
                  "features": _src(s, "features"), "total": _src(s, "price")}
    return out


# ================================================================== food
def _food_total(f: Facts, qty: int, price: float, shares: list, diet_prices: dict, tax_pct: float, fee: float):
    special = sum(s["count"] for s in shares)
    base = (qty - special) * price
    for s in shares:
        base += s["count"] * diet_prices.get(s["diet"], price)
    tax = rnd(base * tax_pct / 100)
    return {"base": rnd(base), "tax": tax, "fee": fee, "total": rnd(base) + tax + fee}


def plan_food(f: Facts, plan: dict) -> dict:
    core = core_counts(f)
    out: dict = {"errors": [], "src": {}}
    if core is None or "error" in core:
        out["errors"].append("cannot size the food order: headcount is unknown")
        return out
    hc = core["headcount"]
    buf = sum(c["val"] for c in f.mapped("food_buffer_pct") if isinstance(c.get("val"), (int, float)))
    qty_req = math.ceil(hc * (1 + buf / 100))
    ordered = [s["v"] for s in f.orders.values() if isinstance(s.get("v"), (int, float))]
    qty = int(max([qty_req] + ordered))
    diets = [c for c in f.mapped("dietary_share") if isinstance(c.get("val"), (int, float))]
    shares = [{"diet": c["subj"] or "special", "pct": c["val"], "count": math.ceil(c["val"] / 100 * qty),
               "src": [c["src"]]} for c in diets]
    diet_prices, tiers = {}, []
    for k, s in f.prices.items():
        if not isinstance(s.get("v"), (int, float)):
            continue
        d = next((x for x in shares if same_subj(x["diet"], s["name"])), None)
        if d:
            diet_prices[d["diet"]] = s["v"]
        else:
            tiers.append((s["name"], s))
    unavail = [c for c in f.mapped("unavailable") if c.get("area") == "food"]
    tiers = [(n, s) for n, s in tiers if not any(same_subj(c["subj"], n) for c in unavail)]
    pp_cap = [c["val"] for c in f.mapped("cost_per_head_cap")
              if cat_of(c["subj"]) == "food" and isinstance(c.get("val"), (int, float))]
    cat_cap = [c["val"] for c in f.mapped("category_cap")
               if cat_of(c["subj"]) == "food" and isinstance(c.get("val"), (int, float))]
    tax_pct = _pct(f, ("tax_pct", "service_charge_pct"), ("food", "all"))
    fee = sum(c["val"] for c in f.mapped("delivery_fee")
              if cat_of(c["subj"]) in ("food", "all") and isinstance(c.get("val"), (int, float)))
    venue, judging = plan.get("venue", {}), plan.get("judging", {})
    eff = effective_budget(f, core)
    others = (venue.get("total") or 0) + (judging.get("honorarium") or 0) + _committed_total(f) + contingency_amount(f)
    room = None if eff is None else eff - others
    if not tiers:
        out["errors"].append("no usable food price tier (none quoted, or all unavailable)")
        return out
    rows = []
    for n, s in sorted(tiers, key=lambda t: -t[1]["v"]):
        t = _food_total(f, qty, s["v"], shares, diet_prices, tax_pct, fee)
        why = []
        if pp_cap and s["v"] > min(pp_cap):
            why.append(f"price {money(s['v'])} over per-head cap {money(min(pp_cap))}")
        if cat_cap and t["total"] > min(cat_cap):
            why.append(f"total {money(t['total'])} over food cap {money(min(cat_cap))}")
        if room is not None and t["total"] > room:
            why.append(f"total {money(t['total'])} does not fit the remaining budget")
        rows.append({"tier": n, "price": s["v"], "total": t["total"], "fits": not why, "reasons": why,
                     "t": t, "slot": s})
    good = [r for r in rows if r["fits"]]
    pick = good[0] if good else min(rows, key=lambda r: r["price"])
    if not good:
        out["errors"].append(f"no food tier fits: cheapest '{pick['tier']}' -> " + "; ".join(pick["reasons"]))
    t = pick["t"]
    alts = []
    for r in rows:
        if r is pick:
            st = "selected" if good else "selected (cheapest, but does not fit)"
        elif r["price"] > pick["price"]:
            st = "rejected"
        else:
            st = "cheaper, not needed"
        alts.append({"tier": r["tier"], "price": r["price"], "total": r["total"], "status": st,
                     "reasons": r["reasons"] if st == "rejected" else []})
    out.update({"headcount": hc, "required": qty_req, "buffer_pct": buf, "quantity": qty, "tier": pick["tier"],
                "unit_price": pick["price"], "base": t["base"], "tax": t["tax"], "delivery": fee,
                "total": t["total"], "shares": [{k: v for k, v in s.items()} for s in shares],
                "tax_pct": tax_pct, "alternatives": alts})
    out["src"] = {"tier": _src(pick["slot"]), "unit_price": _src(pick["slot"]), "total": _src(pick["slot"]),
                  "quantity": f.src("student_count") + f.src("staff_count"), "headcount": f.src("student_count"),
                  "required": f.src("student_count")}
    return out


# ================================================================== schedule
def _overlap(a1, a2, b1, b2) -> bool:
    return a1 < b2 and b1 < a2


def plan_schedule(f: Facts, plan: dict) -> dict:
    out: dict = {"errors": [], "blocks": [], "src": {}}
    D = f.v("duration_minutes")
    if not D:
        out["errors"].append("missing fact: duration_minutes")
        return out
    D = int(D)
    judging = plan.get("judging", {})
    P = int(f.v("break_minutes", 0) or 0)
    mpt, rounds = judging.get("minutes_per_team"), judging.get("rounds")
    if not (mpt and rounds):
        out["errors"].append("judging window unknown (judging facts incomplete)")
        return out
    b = int(max([c["val"] for c in f.mapped("min_buffer") if isinstance(c.get("val"), (int, float))] or [0]))
    start_raw = f.v("start_time")
    start = parse_clock(start_raw) if start_raw is not None else parse_clock(os.getenv("EVENT_START_FALLBACK", ""))
    start_src = "stated in the messages" if start_raw is not None else ("EVENT_START_FALLBACK setting" if start is not None else None)
    S, C, M = POLICY["setup"], POLICY["closing"], POLICY["meal"]
    J = rounds * mpt + POLICY["gap"]
    fixed, flex, errs = [], [], []
    prayer_fixed = False
    for c in f.mapped("fixed_event"):
        name = c["subj"] or "Fixed event"
        t = parse_clock(c.get("val"))
        is_prayer = any(w in name.lower() for w in PRAYER_WORDS)
        mins = c.get("dur") or (P if is_prayer and P else None)
        if t is None or not mins:
            errs.append(f"fixed event '{name}' needs a clock time and a duration")
            continue
        if start is None:
            errs.append(f"fixed event '{name}' at {fmt_clock(t)} needs the event start time, which is not stated")
            continue
        off = t - start
        if off < 0:
            errs.append(f"fixed event '{name}' at {fmt_clock(t)} is before the event starts at {fmt_clock(start)}")
            continue
        fixed.append({"name": name, "type": "break" if is_prayer else "event", "start": int(off),
                      "minutes": int(mins), "src": [c["src"]]})
        prayer_fixed |= is_prayer
    if P and not prayer_fixed:
        flex.append({"name": "Prayer break", "type": "break", "minutes": P, "src": f.src("break_minutes")})
    # judge availability windows (as offsets)
    jbad, jlo, jhi = [], 0, D
    jc = f.mapped("judge_unavailable") + f.mapped("judge_available_from") + f.mapped("judge_available_until")
    if jc and start is None:
        errs.append("judge availability rules need the event start time, which is not stated")
        jc = []
    for c in f.mapped("judge_unavailable"):
        w = parse_window(c.get("val")) if isinstance(c.get("val"), str) else None
        if w and start is not None:
            jbad.append((w[0] - start, w[1] - start))
    for c in f.mapped("judge_available_from"):
        t = parse_clock(c.get("val"))
        if t is not None and start is not None:
            jlo = max(jlo, t - start)
    for c in f.mapped("judge_available_until"):
        t = parse_clock(c.get("val"))
        if t is not None and start is not None:
            jhi = min(jhi, t - start)
    out["errors"] += errs

    def total_required():
        return S + C + (J + b) + sum(x["minutes"] + b for x in fixed) + (M + b) + sum(x["minutes"] + b for x in flex)

    shrink = []
    target = POLICY["min_build"] * (len(flex) + len(fixed) + 2)
    while total_required() > D - target:
        k = max((n for n, v in (("setup", S), ("closing", C), ("meal", M)) if v > FLOORS[n]),
                key=lambda n: {"setup": S, "closing": C, "meal": M}[n], default=None)
        if k is None:
            break
        if k == "setup":
            S -= 5
        elif k == "closing":
            C -= 5
        else:
            M -= 5
        shrink.append(k)
    if total_required() >= D:
        out["errors"].append(f"schedule does not fit: blocks need {total_required()} min but the event is {D} min")
        return out
    busy = [(0, S, "Setup & kickoff", "setup"), (D - C, D, "Closing & awards", "closing")]
    for x in fixed:
        busy.append((x["start"], x["start"] + x["minutes"], x["name"], x["type"]))
        if b:
            busy.append((x["start"] + x["minutes"], x["start"] + x["minutes"] + b, "Buffer", "buffer"))
    # fixed events must not overlap each other or setup/closing
    for i, (a1, a2, n1, _) in enumerate(busy):
        for (c1, c2, n2, _) in busy[i + 1:]:
            if n1 != "Buffer" and n2 != "Buffer" and _overlap(a1, a2, c1, c2):
                out["errors"].append(f"'{n1}' ({a1}-{a2} min) overlaps '{n2}' ({c1}-{c2} min)")
    # judging: latest feasible start, honouring availability
    jstart = None
    for s in range(D - C - J - b, S - 1, -1):
        e = s + J
        if any(_overlap(s, e + b, x[0], x[1]) for x in busy):
            continue
        if s < jlo or e > jhi or any(_overlap(s, e, w0, w1) for w0, w1 in jbad):
            continue
        jstart = s
        break
    if jstart is None:
        out["errors"].append(f"no feasible judging window of {J} min (fixed events or judge availability leave no room)")
        return out
    busy.append((jstart, jstart + J, "Judging window", "judging"))
    if b:
        busy.append((jstart + J, jstart + J + b, "Buffer", "buffer"))

    def gaps():
        pts = sorted(busy, key=lambda x: x[0])
        res, cur = [], 0
        for a1, a2, _, _ in pts:
            if a1 > cur:
                res.append((cur, a1))
            cur = max(cur, a2)
        if cur < D:
            res.append((cur, D))
        return res

    for ev in [{"name": "Meal break", "type": "meal", "minutes": M, "src": []}] + flex:
        foot = ev["minutes"] + b
        cand = [g for g in gaps() if g[1] - g[0] >= foot]
        if not cand:
            out["errors"].append(f"no room for '{ev['name']}' ({ev['minutes']} min)")
            continue
        roomy = [g for g in cand if g[1] - g[0] >= foot + 2 * POLICY["min_build"]] or cand
        g = max(roomy, key=lambda g: (g[1] - g[0], -g[0]))
        st = g[0] + (g[1] - g[0] - foot) // 2
        busy.append((st, st + ev["minutes"], ev["name"], ev["type"]))
        if b:
            busy.append((st + ev["minutes"], st + foot, "Buffer", "buffer"))
    blocks = []
    cur = 0
    for a1, a2, name, typ in sorted(busy, key=lambda x: (x[0], x[1])):
        if a1 > cur:
            blocks.append({"name": "Build time", "type": "build", "start": cur, "end": a1})
        if a1 < cur:  # overlap (already reported) -> clip to keep the timeline well formed
            a1 = cur
        if a2 > a1:
            blocks.append({"name": name, "type": typ, "start": a1, "end": a2})
        cur = max(cur, a2)
    if cur < D:
        blocks.append({"name": "Build time", "type": "build", "start": cur, "end": D})
    for i, bl in enumerate(blocks):
        bl["minutes"] = bl["end"] - bl["start"]
        if start is not None:
            bl["clock_start"], bl["clock_end"] = fmt_clock(start + bl["start"]), fmt_clock(start + bl["end"])
    n = 0
    for bl in blocks:
        if bl["type"] == "build":
            n += 1
            bl["name"] = f"Build time {n}"
    out.update({"duration": D, "blocks": blocks, "total": sum(x["minutes"] for x in blocks),
                "start_clock": fmt_clock(start) if start is not None else None, "start_source": start_src,
                "judging_window": {"start": jstart, "end": jstart + J, "minutes": J, "rounds": rounds,
                                   "minutes_per_team": mpt, "gap": POLICY["gap"]},
                "buffer": b, "defaults": {"setup": S, "closing": C, "meal": M, "gap": POLICY["gap"]},
                "shrunk": shrink})
    out["src"] = {"duration": f.src("duration_minutes"), "break": f.src("break_minutes"),
                  "judging_window": f.src("minutes_per_team")}
    return out


# ================================================================== budget
def plan_budget(f: Facts, plan: dict) -> dict:
    out: dict = {"errors": [], "src": {}}
    tb = f.v("total_budget")
    if tb is None:
        out["errors"].append("missing fact: total_budget")
        return out
    v, fd, jd = plan.get("venue", {}), plan.get("food", {}), plan.get("judging", {})
    lines = []
    if v.get("name"):
        lines.append({"label": f"Venue: {v['name']}", "amount": v["total"], "category": "venue", "src": v["src"].get("price", [])})
    if fd.get("tier"):
        lines.append({"label": f"Food: {fd['tier']} x {fd['quantity']}", "amount": fd["total"], "category": "food",
                      "src": fd["src"].get("unit_price", [])})
    if jd.get("honorarium") is not None:
        lines.append({"label": f"Judges' honoraria: {jd['judges_used']} x {money(jd['fee'])}", "amount": jd["honorarium"],
                      "category": "judging", "src": jd["src"].get("fee", [])})
    for k, s in f.committed.items():
        if isinstance(s.get("v"), (int, float)):
            lines.append({"label": f"Already ordered: {s['name']}", "amount": s["v"], "category": "committed",
                          "src": _src(s), "item": s["name"]})
    cont = contingency_amount(f)
    if cont:
        pct = sum(c["val"] for c in f.mapped("contingency_pct") if isinstance(c.get("val"), (int, float)))
        lines.append({"label": f"Contingency reserve ({pct:g}% of budget)", "amount": cont, "category": "contingency",
                      "src": [c["src"] for c in f.mapped("contingency_pct")][:1]})
    total = sum(l["amount"] for l in lines)
    headroom = tb - total
    by_cat: dict = {}
    for l in lines:
        by_cat[l["category"]] = by_cat.get(l["category"], 0) + l["amount"]
    pending = []
    for k, s in f.requested.items():
        if s.get("approved") or not isinstance(s.get("v"), (int, float)):
            continue
        pending.append({"item": s["name"], "amount": s["v"], "fits": s["v"] <= headroom,
                        "headroom_after": headroom - s["v"], "src": _src(s), "sender": s.get("sender", "")})
    deposits = []
    for c in f.mapped("deposit_pct"):
        cat = cat_of(c["subj"])
        base = by_cat.get("venue" if cat in ("venue", "all") else "food", 0)
        if isinstance(c.get("val"), (int, float)):
            deposits.append({"label": f"{c['val']:g}% deposit for {c['subj'] or 'venue'}",
                             "amount": rnd(base * c["val"] / 100), "src": [c["src"]]})
    out.update({"total_budget": tb, "lines": lines, "total": total, "headroom": headroom, "by_category": by_cat,
                "pending": pending, "deposits": deposits, "contingency": cont})
    out["src"] = {"total_budget": f.src("total_budget"), "total": f.src("total_budget"),
                  "headroom": f.src("total_budget")}
    return out


AREA_FN = {"judging": plan_judging, "venue": plan_venue, "food": plan_food,
           "schedule": plan_schedule, "budget": plan_budget}


# ================================================================== orchestration of areas
def plan_areas(f: Facts, base: dict | None = None, dirty=None) -> tuple[dict, list]:
    """Compute the plan. With `base` and `dirty`, only dirty areas (and areas downstream of any area
    whose output changed) are recomputed; every other area is copied unchanged."""
    plan = copy.deepcopy(base) if base else {}
    todo = set(ORDER) if (dirty is None or not base) else set(dirty)
    done = []
    for area in ORDER:
        if area not in todo and area in plan:
            continue
        try:
            new = AREA_FN[area](f, plan)
        except Exception as e:  # noqa: BLE001  (a bug must not crash the run; it fails the plan visibly)
            new = {"errors": [f"internal error while planning {area}: {e!r}"], "src": {}}
        old = plan.get(area)
        plan[area] = new
        done.append(area)
        if old != new:
            todo |= set(DOWNSTREAM[area])
    core = core_counts(f) or {}
    plan["headcount"] = ({"students": core.get("students"), "staff": core.get("staff"),
                          "judges": core.get("judges_needed"), "total": core.get("headcount")}
                         if "headcount" in core else {})
    plan["errors"] = [f"{a}: {e}" for a in ORDER for e in plan[a].get("errors", [])]
    plan["src"] = {f"{a}.{k}": v for a in ORDER for k, v in plan[a].get("src", {}).items() if v}
    plan["constraints"] = [{"metric": c["metric"], "subj": c["subj"], "val": c.get("val"), "src": c.get("src"),
                            "text": c.get("text", ""), "enforced": c["metric"] in _ENFORCED}
                           for c in f.cons.values()]
    plan["assumptions"] = assumptions(f, plan)
    return plan, done


_ENFORCED = {"power_backup", "dietary_share", "fixed_event", "category_cap", "tax_pct", "service_charge_pct",
             "unavailable", "contingency_pct", "cost_per_head_cap", "overtime_rate", "min_buffer",
             "judge_unavailable", "judge_available_from", "judge_available_until", "delivery_fee",
             "deposit_pct", "food_buffer_pct"}


def assumptions(f: Facts, plan: dict) -> list:
    a = []
    j = plan.get("judging", {})
    if j.get("judges_used") is not None:
        extra = (j.get("judges_confirmed") or 0) - j["judges_used"]
        a.append(f"Only the judges needed are paid and fed: {j['judges_used']} (= ceil({j['teams']} teams / "
                 f"{j['max_teams_per_judge']} per judge))" + (f"; {extra} confirmed judge(s) are not used" if extra > 0 else "")
                 + ". Change this assumption if unused confirmed judges must be paid.")
    s = plan.get("schedule", {})
    if s.get("defaults"):
        d = s["defaults"]
        a.append(f"Planning defaults (not from the messages): setup {d['setup']} min, closing {d['closing']} min, "
                 f"meal {d['meal']} min, {d['gap']} min transition after judging rounds.")
    if s.get("start_source") == "EVENT_START_FALLBACK setting":
        a.append(f"Event start time {s['start_clock']} comes from the EVENT_START_FALLBACK setting, not from the messages.")
    if f.mapped("contingency_pct"):
        a.append("Contingency is computed as a percentage of the total budget and counted as spend.")
    if f.mapped("tax_pct") or f.mapped("service_charge_pct"):
        a.append("Tax / service charge applies to venue and food only (not honoraria or already-ordered items).")
    if f.v("staff_count") is None:
        a.append("Staff count was not stated: assumed 0.")
    if f.v("break_minutes") is None:
        a.append("No prayer/other break length was stated: none scheduled besides the meal break.")
    if f.mapped("dietary_share"):
        a.append("Dietary share is applied to the number of meals ordered; special meals cost the same unless a separate price was quoted.")
    return a


def key_figures(plan: dict) -> dict:
    """Flat view used for the plan diff."""
    out = {}

    def walk(prefix, v):
        if isinstance(v, dict):
            for k, x in v.items():
                if k in ("src", "alternatives", "errors", "table", "slot"):
                    continue
                walk(f"{prefix}.{k}" if prefix else k, x)
        elif isinstance(v, list):
            if v and isinstance(v[0], dict):
                for i, x in enumerate(v):
                    walk(f"{prefix}[{x.get('label') or x.get('name') or x.get('item') or i}]", x)
            else:
                out[prefix] = v
        else:
            out[prefix] = v
    for a in ORDER:
        walk(a, plan.get(a, {}))
    return out
