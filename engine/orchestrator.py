"""Orchestrator: the single entry point used by main.py and server.py.

Full run:  read (1 call) -> ground -> resolve -> plan -> validate -> save.
Update:    read new messages (1 call) -> gate -> recompute ONLY affected areas on a COPY
           -> validate -> commit, or REJECT the message and keep the old plan.
Without it there is nothing that decides when a message is wrong or which areas
may be recomputed.
"""
from __future__ import annotations

import itertools
import time

from . import areas, reader, resolver, store
from .lang import parse_date
from .llm import LLM, BudgetExceeded, Deadline, DeadlineExceeded, LLMError, NoSmallModel
from .planner import key_figures, plan_areas
from .resolver import build_facts, diff_snapshots
from .splitter import split_file, split_text
from .trace import Trace
from .validator import validate

MAX_UPDATE_MESSAGES = 3
FULL_LIMIT, UPDATE_LIMIT = 120.0, 60.0
FULL_MAX_CALLS, UPDATE_MAX_CALLS = 8, 5


def _clean(e: dict) -> dict:
    return {k: v for k, v in e.items() if not k.startswith("_")}


def diff_plans(old: dict | None, new: dict) -> list:
    if not old:
        return []
    a, b = key_figures(old), key_figures(new)
    out = []
    for k in sorted(set(a) | set(b)):
        if a.get(k) != b.get(k):
            out.append({"path": k, "area": k.split(".")[0], "before": a.get(k), "after": b.get(k)})
    return out


def _entries(msgs, claims, meta, run_id, trace) -> list:
    out = []
    for m in msgs:
        mm = meta.get(m.id, {})
        mine = [dict(c, m=m.id) for c in claims if c["m"] == m.id]
        sender = mm.get("sender") or m.sender or ""
        date = mm.get("date") or m.date or ""
        why = mm.get("why") or ""
        rel = "y"
        if mm.get("rel") == "n":
            rel = "n"
            if mine:
                trace.code(f"msg {m.id}: reader marked it irrelevant, its {len(mine)} claim(s) are ignored")
            mine = []
        elif not mine:
            rel = "n"
            why = why or "no claim could be verified against the message text"
        out.append({"id": m.id, "seq": m.id, "hash": m.hash, "text": m.text, "sender": sender, "date": date,
                    "tier": resolver.classify_sender(sender, m.text), "dkey": parse_date(date), "rel": rel, "why": why,
                    "claims": mine, "status": "", "reason": "", "run": run_id})
    return out


def _translate(entries: list, llm, trace, deadline) -> None:
    """Unmapped constraint kinds: ONE area-tagged model call per area, then code re-validation."""
    by_area: dict = {}
    for e in entries:
        if e.get("status") == "rejected":
            continue
        for c in e["claims"]:
            if (c["attr"] == "other_constraint" and (c["metric"] or "") not in areas.REGISTRY
                    and not c.get("tried") and not c.get("skip")):
                by_area.setdefault(c["area"] or "budget", []).append((e, c))
    for area, items in by_area.items():
        pseudo = [{"text": c["ev"] or c["text"], "src": e["id"], "tier": e["tier"]} for e, c in items]
        trace.code(f"{len(items)} constraint(s) of an unknown kind in area {area}: model call to translate them")
        try:
            res = areas.translate(area, pseudo, llm, trace, deadline)
        except (LLMError, BudgetExceeded, DeadlineExceeded) as ex:
            trace.code(f"translation of {area} constraints failed ({ex}); they stay flagged as not enforced")
            res = []
        for e, c in items:
            c["tried"] = True
        for r in res:
            e, orig = items[r["i"] - 1]
            if e["tier"] == "vendor" and not areas.REGISTRY[r["metric"]][1]:
                e["status"], e["reason"] = "rejected", (f"vendor '{e['sender']}' tried to set {r['metric']}, "
                                                        "which only the organizers may set")
                trace.rejected(e["id"], e["reason"])
                break
            orig["skip"] = True
            e["claims"].append({"m": e["id"], "kind": "other_constraint", "area": r["area"],
                                "attr": "other_constraint", "metric": r["metric"], "subj": r["subj"],
                                "val": r["val"], "unit": "", "ev": r["text"], "op": r["op"], "cap": None,
                                "feat": [], "dur": r["dur"], "d": 0, "text": "", "via": "translation"})


def _isolate(accepted: list, first_failure: str, trace) -> tuple | None:
    """A fresh pack gave an invalid plan: find the message(s) whose removal makes it valid."""
    cands = sorted([e for e in accepted if e["claims"]], key=lambda e: (e["_eff"], e["seq"]), reverse=True)
    groups = [(e,) for e in cands] + list(itertools.islice(itertools.combinations(cands, 2), 120))
    for g in groups:
        trial = [x for x in accepted if all(x is not y for y in g)]
        f = build_facts(trial)
        p, _ = plan_areas(f)
        if validate(f, p)["valid"]:
            return g, first_failure
    return None


def _fail(trace, run_id, mode, state, error, t0, reuse_state=True) -> dict:
    trace.code(f"ERROR: {error}")
    run = _run_record(run_id, mode, trace, t0, [], [], [], {}, False, error)
    try:
        store.output_dir().mkdir(parents=True, exist_ok=True)
        (store.output_dir() / "trace.txt").write_text(run["trace"]["text"] + "\n", encoding="utf-8")
    except OSError:
        pass
    return {"ok": False, "error": error, "mode": mode, "state": state, "run": run, "trace": trace}


def _run_record(run_id, mode, trace, t0, new_ids, affected, rejected, diff, valid, error=None) -> dict:
    return {"id": run_id, "mode": mode, "label": "full run" if mode == "full" else "update",
            "started": store.now_iso(), "seconds": round(time.time() - t0, 2), "new_ids": new_ids,
            "affected": affected, "model_areas": trace.areas_with_calls(), "rejected": rejected,
            "diff": diff, "valid": valid, "error": error, "trace": trace.to_dict()}


def process(path: str | None = None, *, text: str | None = None, llm: LLM | None = None, echo: bool = False,
            color: bool = False, full_limit: float = FULL_LIMIT, update_limit: float = UPDATE_LIMIT) -> dict:
    t0 = time.time()
    try:
        msgs = split_text(text) if text is not None else split_file(path)
    except OSError as e:
        t = Trace("run", echo=echo, color=color)
        return _fail(t, 0, "full", None, f"cannot read the message file: {e}", t0)
    state = store.load_state()
    known = {e["hash"] for e in state["ledger"]} if state else set()
    fresh = state is None or len(msgs) > MAX_UPDATE_MESSAGES
    prev_runs = [] if fresh else list(state.get("runs", []))
    run_id = (prev_runs[-1]["id"] + 1) if prev_runs else 1
    mode = "full" if fresh else "update"
    trace = Trace(f"run {run_id} ({'full run' if fresh else 'update'})",
                  max_calls=FULL_MAX_CALLS if fresh else UPDATE_MAX_CALLS, echo=echo, color=color)
    if not msgs:
        return _fail(trace, run_id, mode, state, "the message file is empty", t0)
    if fresh:
        new_msgs = msgs
        why = ("no saved state" if state is None else f"{len(msgs)} messages (more than {MAX_UPDATE_MESSAGES})")
        trace.code(f"mode: FULL RUN, state reset ({why}); {len(msgs)} message(s) read from the file "
                   f"(format: {msgs[0].fmt})")
    else:
        new_msgs = [m for m in msgs if m.hash not in known]
        if not new_msgs:
            trace.code(f"mode: UPDATE; all {len(msgs)} message(s) are already in the ledger, nothing to do")
            return {"ok": True, "noop": True, "mode": "noop", "state": state, "trace": trace, "run": None}
        nid = max(e["id"] for e in state["ledger"]) + 1
        for m in new_msgs:
            m.id, nid = nid, nid + 1
        trace.code(f"mode: UPDATE; {len(new_msgs)} new message(s) (hash not in ledger), "
                   f"{len(msgs) - len(new_msgs)} already known")
    first_id = new_msgs[0].id
    deadline = Deadline(full_limit if fresh else update_limit)
    llm = llm or LLM()
    try:
        llm.ensure_chain(trace)
    except NoSmallModel as e:
        return _fail(trace, run_id, mode, state, str(e).splitlines()[0], t0)
    blob = any(m.blob for m in new_msgs)

    # ---- 1. read: the one batched model call
    try:
        claims, meta, msgs2 = reader.read(new_msgs, llm, trace, deadline, blob=blob)
    except (LLMError, BudgetExceeded, DeadlineExceeded) as e:
        kept = "the previous plan is kept" if (state and not fresh) else "no plan could be produced"
        return _fail(trace, run_id, mode, state, f"reading failed ({e}); {kept}", t0)
    if blob and len(msgs2) > 1:
        shift = first_id - 1
        for m in msgs2:
            m.id += shift
        claims = [dict(c, m=c["m"] + shift) for c in claims]
        meta = {k + shift: v for k, v in meta.items()}
    new_entries = _entries(msgs2, claims, meta, run_id, trace)
    trace.code(f"reader produced {len(claims)} grounded claim(s) for {len(new_entries)} message(s)")

    old_entries = [] if fresh else [dict(e) for e in state["ledger"]]
    prev_plan = None if fresh else state.get("plan")
    all_entries = old_entries + new_entries
    resolver.assign_eff(all_entries)

    if fresh:
        accepted, rej = resolver.resolve_batch([], new_entries, True, trace)
        accepted, rej2 = resolver.post_checks(accepted, trace)
        _translate([e for e in accepted], llm, trace, deadline)
        accepted = [e for e in accepted if e.get("status") != "rejected"]
        facts = build_facts(accepted)
        plan, done = plan_areas(facts)
        val = validate(facts, plan)
        trace.code(f"resolved facts from {len(accepted)} accepted message(s); planned areas in code: {', '.join(done)}")
        if not val["valid"]:
            trace.code("plan is not valid: " + " | ".join(val["failures"][:3]) + "; looking for the message that breaks it")
            iso = _isolate(accepted, val["failures"][0], trace)
            if iso:
                group, first = iso
                for e in group:
                    e["status"] = "rejected"
                    e["reason"] = (f"keeping it makes the plan invalid ({first}); even the cheapest hall and food tier "
                                   f"cannot fix that without it")
                    trace.rejected(e["id"], e["reason"])
                accepted = [e for e in accepted if all(e is not y for y in group)]
                facts = build_facts(accepted)
                plan, done = plan_areas(facts)
                val = validate(facts, plan)
        affected = list(done)
        diff = []
    else:
        accepted = [e for e in old_entries if e.get("status") != "rejected"]
        facts = build_facts(accepted)
        plan, val = prev_plan, state.get("validation", {})
        affected: list = []
        for e in new_entries:
            if e["rel"] == "n" or not e["claims"]:
                accepted.append(e)
                trace.code(f"msg {e['id']}: no plan-relevant content ({e['why'] or 'irrelevant'}); plan untouched")
                continue
            why = resolver.gate(e, facts, False)
            if why:
                e["status"], e["reason"] = "rejected", why
                trace.rejected(e["id"], why)
                continue
            trial = accepted + [e]
            _translate([e], llm, trace, deadline)
            if e.get("status") == "rejected":
                continue
            f2 = build_facts(trial)
            ch = diff_snapshots(facts.snapshot(), f2.snapshot())
            for k, a, b in ch:
                trace.code(f"fact changed: {' / '.join(str(x) for x in k)}: {a} -> {b}")
            dirty = sorted({a for k, _, _ in ch for a in areas.areas_for_key(k)}, key=areas.ORDER.index)
            p2, done = plan_areas(f2, base=plan, dirty=dirty)
            full, _ = plan_areas(f2)
            casc = [a for a in areas.ORDER if full[a] != p2[a]]
            if casc:
                trace.code(f"cascade: {', '.join(casc)} also change (re-optimised from the new budget room)")
                p2, done = full, list(areas.ORDER)
            v2 = validate(f2, p2)
            if not v2["valid"]:
                reason = (f"applying it makes the plan invalid after re-optimising: {v2['failures'][0]}"
                          if v2["failures"] else "applying it makes the plan invalid")
                e["status"], e["reason"] = "rejected", reason
                trace.rejected(e["id"], reason)
                continue
            for a in areas.ORDER:
                if a in done:
                    trace.code(f"area {a} recomputed in code (0 tokens); no model needed for area {a}")
                else:
                    trace.code(f"no model needed for area {a}: its inputs did not change")
            affected += [a for a in done if a not in affected]
            accepted, facts, plan, val = trial, f2, p2, v2
        if not affected:
            trace.code("no area needed recomputing")
        diff = diff_plans(prev_plan, plan)

    # ---- finish: statuses, state, outputs
    acc_ids = {e["id"] for e in accepted}
    resolver.finalize(all_entries, build_facts([e for e in all_entries if e["id"] in acc_ids]))
    rejected = [{"id": e["id"], "reason": e["reason"]} for e in new_entries if e.get("status") == "rejected"]
    t = trace.totals()
    trace.code("validator: " + f"{val.get('six_pass', 0)}/{val.get('total_checks', 6)} scored checks pass; "
               + ("plan VALID" if val.get("valid") else "plan NOT valid: " + "; ".join(val.get("failures", [])[:2])))
    trace.code(f"affected areas: {', '.join(affected) if affected else 'none'}")
    trace.code(f"areas that made model calls: {', '.join(trace.areas_with_calls()) or 'none'}")
    trace.code(f"model calls: {t['calls']}" + (f" of limit {t['limit']}" if t["limit"] else "")
               + f"; elapsed {time.time() - t0:.1f}s of {int(deadline.seconds)}s")
    run = _run_record(run_id, mode, trace, t0, [e["id"] for e in new_entries], affected, rejected, diff,
                      bool(val.get("valid")))
    ledger = [_clean(e) for e in sorted(all_entries, key=lambda e: e["id"])]
    st = {"version": 1, "ledger": ledger, "plan": plan, "validation": val, "runs": prev_runs + [run],
          "created": store.now_iso() if fresh else state.get("created"), "updated": store.now_iso()}
    store.save_state(st)
    store.write_outputs(st, run)
    return {"ok": True, "mode": mode, "state": st, "run": run, "trace": trace, "plan": plan, "validation": val,
            "rejected": rejected}
