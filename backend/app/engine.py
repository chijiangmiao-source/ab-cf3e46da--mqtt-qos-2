"""MQTT 5 QoS 2 重连/重放裁决引擎（确定性、纯函数）。

会话模型
========
每个会话只能从 CONNECT(C2S) 开始，CONNACK(S2C) 成功后进入连接态；连接态内
仅允许 PUBLISH / PUBREC / PUBREL / PUBCOMP。C2S DISCONNECT 为正常关闭，
S2C DISCONNECT 为服务端断链（遥测中继断链）；二者之后必须重新 CONNECT。

QoS 2 四步交换（C2S 方向为入站遥测）：
    C2S PUBLISH(qos=2) -> S2C PUBREC -> C2S PUBREL -> S2C PUBCOMP

逐包校验
--------
1. 阶段合法：是否已连接、控制包与方向是否符合 QoS2 交换阶段；
2. 包标识一致：同一标识符的 PUBLISH/PUBREC/PUBREL/PUBCOMP 串成一个交换；
3. 重放合法：断链重发必须置 DUP=1（PUBREL 重发除外），且载荷与首次交付
   完全一致；PUBREL 已发出后不得再重发 PUBLISH；
4. 会话恢复合法：Clean Start=1 清除持久会话，旧交换的确认不得在新会话继续；
   Clean Start=0 + Session Present=1 时旧交换可恢复，重发后正常闭合则视为
   应用消息“恰好交付一次”。

裁决在首个结构违规处中止，其后各包标记为未检查；未闭合交换与重复交付
作为独立语义结论输出。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .schemas import PacketIn

# ---- 违规依据代码（稳定标识，供页面与测试引用） -------------------------
R_EMPTY = "EMPTY_CAPTURE"
R_FIRST_NOT_CONNECT = "FIRST_PACKET_MUST_BE_CONNECT"
R_CONNECT_IN_SESSION = "CONNECT_WHILE_SESSION_OPEN"
R_CONNECT_DIR = "CONNECT_MUST_BE_C2S"
R_CONNECT_FIELDS = "CONNECT_HAS_NON_CONNECT_FIELDS"
R_CONNECT_CLIENT_MISMATCH = "CONNECT_CLIENT_ID_MISMATCH"
R_CONNACK_WITHOUT_CONNECT = "CONNACK_WITHOUT_PENDING_CONNECT"
R_CONNACK_DIR = "CONNACK_MUST_BE_S2C"
R_CONNACK_FIELDS = "CONNACK_HAS_NON_CONNACK_FIELDS"
R_CONNACK_SESSION_PRESENT_CONFLICT = "SESSION_PRESENT_CONFLICTS_WITH_CLEAN_START"
R_CONNACK_SP1_NO_SESSION = "SESSION_PRESENT_BUT_NO_PERSISTED_SESSION"
R_CONNACK_SP0_WITH_STATE = "SESSION_ABSENT_BUT_PERSISTED_SESSION_EXISTS"
R_CONNACK_REJECTED = "CONNACK_REJECTED"
R_DISCONNECT_FIELDS = "DISCONNECT_HAS_NON_DISCONNECT_FIELDS"
R_NOT_CONNECTED = "PACKET_WHILE_DISCONNECTED"
R_PUBLISH_FIELDS = "PUBLISH_HAS_NON_PUBLISH_FIELDS"
R_PUBLISH_QOS = "PUBLISH_MUST_BE_QOS2"
R_PUBLISH_ID = "PUBLISH_REQUIRES_PACKET_ID_1_TO_65535"
R_PUBLISH_DUP_FIRST = "DUP_ON_FIRST_DELIVERY"
R_PUBLISH_CONFLICT = "PACKET_ID_REUSED_BY_DIFFERENT_PUBLICATION"
R_PUBLISH_DUP_AFTER_COMPLETE = "DUP_PUBLISH_AFTER_QOS2_COMPLETE"
R_PUBLISH_REUSE_WITHOUT_DUP = "PACKET_ID_IN_USE_WITHOUT_DUP_FLAG"
R_PUBLISH_AFTER_PUBREL = "DUP_PUBLISH_AFTER_PUBREL"
R_ACK_FIELDS = "ACK_HAS_FOREIGN_FIELDS"
R_ACK_ID = "ACK_REQUIRES_PACKET_ID_1_TO_65535"
R_ACK_DIR = "ACK_DIRECTION_WRONG"
R_PUBREC_NEW_ID = "PUBREC_FOR_UNKNOWN_PACKET_ID"
R_PUBREL_NEW_ID = "PUBREL_FOR_UNKNOWN_PACKET_ID"
R_PUBCOMP_NEW_ID = "PUBCOMP_FOR_UNKNOWN_PACKET_ID"
R_PUBREC_STATE = "PUBREC_NOT_EXPECTED_IN_THIS_STATE"
R_PUBREL_STATE = "PUBREL_NOT_EXPECTED_IN_THIS_STATE"
R_PUBCOMP_STATE = "PUBCOMP_NOT_EXPECTED_IN_THIS_STATE"

VIOLATION_MESSAGES: dict[str, str] = {
    R_EMPTY: "捕获序列为空，无可裁决的控制包",
    R_FIRST_NOT_CONNECT: "会话必须以 CONNECT 开始",
    R_CONNECT_IN_SESSION: "会话尚未关闭时再次 CONNECT（缺少 DISCONNECT）",
    R_CONNECT_DIR: "CONNECT 方向必须为 C2S",
    R_CONNECT_FIELDS: "CONNECT 携带了非 CONNECT 字段",
    R_CONNECT_CLIENT_MISMATCH: "CONNECT 中的 Client Identifier 与录入的客户端标识不一致",
    R_CONNACK_WITHOUT_CONNECT: "CONNACK 前没有待确认的 CONNECT",
    R_CONNACK_DIR: "CONNACK 方向必须为 S2C",
    R_CONNACK_FIELDS: "CONNACK 携带了非 CONNACK 字段",
    R_CONNACK_SESSION_PRESENT_CONFLICT: "Clean Start=1 时 CONNACK 不得返回 Session Present=1",
    R_CONNACK_SP1_NO_SESSION: "Session Present=1 但服务端不存在可恢复的持久会话",
    R_CONNACK_SP0_WITH_STATE: "Clean Start=0 且服务端仍存有会话，CONNACK 必须返回 Session Present=1",
    R_CONNACK_REJECTED: "CONNACK 原因码表示连接被拒绝，其后交换不得继续",
    R_DISCONNECT_FIELDS: "DISCONNECT 携带了其他控制包字段",
    R_NOT_CONNECTED: "链路未建立时出现应用层控制包，应先 CONNECT/CONNACK",
    R_PUBLISH_FIELDS: "PUBLISH 携带了会话级或确认包字段",
    R_PUBLISH_QOS: "系统仅受理 QoS 2 的 PUBLISH",
    R_PUBLISH_ID: "PUBLISH 必须携带 1..65535 的包标识",
    R_PUBLISH_DUP_FIRST: "未知包标识却置 DUP=1，无法对应任何首次交付",
    R_PUBLISH_CONFLICT: "同一包标识的重发主题/载荷与首次交付冲突",
    R_PUBLISH_DUP_AFTER_COMPLETE: "交换已 PUBCOMP 闭合后又收到 DUP PUBLISH，构成重复交付",
    R_PUBLISH_REUSE_WITHOUT_DUP: "包标识仍在交换中，重发必须置 DUP=1，否则标识复用非法",
    R_PUBLISH_AFTER_PUBREL: "PUBREL 已发出后不得再重发 PUBLISH（阶段倒退）",
    R_ACK_FIELDS: "确认包携带了 PUBLISH 专属或会话级字段",
    R_ACK_ID: "确认包必须携带 1..65535 的包标识",
    R_ACK_DIR: "确认包方向与 QoS2 交换阶段不符",
    R_PUBREC_NEW_ID: "PUBREC 的包标识在当前（可恢复）会话中找不到对应 PUBLISH",
    R_PUBREL_NEW_ID: "PUBREL 的包标识在当前（可恢复）会话中找不到对应 PUBLISH",
    R_PUBCOMP_NEW_ID: "PUBCOMP 的包标识在当前（可恢复）会话中找不到对应 PUBLISH",
    R_PUBREC_STATE: "该交换阶段不期望 PUBREC（阶段跳跃）",
    R_PUBREL_STATE: "该交换阶段不期望 PUBREL（阶段跳跃）",
    R_PUBCOMP_STATE: "该交换阶段不期望 PUBCOMP（阶段跳跃）",
}

# 交换阶段
ST_NEW = "PUBLISH_RCVD"       # 已收首次/重发 PUBLISH，等待 PUBREC
ST_PUBREC = "PUBREC_SENT"     # 已发 PUBREC，等待 PUBREL
ST_PUBREL = "PUBREL_RCVD"     # 已收 PUBREL，等待 PUBCOMP
ST_COMPLETE = "COMPLETE"      # 已 PUBCOMP，恰好交付一次

# 会话连接阶段（展示用）
PH_NO_SESSION = "NO_SESSION"
PH_CONNECT_SENT = "CONNECT_SENT"
PH_CONNECTED = "CONNECTED"
PH_DISCONNECTED = "DISCONNECTED"

# 各控制包允许出现的业务字段
_ALLOWED_FIELDS: dict[str, set[str]] = {
    "CONNECT": {"client_id", "clean_start"},
    "CONNACK": {"session_present", "reason_code"},
    "PUBLISH": {"packet_id", "dup", "qos", "topic", "payload"},
    "PUBREC": {"packet_id", "reason_code"},
    "PUBREL": {"packet_id", "reason_code"},
    "PUBCOMP": {"packet_id", "reason_code"},
    "DISCONNECT": {"reason_code"},
}
_ACK_TYPES = ("PUBREC", "PUBREL", "PUBCOMP")
_ACK_DIR = {"PUBREC": "S2C", "PUBREL": "C2S", "PUBCOMP": "S2C"}
_ACK_NEW_ID_CODE = {
    "PUBREC": R_PUBREC_NEW_ID,
    "PUBREL": R_PUBREL_NEW_ID,
    "PUBCOMP": R_PUBCOMP_NEW_ID,
}
_ACK_STATE_CODE = {
    "PUBREC": R_PUBREC_STATE,
    "PUBREL": R_PUBREL_STATE,
    "PUBCOMP": R_PUBCOMP_STATE,
}


@dataclass
class Exchange:
    packet_id: int
    topic: Optional[str]
    payload: Optional[str]
    state: str = ST_NEW
    first_index: Optional[int] = None
    first_delivered: bool = False
    pubrel_index: Optional[int] = None
    complete_index: Optional[int] = None
    ever_replayed: bool = False
    duplicate_after_complete: bool = False


@dataclass
class PacketEvidence:
    index: int
    type: str
    direction: str
    session_phase: str
    packet_id: Optional[int]
    first_delivery: Optional[bool]
    violation: Optional[str]
    violation_message: Optional[str]
    checked: bool
    note: Optional[str] = None


@dataclass
class _Session:
    gen: int
    clean_start: bool
    persisted: bool
    exchanges: dict[int, Exchange] = field(default_factory=dict)


def _foreign_fields(p: PacketIn) -> bool:
    """是否携带本控制包不允许的业务字段。"""
    names = ("packet_id", "dup", "qos", "topic", "payload", "clean_start",
             "session_present", "reason_code", "client_id")
    provided = set()
    for name in names:
        value = getattr(p, name)
        if name == "dup":
            if value:
                provided.add("dup")
        elif value is not None:
            provided.add(name)
    return bool(provided - _ALLOWED_FIELDS[p.type])


def adjudicate(packets: list[PacketIn], client_id: str) -> dict:
    """对捕获序列执行确定性裁决，返回可直接 JSON 序列化的结构。"""
    if not packets:
        return {
            "verdict": "REJECTED",
            "first_violation": {
                "index": 0,
                "code": R_EMPTY,
                "message": VIOLATION_MESSAGES[R_EMPTY],
            },
            "packets": [],
            "open_exchanges": [],
            "duplicate_delivery": False,
            "summary": {"client_id": client_id, "total_publications": 0,
                        "delivered_once": 0, "open_count": 0,
                        "duplicate_delivery": False},
        }

    evidence: list[PacketEvidence] = []
    # 服务端持久会话：跨连接代际延续，Clean Start=1 时清空
    persistent: dict[int, Exchange] = {}
    session: Optional[_Session] = None
    connected = False
    connect_pending = False
    fatal = False
    first_violation: Optional[dict] = None

    def phase_label() -> str:
        if session is None:
            return PH_NO_SESSION
        if connect_pending:
            return PH_CONNECT_SENT
        return PH_CONNECTED if connected else PH_DISCONNECTED

    def stop(code: str, note: Optional[str] = None) -> None:
        nonlocal fatal, first_violation
        ev = evidence[-1]
        ev.violation = code
        ev.violation_message = VIOLATION_MESSAGES[code]
        if note:
            ev.note = note
        fatal = True
        if first_violation is None:
            first_violation = {"index": ev.index, "code": code,
                               "message": VIOLATION_MESSAGES[code]}

    for i, p in enumerate(packets, start=1):
        ev = PacketEvidence(
            index=i,
            type=p.type,
            direction=p.direction,
            session_phase=phase_label(),
            packet_id=p.packet_id if p.type in (("PUBLISH",) + _ACK_TYPES) else None,
            first_delivery=None,
            violation=None,
            violation_message=None,
            checked=not fatal,
            note=None,
        )
        evidence.append(ev)
        if fatal:
            ev.note = "首个违规已出现，本包未检查"
            continue

        t = p.type

        # 捕获序列必须以 CONNECT 开始
        if i == 1 and t != "CONNECT":
            stop(R_FIRST_NOT_CONNECT)
            continue

        # ---------------- CONNECT ----------------
        if t == "CONNECT":
            if _foreign_fields(p):
                stop(R_CONNECT_FIELDS)
                continue
            if p.direction != "C2S":
                stop(R_CONNECT_DIR)
                continue
            if session is not None and (connected or connect_pending):
                stop(R_CONNECT_IN_SESSION)
                continue
            if p.client_id is not None and p.client_id != client_id:
                stop(R_CONNECT_CLIENT_MISMATCH)
                continue
            clean = bool(p.clean_start)
            if clean:
                persistent = {}
            # 审计口径下，可恢复的会话状态 = 尚未闭合的 QoS2 交换；
            # 已全部闭合后服务端不再持有交付状态，SP=0 合法。
            recoverable = any(e.state != ST_COMPLETE for e in persistent.values())
            session = _Session(
                gen=(session.gen + 1) if session else 1,
                clean_start=clean,
                persisted=recoverable,
            )
            connect_pending = True
            connected = False
            ev.note = ("Clean Start=1，清除并新建持久会话" if clean
                       else "Clean Start=0，请求恢复持久会话")
            continue

        # ---------------- CONNACK ----------------
        if t == "CONNACK":
            if _foreign_fields(p):
                stop(R_CONNACK_FIELDS)
                continue
            if session is None or not connect_pending:
                stop(R_CONNACK_WITHOUT_CONNECT)
                continue
            if p.direction != "S2C":
                stop(R_CONNACK_DIR)
                continue
            sp = bool(p.session_present)
            if sp and session.clean_start:
                stop(R_CONNACK_SESSION_PRESENT_CONFLICT)
                continue
            if sp and not session.persisted:
                stop(R_CONNACK_SP1_NO_SESSION)
                continue
            if not session.clean_start and not sp and session.persisted:
                stop(R_CONNACK_SP0_WITH_STATE)
                continue
            if p.reason_code is not None and p.reason_code >= 0x80:
                stop(R_CONNACK_REJECTED)
                continue
            session.exchanges = persistent
            connect_pending = False
            connected = True
            ev.note = ("恢复既有持久会话（Session Present=1）" if sp
                       else "Session Present=0，建立新会话上下文")
            continue

        # ---------------- DISCONNECT ----------------
        if t == "DISCONNECT":
            if _foreign_fields(p):
                stop(R_DISCONNECT_FIELDS)
                continue
            if not connected:
                stop(R_NOT_CONNECTED)
                continue
            open_ids = [e.packet_id for e in session.exchanges.values()
                        if e.state != ST_COMPLETE]
            if p.direction == "C2S":
                ev.note = "客户端正常关闭链路" + (
                    f"，未闭合交换 {open_ids} 留待重连" if open_ids else "")
            elif p.direction == "S2C":
                ev.note = "服务端断开链路（遥测中继断链）" + (
                    f"，丢失确认的未闭合交换 {open_ids}" if open_ids
                    else "，无未闭合交换")
            else:  # pragma: no cover - 模型已限定方向
                stop(R_ACK_DIR)
                continue
            connected = False
            connect_pending = False
            continue

        # ---------------- 应用层四包：必须在线 ----------------
        if not connected or session is None:
            stop(R_NOT_CONNECTED)
            continue

        # ---------------- PUBLISH (QoS 2) ----------------
        if t == "PUBLISH":
            if _foreign_fields(p):
                stop(R_PUBLISH_FIELDS)
                continue
            if p.qos != 2:
                stop(R_PUBLISH_QOS)
                continue
            pid = p.packet_id
            if pid is None or pid == 0:
                stop(R_PUBLISH_ID)
                continue
            ex = session.exchanges.get(pid)
            if ex is None:
                if p.dup:
                    stop(R_PUBLISH_DUP_FIRST)
                    continue
                ex = Exchange(packet_id=pid, topic=p.topic, payload=p.payload,
                              first_index=i, first_delivered=True)
                session.exchanges[pid] = ex
                persistent[pid] = ex
                ev.first_delivery = True
                ev.note = "QoS2 首次交付，等待 PUBREC"
                continue

            ev.first_delivery = False
            if ex.topic != p.topic or ex.payload != p.payload:
                stop(R_PUBLISH_CONFLICT)
                continue
            if ex.state == ST_COMPLETE:
                if p.dup:
                    ex.duplicate_after_complete = True
                    stop(R_PUBLISH_DUP_AFTER_COMPLETE)
                else:
                    stop(R_PUBLISH_REUSE_WITHOUT_DUP)
                continue
            if not p.dup:
                stop(R_PUBLISH_REUSE_WITHOUT_DUP)
                continue
            if ex.state == ST_PUBREL:
                # PUBREL 已发出，发布流程所有权已移交，重发 PUBLISH 属阶段倒退
                stop(R_PUBLISH_AFTER_PUBREL,
                     note=f"交换当前阶段为 {ex.state}")
                continue
            # ST_NEW：未收到 PUBREC 的重发；ST_PUBREC：PUBREC 丢失的重发，
            # 两种情况下阶段保持不变，服务端对 ST_PUBREC 将幂等重发 PUBREC
            ex.ever_replayed = True
            ev.note = ("断链重发 DUP=1 且载荷一致，重新等待 PUBREC"
                       if ex.state == ST_NEW
                       else "断链重发 DUP=1（PUBREC 已丢失），等待服务端幂等 PUBREC")
            continue

        # ---------------- PUBREC / PUBREL / PUBCOMP ----------------
        if _foreign_fields(p):
            stop(R_ACK_FIELDS)
            continue
        pid = p.packet_id
        if pid is None or pid == 0:
            stop(R_ACK_ID)
            continue
        want_dir = _ACK_DIR[t]
        if p.direction != want_dir:
            stop(R_ACK_DIR, note=f"{t} 的合法方向为 {want_dir}")
            continue
        ex = session.exchanges.get(pid)
        if ex is None:
            stop(_ACK_NEW_ID_CODE[t])
            continue
        ev.packet_id = pid

        if t == "PUBREC":
            if ex.state == ST_NEW:
                ex.state = ST_PUBREC
                ev.note = "服务端确认收到 PUBLISH，等待 PUBREL"
            elif ex.state == ST_PUBREC:
                ex.ever_replayed = True
                ev.note = "对 DUP 重发 PUBLISH 的幂等 PUBREC"
            else:
                stop(_ACK_STATE_CODE[t], note=f"交换当前阶段为 {ex.state}")
        elif t == "PUBREL":
            if ex.state == ST_PUBREC:
                ex.state = ST_PUBREL
                ex.pubrel_index = i
                ev.note = "客户端释放发布，等待 PUBCOMP"
            elif ex.state == ST_PUBREL:
                # PUBCOMP 丢失：客户端重发 PUBREL，服务端须重发 PUBCOMP
                ex.ever_replayed = True
                ev.note = "PUBREL 重发（PUBCOMP 丢失），等待服务端重发 PUBCOMP"
            else:
                stop(_ACK_STATE_CODE[t], note=f"交换当前阶段为 {ex.state}")
        else:  # PUBCOMP
            if ex.state != ST_PUBREL:
                stop(_ACK_STATE_CODE[t], note=f"交换当前阶段为 {ex.state}")
                continue
            ex.state = ST_COMPLETE
            ex.complete_index = i
            ev.note = "QoS2 交换闭合，应用消息恰好交付一次"

    # ---------------- 汇总结论 ----------------
    all_exchanges = list(persistent.values())
    open_ex = [
        {
            "packet_id": e.packet_id,
            "state": e.state,
            "first_index": e.first_index,
            "replayed": e.ever_replayed,
        }
        for e in all_exchanges if e.state != ST_COMPLETE
    ]
    duplicate = any(e.duplicate_after_complete for e in all_exchanges)
    completed = sum(1 for e in all_exchanges if e.state == ST_COMPLETE)

    if first_violation is not None:
        verdict = "REJECTED"
    elif open_ex:
        verdict = "OPEN"
    else:
        verdict = "ACCEPTED"

    return {
        "verdict": verdict,
        "first_violation": first_violation,
        "packets": [
            {
                "index": e.index,
                "type": e.type,
                "direction": e.direction,
                "session_phase": e.session_phase,
                "packet_id": e.packet_id,
                "first_delivery": e.first_delivery,
                "checked": e.checked,
                "violation": e.violation,
                "violation_message": e.violation_message,
                "note": e.note,
            }
            for e in evidence
        ],
        "open_exchanges": open_ex,
        "duplicate_delivery": duplicate,
        "summary": {
            "client_id": client_id,
            "total_publications": len(all_exchanges),
            "delivered_once": completed,
            "open_count": len(open_ex),
            "duplicate_delivery": duplicate,
        },
    }
