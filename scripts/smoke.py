"""HTTP 冒烟验证（由 compose verify 与本地 verify.sh 调用）。

校验：
1. 健康检查可用；
2. 断链后合法重发：裁决 ACCEPTED、恰好交付一次、无未闭合交换；
3. 闭合后 DUP 重发（冲突重发）：裁决 REJECTED、构成重复交付、定位违规包；
4. 相同标识 + 相同输入回放原裁决；相同标识 + 改动输入返回 409 且原证据保留；
5. 按审计标识重新打开裁决。

任何一步失败即以非零退出码结束。
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8000").rstrip("/")
# 每次运行使用独立标识，保证对持久化证据库重复执行时仍是“首次提交”
RUN_TAG = str(int(time.time() * 1000))
PASS = 0


def call(method: str, path: str, payload=None, expect=(200, 201)):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            code = resp.status
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        code = exc.code
        body = json.loads(exc.read().decode())
    if code not in expect:
        fail(f"{method} {path} 期望 {expect}，实际 {code}：{body}")
    return code, body


def check(name: str, cond, detail=""):
    global PASS
    if not cond:
        fail(f"{name} {detail}")
    PASS += 1
    print(f"  ✓ {name}")


def fail(msg: str):
    print(f"  ✗ {msg}")
    sys.exit(1)


def connect_packets(clean=False, sp=False):
    return [
        {"type": "CONNECT", "direction": "C2S", "clean_start": clean},
        {"type": "CONNACK", "direction": "S2C", "session_present": sp},
    ]


def full_qos2(pid):
    return [
        {"type": "PUBLISH", "direction": "C2S", "packet_id": pid, "qos": 2,
         "dup": False, "topic": "sat/tm/temp", "payload": "temp=21.5"},
        {"type": "PUBREC", "direction": "S2C", "packet_id": pid},
        {"type": "PUBREL", "direction": "C2S", "packet_id": pid},
        {"type": "PUBCOMP", "direction": "S2C", "packet_id": pid},
    ]


def main():
    print(f"冒烟目标: {BASE}")

    code, h = call("GET", "/api/health")
    check("健康检查返回 ok", h.get("status") == "ok")

    # ---- 场景 1：断链后合法重发，仅交付一次 ----
    print("[1] 断链后合法重发")
    packets = (
        connect_packets(clean=False, sp=False)
        + [{"type": "PUBLISH", "direction": "C2S", "packet_id": 101, "qos": 2,
            "dup": False, "topic": "sat/tm/temp", "payload": "temp=21.5"},
           {"type": "DISCONNECT", "direction": "S2C"}]
        + connect_packets(clean=False, sp=True)
        + [{"type": "PUBLISH", "direction": "C2S", "packet_id": 101, "qos": 2,
            "dup": True, "topic": "sat/tm/temp", "payload": "temp=21.5"},
           {"type": "PUBREC", "direction": "S2C", "packet_id": 101},
           {"type": "PUBREL", "direction": "C2S", "packet_id": 101},
           {"type": "PUBCOMP", "direction": "S2C", "packet_id": 101}]
    )
    body = {"audit_id": f"SMOKE-LEGAL-{RUN_TAG}", "client_id": "SAT-AOCS-07",
            "packets": packets}
    code, res = call("POST", "/api/audits", body, expect=(201,))
    original_verdict = res["verdict"]
    v = original_verdict
    check("合法重发裁决为 ACCEPTED", v["verdict"] == "ACCEPTED",
          detail=f"实际 {v['verdict']} / {v['first_violation']}")
    check("应用消息恰好交付一次",
          v["summary"]["delivered_once"] == 1
          and v["summary"]["total_publications"] == 1,
          detail=str(v["summary"]))
    check("无未闭合交换", v["summary"]["open_count"] == 0)
    check("不构成重复交付", v["duplicate_delivery"] is False)

    # ---- 场景 2：闭合后 DUP 重发（冲突重发）被拒绝 ----
    print("[2] 闭合后冲突重发")
    bad = (
        connect_packets()
        + full_qos2(303)
        + [{"type": "PUBLISH", "direction": "C2S", "packet_id": 303, "qos": 2,
            "dup": True, "topic": "sat/tm/temp", "payload": "temp=21.5"}]
    )
    code, res = call("POST", "/api/audits",
                     {"audit_id": f"SMOKE-DUP-{RUN_TAG}", "client_id": "SAT-AOCS-07",
                      "packets": bad}, expect=(201,))
    v = res["verdict"]
    check("冲突重发裁决为 REJECTED", v["verdict"] == "REJECTED")
    check("识别为重复交付", v["duplicate_delivery"] is True)
    fv = v["first_violation"]
    check("稳定定位违规包序号",
          fv["index"] == len(bad) and fv["code"] == "DUP_PUBLISH_AFTER_QOS2_COMPLETE",
          detail=str(fv))

    # ---- 场景 3：同标识同输入回放，改输入被拒且原证据保留 ----
    print("[3] 回放与冲突保留")
    code, res2 = call("POST", "/api/audits", body, expect=(200,))
    check("相同输入回放原裁决（HTTP 200, replayed=true）",
          res2.get("replayed") is True
          and res2["verdict"] == original_verdict)

    tampered = json.loads(json.dumps(body))
    tampered["packets"][6]["payload"] = "temp=99.9-tampered"
    code, res3 = call("POST", "/api/audits", tampered, expect=(409,))
    check("改动输入复用标识被拒绝（409）",
          res3.get("error") == "AUDIT_ID_INPUT_CONFLICT")
    code, reopened = call("GET", f"/api/audits/SMOKE-LEGAL-{RUN_TAG}")
    check("原证据保留且可重开",
          reopened["verdict"]["verdict"] == "ACCEPTED"
          and reopened["submission"]["packets"][6]["payload"] == "temp=21.5")

    print(f"\n全部冒烟检查通过（{PASS} 项断言）。")


if __name__ == "__main__":
    main()
