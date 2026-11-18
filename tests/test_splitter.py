import json

from engine.splitter import split_text


def test_numbered_blocks_sample():
    m = split_text(open("fixtures/sample_pack.txt", encoding="utf-8").read())
    assert len(m) == 8 and [x.id for x in m] == list(range(1, 9))
    assert "Caterer" in m[3].text and "12 Nov" in m[3].text


def test_json_list():
    raw = json.dumps([{"from": "A", "date": "1 Jan", "message": "hello one"}, {"sender": "B", "text": "hello two"}])
    m = split_text(raw)
    assert [x.sender for x in m] == ["A", "B"] and m[1].text == "hello two"


def test_csv_table():
    m = split_text(open("fixtures/synthetic_4.csv", encoding="utf-8").read())
    assert len(m) == 5 and m[1].sender == "Pearl Banquet Halls"


def test_numbered_lines():
    m = split_text(open("fixtures/synthetic_2.txt", encoding="utf-8").read())
    assert len(m) == 6 and "ghantay" in m[0].text


def test_separator_blocks():
    m = split_text("first message here\n---\nsecond message here\n---\nthird one")
    assert len(m) == 3


def test_whatsapp_lines():
    raw = "12/11/2026, 10:01 - Ali: hello there\n12/11/2026, 10:05 - Sara: budget is fine"
    m = split_text(raw)
    assert len(m) == 2 and "Ali" in m[0].text and "Sara" in m[1].text


def test_header_blocks():
    m = split_text("From: Finance\nDate: 1 Nov\nPay it.\n\nFrom: Dean\nDate: 2 Nov\nOK.")
    assert len(m) == 2 and m[1].text.startswith("From: Dean") and "OK." in m[1].text


def test_blob_fallback():
    m = split_text("one long unstructured paragraph that has no markers at all and just keeps going on")
    assert len(m) == 1 and m[0].text.startswith("one long")


def test_hash_ignores_whitespace():
    a = split_text("x   y  z")[0].hash
    b = split_text("x y z")[0].hash
    assert a == b


def test_practice_single_line():
    m = split_text(open("fixtures/practice_change.txt").read())
    assert len(m) == 1
