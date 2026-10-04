"""MQTT 5 QoS2 审计裁决联调页面的 HTTP API。"""
from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .engine import adjudicate
from .schemas import AuditSubmission
from .storage import AuditConflictError, AuditStore

STATIC_DIR = os.environ.get(
    "STATIC_DIR", os.path.join(os.path.dirname(__file__), "..", "static")
)

app = FastAPI(title="MQTT 5 QoS2 遥测重放审计", version="1.0.0")
store = AuditStore()


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "service": "mqtt-qos2-audit"}


@app.post("/api/audits")
def submit_audit(submission: AuditSubmission) -> JSONResponse:
    verdict = adjudicate(submission.packets, submission.client_id)
    try:
        record, replayed = store.submit(submission, verdict)
    except AuditConflictError as exc:
        return JSONResponse(
            status_code=409,
            content={
                "error": "AUDIT_ID_INPUT_CONFLICT",
                "message": (
                    "该审计标识已绑定另一份输入，原裁决证据予以保留；"
                    "请使用新的审计标识提交改动后的输入"
                ),
                "audit_id": submission.audit_id,
                "existing_fingerprint": exc.existing["fingerprint"],
                "existing_verdict": exc.existing["verdict"]["verdict"],
                "reopen": f"/api/audits/{submission.audit_id}",
            },
        )
    return JSONResponse(
        status_code=200 if replayed else 201,
        content={
            "replayed": replayed,
            "audit_id": record["audit_id"],
            "client_id": record["client_id"],
            "fingerprint": record["fingerprint"],
            "verdict": record["verdict"],
        },
    )


@app.get("/api/audits/{audit_id}")
def get_audit(audit_id: str) -> dict:
    record = store.get(audit_id)
    if record is None:
        raise HTTPException(status_code=404, detail="审计标识不存在")
    return {
        "audit_id": record["audit_id"],
        "client_id": record["client_id"],
        "fingerprint": record["fingerprint"],
        "verdict": record["verdict"],
        "submission": record["submission"],
    }


@app.get("/api/audits")
def list_audits() -> dict:
    return {"audit_ids": store.all_ids()}


# 静态联调页面（容器构建时由前端产物拷贝至 static/）
if os.path.isdir(STATIC_DIR):
    app.mount(
        "/static",
        StaticFiles(directory=STATIC_DIR),
        name="static",
    )

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))
