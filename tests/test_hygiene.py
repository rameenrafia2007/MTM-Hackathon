"""No pack facts, pack numbers, message text or model names inside engine/ or ui/index.html; git-ignore checks."""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WHITELIST = {"100", "1000", "100000", "1000000", "10000000"}
MODEL_RE = re.compile(r"(gemini|gemma|llama|qwen|phi|mistral|gpt|claude|deepseek)[-_ ]?\d", re.I)


def _targets():
    files = sorted((ROOT / "engine").glob("*.py")) + [ROOT / "ui" / "index.html", ROOT / "main.py", ROOT / "server.py"]
    return [(f, f.read_text(encoding="utf-8")) for f in files]


def _numbers(text):
    return {t.replace(",", "") for t in re.findall(r"\d[\d,]*\d|\d", text) if len(t.replace(",", "")) >= 3}


def test_no_pack_numbers_in_code():
    pack = (ROOT / "fixtures" / "sample_pack.txt").read_text(encoding="utf-8")
    bad_nums = _numbers(pack) - WHITELIST
    # "Message 10" style headers are not facts; only digits inside the message bodies count
    bodies = "\n".join(re.findall(r"Message:\s*(.*)", pack))
    bad_nums = _numbers(bodies) - WHITELIST
    found = []
    for f, txt in _targets():
        for n in sorted(bad_nums):
            if re.search(r"(?<![\d.])" + n[:-3] + r",?" + n[-3:] + r"(?![\d])" if len(n) > 3 else r"(?<![\d.])" + n + r"(?![\d])", txt):
                found.append((f.name, n))
    assert not found, found


def test_no_message_phrases_in_code():
    pack = (ROOT / "fixtures" / "sample_pack.txt").read_text(encoding="utf-8")
    words = []
    for body in re.findall(r"Message:\s*(.*)", pack):
        w = re.findall(r"[A-Za-z]+", body.lower())
        words += [" ".join(w[i:i + 4]) for i in range(len(w) - 3)]
    found = [(f.name, p) for f, txt in _targets() for p in words if p in re.sub(r"[^a-z]+", " ", txt.lower())]
    assert not found, found[:5]


def test_no_model_names_in_code():
    found = [(f.name, m.group(0)) for f, txt in _targets() for m in MODEL_RE.finditer(txt)]
    assert not found, found


def test_state_and_output_are_gitignored():
    gi = (ROOT / ".gitignore").read_text().split()
    for entry in ("state/", "output/", ".env", "__pycache__/"):
        assert entry in gi


def test_git_check_ignore(tmp_path):
    import shutil
    dst = tmp_path / "p"
    shutil.copytree(ROOT, dst, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "state", "output", ".git"))
    subprocess.run(["git", "init", "-q"], cwd=dst, check=True)
    (dst / "state").mkdir()
    (dst / "output").mkdir()
    (dst / "state" / "x.json").write_text("{}")
    (dst / "output" / "y.txt").write_text("x")
    (dst / ".env").write_text("A=1")
    for p in ("state/x.json", "output/y.txt", ".env"):
        r = subprocess.run(["git", "check-ignore", p], cwd=dst, capture_output=True)
        assert r.returncode == 0, p
