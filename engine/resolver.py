"""Resolver (pure code): who may say what, which message wins, which is wrong.

Turns grounded claims into one consistent set of facts. Latest date wins (older
claims become "superseded by msg X"); authority decides conflicts; plausible but
wrong messages are rejected here with a specific reason. No model is involved.
Without it, stale prices, vendor-changed headcounts and impersonators would
flow straight into the plan.
"""
from __future__ import annotations

import math
import re
from collections import Counter

from .areas import REGISTRY
from .lang import canon_subj, money, parse_date, same_subj, unit_from_ev

RANK = {"authority": 4, "domain": 3, "vendor": 2, "unknown": 1, "individual": 0, "unverified": -1}
ROLE = {"authority": "organizer-level sender", "domain": "judging coordinator", "vendor": "vendor",
        "unknown": "unrecognised sender", "individual": "individual participant", "unverified": "unverified sender"}

_UNVERIFIED = re.compile(r"unknown|unverified|new number|anonymous|\+\d|\b\d{7,}\b|@|gmail|yahoo|hotmail|"
                         r"outlook|proton|not sure|someone|forwarded|pretend|impersonat|fake|claims? to be|"
                         r"unsaved|random", re.I)
_DOMAIN = re.compile(r"(coordinator|convener|convenor|panel|committee|head|lead|manager|chair|in-?charge|"
                     r"organi[sz]er)", re.I)
_VENDOR = re.compile(r"\b(halls?|venue|caterers?|catering|vendors?|suppliers?|rental|restaurant|bakery|"
                     r"kitchen|cafe|café|decor|sound|printers?|printing press|transport|equipment|"
                     r"booking|marquee|banquet|lighting|hotel)\b", re.I)
_AUTH = re.compile(r"\b(dean|organi[sz]ers?|organi[sz]ing|finance|accounts?|registrar|director|principal|admin|"
                   r"administration|management|committee|chairman|chair|hod|head of|vice[- ]chancellor|vc|"
                   r"rector|bursar|treasurer|chief|ceo|cfo|operations|ops|university|office of)\b", re.I)
_INDIV = re.compile(r"\b(student|students|society|club|volunteers?|participants?|mentors?|members?|attendees?|"
                    r"judge|speaker|guest|team)\b", re.I)

AUTH_ONLY = {"total_budget", "student_count", "team_size", "staff_count", "duration_minutes",
             "break_minutes", "start_time", "committed_spend"}
DOMAIN_ATTRS = {"judges_confirmed", "judge_fee", "minutes_per_team", "max_teams_per_judge"}
VENDOR_ATTRS = {"venue_option", "unit_price", "ordered_quantity"}
POSITIVE = {"total_budget", "student_count", "team_size", "duration_minutes", "minutes_per_team",
            "max_teams_per_judge", "judges_confirmed", "unit_price", "venue_option"}
CUES = re.compile(r"\b(ab|now|revised?|instead|correction|corrected|updated?|changed?|naya|new rate|"
                  r"not rs|replaces?)\b", re.I)
FEATURE_SYNONYMS = {"power_backup": ("generator", "power backup", "backup power", "ups", "inverter",
                                     "bijli", "standby power", "backup supply")}


_TEXT_UNVERIFIED = re.compile(r"new number|another number|different number|unknown number|\+\d{2}[\s\d-]{8,}|"
                              r"\b0\d{3}[\s-]?\d{7}\b|gmail|yahoo|hotmail|can'?t talk|pretend", re.I)


def classify_sender(sender: str, text: str = "") -> str:
    """Tier from the sender label. The message text can only DOWNGRADE trust (new number, free-mail...)."""
    s = sender or ""
    if _TEXT_UNVERIFIED.search(text or ""):
        return "unverified"
    if not s.strip():
        return "unknown"
    if _UNVERIFIED.search(s):
        return "unverified"
    if re.search(r"judg", s, re.I):
        return "domain" if _DOMAIN.search(s) else "individual"
    if _VENDOR.search(s):
        return "vendor"
    if _AUTH.search(s):
        return "authority"
    if _INDIV.search(s):
        return "individual"
    return "unknown"


def norm_feature(f: str) -> str:
    t = f.lower().strip()
    for canon, words in FEATURE_SYNONYMS.items():
        if any(w in t for w in words):
            return canon
    return t


def _int(x):
    return int(x) if isinstance(x, (int, float)) and float(x).is_integer() else x


def describe(c: dict) -> str:
    name = c["metric"] if c["attr"] == "other_constraint" and c.get("metric") else c["attr"]
    subj = f"[{c['subj']}]" if c.get("subj") else ""
    v = c["val"]
    return f"{name}{subj}={_int(v) if isinstance(v, (int, float)) else v}"


# ------------------------------------------------------------------ facts
class Facts:
    """Resolved facts. Every slot records which message (and claim) owns each value."""

    def __init__(self):
        self.sc, self.venues, self.prices, self.committed = {}, {}, {}, {}
        self.requested, self.orders, self.cons = {}, {}, {}
        self.notes: list = []
        self.alive: dict = {}      # (eid, idx) -> set of (slot-id, field) the claim still owns
        self.sup_by: dict = {}     # (eid, idx) -> entry id that overrode it
        self.touched: set = set()  # claims that registered anything

    # -- accessors
    def v(self, attr, default=None):
        s = self.sc.get(attr)
        return _int(s["v"]) if s and "v" in s else default

    def src(self, attr):
        s = self.sc.get(attr)
        return [s["own"]["v"][0]] if s and "v" in s.get("own", {}) else []

    @staticmethod
    def find(table: dict, subj: str):
        for k, slot in table.items():
            if same_subj(k, subj):
                return k, slot
        return None, None

    def unmapped(self) -> list:
        return [c for c in self.cons.values() if c["metric"] not in REGISTRY]

    def mapped(self, metric: str) -> list:
        return [c for c in self.cons.values() if c["metric"] == metric]

    # -- ownership
    def _own(self, table_name, slot, sid, field, new, eid, idx):
        prev = slot["own"].get(field)
        same = field in slot and slot[field] == new
        if prev is not None and prev != (eid, idx) and not same:
            self.alive.get(prev, set()).discard((sid, field))
            if not self.alive.get(prev):
                self.sup_by[prev] = eid
        if not (same and prev is not None):
            slot[field] = new
            slot["own"][field] = (eid, idx)
        self.alive.setdefault((eid, idx), set()).add((sid, field))
        self.touched.add((eid, idx))

    def _slot(self, table: dict, subj: str, make):
        k, slot = self.find(table, subj)
        if slot is None:
            k = canon_subj(subj)
            slot = make()
            slot["name"] = subj
            slot["own"] = {}
            table[k] = slot
        return k, slot

    # -- applying a claim
    def apply(self, e: dict, idx: int, c: dict):
        eid, attr, v = e["id"], c["attr"], c["val"]
        if c.get("skip"):          # replaced by a translated claim; kept only for display
            return
        meta = {"tier": e["tier"], "eff": e.get("_eff"), "dated": e.get("_dated", False),
                "sender": e["sender"], "ev": c["ev"]}
        self.touched.add((eid, idx))
        if attr in ("availability_note",):
            self.notes.append({"src": eid, "text": c["ev"], "tier": e["tier"]})
            return
        if c["kind"] == "request" or attr == "requested_spend":
            if isinstance(v, (int, float)):
                k, s = self._slot(self.requested, c["subj"], lambda: {})
                s.update(meta)
                self._own("requested", s, ("requested", k), "v", v, eid, idx)
            return
        if attr == "other_constraint":
            metric = c["metric"] or canon_subj(c["subj"])
            k = (metric, canon_subj(c["subj"]))
            s = self.cons.get(k)
            if s is None:
                s = {"metric": metric, "subj": c["subj"], "own": {}, "area": c["area"], "text": c["ev"] or c["text"]}
                self.cons[k] = s
            if metric in REGISTRY:
                s["area"] = REGISTRY[metric][0] if metric != "unavailable" else (c["area"] or "venue")
            s.update(meta)
            s["text"] = c["ev"] or c["text"]
            s["src"] = eid
            s["op"], s["dur"] = c["op"], c["dur"]
            self._own("cons", s, ("cons", k), "val", (v, c["op"], c["dur"]), eid, idx)
            s["val"] = v
            return
        if attr == "venue_option":
            k, s = self._slot(self.venues, c["subj"], lambda: {"features": []})
            s.update(meta)
            if isinstance(v, (int, float)):
                self._own("venue", s, ("venue", k), "price", v, eid, idx)
                s["unit"] = "hour" if unit_from_ev(c["ev"]) == "per_hour" else "flat"
            if isinstance(c.get("cap"), (int, float)):
                self._own("venue", s, ("venue", k), "capacity", c["cap"], eid, idx)
            if c["feat"]:
                self._own("venue", s, ("venue", k), "features", [norm_feature(f) for f in c["feat"]], eid, idx)
            return
        table = {"unit_price": self.prices, "committed_spend": self.committed, "ordered_quantity": self.orders}.get(attr)
        if table is not None:
            if not isinstance(v, (int, float)):
                return
            k, s = self._slot(table, c["subj"], lambda: {})
            if c.get("d") and "v" in s:
                v = s["v"] + v
            s.update(meta)
            self._own(attr, s, (attr, k), "v", v, eid, idx)
            return
        if attr in ("total_budget", "student_count", "team_size", "staff_count", "duration_minutes",
                    "break_minutes", "start_time", "judges_confirmed", "judge_fee", "minutes_per_team",
                    "max_teams_per_judge"):
            s = self.sc.setdefault(attr, {"own": {}})
            if c.get("d") and isinstance(v, (int, float)) and isinstance(s.get("v"), (int, float)):
                v = s["v"] + v
            s.update(meta)
            self._own("sc", s, ("sc", attr), "v", v, eid, idx)

    # -- change detection
    def snapshot(self) -> dict:
        out = {}
        for a, s in self.sc.items():
            out[("sc", a)] = s.get("v")
        for k, s in self.venues.items():
            out[("venue", k)] = (s.get("price"), s.get("capacity"), tuple(s.get("features", [])), s.get("unit"))
        for name, t in (("price", self.prices), ("committed", self.committed),
                        ("requested", self.requested), ("order", self.orders)):
            for k, s in t.items():
                out[(name, k)] = s.get("v")
        for (m, sj), s in self.cons.items():
            out[("cons", m, sj)] = (s.get("val"), s.get("op"), s.get("dur"))
        return out

    def claim_state(self, eid: int, idx: int):
        if (eid, idx) in self.sup_by and not self.alive.get((eid, idx)):
            return "superseded", self.sup_by[(eid, idx)]
        return "used", None


def diff_snapshots(a: dict, b: dict) -> list:
    out = []
    for k in sorted(set(a) | set(b), key=str):
        if a.get(k) != b.get(k):
            out.append((k, a.get(k), b.get(k)))
    return out


# ------------------------------------------------------------------ dates / ordering
def assign_eff(entries: list) -> None:
    """Give each entry a comparable effective date. Undated messages inherit the latest date so far."""
    parsed = [parse_date(e.get("date") or "") for e in entries]
    years = Counter(p[0] for p in parsed if p and p[0])
    fill = years.most_common(1)[0][0] if years else 0
    best = (0, 0, 0, 0)
    for e, p in sorted(zip(entries, parsed), key=lambda t: t[0]["seq"]):
        if p:
            key = (p[0] or fill, p[1], p[2], p[3])
            e["_dated"] = True
        else:
            key = best
            e["_dated"] = False
        e["_eff"] = key
        best = max(best, key)


def chrono(entries: list) -> list:
    return sorted(entries, key=lambda e: (e["_eff"], e["seq"]))


def build_facts(entries: list) -> Facts:
    f = Facts()
    for e in chrono(entries):
        if e.get("status") == "rejected" or e.get("rel") == "n":
            continue
        for i, c in enumerate(e["claims"]):
            f.apply(e, i, c)
    for k, s in f.requested.items():
        ck, cs = Facts.find(f.committed, s.get("name", k))
        s["approved"] = cs is not None
        if cs is not None and (s["own"]["v"]) in f.alive:
            pass
    return f


# ------------------------------------------------------------------ derived counts
def core_counts(f: Facts) -> dict | None:
    """students/teams/judges needed/headcount, or None if the facts are incomplete."""
    st, ts, mt = f.v("student_count"), f.v("team_size"), f.v("max_teams_per_judge")
    if not all(isinstance(x, (int, float)) and x > 0 for x in (st, ts, mt)):
        return None
    if st % ts:
        return {"error": f"{st} students cannot be split into teams of {ts} ({st} / {ts} is not whole)",
                "students": st, "team_size": ts}
    teams = int(st // ts)
    needed = math.ceil(teams / mt)
    staff = f.v("staff_count", 0) or 0
    return {"students": int(st), "team_size": int(ts), "teams": teams, "judges_needed": needed,
            "staff": int(staff), "headcount": int(st + staff + needed)}


# ------------------------------------------------------------------ gates
def _allowed(tier: str, c: dict) -> bool:
    attr = c["attr"]
    if tier in ("authority", "unknown"):
        return True
    if tier == "individual":
        return False
    if attr == "other_constraint":
        m = REGISTRY.get(c["metric"])
        if tier == "vendor":
            return bool(m and m[1])
        if tier == "domain":
            return bool(m and m[0] == "judging")
        return False
    if tier == "domain":
        return attr in DOMAIN_ATTRS
    if tier == "vendor":
        return attr in VENDOR_ATTRS
    return False


def _need(c: dict) -> str:
    if c["attr"] in DOMAIN_ATTRS:
        return "the judging coordinator or the organizers"
    if c["attr"] in VENDOR_ATTRS:
        return "a vendor or the organizers"
    return "the organizers, the dean's office or finance"


def _expected_unit_problem(c: dict) -> str | None:
    u = unit_from_ev(c["ev"])
    if c["attr"] == "unit_price" and u == "total":
        return "price quoted as a total, but a per-head price is required"
    if c["attr"] in ("total_budget", "committed_spend") and u in ("per_head", "per_hour"):
        return f"amount quoted {u.replace('_', ' ')}, but a total amount is required"
    return None


def check_order(e: dict, facts: Facts) -> str | None:
    need = (core_counts(facts) or {}).get("headcount")
    if not need:
        return None
    for c in e["claims"]:
        if c["attr"] == "ordered_quantity" and isinstance(c["val"], (int, float)) and c["val"] < need:
            who = e["sender"] or "sender"
            item = c["subj"] or "meal"
            return (f"{who} ({ROLE[e['tier']]}) set the {item} order to {_int(c['val'])}, below the {need} people "
                    f"to feed (short by {need - int(c['val'])}); an order below headcount is not allowed")
    return None


def gate(e: dict, facts: Facts, fresh: bool) -> str | None:
    """Return a rejection reason (with numbers), or None if the message may be applied."""
    tier = e["tier"]
    who = e["sender"] or "unknown sender"
    for c in e["claims"]:
        attr, kind = c["attr"], c["kind"]
        if attr in ("requested_spend", "availability_note") or kind == "request":
            continue
        lab = describe(c)
        if tier == "unverified":
            return (f"sender '{who}' is unverified (unknown number, free-mail address or unconfirmed identity) "
                    f"and tries to set {lab}; a verified sender is required")
        if not _allowed(tier, c):
            return f"{ROLE[tier]} '{who}' cannot set {lab}; that needs {_need(c)}"
        v = c["val"]
        num = isinstance(v, (int, float)) and not isinstance(v, bool)
        if num and v <= 0 and attr in POSITIVE and not c.get("d"):
            return f"{lab} is zero or negative, which is not a valid value"
        prob = _expected_unit_problem(c)
        if prob:
            return f"unit mismatch in {lab}: {prob} (evidence: '{c['ev'][:60]}')"
        cur = _current(facts, c)
        if c.get("d") and num:
            if cur is None:
                return f"relative change {lab} has no earlier value to apply to"
            v = cur["v"] + v
            if v <= 0 and attr in POSITIVE:
                return f"relative change {lab} would make {attr} {_int(v)}, which is not valid"
        elif cur is not None and cur.get("v", None) != v and "v" in cur:
            ex_tier, ex_src = cur["tier"], cur["own"]["v"][0]
            ex_who = cur.get("sender") or "earlier sender"
            if num and isinstance(cur["v"], (int, float)) and cur["v"] > 0:
                ratio = max(v, cur["v"]) / min(v, cur["v"])
                if ratio >= 5:
                    return (f"implausible change in {attr}: {_int(v):,} vs {_int(cur['v']):,} in msg {ex_src} "
                            f"({ratio:.0f}x off); likely a typo or unit error")
            if not fresh and e["_dated"] and cur.get("dated") and e["_eff"] < cur["eff"]:
                return (f"stale: dated '{e.get('date')}', but msg {ex_src} from {ex_who} is newer and already "
                        f"sets {attr}; an out-of-date message cannot override it")
            if RANK[tier] < RANK[ex_tier]:
                return (f"{who} ({ROLE[tier]}) cannot override {attr}={_int(cur['v'])} set by {ex_who} "
                        f"({ROLE[ex_tier]}) in msg {ex_src}")
            if e["_dated"] and cur.get("dated") and e["_eff"] == cur["eff"] and RANK[tier] == RANK[ex_tier] \
                    and not (kind == "correction" or CUES.search(e["text"])):
                return (f"conflict on the same date: {attr}={_int(v)} vs {_int(cur['v'])} in msg {ex_src}, same "
                        f"authority and no correction wording; kept msg {ex_src}, this one is quarantined")
        elif cur is not None and attr == "venue_option" and num and cur.get("price"):
            if cur["price"] != v and max(v, cur["price"]) / min(v, cur["price"]) >= 5:
                return f"implausible hall price {v:,.0f} vs {cur['price']:,.0f} in an earlier message"
    msg = check_order(e, facts)
    if msg:
        return msg
    return None


def _current(facts: Facts, c: dict):
    attr = c["attr"]
    if attr == "other_constraint":
        return None
    if attr in facts.sc or attr in ("total_budget", "student_count", "team_size", "staff_count",
                                    "duration_minutes", "break_minutes", "judges_confirmed",
                                    "judge_fee", "minutes_per_team", "max_teams_per_judge", "start_time"):
        s = facts.sc.get(attr)
        return s if s and "v" in s else None
    table = {"unit_price": facts.prices, "committed_spend": facts.committed,
             "ordered_quantity": facts.orders, "venue_option": facts.venues}.get(attr)
    if table is None:
        return None
    _, s = Facts.find(table, c["subj"])
    if s is None:
        return None
    if attr == "venue_option":
        return {"v": c["val"], "price": s.get("price"), "tier": s["tier"], "own": {"v": (0, 0)}} \
            if s.get("price") is not None else None
    return s if "v" in s else None


# ------------------------------------------------------------------ batch processing
def resolve_batch(accepted: list, new: list, fresh: bool, trace) -> tuple[list, list]:
    """Gate new messages one by one against the facts so far. Returns (all accepted, rejected)."""
    cur = list(accepted)
    rejected = []
    order = sorted(new, key=lambda e: (e["_eff"], e["seq"])) if fresh else list(new)
    for e in order:
        if e.get("rel") == "n" or not e["claims"]:
            cur.append(e)
            continue
        why = gate(e, build_facts(cur), fresh)
        if why:
            e["status"], e["reason"] = "rejected", why
            rejected.append(e)
            trace.rejected(e["id"], why)
        else:
            cur.append(e)
    return cur, rejected


def post_checks(entries: list, trace) -> tuple[list, list]:
    """Checks that need the final headcount (orders below headcount)."""
    rejected = []
    keep = list(entries)
    for e in sorted(entries, key=lambda x: x["seq"]):
        if e.get("status") == "rejected" or not e["claims"]:
            continue
        others = [x for x in keep if x is not e]
        msg = check_order(e, build_facts(others + [e]))
        if msg:
            e["status"], e["reason"] = "rejected", msg
            keep.remove(e)
            rejected.append(e)
            trace.rejected(e["id"], msg)
    return keep, rejected


def finalize(entries: list, facts: Facts) -> None:
    """Set status/reason on every non-rejected entry from the resolved facts."""
    for e in entries:
        if e.get("status") == "rejected":
            continue
        if e.get("rel") == "n" or not e["claims"]:
            e["status"], e["reason"] = "irrelevant", e.get("why") or "no plan-relevant content"
            for c in e["claims"]:
                c["st"] = "irrelevant"
            continue
        states = []
        for i, c in enumerate(e["claims"]):
            if c["attr"] == "availability_note":
                c["st"], c["by"] = "note", None
            elif c["kind"] == "request" or c["attr"] == "requested_spend":
                _, s = Facts.find(facts.requested, c["subj"])
                _, cm = Facts.find(facts.committed, c["subj"])
                if cm is not None:
                    c["st"], c["by"] = "superseded", cm["own"]["v"][0]
                else:
                    c["st"], c["by"] = "pending", None
            else:
                c["st"], c["by"] = facts.claim_state(e["id"], i)
            states.append(c["st"])
        if all(s == "note" for s in states):
            e["status"], e["reason"] = "irrelevant", "availability note; does not change the plan"
        elif all(s == "superseded" for s in states):
            c0 = e["claims"][0]
            e["status"] = "superseded"
            e["reason"] = f"superseded by msg {c0['by']}: {describe(c0)} was replaced"
        elif all(s in ("pending", "note") for s in states):
            e["status"] = "pending"
            c0 = next(c for c in e["claims"] if c["st"] == "pending")
            e["reason"] = (f"request for {money(c0['val'])} ({c0['subj'] or 'item'}) has no approval; "
                           f"kept out of the totals")
        else:
            part = [f"{describe(c)} -> msg {c['by']}" for c in e["claims"] if c["st"] == "superseded"]
            e["status"] = "used"
            e["reason"] = ("used" + (f" (partly superseded: {'; '.join(part)})" if part else "")
                           + ": " + "; ".join(describe(c) for c in e["claims"][:4])
                           + ("; ..." if len(e["claims"]) > 4 else ""))
