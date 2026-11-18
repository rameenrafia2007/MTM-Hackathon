import json

from engine import llm as L
from engine import orchestrator, store
from tests.canned_all import RESPONDER
from tests.stubs import C, MS, OC, canned, make_llm, out
from tests.fake_llm_server import FakeLLMServer


def test_update_rule_three_or_fewer_updates_more_resets(run):
    run("sample_pack.txt")
    r = run("practice_change.txt")
    assert r["mode"] == "update" and len(r["state"]["ledger"]) == 9
    r = run("synthetic_2.txt")                       # 6 messages: fresh run, old state gone
    assert r["mode"] == "full" and len(r["state"]["ledger"]) == 6 and r["state"]["runs"][-1]["id"] == 1


def test_update_processes_only_unseen_messages(run):
    run("sample_pack.txt")
    r = run("practice_change.txt")
    assert r["run"]["new_ids"] == [9]
    again = run("practice_change.txt")
    assert again.get("noop") and len(again["trace"].calls) == 0


def test_unmapped_constraint_costs_one_extra_area_tagged_call_and_is_code_validated(run):
    run("sample_pack.txt")
    txt = "Dean's office: the hall must keep a minimum of 25 percent spare seats."
    first = out(MS((1, "Dean's office", "")), [OC(1, "spare_seat_share", "minimum of 25 percent spare seats",
                                                  "", 25, "min", "venue")])
    tr = json.dumps({"c": [{"i": 1, "metric": "contingency_pct", "subj": "", "op": "eq", "val": 25}]})

    def resp(system, user, model):
        return tr if system.startswith("Map each stated") else canned({"spare seats": first})(system, user, model)
    r = orchestrator.process(text=txt, llm=make_llm(resp))
    calls = r["trace"].calls
    assert [c["area"] for c in calls] == ["reading", "venue"] and [c["n"] for c in calls] == [1, 2]
    assert len(calls) <= 5 and set(r["run"]["model_areas"]) == {"reading", "venue"}


def test_bad_translation_is_dropped_by_code(run):
    from engine.areas import validate_translation
    orig = {1: {"text": "keep 25 percent spare seats", "src": 1, "tier": "authority"}}
    assert validate_translation([{"i": 1, "metric": "contingency_pct", "val": 99}], orig) == []      # 99 not in text
    assert validate_translation([{"i": 1, "metric": "not_in_registry", "val": 25}], orig) == []
    assert len(validate_translation([{"i": 1, "metric": "contingency_pct", "val": 25}], orig)) == 1


def test_reader_failure_does_not_corrupt_state(run):
    run("sample_pack.txt")
    before = store.load_state()
    r = orchestrator.process(text="Finance: printing is now Rs 15,000", llm=make_llm(lambda s, u, m: "not json"))
    assert not r["ok"]
    assert store.load_state()["plan"] == before["plan"]


def test_no_small_model_gives_clear_error(run):
    from tests.stubs import StubBackend
    b = StubBackend(RESPONDER, models=["giant-pro"])
    llm = L.LLM(L.Config(provider="gemini", gemini_key="k"), b)
    r = orchestrator.process("fixtures/sample_pack.txt", llm=llm)
    assert not r["ok"] and "no small model" in r["error"].lower() and b.calls == []


def test_full_run_limits(run):
    r = run("sample_pack.txt")
    assert r["trace"].max_calls == 8 and r["run"]["seconds"] < 120


def test_real_http_path_against_fake_server(monkeypatch):
    srv = FakeLLMServer(RESPONDER).start()
    try:
        cfg = L.Config(provider="openai_compatible", base_url=srv.base_url, api_key="x")
        llm = L.LLM(cfg)
        r = orchestrator.process("fixtures/sample_pack.txt", llm=llm)
        assert r["ok"] and r["validation"]["valid"]
        assert {q["model"] for q in srv.requests} <= {"fake-small-a", "fake-small-b"}   # embed model never called
        assert r["trace"].calls[0]["tokens"]["total"] > 0
    finally:
        srv.stop()


def test_http_failover_to_second_model():
    srv = FakeLLMServer(RESPONDER, fails={"fake-small-a": 500}).start()
    try:
        llm = L.LLM(L.Config(provider="openai_compatible", base_url=srv.base_url, api_key="x", primary="fake-small-a"))
        r = orchestrator.process("fixtures/sample_pack.txt", llm=llm)
        assert r["ok"] and r["trace"].calls[0]["model"] == "fake-small-b" and len(r["trace"].calls) == 1
    finally:
        srv.stop()
