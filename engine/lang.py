"""Language helpers shared by reader, resolver and validator.

Pure code: number words (English + Roman Urdu: lakh, hazaar, paanch...), clock
times, dates, units and subject matching. No model, no pack facts.
"""
from __future__ import annotations

import re

_CURLY = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"',
                        "–": "-", "—": "-", " ": " "})


def norm_text(s) -> str:
    return " ".join(str(s).translate(_CURLY).lower().split())


# ------------------------------------------------------------- numbers
_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
    # Roman Urdu
    "ek": 1, "aik": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5,
    "chhe": 6, "chay": 6, "che": 6, "chhah": 6, "saat": 7, "aath": 8, "nau": 9, "das": 10,
    "gyarah": 11, "barah": 12, "terah": 13, "chaudah": 14, "pandrah": 15, "solah": 16,
    "satrah": 17, "atharah": 18, "unnees": 19, "bees": 20, "pachees": 25, "tees": 30,
    "chalis": 40, "paintalis": 45, "pachas": 50, "saath": 60, "sattar": 70, "assi": 80,
    "nabbe": 90,
}
_HUND = {"hundred": 100, "sau": 100}
_MULT = {"thousand": 1e3, "k": 1e3, "hazaar": 1e3, "hazar": 1e3, "lakh": 1e5, "lakhs": 1e5,
         "lac": 1e5, "lacs": 1e5, "million": 1e6, "crore": 1e7}
_FRAC = {"dedh": 1.5, "dhai": 2.5, "dhaai": 2.5, "sawa": 1.25, "adha": 0.5, "half": 0.5}
_TOK = re.compile(r"\d[\d,]*(?:\.\d+)?|[a-z]+|[^\sa-z0-9]")
_TENS_UNIT = re.compile(r"\b(twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)-(one|two|three|four|five|six|seven|eight|nine)\b")


def _classify(tok: str):
    if tok[0].isdigit():
        try:
            return "num", float(tok.rstrip(",").replace(",", ""))
        except ValueError:
            return None, 0
    if tok in _UNITS:
        return "unit", float(_UNITS[tok])
    if tok in _HUND:
        return "hund", 100.0
    if tok in _MULT:
        return "mult", float(_MULT[tok])
    if tok in _FRAC:
        return "frac", _FRAC[tok]
    if tok in ("and", "aur"):
        return "and", 0.0
    return None, 0.0


def _eval(phrase) -> float:
    total, cur = 0.0, 0.0
    for kind, v in phrase:
        if kind in ("num", "unit", "frac"):
            cur += v
        elif kind == "hund":
            cur = (cur or 1) * 100
        elif kind == "mult":
            total += (cur or 1) * v
            cur = 0.0
    return total + cur


def numbers_in(text: str) -> set:
    """All numeric values readable from `text` (digits, number words, lakh/k/hazaar)."""
    out: set = set()
    phrase: list = []
    last = None

    def flush():
        nonlocal phrase, last
        if phrase and any(k != "and" for k, _ in phrase):
            while phrase and phrase[-1][0] == "and":
                phrase.pop()
            out.add(round(_eval(phrase), 6))
        phrase, last = [], None

    for tok in _TOK.findall(_TENS_UNIT.sub(r"\1 \2", norm_text(text))):
        kind, v = _classify(tok)
        if kind is None or (kind == "and" and not phrase):
            flush()
            continue
        if kind == "num":
            out.add(v)
        if kind in ("num", "unit", "frac") and last in ("num", "unit", "frac"):
            prev = phrase[-1][1]
            if not (kind == "unit" and last == "unit" and prev >= 20 and prev % 10 == 0 and v < 10):
                flush()
        phrase.append((kind, v))
        last = kind
    flush()
    return out


def derivable(val, text: str) -> bool:
    """True when numeric `val` can be read (or simply converted) from `text`."""
    try:
        x = abs(float(val))
    except (TypeError, ValueError):
        return False
    nums = numbers_in(text)
    cands = set(nums)
    for a in nums:
        cands.add(a * 60)               # hours -> minutes
        for b in nums:
            cands.add(a * 60 + b)       # "2 hours 30 min"
    return any(abs(c - x) <= 1e-6 * max(1.0, x) for c in cands)


# ------------------------------------------------------------- clock times
_CLK = re.compile(r"(?<![\d:])(\d{1,2})\s*[:.]\s*(\d{2})\s*(a\.?m\.?|p\.?m\.?)?(?!\d)"
                  r"|(?<![\d:.])(\d{1,2})\s*(a\.?m\.?|p\.?m\.?|baje|o'?clock)(?![a-z])")


def clocks_in(text: str) -> set:
    """Minutes-since-midnight for every clock time in text (both AM/PM when unmarked)."""
    out: set = set()
    for m in _CLK.finditer(norm_text(text)):
        if m.group(1):
            h, mi, ap = int(m.group(1)), int(m.group(2)), m.group(3)
        else:
            h, mi, ap = int(m.group(4)), 0, m.group(5)
        if h > 24 or mi > 59:
            continue
        ap = (ap or "").replace(".", "")
        if ap == "pm":
            out.add(((h if h == 12 else h + 12) % 24) * 60 + mi)
        elif ap == "am":
            out.add(((0 if h == 12 else h) % 24) * 60 + mi)
        else:
            out.add((h % 24) * 60 + mi)
            if h < 12:
                out.add(((h + 12) % 24) * 60 + mi)
    return out


def parse_clock(s) -> int | None:
    if s is None:
        return None
    s = norm_text(s)
    m = re.fullmatch(r"(\d{1,2})\s*[:.]\s*(\d{2})\s*(a\.?m\.?|p\.?m\.?)?", s)
    if not m:
        m2 = re.fullmatch(r"(\d{1,2})\s*(a\.?m\.?|p\.?m\.?)", s)
        if not m2:
            return None
        h, mi, ap = int(m2.group(1)), 0, m2.group(2)
    else:
        h, mi, ap = int(m.group(1)), int(m.group(2)), m.group(3)
    if h > 24 or mi > 59:
        return None
    ap = (ap or "").replace(".", "")
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    return (h % 24) * 60 + mi


def parse_window(s) -> tuple | None:
    m = re.fullmatch(r"\s*(.+?)\s*(?:-|to|se|until)\s*(.+?)\s*", norm_text(s))
    if not m:
        return None
    a, b = parse_clock(m.group(1)), parse_clock(m.group(2))
    return (a, b) if a is not None and b is not None else None


def fmt_clock(minutes: int) -> str:
    return f"{(minutes // 60) % 24:02d}:{minutes % 60:02d}"


# ------------------------------------------------------------- dates
_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8,
           "sep": 9, "oct": 10, "nov": 11, "dec": 12}


def parse_date(s):
    """-> (year|None, month, day, minute_of_day) or None."""
    if not s:
        return None
    t = norm_text(s)
    y = mo = d = None
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if mo is None:
        for m in re.finditer(r"(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?([a-z]{3,9})\.?,?\s*(\d{4})?", t):
            if m.group(2)[:3] in _MONTHS:
                d, mo, y = int(m.group(1)), _MONTHS[m.group(2)[:3]], int(m.group(3)) if m.group(3) else None
                break
    if mo is None:
        for m in re.finditer(r"([a-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(\d{4}))?", t):
            if m.group(1)[:3] in _MONTHS:
                mo, d, y = _MONTHS[m.group(1)[:3]], int(m.group(2)), int(m.group(3)) if m.group(3) else None
                break
    if mo is None:
        m = re.search(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})", t)
        if m:
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            y = y + 2000 if y < 100 else y
    if mo is None or not (1 <= mo <= 12 and 1 <= (d or 0) <= 31):
        return None
    cl = clocks_in(re.sub(r"\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}|\d{4}-\d{1,2}-\d{1,2}", " ", t))
    return (y, mo, d, min(cl) if cl else 0)


# ------------------------------------------------------------- units / subjects
def unit_from_ev(ev: str) -> str | None:
    t = norm_text(ev)
    if re.search(r"per\s*(head|person|plate|box|pax|guest|student|meal|banda|bande)\b|/\s*(head|person|plate|box)\b|"
                 r"\beach\b|\bhar\s+(banda|bande|ek)\b|\ba\s+head\b|\bper\s+piece\b", t):
        return "per_head"
    if re.search(r"per\s*(hour|hr)\b|/\s*(hr|hour)\b|\bhourly\b|\ban\s+hour\b|\bghant[ae]\b", t):
        return "per_hour"
    if re.search(r"\b(in\s+total|total|all[\s-]*in|overall|lump\s*sum|altogether|kul)\b|for\s+the\s+(whole|entire)", t):
        return "total"
    return None


_STOP = {"lunch", "box", "boxes", "meal", "meals", "tier", "package", "pack", "the", "bill",
         "cost", "costs", "charges", "charge", "expense", "expenses", "wala", "wali"}


def canon_subj(s) -> str:
    toks = re.findall(r"[a-z0-9]+", norm_text(s or ""))
    kept = [t for t in toks if t not in _STOP]
    return " ".join(kept or toks)


def same_subj(a: str, b: str) -> bool:
    ta, tb = set(canon_subj(a).split()), set(canon_subj(b).split())
    if not ta and not tb:
        return True
    if not ta or not tb:
        return False
    return ta == tb or ta <= tb or tb <= ta


def money(n) -> str:
    try:
        return f"Rs {float(n):,.0f}"
    except (TypeError, ValueError):
        return str(n)
