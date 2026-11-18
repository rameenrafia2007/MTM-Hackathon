"""Splitter: turns an unknown message-file format into a list of messages.

Pure code, no model. Text is kept exactly as given; each message gets a hash
used by the ledger to recognise messages it has already processed.
Detection order: JSON -> CSV/TSV/pipe table -> numbered blocks -> blank-line or
`---` blocks -> From:/Date: headers -> one message per line -> single blob.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass, field

TEXT_KEYS = ("message", "text", "body", "content", "msg", "m")
FROM_KEYS = ("from", "sender", "author", "name", "who")
DATE_KEYS = ("date", "time", "timestamp", "sent", "when")
SEP_LINE = re.compile(r"^\s*(?:[-=*_~]{3,}|#{3,})\s*$")


@dataclass
class Msg:
    id: int
    text: str
    hash: str
    sender: str | None = None
    date: str | None = None
    blob: bool = False
    fmt: str = ""

    def to_dict(self):
        return {"id": self.id, "text": self.text, "hash": self.hash, "sender": self.sender,
                "date": self.date}


def norm(text: str) -> str:
    return " ".join(text.split())


def msg_hash(text: str) -> str:
    return hashlib.sha256(norm(text).encode("utf-8")).hexdigest()[:16]


def _pick(d: dict, keys):
    low = {str(k).strip().lower(): v for k, v in d.items()}
    for k in keys:
        if k in low and low[k] not in (None, ""):
            return str(low[k])
    return None


# ------------------------------------------------------------------ formats
def _try_json(raw: str):
    s = raw.strip()
    if not s or s[0] not in "[{":
        return None
    items = None
    try:
        data = json.loads(s)
        if isinstance(data, dict):
            for k in ("messages", "data", "items", "pack"):
                if isinstance(data.get(k), list):
                    data = data[k]
                    break
            else:
                data = [data]
        items = data if isinstance(data, list) else None
    except ValueError:
        rows = []
        for line in s.splitlines():  # JSON lines
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    return None
        items = rows or None
    if not items:
        return None
    out = []
    for it in items:
        if isinstance(it, str):
            out.append((it, None, None))
        elif isinstance(it, dict):
            t = _pick(it, TEXT_KEYS)
            if t is None:
                continue
            out.append((t, _pick(it, FROM_KEYS), _pick(it, DATE_KEYS)))
    return out or None


def _try_table(raw: str):
    lines = [l for l in raw.splitlines() if l.strip()]
    if len(lines) < 2:
        return None
    for delim in ("\t", "|", ",", ";"):
        if delim not in lines[0]:
            continue
        rows = [r for r in csv.reader(io.StringIO(raw), delimiter=delim) if any(c.strip() for c in r)]
        if delim == "|":
            rows = [[c for c in (r[1:] if not r[0].strip() else r)] for r in rows]
            rows = [r[:-1] if r and not r[-1].strip() else r for r in rows]
            rows = [r for r in rows if not all(re.fullmatch(r"[-:\s]*", c) for c in r)]
        if not rows:
            continue
        head = [c.strip().strip('"').lower() for c in rows[0]]
        ti = next((i for i, h in enumerate(head) if h in TEXT_KEYS), None)
        if ti is None or len(head) < 2:
            continue
        fi = next((i for i, h in enumerate(head) if h in FROM_KEYS), None)
        di = next((i for i, h in enumerate(head) if h in DATE_KEYS), None)
        out = []
        for r in rows[1:]:
            if len(r) <= ti or not r[ti].strip():
                continue
            out.append((r[ti].strip(), r[fi].strip() if fi is not None and fi < len(r) else None,
                        r[di].strip() if di is not None and di < len(r) else None))
        if out:
            return out
    return None


_NUM_STRICT = re.compile(r"^\s*(?:(?:message|msg)\s*#?\s*|#\s*)?(\d{1,3})\s*(?:[.):\-–]|\s)\s*(.*)$", re.I)
_NUM_PREFIXED = re.compile(r"^\s*(?:(?:message|msg)\s*#?\s*(\d{1,3})\s*[.):\-–]?|#\s*(\d{1,3})\s*[.):\-–]?|(\d{1,3})\s*[.):])\s*(.*)$", re.I)


def _numbered(raw: str, pat, bare: bool):
    blocks, cur, expect = [], None, 1
    for line in raw.splitlines():
        m = pat.match(line)
        num = None
        rest = ""
        if m:
            if pat is _NUM_PREFIXED:
                num = next(int(g) for g in m.groups()[:3] if g)
                rest = m.group(4)
            else:
                num, rest = int(m.group(1)), m.group(2)
        if m and num == expect:
            if cur is not None:
                blocks.append(cur)
            cur = [rest]
            expect += 1
        elif cur is not None:
            cur.append(line)
    if cur is not None:
        blocks.append(cur)
    texts = ["\n".join(b).strip() for b in blocks]
    texts = [t for t in texts if t]
    return texts if len(texts) >= 2 else None


def _try_numbered(raw: str):
    t = _numbered(raw, _NUM_PREFIXED, False)
    if t is None:
        t = _numbered(raw, _NUM_STRICT, True)  # bare "1 text" lines, last resort
    return [(x, None, None) for x in t] if t else None


def _try_separated(raw: str):
    parts, cur = [], []
    has_sep = any(SEP_LINE.match(l) for l in raw.splitlines())
    if has_sep:
        for line in raw.splitlines():
            if SEP_LINE.match(line):
                parts.append("\n".join(cur).strip())
                cur = []
            else:
                cur.append(line)
        parts.append("\n".join(cur).strip())
        parts = [p for p in parts if p]
        if len(parts) >= 2:
            return [(p, None, None) for p in parts]
    parts = [p.strip() for p in re.split(r"\n\s*\n", raw) if p.strip()]
    if len(parts) >= 2:
        return [(p, None, None) for p in parts]
    return None


_HDR = re.compile(r"^\s*(?:from|sender)\s*:", re.I)


def _try_headers(raw: str):
    lines = raw.splitlines()
    if sum(1 for l in lines if _HDR.match(l)) < 2:
        return None
    blocks, cur = [], []
    for l in lines:
        if _HDR.match(l) and cur:
            blocks.append(cur)
            cur = []
        cur.append(l)
    blocks.append(cur)
    out = []
    for b in blocks:
        text = "\n".join(b).strip()
        if not text:
            continue
        sender = date = None
        for l in b:
            m = re.match(r"^\s*(from|sender)\s*:\s*(.+)$", l, re.I)
            if m and not sender:
                sender = m.group(2).strip()
            m = re.match(r"^\s*date\s*:\s*(.+)$", l, re.I)
            if m and not date:
                date = m.group(1).strip()
        out.append((text, sender, date))
    return out or None


_WA = re.compile(r"^\s*\[?\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4},?\s+\d{1,2}:\d{2}")


def _try_lines(raw: str):
    lines = [l for l in raw.splitlines() if l.strip()]
    if not lines:
        return None
    if any(_WA.match(l) for l in lines):  # WhatsApp export: continuation lines join the previous message
        out, cur = [], []
        for l in lines:
            if _WA.match(l) and cur:
                out.append("\n".join(cur))
                cur = []
            cur.append(l.strip())
        out.append("\n".join(cur))
        return [(o, None, None) for o in out]
    if len(lines) == 1:
        return [(lines[0].strip(), None, None)]
    # hard-wrapped prose (not one message per line) -> no structure: keep as one blob
    ends = sum(1 for l in lines if re.search(r"[.!?\"”۔]\s*$", l))
    avg = sum(len(l) for l in lines) / len(lines)
    if ends / len(lines) < 0.4 and 40 <= avg <= 100 and len(lines) >= 4:
        return None
    return [(l.strip(), None, None) for l in lines]


FORMATS = (("json", _try_json), ("table", _try_table), ("numbered", _try_numbered),
           ("blocks", _try_separated), ("headers", _try_headers), ("lines", _try_lines))


def split_text(raw: str) -> list[Msg]:
    raw = raw.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    if not raw.strip():
        return []
    for name, fn in FORMATS:
        got = fn(raw)
        if got:
            return [Msg(i + 1, t, msg_hash(t), s, d, False, name) for i, (t, s, d) in enumerate(got)]
    text = raw.strip()
    return [Msg(1, text, msg_hash(text), None, None, True, "blob")]


def split_file(path: str) -> list[Msg]:
    with open(path, "rb") as fh:
        data = fh.read()
    try:
        raw = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raw = data.decode("utf-8", errors="replace")
    return split_text(raw)
