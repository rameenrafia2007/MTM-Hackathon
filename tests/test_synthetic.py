"""Four synthetic packs with different numbers, wording, senders, languages and file formats.
Expected values are hand-derived below, not read back from the engine."""
import math


def _ceil(a, b):
    return math.ceil(a / b)


def test_pack1_json_power_diet_tax(run):
    r = run("synthetic_1.json")
    p = r["plan"]
    teams = 120 // 4
    judges = _ceil(teams, 5)
    head = 120 + 8 + judges
    assert (teams, judges, head) == (30, 6, 134)
    assert r["validation"]["valid"]
    assert p["headcount"]["total"] == head and p["judging"]["judges_used"] == judges
    assert p["venue"]["name"] == "Auditorium X"          # Seminar 1 too small, generator required
    assert p["food"]["tier"] == "premium" and p["food"]["quantity"] == head
    assert p["food"]["total"] == round(1500 * head * 1.05)
    assert p["budget"]["total"] == 150000 + judges * 8000 + 72000 + round(1500 * head * 1.05)
    assert p["schedule"]["duration"] == 300
    jw = p["schedule"]["judging_window"]
    assert jw["end"] - jw["start"] == 5 * 20 + 5
    assert any(c["metric"] == "power_backup" and c["status"] == "PASS" for c in r["validation"]["constraints"])
    assert any(c["metric"] == "dietary_share" for c in r["validation"]["constraints"])
    assert r["trace"].calls[0]["area"] == "reading"


def test_pack2_roman_urdu_lakh(run):
    r = run("synthetic_2.txt")
    p = r["plan"]
    head = 90 + 6 + _ceil(30, 4)
    assert head == 104 and r["validation"]["valid"]
    assert p["headcount"]["total"] == head and p["judging"]["judges_used"] == 8
    assert p["venue"]["name"] == "Hall Alpha"
    assert p["food"]["total"] == 700 * head
    assert p["budget"]["total"] == 80000 + 700 * head + 8 * 4000 + 58000
    assert p["budget"]["headroom"] == 500000 - p["budget"]["total"]
    assert {e["id"]: e["status"] for e in r["state"]["ledger"]}[6] == "irrelevant"


def test_pack3_fixed_prayer_unavailable_hall_contingency(run):
    r = run("synthetic_3.txt")
    p = r["plan"]
    head = 200 + 12 + _ceil(40, 4)
    assert head == 222 and r["validation"]["valid"]
    assert p["venue"]["name"] == "Hall South"                 # Hall North is unavailable
    z = [b for b in p["schedule"]["blocks"] if "Zuhr" in b["name"]][0]
    assert (z["start"], z["end"]) == (240, 270)               # 13:00 - 09:00 = 240 min
    assert p["food"]["tier"] == "deluxe"
    assert p["budget"]["total"] == 230000 + 10 * 6000 + 150000 + 1800 * head + 120000
    assert p["budget"]["total"] <= 1200000


def test_pack4_csv_delivery_judge_hours(run):
    r = run("synthetic_4.csv")
    p = r["plan"]
    head = 100 + 5 + _ceil(50, 5)
    assert head == 115 and r["validation"]["valid"]
    assert p["venue"]["name"] == "Hall Ruby"
    assert p["food"]["total"] == 1000 * head + 3000
    assert p["budget"]["total"] == 70000 + 118000 + 10 * 5000 + 80000
    jw = p["schedule"]["judging_window"]
    assert jw["end"] <= 180                                    # judges leave 15:00, start 12:00
