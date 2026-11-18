"""Trace: the honest record of a run.

Every model call is numbered and shows model, ONE area tag, full input, full
output and the token counts exactly as the provider reported them. Everything
that is not a model call is an unnumbered [CODE] line with 0 tokens.
Without this module the run cannot be audited (and misreporting disqualifies).
"""
from __future__ import annotations

import sys
import time

AREAS = ("reading", "venue", "schedule", "food", "judging", "budget")
NR = "not reported"


class Trace:
    def __init__(self, title: str = "run", max_calls: int | None = None,
                 echo: bool = False, out=None, color: bool = False):
        self.title = title
        self.max_calls = max_calls
        self.echo = echo
        self.color = color
        self.out = out or sys.stdout
        self.events: list[dict] = []
        self.calls: list[dict] = []
        self.t0 = time.time()

    # ---- recording -----------------------------------------------------
    def code(self, text: str) -> None:
        self._add({"type": "code", "text": str(text)})

    def rejected(self, msg_id, reason: str) -> None:
        self._add({"type": "rejected", "msg": msg_id,
                   "text": f"[REJECTED] msg {msg_id}: {reason}"})

    def can_call(self) -> bool:
        return self.max_calls is None or len(self.calls) < self.max_calls

    def call(self, area: str, model: str, inp: str, out: str, usage: dict | None,
             small: bool, requested: str | None = None) -> dict:
        """Record one model call. `usage` holds provider-reported ints or None."""
        if area not in AREAS:
            raise ValueError(f"invalid area tag {area!r}; must be one of {AREAS}")
        usage = usage or {}
        rec = {
            "type": "call",
            "n": len(self.calls) + 1,
            "area": area,
            "model": model or (requested or "unknown"),
            "model_reported": bool(model),
            "requested": requested,
            "small": bool(small),
            "input": inp,
            "output": out,
            "tokens": {k: usage.get(k) for k in ("input", "output", "total")},
            "thinking": usage.get("thinking"),
        }
        self.calls.append(rec)
        self._add(rec)
        return rec

    # ---- queries -------------------------------------------------------
    def totals(self) -> dict:
        reported = [c["tokens"]["total"] for c in self.calls if c["tokens"]["total"] is not None]
        return {
            "total_tokens": sum(reported),
            "calls": len(self.calls),
            "unreported_calls": len(self.calls) - len(reported),
            "limit": self.max_calls,
        }

    def areas_with_calls(self) -> list[str]:
        seen: list[str] = []
        for c in self.calls:
            if c["area"] not in seen:
                seen.append(c["area"])
        return seen

    # ---- rendering -----------------------------------------------------
    def _fmt_tok(self, v):
        return NR if v is None else str(v)

    def render_event(self, ev: dict) -> str:
        if ev["type"] == "code":
            return f"[CODE] {ev['text']}   (0 tokens)"
        if ev["type"] == "rejected":
            t = ev["text"]
            return f"\033[1;31m{t}\033[0m" if self.color else t
        tk = ev["tokens"]
        model = ev["model"] if ev["model_reported"] else f"{ev['model']} (requested; provider did not report a model name)"
        think = f" | thinking {ev['thinking']}" if ev.get("thinking") else ""
        lines = [
            "=" * 78,
            f"CALL {ev['n']}  |  model: {model}  |  area: {ev['area']}  |  small-model: {'yes' if ev['small'] else 'no'}",
            f"tokens: input {self._fmt_tok(tk['input'])} | output {self._fmt_tok(tk['output'])} | total {self._fmt_tok(tk['total'])}{think}",
            "--- INPUT ---",
            ev["input"].rstrip(),
            "--- OUTPUT ---",
            (ev["output"] or "").rstrip(),
            "=" * 78,
        ]
        return "\n".join(lines)

    def footer(self) -> str:
        t = self.totals()
        lim = f" (limit {t['limit']})" if t["limit"] else ""
        extra = f" (+{t['unreported_calls']} call(s) with tokens not reported)" if t["unreported_calls"] else ""
        return f"TOTAL TOKENS: {t['total_tokens']}{extra}  |  MODEL CALLS: {t['calls']}{lim}"

    def render(self) -> str:
        head = f"TRACE: {self.title}"
        body = [self.render_event(e) for e in self.events]
        return "\n".join([head, "-" * len(head)] + body + [self.footer()])

    def to_dict(self) -> dict:
        return {"title": self.title, "events": self.events, "totals": self.totals(),
                "areas": self.areas_with_calls(), "text": self.render_plain()}

    def render_plain(self) -> str:
        c, self.color = self.color, False
        try:
            return self.render()
        finally:
            self.color = c

    # ---- internals -----------------------------------------------------
    def _add(self, ev: dict) -> None:
        self.events.append(ev)
        if self.echo:
            print(self.render_event(ev), file=self.out, flush=True)
