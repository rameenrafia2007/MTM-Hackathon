"""Test doubles: an offline stub model backend and a tiny builder for canned reader output.

The canned outputs imitate what a good reading model returns for each fixture. They
live in tests/ only (never in engine/), as required: no pack facts in application code.
"""
from __future__ import annotations

import json
import time

from engine.llm import LLM, Config, Raw


class StubBackend:
    """Stands in for a provider. `responder(system, user, model) -> str`."""

    def __init__(self, responder, models=("stub-small-a", "stub-small-b"), delay=0.0, list_fail=None,
                 report_usage=True, fail_models=()):
        self.responder, self.models, self.delay = responder, list(models), delay
        self.list_fail, self.report_usage, self.fail_models = list_fail, report_usage, set(fail_models)
        self.calls: list = []

    def list_models(self, timeout):
        if self.list_fail:
            raise RuntimeError(self.list_fail)
        return list(self.models)

    def generate(self, model, system, user, max_tokens, timeout, json_mode=True):
        self.calls.append(model)
        if self.delay:
            time.sleep(self.delay)
        if model in self.fail_models:
            from engine.llm import LLMError
            raise LLMError("HTTP 500: stub failure", transient=False)
        text = self.responder(system, user, model)
        usage = ({"input": (len(system) + len(user)) // 4, "output": len(text) // 4,
                  "total": (len(system) + len(user) + len(text)) // 4, "thinking": None}
                 if self.report_usage else {"input": None, "output": None, "total": None, "thinking": None})
        return Raw(text, model, usage)


def local_cfg(**kw) -> Config:
    base = dict(provider="openai_compatible", base_url="http://localhost:11434/v1", api_key="x")
    base.update(kw)
    return Config(**base)


def make_llm(responder, **kw) -> LLM:
    cfg_kw = {k: kw.pop(k) for k in ("primary", "fallback", "include", "exclude", "exact", "provider", "base_url")
              if k in kw}
    return LLM(local_cfg(**cfg_kw), StubBackend(responder, **kw))


# ------------------------------------------------------------------ canned output builder
def C(m, attr, val=None, subj="", ev="", kind="fact", area="", **kw):
    d = {"m": m, "kind": kind, "area": area, "attr": attr, "subj": subj, "val": val, "unit": "", "ev": ev}
    d.update(kw)
    return d


def OC(m, metric, ev, subj="", val=None, op="eq", area="budget", **kw):
    return C(m, "other_constraint", val, subj, ev, "other_constraint", area, metric=metric, op=op, **kw)


def MS(*items):
    """items: (m, sender, date[, rel, why])"""
    out = []
    for it in items:
        d = {"m": it[0], "sender": it[1], "date": it[2], "rel": it[3] if len(it) > 3 else "y"}
        if len(it) > 4:
            d["why"] = it[4]
        out.append(d)
    return out


def out(msgs, claims) -> str:
    return json.dumps({"msgs": msgs, "claims": claims})


def canned(table: dict, default=None):
    """Responder that picks the canned output whose key (a distinctive substring) occurs in the prompt.
    Canned claim ids are RELATIVE (1 = first message in the prompt); they are remapped to the real ids."""
    import re

    def respond(system, user, model):
        ids = [int(x) for x in re.findall(r"^\[(\d+)\]", user, re.M)]
        for key, val in table.items():
            if key in user:
                data = json.loads(val) if isinstance(val, str) else json.loads(json.dumps(val))
                for sec in ("msgs", "claims"):
                    for it in data.get(sec, []):
                        if 1 <= it["m"] <= len(ids):
                            it["m"] = ids[it["m"] - 1]
                return json.dumps(data)
        if default is not None:
            return default
        raise AssertionError("no canned reader output for prompt: " + user[:120])
    return respond
