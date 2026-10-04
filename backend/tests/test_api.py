"""API 层：回放原裁决、冲突拒绝保留证据、重开历史裁决。"""
from __future__ import annotations

import importlib
import os

import pytest


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_STORE_PATH", str(tmp_path / "audits.json"))
    from app import main
    importlib.reload(main)
    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        yield c


def _payload(audit_id="A-1", packets=None):
    if packets is None:
        packets = [
            {"type": "CONNECT", "direction": "C2S", "clean_start": False},
            {"type": "CONNACK", "direction": "S2C", "session_present": False},
            {"type": "PUBLISH", "direction": "C2S", "packet_id": 1,
             "qos": 2, "dup": False, "topic": "tm/t", "payload": "v1"},
            {"type": "PUBREC", "direction": "S2C", "packet_id": 1},
            {"type": "PUBREL", "direction": "C2S", "packet_id": 1},
            {"type": "PUBCOMP", "direction": "S2C", "packet_id": 1},
        ]
    return {"audit_id": audit_id, "client_id": "sat-01", "packets": packets}


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_submit_then_replay_identical(client):
    body = _payload()
    r1 = client.post("/api/audits", json=body)
    assert r1.status_code == 201
    assert r1.json()["verdict"]["verdict"] == "ACCEPTED"

    r2 = client.post("/api/audits", json=body)
    assert r2.status_code == 200
    assert r2.json()["replayed"] is True
    assert r2.json()["verdict"] == r1.json()["verdict"]


def test_conflicting_input_same_audit_id_rejected_and_preserved(client):
    body = _payload()
    r1 = client.post("/api/audits", json=body)
    assert r1.status_code == 201
    original = r1.json()

    changed = _payload()
    changed["packets"][2]["payload"] = "v2-tampered"
    r2 = client.post("/api/audits", json=changed)
    assert r2.status_code == 409
    assert r2.json()["error"] == "AUDIT_ID_INPUT_CONFLICT"
    assert r2.json()["existing_verdict"] == "ACCEPTED"

    # 原证据保留且可重开
    r3 = client.get("/api/audits/A-1")
    assert r3.status_code == 200
    assert r3.json()["verdict"] == original["verdict"]
    assert r3.json()["submission"]["packets"][2]["payload"] == "v1"


def test_reopen_returns_full_evidence(client):
    client.post("/api/audits", json=_payload("A-2"))
    r = client.get("/api/audits/A-2")
    assert r.status_code == 200
    packets = r.json()["verdict"]["packets"]
    assert len(packets) == 6
    assert packets[2]["first_delivery"] is True
    assert packets[2]["session_phase"] == "CONNECTED"


def test_too_many_packets_rejected(client):
    body = _payload("A-3", packets=[{"type": "CONNECT", "direction": "C2S"}] * 49)
    r = client.post("/api/audits", json=body)
    assert r.status_code == 422


def test_duplicate_resend_flow_rejected_via_api(client):
    packets = [
        {"type": "CONNECT", "direction": "C2S", "clean_start": False},
        {"type": "CONNACK", "direction": "S2C", "session_present": False},
        {"type": "PUBLISH", "direction": "C2S", "packet_id": 1,
         "qos": 2, "dup": False, "topic": "tm/t", "payload": "v1"},
        {"type": "PUBREC", "direction": "S2C", "packet_id": 1},
        {"type": "PUBREL", "direction": "C2S", "packet_id": 1},
        {"type": "PUBCOMP", "direction": "S2C", "packet_id": 1},
        {"type": "PUBLISH", "direction": "C2S", "packet_id": 1,
         "qos": 2, "dup": True, "topic": "tm/t", "payload": "v1"},
    ]
    r = client.post("/api/audits", json=_payload("A-4", packets))
    assert r.status_code == 201
    data = r.json()["verdict"]
    assert data["verdict"] == "REJECTED"
    assert data["duplicate_delivery"] is True
    assert data["first_violation"]["code"] == "DUP_PUBLISH_AFTER_QOS2_COMPLETE"
