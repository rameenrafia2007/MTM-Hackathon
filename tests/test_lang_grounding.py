from engine.lang import clocks_in, derivable, numbers_in, parse_clock
from engine.reader import ground_claim, normalize_claim, parse_output
from engine.splitter import Msg


def test_number_forms():
    assert derivable(650000, "Rs 650,000")
    assert derivable(500000, "Budget 5 lakh")
    assert derivable(110000, "1 lakh 10 hazaar")
    assert derivable(80000, "80k")
    assert derivable(240, "a 4-hour hackathon")
    assert derivable(3, "no more than three teams")
    assert derivable(150000, "150k")
    assert not derivable(700000, "Rs 650,000")


def test_clocks():
    assert parse_clock("13:00") == 13 * 60
    assert 13 * 60 in clocks_in("fixed at 13:00 for 30 minutes")


def _msg(text):
    return Msg(id=1, text=text, hash="h", sender="", date="")


def test_ungrounded_evidence_dropped():
    c = normalize_claim({"m": 1, "attr": "student_count", "val": 150, "ev": "150 students", "kind": "fact"})
    assert ground_claim(c, _msg("We expect 150 students.")) is None
    assert ground_claim(c, _msg("We expect many students.")) is not None


def test_number_not_in_evidence_dropped():
    c = normalize_claim({"m": 1, "attr": "student_count", "val": 999, "ev": "150 students", "kind": "fact"})
    assert ground_claim(c, _msg("We expect 150 students.")) is not None


def test_parse_output_rejects_unknown_ids():
    import pytest
    with pytest.raises(Exception):
        parse_output('{"msgs":[],"claims":[{"m":9,"attr":"student_count","val":1,"ev":"x"}]}', [1, 2])
