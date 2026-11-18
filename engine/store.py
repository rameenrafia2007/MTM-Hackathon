"""Store: JSON state on disk (no database) and the text exports.

State lives in state/ and exports in output/ (both git-ignored). Writing is atomic
so a killed run never leaves a half-written file. Without it an update would have
nothing to update.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

NR = "not reported"
ROOT = Path(__file__).resolve().parent.parent


def state_dir() -> Path:
    return Path(os.environ.get("MTM_STATE_DIR") or ROOT / "state")


def output_dir() -> Path:
    return Path(os.environ.get("MTM_OUTPUT_DIR") or ROOT / "output")


def _atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def load_json(name: str, default=None):
    try:
        return json.loads((state_dir() / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def save_json(name: str, data) -> None:
    _atomic(state_dir() / name, json.dumps(data, indent=1, ensure_ascii=False, default=list))


def load_state() -> dict | None:
    st = load_json("state.json")
    return st if isinstance(st, dict) and st.get("ledger") is not None else None


def save_state(st: dict) -> None:
    save_json("state.json", st)


def reset_state() -> None:
    p = state_dir() / "state.json"
    if p.exists():
        p.unlink()


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ------------------------------------------------------------------ text rendering
def _m(n):
    try:
        return f"Rs {float(n):,.0f}"
    except (TypeError, ValueError):
        return "n/a"


def render_plan_text(state: dict) -> str:
    p, v = state.get("plan") or {}, state.get("validation") or {}
    L = []
    h = p.get("headcount") or {}
    L.append("=" * 78)
    L.append("MTM PLANNER - PLAN" + ("" if v.get("valid") else "   *** PLAN IS NOT VALID ***"))
    L.append("=" * 78)
    if h:
        L.append(f"Headcount to seat and feed: {h.get('total')} = {h.get('students')} students + "
                 f"{h.get('staff')} staff + {h.get('judges')} judges used")
    ven, jud, food, sch, bud = (p.get(a, {}) for a in ("venue", "judging", "food", "schedule", "budget"))
    L.append("")
    L.append(f"VENUE    {ven.get('name', 'NONE')} - seats {ven.get('capacity')}, quoted {_m(ven.get('price'))}"
             + (" per hour" if ven.get("unit") == "hour" else "") + f", cost {_m(ven.get('total'))}"
             + (f", features: {', '.join(ven.get('features') or [])}" if ven.get("features") else ""))
    L.append(f"FOOD     {food.get('tier', 'NONE')} x {food.get('quantity')} at {_m(food.get('unit_price'))} per head"
             f" = {_m(food.get('total'))}" + (f" (tax {_m(food.get('tax'))})" if food.get("tax") else ""))
    for s in food.get("shares", []):
        L.append(f"         {s['diet']}: {s['count']} meals ({s['pct']:g}% minimum)")
    L.append(f"JUDGING  {jud.get('teams')} teams of {jud.get('team_size')}; {jud.get('judges_used')} judges used "
             f"({jud.get('judges_confirmed')} confirmed), max {jud.get('max_teams_per_judge')} teams each, "
             f"{jud.get('minutes_per_team')} min per team; honoraria {_m(jud.get('honorarium'))}")
    L.append("")
    L.append(f"SCHEDULE ({sch.get('duration')} min" + (f", starts {sch['start_clock']}" if sch.get("start_clock") else "") + ")")
    for b in sch.get("blocks", []):
        clock = f"  [{b['clock_start']}-{b['clock_end']}]" if b.get("clock_start") else ""
        L.append(f"  {b['start']:>3}-{b['end']:<3} {b['minutes']:>3} min  {b['name']}{clock}")
    L.append("")
    L.append(f"BUDGET   total {_m(bud.get('total'))} of {_m(bud.get('total_budget'))}  -> headroom {_m(bud.get('headroom'))}")
    for ln in bud.get("lines", []):
        L.append(f"  {ln['label']:<46}{_m(ln['amount']):>14}")
    for d in bud.get("deposits", []):
        L.append(f"  (cash timing) {d['label']}: {_m(d['amount'])}")
    for pr in bud.get("pending", []):
        L.append(f"PENDING  {pr['item']} {_m(pr['amount'])} - not approved, NOT in totals "
                 f"({'would fit' if pr['fits'] else 'would NOT fit'} in headroom)")
    if p.get("assumptions"):
        L.append("")
        L.append("ASSUMPTIONS")
        L += [f"  - {a}" for a in p["assumptions"]]
    L.append("")
    L.append(f"CHECKS   {v.get('six_pass', 0)}/{v.get('total_checks', 6)} of the six scored checks pass")
    for c in v.get("checks", []):
        L.append(f"  [{'PASS' if c['pass'] else 'FAIL'}] {c['id']}. {c['name']}: {c['detail']}")
    for i in v.get("invariants", []):
        L.append(f"  [{'PASS' if i['ok'] else 'FAIL'}] invariant {i['name']}: {i['detail']}")
    for c in v.get("constraints", []):
        L.append(f"  [{c['status']}] constraint {c['metric']}: {c['detail']}")
    L.append("")
    L.append("MESSAGE LEDGER")
    for e in state.get("ledger", []):
        L.append(f"  msg {e['id']:>2} [{e['status'].upper():<10}] {e.get('sender') or '?'} - {e['reason']}")
    return "\n".join(L)


def render_final(state: dict) -> str:
    runs = state.get("runs", [])
    parts = [render_plan_text(state), "", "#" * 78, "FULL TRACE OF EVERY RUN SINCE THE LAST FULL RUN", "#" * 78]
    grand, calls = 0, 0
    for r in runs:
        parts += ["", r["trace"]["text"], f"AREAS WITH MODEL CALLS (run {r['id']}): "
                  + (", ".join(r["trace"]["areas"]) or "none")]
        t = r["trace"]["totals"]
        grand += t["total_tokens"]
        calls += t["calls"]
    parts += ["", f"GRAND TOTAL TOKENS: {grand}  |  MODEL CALLS: {calls}  |  RUNS: {len(runs)}"]
    if runs:
        parts.append("AREAS WITH MODEL CALLS: " + (", ".join(runs[-1]["trace"]["areas"]) or "none"))
    return "\n".join(parts)


def write_outputs(state: dict, run: dict | None) -> dict:
    out = output_dir()
    paths = {"plan": out / "plan.json", "trace": out / "trace.txt", "final": out / "final_plan_and_trace.txt"}
    _atomic(paths["plan"], json.dumps({"plan": state.get("plan"), "validation": state.get("validation"),
                                       "ledger": state.get("ledger")}, indent=1, ensure_ascii=False, default=list))
    _atomic(paths["trace"], (run["trace"]["text"] if run else "") + "\n")
    _atomic(paths["final"], render_final(state) + "\n")
    return {k: str(v) for k, v in paths.items()}
