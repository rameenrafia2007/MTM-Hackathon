import json
import time

import pytest

from engine import llm as L
from engine.trace import Trace
from tests.stubs import StubBackend, local_cfg, make_llm


def cfg(**kw):
    return local_cfg(**kw)


def remote(**kw):
    base = dict(provider="gemini", gemini_key="k")
    base.update(kw)
    return L.Config(**base)


def ok(system, user, model):
    return "{}"


def run_call(llm, **kw):
    t = Trace("t", max_calls=kw.pop("max_calls", 5))
    res = llm.call("reading", "sys", "usr", trace=t, deadline=L.Deadline(60), **kw)
    return res, t


def test_exclude_beats_include():
    c = remote(include=["flash"], exclude=["pro"])
    assert L.small_rule("model-flash-pro", c)[0] is False
    assert L.small_rule("model-flash", c)[0] is True


def test_exact_and_unlisted_remote_not_small():
    c = remote(exact=["tiny-1"], include=["lite"])
    assert L.small_rule("tiny-1", c)[0] and L.small_rule("x-lite", c)[0]
    assert not L.small_rule("something-else", c)[0]


def test_localhost_auto_small_but_exclude_still_wins():
    c = cfg(exclude=["huge"])
    assert L.small_rule("anything", c)[0]
    assert not L.small_rule("huge-model", c)[0]


def test_embedding_models_dropped():
    assert not L.small_rule("nomic-embed-text", cfg())[0]


def test_chain_order_primary_fallback_discovery():
    b = StubBackend(ok, models=["zeta", "alpha", "mid", "beta"])
    ch = L.build_chain(cfg(primary="mid", fallback="beta"), b, use_cache=False)
    assert ch.models == ["mid", "beta", "zeta", "alpha"]


def test_empty_chain_stops_with_clear_error():
    b = StubBackend(ok, models=["big-model"])
    with pytest.raises(L.NoSmallModel) as e:
        L.build_chain(remote(), b, use_cache=False)
    assert "no small model" in str(e.value)


def test_discovery_failure_falls_back_to_env_names():
    b = StubBackend(ok, list_fail="boom")
    ch = L.build_chain(cfg(primary="p1", fallback="f1"), b, use_cache=False)
    assert ch.models == ["p1", "f1"] and ch.source == "env"


def test_chain_cached_ten_minutes(tmp_path):
    b = StubBackend(ok, models=["m1"])
    L.build_chain(cfg(), b)
    cache = json.loads((tmp_path / "state" / "models.json").read_text())
    assert cache["chain"]["models"] == ["m1"]
    b2 = StubBackend(ok, models=["different"])
    assert L.build_chain(cfg(), b2).models == ["m1"]        # served from cache


def test_non_small_model_never_called():
    b = StubBackend(ok, models=["small-x", "pro-big"])
    llm = L.LLM(cfg(exclude=["pro"]), b)
    run_call(llm)
    assert b.calls == ["small-x"]
    with pytest.raises(PermissionError):
        llm._assert_small("pro-big")


def test_failed_attempts_without_usage_are_code_lines_only():
    b = StubBackend(ok, models=["a", "b"], fail_models=["a"])
    res, t = run_call(L.LLM(cfg(), b))
    assert res.model == "b"
    assert len(t.calls) == 1 and t.calls[0]["n"] == 1 and t.calls[0]["model"] == "b"
    assert any("model a failed" in e["text"] for e in t.events if e["type"] == "code")


def test_numbered_calls_consecutive_and_tokens_provider_only():
    b = StubBackend(ok, models=["a"])
    llm = L.LLM(cfg(), b)
    t = Trace("t", max_calls=5)
    for _ in range(3):
        llm.call("venue", "s", "u", trace=t, deadline=L.Deadline(60))
    assert [c["n"] for c in t.calls] == [1, 2, 3]
    assert t.totals()["total_tokens"] == sum(c["tokens"]["total"] for c in t.calls)


def test_missing_usage_reported_as_not_reported():
    b = StubBackend(ok, models=["a"], report_usage=False)
    _, t = run_call(L.LLM(cfg(), b))
    assert t.calls[0]["tokens"]["total"] is None
    assert t.totals()["total_tokens"] == 0 and t.totals()["unreported_calls"] == 1
    assert "not reported" in t.render_plain()


def test_slow_model_abandoned_then_next_used(monkeypatch):
    monkeypatch.setattr(L, "CALL_TIMEOUT", 1.2)
    state = {"n": 0}

    class Slow(StubBackend):
        def generate(self, model, *a, **k):
            if model == "slow":
                time.sleep(3)
            return super().generate(model, *a, **k)
    b = Slow(ok, models=["slow", "fast"])
    t0 = time.time()
    res, t = run_call(L.LLM(cfg(primary="slow"), b))
    assert res.model == "fast" and time.time() - t0 < 2.8
    assert len(t.calls) == 1


def test_call_budget_enforced():
    llm = make_llm(ok)
    t = Trace("t", max_calls=1)
    llm.call("reading", "s", "u", trace=t, deadline=L.Deadline(60))
    with pytest.raises(L.BudgetExceeded):
        llm.call("venue", "s", "u", trace=t, deadline=L.Deadline(60))


def test_unusable_output_counts_as_a_numbered_call_then_falls_back():
    def resp(s, u, m):
        return "garbage" if m == "a" else "{}"
    b = StubBackend(resp, models=["a", "b"])

    def val(txt):
        return json.loads(txt)
    res, t = run_call(L.LLM(cfg(), b), validate=val)
    assert res.model == "b" and [c["model"] for c in t.calls] == ["a", "b"]      # both consumed tokens


def test_area_tag_validated():
    t = Trace("t")
    with pytest.raises(ValueError):
        t.call("nonsense", "m", "i", "o", {}, True)


def test_selftest_runs_against_stub(monkeypatch, capsys):
    b = StubBackend(lambda s, u, m: "ok", models=["tiny"])
    monkeypatch.setattr(L, "make_backend", lambda c: b)
    monkeypatch.setattr(L, "load_config", lambda: cfg())
    rc = L.selftest()
    assert "BONUS-SAFE: yes" in capsys.readouterr().out and rc == 0
