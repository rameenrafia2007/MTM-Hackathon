"""Validator (pure code): the six scored checks plus invariants and constraint checks.

It recomputes the key figures from the FACTS with its own arithmetic (it does not
call the planner's functions), so a planner bug cannot hide itself. A plan that
fails is never committed: updates are rejected with these failure texts as the reason.
"""
from __future__ import annotations

import math

from .areas import PLAN_AREAS, REGISTRY, cat_of
from .lang import fmt_clock, money, parse_clock, parse_window, same_subj
from .resolver import Facts


def _r(x):
    return int(math.floor(x + 0.5))


def _chk(cid, name, subs, extra_fail=None):
    fails = [s["detail"] for s in subs if not s["ok"]] + list(extra_fail or [])
    return {"id": cid, "name": name, "pass": not fails, "subs": subs, "detail": "; ".join(fails) if fails else
            "; ".join(s["detail"] for s in subs[:3]), "failures": fails}


def _sub(label, expected, actual, fmt=money):
    ok = expected == actual
    d = f"{label} {fmt(actual) if actual is not None else 'n/a'}"
    if not ok:
        d = f"{label}: plan has {fmt(actual) if actual is not None else 'nothing'}, recomputed {fmt(expected)}"
    return {"label": label, "expected": expected, "actual": actual, "ok": ok, "detail": d}


def _num(x):
    return f"{x:g}" if isinstance(x, float) else str(x)


def _find_venue(f: Facts, name):
    for k, s in f.venues.items():
        if name and same_subj(s["name"], name):
            return s
    return None


def independent_totals(f: Facts, plan: dict) -> dict | None:
    st, ts, mt, fee = f.v("student_count"), f.v("team_size"), f.v("max_teams_per_judge"), f.v("judge_fee")
    tb, D = f.v("total_budget"), f.v("duration_minutes")
    if None in (st, ts, mt, fee, tb) or not ts or not mt or st % ts:
        return None
    teams = int(st // ts)
    needed = -(-teams // int(mt))
    staff = f.v("staff_count", 0) or 0
    hc = int(st + staff + needed)
    return {"teams": teams, "needed": needed, "hc": hc, "honor": needed * fee, "budget": tb, "D": D}


def _why_missing(f: Facts) -> str:
    from .resolver import core_counts
    core = core_counts(f)
    if core and "error" in core:
        return "cannot recompute: " + core["error"]
    return "cannot recompute: students, team size, judges per team, judge fee or budget missing/invalid"


def validate(f: Facts, plan: dict) -> dict:
    checks, failures = [], []
    t = independent_totals(f, plan)
    ven, food, jud, sch, bud = (plan.get(a, {}) for a in ("venue", "food", "judging", "schedule", "budget"))

    # ---- 1 figures
    subs = []
    if t is None:
        subs.append({"label": "inputs", "ok": False, "expected": None, "actual": None,
                     "detail": _why_missing(f)})
    else:
        subs += [_sub("teams", t["teams"], jud.get("teams"), _num),
                 _sub("judges needed", t["needed"], jud.get("judges_needed"), _num),
                 _sub("headcount", t["hc"], (plan.get("headcount") or {}).get("total"), _num),
                 _sub("honoraria", t["honor"], jud.get("honorarium"))]
        vs = _find_venue(f, ven.get("name"))
        vexp = None
        if vs and vs.get("price") is not None and t["D"]:
            hrs = -(-int(t["D"]) // 60)
            base = vs["price"] * hrs if vs.get("unit") == "hour" else vs["price"]
            ot = sum(_r(c["val"] * max(0, hrs - c["dur"] / 60)) for c in f.mapped("overtime_rate")
                     if isinstance(c.get("val"), (int, float)) and isinstance(c.get("dur"), (int, float)))
            pct = sum(c["val"] for m in ("tax_pct", "service_charge_pct") for c in f.mapped(m)
                      if cat_of(c["subj"]) in ("venue", "all") and isinstance(c.get("val"), (int, float)))
            fee = sum(c["val"] for c in f.mapped("delivery_fee") if cat_of(c["subj"]) == "venue"
                      and isinstance(c.get("val"), (int, float)))
            vexp = base + ot + _r((base + ot) * pct / 100) + fee
        subs.append(_sub("venue cost", vexp, ven.get("total")) if vexp is not None else
                    {"label": "venue cost", "ok": False, "expected": None, "actual": ven.get("total"),
                     "detail": "venue cost cannot be recomputed (no hall chosen)"})
        # food
        buf = sum(c["val"] for c in f.mapped("food_buffer_pct") if isinstance(c.get("val"), (int, float)))
        req = math.ceil(t["hc"] * (1 + buf / 100))
        orders = [s["v"] for s in f.orders.values() if isinstance(s.get("v"), (int, float))]
        qexp = int(max([req] + orders))
        subs.append(_sub("meals ordered", qexp, food.get("quantity"), _num))
        price = None
        for k, s in f.prices.items():
            if food.get("tier") and same_subj(s["name"], food["tier"]) and isinstance(s.get("v"), (int, float)):
                price = s["v"]
        fexp = None
        if price is not None and food.get("quantity"):
            q = food["quantity"]
            shares = [(c, math.ceil(c["val"] / 100 * q)) for c in f.mapped("dietary_share")
                      if isinstance(c.get("val"), (int, float))]
            base = (q - sum(n for _, n in shares)) * price
            for c, n in shares:
                dp = next((s["v"] for s in f.prices.values() if same_subj(s["name"], c["subj"])
                           and isinstance(s.get("v"), (int, float))), price)
                base += n * dp
            pct = sum(c["val"] for m in ("tax_pct", "service_charge_pct") for c in f.mapped(m)
                      if cat_of(c["subj"]) in ("food", "all") and isinstance(c.get("val"), (int, float)))
            fee = sum(c["val"] for c in f.mapped("delivery_fee") if cat_of(c["subj"]) in ("food", "all")
                      and isinstance(c.get("val"), (int, float)))
            fexp = _r(base) + _r(base * pct / 100) + fee
        subs.append(_sub("food cost", fexp, food.get("total")) if fexp is not None else
                    {"label": "food cost", "ok": False, "expected": None, "actual": food.get("total"),
                     "detail": "food cost cannot be recomputed (no tier chosen)"})
        committed = sum(s["v"] for s in f.committed.values() if isinstance(s.get("v"), (int, float)))
        cpct = sum(c["val"] for c in f.mapped("contingency_pct") if isinstance(c.get("val"), (int, float)))
        cont = _r(cpct / 100 * t["budget"]) if cpct else 0
        if vexp is not None and fexp is not None:
            total = vexp + fexp + t["honor"] + committed + cont
            subs.append(_sub("total cost", total, bud.get("total")))
            subs.append(_sub("headroom", t["budget"] - total, bud.get("headroom")))
        else:
            subs.append({"label": "total cost", "ok": False, "expected": None, "actual": bud.get("total"),
                         "detail": "total cannot be recomputed (venue or food missing)"})
    checks.append(_chk(1, "Correct figures", subs))

    # ---- 2 within budget
    tb, total = bud.get("total_budget"), bud.get("total")
    if tb is None or total is None:
        checks.append(_chk(2, "Within budget", [{"label": "budget", "ok": False, "detail": "budget or total missing"}]))
    else:
        ok = total <= tb
        d = (f"total {money(total)} <= budget {money(tb)} (headroom {money(tb - total)})" if ok else
             f"total {money(total)} exceeds budget {money(tb)} by {money(total - tb)}")
        checks.append(_chk(2, "Within budget", [{"label": "total vs budget", "ok": ok, "detail": d}]))

    # ---- 3 venue fits
    hc = (plan.get("headcount") or {}).get("total")
    vs = _find_venue(f, ven.get("name"))
    subs = []
    if not vs or hc is None:
        subs.append({"label": "venue", "ok": False, "detail": "no hall chosen" if not vs else "headcount unknown"})
    else:
        cap = vs.get("capacity")
        subs.append({"label": "capacity", "ok": isinstance(cap, (int, float)) and cap >= hc,
                     "detail": f"{vs['name']} seats {_num(cap)} >= headcount {hc}" if cap is not None and cap >= hc
                     else f"{vs['name']} seats {_num(cap)} < headcount {hc}"})
        if any(c.get("op") != "forbid" for c in f.mapped("power_backup")):
            has = "power_backup" in vs.get("features", [])
            subs.append({"label": "power backup", "ok": has,
                         "detail": f"{vs['name']} has power backup" if has else f"{vs['name']} lacks the required power backup"})
        if any(same_subj(c["subj"], vs["name"]) for c in f.mapped("unavailable") if c.get("area") != "food"):
            subs.append({"label": "availability", "ok": False, "detail": f"{vs['name']} is stated as unavailable"})
    checks.append(_chk(3, "Venue fits", subs))

    # ---- 4 schedule
    checks.append(_check_schedule(f, plan))

    # ---- 5 judges
    subs = []
    if not jud.get("table"):
        subs.append({"label": "judges", "ok": False, "detail": "judging plan missing"})
    else:
        conf, need, mt, teams = jud["judges_confirmed"], jud["judges_needed"], jud["max_teams_per_judge"], jud["teams"]
        subs.append({"label": "confirmed", "ok": conf is not None and conf >= need,
                     "detail": f"{conf} confirmed >= {need} needed" if conf is not None and conf >= need
                     else f"only {conf} confirmed, {need} needed for {teams} teams (max {mt} each)"})
        covered = sorted(x for r in jud["table"] for x in r["teams"])
        subs.append({"label": "coverage", "ok": covered == list(range(1, teams + 1)),
                     "detail": f"all {teams} teams covered exactly once" if covered == list(range(1, teams + 1))
                     else f"team coverage broken: {len(covered)} assignments for {teams} teams"})
        over = [r["judge"] for r in jud["table"] if len(r["teams"]) > mt]
        subs.append({"label": "max per judge", "ok": not over,
                     "detail": f"no judge has more than {mt} teams" if not over else f"judges {over} exceed {mt} teams"})
    checks.append(_chk(5, "Enough judges", subs))

    # ---- 6 areas present
    need_keys = {"venue": "name", "schedule": "blocks", "food": "tier", "judging": "table", "budget": "total"}
    subs = []
    for a, k in need_keys.items():
        v = plan.get(a, {}).get(k)
        ok = bool(v) or (a == "budget" and v is not None)
        subs.append({"label": a, "ok": ok, "detail": f"{a}: present" if ok else f"{a}: missing"})
    checks.append(_chk(6, "All areas covered", subs))
    checks.sort(key=lambda c: c["id"])

    # ---- invariants
    inv = []
    f_hc = (plan.get("headcount") or {}).get("total")
    if food.get("quantity") is not None and f_hc is not None:
        inv.append({"name": "meals >= headcount", "ok": food["quantity"] >= f_hc,
                    "detail": f"{food['quantity']} meals vs headcount {f_hc}"})
    if jud.get("teams") is not None:
        inv.append({"name": "teams x team size = students", "ok": jud["teams"] * jud["team_size"] == jud["students"],
                    "detail": f"{jud['teams']} x {jud['team_size']} = {jud['teams'] * jud['team_size']} vs {jud['students']} students"})
    bad = []
    for a in ("venue", "food", "judging", "budget"):
        for k, v in plan.get(a, {}).items():
            if k in ("headroom", "src", "room", "buffer_pct", "tax", "tax_pct", "delivery", "fee", "overtime",
                     "seats_spare", "required", "alternatives", "errors", "table", "lines", "pending", "deposits",
                     "by_category", "contingency", "shares", "features"):
                continue
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v < 0:
                bad.append(f"{a}.{k}={v}")
            if k in ("quantity", "total", "capacity", "teams", "judges_used") and isinstance(v, (int, float)) and v == 0:
                bad.append(f"{a}.{k}=0")
    inv.append({"name": "no zero or negative values", "ok": not bad,
                "detail": "all quantities and amounts are positive" if not bad else "bad values: " + ", ".join(bad)})

    # ---- constraints
    cons = _check_constraints(f, plan)

    for c in checks:
        failures += [f"{c['name']}: {x}" for x in c["failures"]]
    failures += [f"invariant '{i['name']}': {i['detail']}" for i in inv if not i["ok"]]
    failures += [f"constraint '{c['metric']}': {c['detail']}" for c in cons if c["status"] == "FAIL"]
    if not failures:
        failures += list(plan.get("errors", []))
    return {"checks": checks, "invariants": inv, "constraints": cons, "failures": failures,
            "valid": not failures, "six_pass": sum(1 for c in checks if c["pass"]),
            "total_checks": len(checks)}


def _check_schedule(f: Facts, plan: dict) -> dict:
    sch, jud = plan.get("schedule", {}), plan.get("judging", {})
    blocks, D = sch.get("blocks") or [], f.v("duration_minutes")
    subs = []
    if not blocks or not D:
        return _chk(4, "Schedule valid", [{"label": "schedule", "ok": False, "detail": "schedule missing"}],
                    sch.get("errors"))
    s = sum(b["minutes"] for b in blocks)
    subs.append({"label": "sum", "ok": s == D, "detail": f"blocks sum to {s} = duration {int(D)} min" if s == D
                 else f"blocks sum to {s} min but the event is {int(D)} min"})
    pos = all(b["end"] - b["start"] > 0 for b in blocks)
    cont = blocks[0]["start"] == 0 and blocks[-1]["end"] == D and all(
        blocks[i]["end"] == blocks[i + 1]["start"] for i in range(len(blocks) - 1))
    subs.append({"label": "no overlaps", "ok": pos and cont,
                 "detail": "blocks are contiguous with no overlap or gap" if pos and cont
                 else "blocks overlap, leave a gap or have zero length"})
    meals = [b for b in blocks if b["type"] == "meal"]
    subs.append({"label": "meal break", "ok": bool(meals), "detail": "meal break present" if meals else "no meal break"})
    P = f.v("break_minutes", 0) or 0
    if P:
        pr = sum(b["minutes"] for b in blocks if b["type"] == "break")
        subs.append({"label": "prayer break", "ok": pr >= P,
                     "detail": f"prayer/other break {pr} min >= {int(P)} min required" if pr >= P
                     else f"prayer/other break only {pr} min, {int(P)} required"})
    jb = [b for b in blocks if b["type"] == "judging"]
    need = (jud.get("rounds") or 0) * (jud.get("minutes_per_team") or 0)
    subs.append({"label": "judging fits", "ok": bool(jb) and jb[0]["minutes"] >= need and need > 0,
                 "detail": f"judging window {jb[0]['minutes'] if jb else 0} min >= {need} min ({jud.get('rounds')} rounds x {jud.get('minutes_per_team')} min)"
                 if jb and jb[0]["minutes"] >= need else f"judging window too short for {need} min of judging"})
    start = parse_clock(f.v("start_time")) if f.v("start_time") is not None else sch.get("start_clock") and parse_clock(sch["start_clock"])
    for c in f.mapped("fixed_event"):
        tt = parse_clock(c.get("val"))
        if tt is None or start is None:
            continue
        name = c["subj"] or "Fixed event"
        hit = [b for b in blocks if b["start"] == tt - start and b["type"] in ("break", "event")]
        subs.append({"label": f"fixed {name}", "ok": bool(hit),
                     "detail": f"{name} placed at {fmt_clock(tt)}" if hit else f"{name} is not at its fixed time {fmt_clock(tt)}"})
    if jb and start is not None:
        for c in f.mapped("judge_unavailable"):
            w = parse_window(c["val"]) if isinstance(c.get("val"), str) else None
            if w:
                bad = jb[0]["start"] < w[1] - start and w[0] - start < jb[0]["end"]
                subs.append({"label": "judge unavailable", "ok": not bad,
                             "detail": f"judging avoids {fmt_clock(w[0])}-{fmt_clock(w[1])}" if not bad
                             else f"judging overlaps the {fmt_clock(w[0])}-{fmt_clock(w[1])} window when judges are unavailable"})
        for c in f.mapped("judge_available_from"):
            t0 = parse_clock(c.get("val"))
            if t0 is not None:
                ok = jb[0]["start"] >= t0 - start
                subs.append({"label": "judges available from", "ok": ok,
                             "detail": f"judging starts after {fmt_clock(t0)}" if ok else f"judging starts before judges are available at {fmt_clock(t0)}"})
        for c in f.mapped("judge_available_until"):
            t1 = parse_clock(c.get("val"))
            if t1 is not None:
                ok = jb[0]["end"] <= t1 - start
                subs.append({"label": "judges available until", "ok": ok,
                             "detail": f"judging ends before {fmt_clock(t1)}" if ok else f"judging runs past {fmt_clock(t1)}, when judges leave"})
    b = max([c["val"] for c in f.mapped("min_buffer") if isinstance(c.get("val"), (int, float))] or [0])
    if b:
        bad = 0
        for i in range(len(blocks) - 1):
            x, y = blocks[i], blocks[i + 1]
            if x["type"] in ("break", "meal", "judging", "event") and y["type"] in ("break", "meal", "judging", "event", "closing"):
                bad += 1
        subs.append({"label": "buffers", "ok": bad == 0,
                     "detail": f"{int(b)} min buffer kept between blocks" if not bad else f"{bad} block pair(s) lack the {int(b)} min buffer"})
    return _chk(4, "Schedule valid", subs, sch.get("errors"))


def _check_constraints(f: Facts, plan: dict) -> list:
    out = []
    ven, food, bud = plan.get("venue", {}), plan.get("food", {}), plan.get("budget", {})
    hc = (plan.get("headcount") or {}).get("total")

    def add(c, status, detail):
        out.append({"metric": c["metric"], "subj": c.get("subj", ""), "status": status, "detail": detail,
                    "text": c.get("text", ""), "src": c.get("src")})

    for c in f.cons.values():
        m, v = c["metric"], c.get("val")
        if m not in REGISTRY:
            add(c, "WARN", f"not enforced: '{c.get('text', '')[:80]}' is not a known constraint type")
        elif m == "power_backup":
            vs = _find_venue(f, ven.get("name"))
            ok = bool(vs) and (("power_backup" in vs.get("features", [])) == (c.get("op") != "forbid"))
            add(c, "PASS" if ok else "FAIL", "hall power backup rule satisfied" if ok else "chosen hall breaks the power backup rule")
        elif m == "dietary_share" and isinstance(v, (int, float)):
            q = food.get("quantity") or 0
            have = next((s["count"] for s in food.get("shares", []) if same_subj(s["diet"], c["subj"])), 0)
            need = math.ceil(v / 100 * q)
            add(c, "PASS" if have >= need else "FAIL", f"{c['subj'] or 'special'} meals {have} >= {need} ({v:g}% of {q})" if have >= need
                else f"{c['subj'] or 'special'} meals {have} < {need} ({v:g}% of {q})")
        elif m == "category_cap" and isinstance(v, (int, float)):
            cat = cat_of(c["subj"])
            if cat in ("venue", "food", "judging"):
                amt = (ven.get("total") if cat == "venue" else food.get("total") if cat == "food"
                       else plan.get("judging", {}).get("honorarium")) or 0
            else:
                amt = sum(l["amount"] for l in bud.get("lines", []) if l["category"] == "committed"
                          and same_subj(l.get("item", ""), c["subj"]))
            add(c, "PASS" if amt <= v else "FAIL", f"{c['subj'] or 'category'} spend {money(amt)} <= cap {money(v)}" if amt <= v
                else f"{c['subj'] or 'category'} spend {money(amt)} exceeds cap {money(v)} by {money(amt - v)}")
        elif m == "unavailable":
            if c.get("area") == "food":
                bad = same_subj(c["subj"], food.get("tier") or "\0")
            else:
                bad = same_subj(c["subj"], ven.get("name") or "\0")
            add(c, "FAIL" if bad else "PASS", f"{c['subj']} is unavailable but chosen" if bad else f"{c['subj']} not used (unavailable)")
        elif m == "contingency_pct":
            cont = bud.get("contingency", 0)
            exp = _r(v / 100 * (bud.get("total_budget") or 0)) if isinstance(v, (int, float)) else 0
            add(c, "PASS" if cont >= exp else "FAIL", f"contingency reserve {money(cont)} >= {money(exp)} ({v:g}% of budget)" if cont >= exp
                else f"contingency reserve {money(cont)} below the required {money(exp)}")
        elif m == "cost_per_head_cap" and isinstance(v, (int, float)):
            if cat_of(c["subj"]) == "food":
                ok = (food.get("unit_price") or 0) <= v
                add(c, "PASS" if ok else "FAIL", f"food {money(food.get('unit_price'))} per head <= cap {money(v)}" if ok
                    else f"food {money(food.get('unit_price'))} per head exceeds cap {money(v)}")
            elif hc:
                per = (bud.get("total") or 0) / hc
                ok = per <= v + 1e-9
                add(c, "PASS" if ok else "FAIL", f"cost per head {money(per)} <= cap {money(v)}" if ok
                    else f"cost per head {money(per)} exceeds cap {money(v)}")
        elif m == "food_buffer_pct" and isinstance(v, (int, float)) and hc:
            need = math.ceil(hc * (1 + v / 100))
            ok = (food.get("quantity") or 0) >= need
            add(c, "PASS" if ok else "FAIL", f"meals {food.get('quantity')} >= {need} ({v:g}% extra)" if ok else f"meals {food.get('quantity')} < {need}")
        elif m == "deposit_pct":
            ok = any(d for d in bud.get("deposits", []))
            add(c, "PASS" if ok else "FAIL", "deposit scheduled" if ok else "deposit not shown in budget")
        else:  # tax, service charge, delivery, overtime, buffers, fixed events, judge windows: verified in figures/schedule
            add(c, "PASS", "applied (verified in the figures / schedule checks)")
    return out
