import threading
import time

import pytest
import requests
import uvicorn

from tests.canned_all import RESPONDER
from tests.fake_llm_server import FakeLLMServer


@pytest.fixture
def api(monkeypatch):
    srv = FakeLLMServer(RESPONDER).start()
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("OPENAI_BASE_URL", srv.base_url)
    import server
    cfg = uvicorn.Config(server.app, host="127.0.0.1", port=0, log_level="error")
    u = uvicorn.Server(cfg)
    t = threading.Thread(target=u.run, daemon=True)
    t.start()
    for _ in range(100):
        if u.started:
            break
        time.sleep(0.05)
    port = u.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    u.should_exit = True
    t.join(3)
    srv.stop()


def test_ui_and_branding_served(api):
    assert "MTM Planner" in requests.get(api + "/").text
    assert "sponsors" in requests.get(api + "/branding.json").json()


def test_run_update_slip_flow(api):
    pack = open("fixtures/sample_pack.txt", encoding="utf-8").read()
    r = requests.post(api + "/api/run", json={"text": pack}).json()
    assert r["ok"]
    s = requests.get(api + "/api/state").json()
    assert s["state"]["validation"]["six_pass"] == 6
    lock = requests.post(api + "/api/slip", json={"areas": ["reading", "budget"]})
    assert lock.status_code == 200
    assert requests.post(api + "/api/slip", json={"areas": []}).status_code == 409        # immutable
    upd = open("fixtures/practice_change.txt").read()
    requests.post(api + "/api/run", json={"text": upd})
    slips = requests.get(api + "/api/state").json()["slips"]
    assert slips[0]["status"] == "resolved" and slips[0]["match"] is False      # predicted budget, only reading called
    assert "TOTAL TOKENS" in requests.get(api + "/api/export?fmt=txt").text
    assert requests.get(api + "/api/export?fmt=json").json()["plan"]


def test_empty_text_is_an_error(api):
    r = requests.post(api + "/api/run", json={"text": ""})
    assert r.status_code >= 400 or not r.json().get("ok")
