import pytest


def _base(run):
    return run("sample_pack.txt")["plan"]


@pytest.mark.parametrize("fx,needle", [
    ("adv_impersonate.txt", "unverified"),
    ("adv_vendor_headcount.txt", "cannot set student_count=200"),
    ("adv_stale.txt", "stale"),
    ("adv_budget_break.txt", "600,000"),
    ("adv_team_size.txt", "150 students cannot be split into teams of 4"),
])
def test_rejected_with_reason_and_plan_unchanged(run, fx, needle):
    base = _base(run)
    r = run(fx)
    assert len(r["rejected"]) == 1 and needle in r["rejected"][0]["reason"]
    assert r["plan"]["budget"]["total"] == base["budget"]["total"]
    assert r["plan"]["headcount"]["total"] == base["headcount"]["total"]
    assert r["validation"]["valid"]
    assert r["trace"].render_plain().count("[REJECTED] msg") == 1
    assert len(r["trace"].calls) <= 5


def test_budget_breaking_but_plausible_message_rejected_by_validator(run):
    """A change inside the 5x plausibility band that still breaks the budget is stopped by the validator."""
    from engine import orchestrator
    from tests.stubs import C, MS, canned, make_llm, out
    run("sample_pack.txt")

    def upd(amount, was, key):
        txt = f"Finance here. The printing bill came in at Rs {amount:,}, not Rs {was:,}."
        reply = out(MS((1, "Finance", "")), [C(1, "committed_spend", amount, "printing",
                                              f"printing bill came in at Rs {amount:,}", "correction", "budget")])
        return orchestrator.process(text=txt, llm=make_llm(canned({key: reply})))

    r = upd(45000, 10000, "Rs 45,000")           # 4.5x: plausible and fits (582,400 <= 650,000)
    assert not r["rejected"] and r["plan"]["budget"]["total"] == 120000 + 1200 * 177 + 85000 + 120000 + 45000
    r2 = upd(220000, 45000, "Rs 220,000")        # 4.9x: plausible, but 757,400 > 650,000
    assert len(r2["rejected"]) == 1 and "invalid" in r2["rejected"][0]["reason"]
    assert r2["plan"]["budget"]["total"] == r["plan"]["budget"]["total"] and r2["validation"]["valid"]
