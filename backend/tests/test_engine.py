"""裁决引擎：重连闭合、合法重放、未闭合会话与各类违规判定。"""
from __future__ import annotations

import pytest

from app.engine import (
    R_CONNACK_SP1_NO_SESSION,
    R_FIRST_NOT_CONNECT,
    R_NOT_CONNECTED,
    R_PUBCOMP_NEW_ID,
    R_PUBCOMP_STATE,
    R_PUBLISH_AFTER_PUBREL,
    R_PUBLISH_CONFLICT,
    R_PUBLISH_DUP_AFTER_COMPLETE,
    R_PUBLISH_DUP_FIRST,
    R_PUBLISH_QOS,
    R_PUBLISH_REUSE_WITHOUT_DUP,
    R_PUBREL_STATE,
    R_ACK_DIR,
    adjudicate,
)
from app.schemas import PacketIn as P


def pk(t, d, **kw):
    return P(type=t, direction=d, **kw)


CONNECT = lambda clean=False: pk("CONNECT", "C2S", clean_start=clean)  # noqa: E731
CONNACK = lambda sp=False: pk("CONNACK", "S2C", session_present=sp)  # noqa: E731
DISC_C = pk("DISCONNECT", "C2S")
DISC_S = pk("DISCONNECT", "S2C")


def pub(pid, dup=False, topic="t", payload="x"):
    return pk("PUBLISH", "C2S", packet_id=pid, qos=2, dup=dup,
              topic=topic, payload=payload)


def test_happy_path_delivered_once():
    r = adjudicate([
        CONNECT(), CONNACK(),
        pub(1), pk("PUBREC", "S2C", packet_id=1),
        pk("PUBREL", "C2S", packet_id=1), pk("PUBCOMP", "S2C", packet_id=1),
    ], "sat-01")
    assert r["verdict"] == "ACCEPTED"
    assert r["summary"]["delivered_once"] == 1
    assert r["open_exchanges"] == []
    assert r["duplicate_delivery"] is False
    ev3 = r["packets"][2]
    assert ev3["first_delivery"] is True
    assert ev3["session_phase"] == "CONNECTED"


def test_legal_resend_after_link_break_closes_once():
    """断链发生在 PUBREC 前后：重连后 DUP 重发，最终只交付一次。"""
    r = adjudicate([
        CONNECT(), CONNACK(),
        pub(7),
        DISC_S,  # 遥测中继断链，PUBREC 丢失
        CONNECT(), CONNACK(sp=True),
        pub(7, dup=True),  # 合法重放
        pk("PUBREC", "S2C", packet_id=7),
        pk("PUBREL", "C2S", packet_id=7),
        pk("PUBCOMP", "S2C", packet_id=7),
    ], "sat-01")
    assert r["verdict"] == "ACCEPTED", r["first_violation"]
    assert r["summary"]["delivered_once"] == 1
    assert r["summary"]["total_publications"] == 1
    reopen = [e for e in r["packets"] if e["note"] and "重发" in e["note"]]
    assert reopen  # 重发包有明确证据说明


def test_pubrel_resend_when_pubcomp_lost():
    r = adjudicate([
        CONNECT(), CONNACK(),
        pub(9), pk("PUBREC", "S2C", packet_id=9),
        pk("PUBREL", "C2S", packet_id=9),
        DISC_S,  # PUBCOMP 丢失
        CONNECT(), CONNACK(sp=True),
        pk("PUBREL", "C2S", packet_id=9),  # PUBREL 重放，不带 DUP
        pk("PUBCOMP", "S2C", packet_id=9),
    ], "sat-01")
    assert r["verdict"] == "ACCEPTED", r["first_violation"]
    assert r["summary"]["delivered_once"] == 1


def test_unclosed_exchange_reported_open():
    r = adjudicate([
        CONNECT(), CONNACK(),
        pub(3), pk("PUBREC", "S2C", packet_id=3),
        pk("PUBREL", "C2S", packet_id=3),
        DISC_C,  # PUBCOMP 永远缺失：丢失确认导致的未闭合会话
    ], "sat-01")
    assert r["verdict"] == "OPEN"
    assert r["open_exchanges"] == [{
        "packet_id": 3, "state": "PUBREL_RCVD",
        "first_index": 3, "replayed": False,
    }]


def test_dup_after_complete_is_duplicate_delivery():
    r = adjudicate([
        CONNECT(), CONNACK(),
        pub(1), pk("PUBREC", "S2C", packet_id=1),
        pk("PUBREL", "C2S", packet_id=1), pk("PUBCOMP", "S2C", packet_id=1),
        pub(1, dup=True),  # 闭合后重发 -> 重复交付
    ], "sat-01")
    assert r["verdict"] == "REJECTED"
    assert r["first_violation"]["index"] == 7
    assert r["first_violation"]["code"] == R_PUBLISH_DUP_AFTER_COMPLETE
    assert r["duplicate_delivery"] is True


def test_payload_conflict_on_resend():
    r = adjudicate([
        CONNECT(), CONNACK(),
        pub(2, payload="temp=30"),
        DISC_S,
        CONNECT(), CONNACK(sp=True),
        pub(2, dup=True, payload="temp=31"),  # 载荷冲突
    ], "sat-01")
    assert r["verdict"] == "REJECTED"
    assert r["first_violation"]["code"] == R_PUBLISH_CONFLICT
    assert r["first_violation"]["index"] == 7


def test_phase_skip_pubrel_without_pubrec():
    r = adjudicate([
        CONNECT(), CONNACK(),
        pub(4), pk("PUBREL", "C2S", packet_id=4),
    ], "sat-01")
    assert r["first_violation"]["code"] == R_PUBREL_STATE
    assert r["first_violation"]["index"] == 4


def test_phase_skip_pubcomp_early():
    r = adjudicate([
        CONNECT(), CONNACK(),
        pub(4), pk("PUBREC", "S2C", packet_id=4),
        pk("PUBCOMP", "S2C", packet_id=4),
    ], "sat-01")
    assert r["first_violation"]["code"] == R_PUBCOMP_STATE
    assert r["first_violation"]["index"] == 5


def test_wrong_direction_is_located():
    r = adjudicate([
        CONNECT(), CONNACK(),
        pub(4), pk("PUBREC", "C2S", packet_id=4),  # PUBREC 只能 S2C
    ], "sat-01")
    assert r["first_violation"]["code"] == R_ACK_DIR
    assert r["first_violation"]["index"] == 4


def test_clean_session_drops_old_acks():
    """Clean Start=1 清除会话后继续旧确认 -> 未知包标识。"""
    r = adjudicate([
        CONNECT(), CONNACK(),
        pub(5), DISC_S,
        CONNECT(clean=True), CONNACK(sp=False),
        pk("PUBREC", "S2C", packet_id=5),  # 旧交换已被清除
    ], "sat-01")
    assert r["verdict"] == "REJECTED"
    assert r["first_violation"]["index"] == 7
    assert r["first_violation"]["code"] == "PUBREC_FOR_UNKNOWN_PACKET_ID"


def test_session_present_without_persisted_session_rejected():
    r = adjudicate([CONNECT(), CONNACK(sp=True)], "sat-01")
    assert r["first_violation"]["code"] == R_CONNACK_SP1_NO_SESSION


def test_session_absent_while_state_exists_rejected():
    """Clean Start=0 重连且服务端仍有会话状态时，SP 必须为 1。"""
    r = adjudicate([
        CONNECT(), CONNACK(),
        pub(1), DISC_S,
        CONNECT(clean=False), CONNACK(sp=False),
    ], "sat-01")
    assert r["first_violation"]["code"] == "SESSION_ABSENT_BUT_PERSISTED_SESSION_EXISTS"
    assert r["first_violation"]["index"] == 6


def test_dup_on_unknown_id():
    r = adjudicate([
        CONNECT(), CONNACK(),
        pub(8, dup=True),
    ], "sat-01")
    assert r["first_violation"]["code"] == R_PUBLISH_DUP_FIRST


def test_resend_without_dup_flag():
    r = adjudicate([
        CONNECT(), CONNACK(),
        pub(8),
        pub(8),  # 未置 DUP 的重复 PUBLISH
    ], "sat-01")
    assert r["first_violation"]["code"] == R_PUBLISH_REUSE_WITHOUT_DUP


def test_publish_after_pubrel_is_phase_regression():
    r = adjudicate([
        CONNECT(), CONNACK(),
        pub(8), pk("PUBREC", "S2C", packet_id=8),
        pk("PUBREL", "C2S", packet_id=8),
        pub(8, dup=True),
    ], "sat-01")
    assert r["first_violation"]["code"] == R_PUBLISH_AFTER_PUBREL


def test_first_packet_must_be_connect():
    r = adjudicate([CONNACK()], "sat-01")
    assert r["first_violation"]["code"] == R_FIRST_NOT_CONNECT


def test_packet_while_disconnected():
    r = adjudicate([
        CONNECT(), CONNACK(), DISC_C,
        pub(1),
    ], "sat-01")
    assert r["first_violation"]["code"] == R_NOT_CONNECTED
    assert r["first_violation"]["index"] == 4


def test_non_qos2_publish_rejected():
    r = adjudicate([
        CONNECT(), CONNACK(),
        pk("PUBLISH", "C2S", packet_id=1, qos=1),
    ], "sat-01")
    assert r["first_violation"]["code"] == R_PUBLISH_QOS


def test_later_packets_marked_unchecked_after_fatal():
    r = adjudicate([
        CONNECT(), CONNACK(),
        pub(1), pk("PUBREL", "C2S", packet_id=1),  # 阶段跳跃
        pk("PUBCOMP", "S2C", packet_id=1),
    ], "sat-01")
    assert r["packets"][4]["checked"] is False
    assert r["packets"][4]["note"]


def test_publish_carrying_session_fields_rejected():
    r = adjudicate([
        CONNECT(), CONNACK(),
        P(type="PUBLISH", direction="C2S", packet_id=1, qos=2,
          clean_start=True),
    ], "sat-01")
    assert r["first_violation"]["code"] == "PUBLISH_HAS_NON_PUBLISH_FIELDS"
    assert r["first_violation"]["index"] == 3


def test_ack_carrying_publish_fields_rejected():
    r = adjudicate([
        CONNECT(), CONNACK(),
        pub(1),
        P(type="PUBREC", direction="S2C", packet_id=1, qos=2),
    ], "sat-01")
    assert r["first_violation"]["code"] == "ACK_HAS_FOREIGN_FIELDS"
    assert r["first_violation"]["index"] == 4


def test_pubcomp_unknown_id_after_clean_session():
    r = adjudicate([
        CONNECT(), CONNACK(),
        pub(6), pk("PUBREC", "S2C", packet_id=6),
        pk("PUBREL", "C2S", packet_id=6),
        DISC_S,
        CONNECT(clean=True), CONNACK(),
        pk("PUBCOMP", "S2C", packet_id=6),
    ], "sat-01")
    assert r["first_violation"]["index"] == 9
    assert r["first_violation"]["code"] == "PUBCOMP_FOR_UNKNOWN_PACKET_ID"


def test_empty_capture_rejected():
    r = adjudicate([], "sat-01")
    assert r["verdict"] == "REJECTED"
    assert r["first_violation"]["code"] == "EMPTY_CAPTURE"


def test_deterministic_same_input_same_output():
    seq = [
        CONNECT(), CONNACK(),
        pub(1), pk("PUBREC", "S2C", packet_id=1),
        pk("PUBREL", "C2S", packet_id=1), pk("PUBCOMP", "S2C", packet_id=1),
    ]
    a = adjudicate(seq, "sat-01")
    b = adjudicate(seq, "sat-01")
    assert a == b
