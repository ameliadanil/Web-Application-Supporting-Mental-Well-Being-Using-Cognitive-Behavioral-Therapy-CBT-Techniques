# tests/test_fsm.py
import os, sys
# pozwala importować main.py z katalogu projektu
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

def test_healthz():
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}

def start_session(name="Ania"):
    r = client.post("/session", json={"user_name": name})
    assert r.status_code == 200
    data = r.json()
    assert data["state"] == "START"
    return data["session_id"]

def send(sid, text):
    r = client.post("/message", json={"session_id": sid, "text": text})
    assert r.status_code == 200
    return r.json()

def test_happy_path_cbt():
    sid = start_session()

    # START -> IDENTIFY_THOUGHT
    resp = send(sid, "Stresuje mnie egzamin")
    assert resp["state"] in ("IDENTIFY_THOUGHT", "EVIDENCE_FOR")

    # IDENTIFY_THOUGHT -> EVIDENCE_FOR
    resp = send(sid, "Mało się uczyłam i boję się że nie zdam")
    assert resp["state"] in ("EVIDENCE_FOR", "EVIDENCE_AGAINST")

    # EVIDENCE_FOR -> EVIDENCE_AGAINST
    resp = send(sid, "Zwykle jednak zdawałam egzaminy")
    assert resp["state"] in ("EVIDENCE_AGAINST", "REFRAME")

    # EVIDENCE_AGAINST -> REFRAME
    resp = send(sid, "Jeśli zrobię plan, dam radę")
    assert resp["state"] in ("REFRAME", "SUMMARY")

    # REFRAME -> SUMMARY (samopoczucie)
    resp = send(sid, "Czuję się spokojniej")
    assert resp["state"] == "SUMMARY"
    assert "Podsumowanie" in resp["reply"]

    # SUMMARY -> END
    resp = send(sid, "koniec")
    assert resp["state"] == "END"

def test_safety_crisis_detection():
    sid = start_session()
    resp = send(sid, "zabije sie")
    assert resp["safety_escalted"] is True
    assert resp["emotion"] == "crisis"
    assert "Nie jestem terapią ani pomocą kryzysową" in resp["reply"]
