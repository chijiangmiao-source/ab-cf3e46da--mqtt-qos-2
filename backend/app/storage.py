"""裁决证据的持久化存储。

以审计标识为键保存“原始输入指纹 + 裁决结果 + 原始提交”，保证：
* 相同审计标识、完全相同输入 -> 回放原裁决；
* 相同审计标识、改动输入 -> 拒绝并保留原证据；
* 按审计标识可重新打开历史裁决。

数据落盘为单个 JSON 文件，容器重启后历史证据仍可复核。
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from typing import Optional

from .schemas import AuditSubmission

_LOCK = threading.Lock()


def fingerprint(submission: AuditSubmission) -> str:
    """对提交内容计算稳定指纹（审计标识、客户端标识、全部包逐字段参与）。"""
    canonical = json.dumps(
        submission.model_dump(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class AuditStore:
    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or os.environ.get(
            "AUDIT_STORE_PATH", "/data/audits.json"
        )
        self._records: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                self._records = json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError):
            self._records = {}

    def _save_locked(self) -> None:
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        tmp = f"{self.path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self._records, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def submit(self, submission: AuditSubmission, verdict: dict) -> dict:
        """登记裁决。返回 (记录, 是否为回放)；冲突抛 ValueError。"""
        fp = fingerprint(submission)
        with _LOCK:
            existing = self._records.get(submission.audit_id)
            if existing is not None:
                if existing["fingerprint"] == fp:
                    return existing, True
                raise AuditConflictError(existing)
            record = {
                "audit_id": submission.audit_id,
                "client_id": submission.client_id,
                "fingerprint": fp,
                "verdict": verdict,
                "submission": submission.model_dump(),
            }
            self._records[submission.audit_id] = record
            self._save_locked()
            return record, False

    def get(self, audit_id: str) -> Optional[dict]:
        with _LOCK:
            return self._records.get(audit_id)

    def all_ids(self) -> list[str]:
        with _LOCK:
            return sorted(self._records.keys())


class AuditConflictError(Exception):
    def __init__(self, existing: dict) -> None:
        self.existing = existing
        super().__init__(
            f"审计标识 {existing['audit_id']} 已绑定不同输入"
        )
