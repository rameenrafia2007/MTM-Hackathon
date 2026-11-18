import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MTM_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("MTM_OUTPUT_DIR", str(tmp_path / "output"))
    from engine import llm
    llm._PROCESS_CHAINS.clear()
    yield


@pytest.fixture
def run():
    from engine import orchestrator
    from tests.canned_all import RESPONDER
    from tests.stubs import make_llm

    def _run(name, **kw):
        llm = kw.pop("llm", None) or make_llm(RESPONDER)
        p = ROOT / "fixtures" / name
        return orchestrator.process(str(p), llm=llm, **kw)
    return _run
