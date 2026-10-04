"""MQTT 5 QoS2 审计裁决的输入模型。"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

PacketTypeName = Literal[
    "CONNECT", "CONNACK", "PUBLISH", "PUBREC", "PUBREL", "PUBCOMP", "DISCONNECT"
]
DirectionName = Literal["C2S", "S2C"]

_MAX_PACKETS = 48
_CLIENT_ID_MAX = 236
_AUDIT_ID_MAX = 128


class PacketIn(BaseModel):
    """按捕获顺序录入的一条 MQTT 5 控制包。"""

    model_config = ConfigDict(extra="forbid")

    type: PacketTypeName
    direction: DirectionName
    # PUBLISH / PUBREC / PUBREL / PUBCOMP 的报文标识符（1..65535，0 非法）
    packet_id: Optional[int] = Field(default=None, ge=0, le=65535)
    # 仅 PUBLISH：重发标志
    dup: bool = False
    # 仅 PUBLISH：系统仅受理 QoS 2
    qos: Optional[int] = Field(default=None, ge=0, le=2)
    topic: Optional[str] = None
    payload: Optional[str] = None
    # CONNECT：Clean Start
    clean_start: Optional[bool] = None
    # CONNACK：Session Present
    session_present: Optional[bool] = None
    # CONNACK / DISCONNECT：原因码（>=0x80 视为失败）
    reason_code: Optional[int] = Field(default=None, ge=0, le=255)
    # CONNECT 中携带的 Client Identifier，缺省视为与顶层 client_id 一致
    client_id: Optional[str] = None

    @field_validator("topic", "payload", "client_id")
    @classmethod
    def _reject_empty_str(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v == "":
            raise ValueError("空字符串请省略该字段或填写实际内容")
        return v


class AuditSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audit_id: str = Field(min_length=1, max_length=_AUDIT_ID_MAX)
    client_id: str = Field(min_length=1, max_length=_CLIENT_ID_MAX)
    packets: list[PacketIn] = Field(max_length=_MAX_PACKETS)


MAX_PACKETS = _MAX_PACKETS
