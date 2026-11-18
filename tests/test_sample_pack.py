"""Expectations for the sample pack and the two practice messages (hand-derived, independent of the engine)."""
from engine import store

# independent arithmetic: 150 students + 10 staff + ceil(50 teams / 3) judges
TEAMS = 150 // 3
JUDGES = -(-TEAMS // 3)
HEAD = 150 + 10 + JUDGES


def test_sample_full(run):
    r = run("sample_pack.txt")
    assert r["ok"] and r["mode"] == "full"
    p, v = r["plan"], r["validation"]
    assert JUDGES == 17 and HEAD == 177
    assert p["headcount"]["total"] == HEAD
    assert p["judging"]["judges_used"] == JUDGES and p["judging"]["teams"] == TEAMS
    assert p["venue"]["name"] == "Hall A" and p["venue"]["total"] == 120000
    assert p["food"]["unit_price"] == 1200 and p["food"]["quantity"] == HEAD
    assert p["budget"]["total"] == 120000 + 1200 * HEAD + 5000 * JUDGES + 130000
    assert p["budget"]["headroom"] == 650000 - p["budget"]["total"] >= 0
    assert v["valid"] and v["six_pass"] == 6
    led = {e["id"]: e["status"] for e in r["state"]["ledger"]}
    assert led[3] == "superseded" and led[7] == "pending" and led[8] == "irrelevant"
    pend = p["budget"]["pending"]
    assert len(pend) == 1 and pend[0]["amount"] == 50000 and pend[0]["fits"]


def test_one_reading_call_only(run):
    r = run("sample_pack.txt")
    t = r["trace"]
    assert len(t.calls) == 1 and t.calls[0]["area"] == "reading"
    assert t.totals()["total_tokens"] == t.calls[0]["tokens"]["total"] > 0


def test_practice_change_only_budget_recomputed(run):
    run("sample_pack.txt")
    r = run("practice_change.txt")
    assert r["mode"] == "update" and len(r["trace"].calls) == 1
    assert r["run"]["affected"] == ["budget"]
    assert r["run"]["model_areas"] == ["reading"]
    assert r["plan"]["budget"]["total"] == 120000 + 1200 * HEAD + 5000 * JUDGES + 135000
    assert r["validation"]["valid"]


def test_practice_wrong_rejected(run):
    base = run("sample_pack.txt")["plan"]
    r = run("practice_wrong.txt")
    assert len(r["rejected"]) == 1
    assert "100" in r["rejected"][0]["reason"] and "177" in r["rejected"][0]["reason"]
    assert f"[REJECTED] msg {r['rejected'][0]['id']}:" in r["trace"].render_plain()
    assert r["plan"]["budget"]["total"] == base["budget"]["total"]
    assert r["validation"]["valid"]


def test_rerun_same_message_is_noop(run):
    run("sample_pack.txt")
    run("practice_change.txt")
    assert run("practice_change.txt").get("noop")


def test_outputs_written(run):
    run("sample_pack.txt")
    out = store.output_dir()
    assert (out / "plan.json").exists() and (out / "final_plan_and_trace.txt").exists()
    txt = (out / "final_plan_and_trace.txt").read_text()
    assert "TOTAL TOKENS" in txt and "[CODE]" in txt


def test_state_rule_more_than_three_is_fresh(run):
    run("sample_pack.txt")
    r = run("sample_pack.txt")  # 8 messages again, state exists -> fresh full run, state reset
    assert r["mode"] == "full" and len(r["state"]["ledger"]) == 8 and len(r["state"]["runs"]) == 1
