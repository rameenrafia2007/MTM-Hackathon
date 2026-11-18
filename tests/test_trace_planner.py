from engine import planner, resolver, validator
from engine.trace import Trace


def test_trace_format():
    t = Trace("x", max_calls=2)
    t.code("hello")
    t.call("food", "m1", "in", "out", {"input": 3, "output": 4, "total": 7}, True)
    t.call("venue", "m1", "in", "out", {"input": None, "output": None, "total": None}, True)
    s = t.render_plain()
    assert "[CODE] hello   (0 tokens)" in s
    assert "CALL 1" in s and "CALL 2" in s and "area: food" in s and "small-model: yes" in s
    assert "tokens: input not reported" in s
    assert s.strip().endswith("TOTAL TOKENS: 7  |  MODEL CALLS: 2 (limit 2)") or "TOTAL TOKENS: 7" in s.splitlines()[-1]
    assert not t.can_call()


def _capture(monkeypatch):
    from engine import orchestrator
    seen = {}
    orig = orchestrator.build_facts

    def spy(entries):
        f = orig(entries)
        seen["facts"] = f
        return f
    monkeypatch.setattr(orchestrator, "build_facts", spy)
    return seen


def test_incremental_equals_full_recompute(run, monkeypatch):
    """After an update, the stored plan equals a from-scratch plan of the same facts (no stale areas)."""
    run("sample_pack.txt")
    seen = _capture(monkeypatch)
    r = run("practice_change.txt")
    full, _ = planner.plan_areas(seen["facts"])
    for k in ("judging", "venue", "food", "schedule", "budget", "headcount"):
        assert full[k] == r["state"]["plan"][k], k


def test_validator_flags_a_tampered_plan(run, monkeypatch):
    seen = _capture(monkeypatch)
    r = run("sample_pack.txt")
    bad = dict(r["state"]["plan"])
    bad["budget"] = dict(bad["budget"], total=bad["budget"]["total"] - 5000)
    assert not validator.validate(seen["facts"], bad)["valid"]
    assert validator.validate(seen["facts"], r["state"]["plan"])["valid"]
