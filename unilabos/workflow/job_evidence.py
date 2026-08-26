"""工作流节点作业（WorkflowNodeJob）的不可变反馈与结果证据。"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import uuid4

from unilabos.workflow.json_codec import decode_json_bytes, encode_json
from unilabos.workflow.store import StoreConflict, StoreNotFound, WorkflowStore, utc_now

_TERMINAL_JOB_STATES = {"succeeded", "failed", "skipped", "canceled", "timeout"}


def _json(value: Any) -> str:
    """编码稳定 JSON 文本。"""

    return encode_json(value, sort_keys=True).decode("utf-8")


def _load(value: str) -> Any:
    """解码 SQLite JSON 文本。"""

    return decode_json_bytes(value.encode("utf-8"))


def ensure_job_evidence_schema(connection: sqlite3.Connection) -> None:
    """幂等创建不可变作业结果和有序反馈历史表。"""

    statements = (
        """
        CREATE TABLE IF NOT EXISTS workflow_node_job_result (
            uuid TEXT PRIMARY KEY,
            create_time TEXT NOT NULL,
            update_time TEXT NOT NULL,
            deleted_at TEXT,
            description TEXT,
            meta_data TEXT NOT NULL DEFAULT '{}',
            workflow_node_job_uuid TEXT NOT NULL,
            edge_command_uuid TEXT NOT NULL,
            job_access_token_hash TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            outcome TEXT NOT NULL CHECK (
                outcome IN ('succeeded', 'failed', 'canceled', 'timeout')
            ),
            return_info TEXT NOT NULL DEFAULT '{}',
            error_info TEXT NOT NULL DEFAULT '[]',
            committed_at TEXT NOT NULL,
            consumed_at TEXT,
            FOREIGN KEY(workflow_node_job_uuid)
                REFERENCES workflow_node_job(uuid) ON DELETE RESTRICT,
            UNIQUE(workflow_node_job_uuid),
            UNIQUE(workflow_node_job_uuid, idempotency_key)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_workflow_node_job_result_unconsumed
            ON workflow_node_job_result(committed_at, uuid)
            WHERE deleted_at IS NULL AND consumed_at IS NULL
        """,
        """
        CREATE TABLE IF NOT EXISTS workflow_node_job_feedback_history (
            uuid TEXT PRIMARY KEY,
            create_time TEXT NOT NULL,
            update_time TEXT NOT NULL,
            deleted_at TEXT,
            description TEXT,
            meta_data TEXT NOT NULL DEFAULT '{}',
            workflow_node_job_uuid TEXT NOT NULL,
            sequence INTEGER NOT NULL CHECK (sequence > 0),
            feedback_type TEXT NOT NULL,
            data TEXT NOT NULL DEFAULT '{}',
            observed_at TEXT NOT NULL,
            received_at TEXT NOT NULL,
            published_at TEXT,
            idempotency_key TEXT NOT NULL,
            FOREIGN KEY(workflow_node_job_uuid)
                REFERENCES workflow_node_job(uuid) ON DELETE RESTRICT,
            UNIQUE(workflow_node_job_uuid, sequence),
            UNIQUE(workflow_node_job_uuid, idempotency_key)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_workflow_node_job_feedback_timeline
            ON workflow_node_job_feedback_history(
                workflow_node_job_uuid, observed_at DESC, uuid DESC
            )
        """,
    )
    for statement in statements:
        connection.execute(statement)


def record_job_result(
    connection: sqlite3.Connection,
    *,
    job_row: sqlite3.Row,
    outcome: str,
    return_info: Mapping[str, Any],
    error_info: Sequence[Any],
) -> dict[str, Any]:
    """在调用方事务中幂等冻结一个作业终态结果。"""

    job_uuid = str(job_row["uuid"])
    idempotency_key = f"local-scheduler:{job_uuid}:outcome"
    edge_command_uuid = str(job_row["edge_command_uuid"] or job_uuid)
    existing = connection.execute(
        """
        SELECT * FROM workflow_node_job_result
        WHERE workflow_node_job_uuid = ? AND deleted_at IS NULL
        """,
        (job_uuid,),
    ).fetchone()
    normalized_return = dict(return_info)
    normalized_error = list(error_info)
    if existing is not None:
        if (
            existing["outcome"] != outcome
            or _load(existing["return_info"]) != normalized_return
            or _load(existing["error_info"]) != normalized_error
        ):
            raise StoreConflict(f"作业不可变结果冲突：{job_uuid}")
        return _result_row(existing)
    now = utc_now()
    result_uuid = str(uuid4())
    connection.execute(
        """
        INSERT INTO workflow_node_job_result(
            uuid, create_time, update_time, deleted_at, description, meta_data,
            workflow_node_job_uuid, edge_command_uuid, job_access_token_hash,
            idempotency_key, outcome, return_info, error_info, committed_at,
            consumed_at
        ) VALUES (?, ?, ?, NULL, NULL, '{}', ?, ?, ?, ?, ?, ?, ?, ?, NULL)
        """,
        (
            result_uuid,
            now,
            now,
            job_uuid,
            edge_command_uuid,
            str(job_row["job_access_token_hash"] or ""),
            idempotency_key,
            outcome,
            _json(normalized_return),
            _json(normalized_error),
            now,
        ),
    )
    row = connection.execute(
        "SELECT * FROM workflow_node_job_result WHERE uuid = ?",
        (result_uuid,),
    ).fetchone()
    assert row is not None
    return _result_row(row)


class JobEvidenceStore:
    """围绕 ``WorkflowStore`` 提供反馈提交与管理员查询。"""

    def __init__(self, store: WorkflowStore) -> None:
        self._store = store
        with store.transaction() as connection:
            ensure_job_evidence_schema(connection)

    def commit_feedback(
        self,
        *,
        job_uuid: str,
        sequence: int,
        feedback_type: str,
        data: Mapping[str, Any],
        observed_at: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """幂等提交单个有序反馈样本并更新作业最新摘要。"""

        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise StoreConflict("反馈 sequence 必须是正整数")
        normalized_type = feedback_type.strip() if isinstance(feedback_type, str) else ""
        normalized_key = idempotency_key.strip() if isinstance(idempotency_key, str) else ""
        if not normalized_type or not normalized_key or not isinstance(data, Mapping):
            raise StoreConflict("反馈类型、数据和幂等键不完整")
        if not isinstance(observed_at, str) or not observed_at.strip():
            raise StoreConflict("反馈 observed_at 不完整")
        normalized_data = dict(data)
        with self._store.transaction() as connection:
            job = connection.execute(
                """
                SELECT * FROM workflow_node_job
                WHERE uuid = ? AND deleted_at IS NULL
                """,
                (job_uuid,),
            ).fetchone()
            if job is None:
                raise StoreNotFound(f"workflow node job {job_uuid} not found")
            if str(job["status"]) in _TERMINAL_JOB_STATES:
                raise StoreConflict("作业终态后不能继续提交反馈")
            if str(job["status"]) not in {"dispatched", "running", "execution_unknown"}:
                raise StoreConflict("作业尚未派发，不能提交反馈")
            by_key = connection.execute(
                """
                SELECT * FROM workflow_node_job_feedback_history
                WHERE workflow_node_job_uuid = ? AND idempotency_key = ?
                  AND deleted_at IS NULL
                """,
                (job_uuid, normalized_key),
            ).fetchone()
            by_sequence = connection.execute(
                """
                SELECT * FROM workflow_node_job_feedback_history
                WHERE workflow_node_job_uuid = ? AND sequence = ?
                  AND deleted_at IS NULL
                """,
                (job_uuid, sequence),
            ).fetchone()
            existing = by_key or by_sequence
            if existing is not None:
                if (
                    int(existing["sequence"]) != sequence
                    or existing["feedback_type"] != normalized_type
                    or _load(existing["data"]) != normalized_data
                    or existing["observed_at"] != observed_at.strip()
                    or existing["idempotency_key"] != normalized_key
                ):
                    raise StoreConflict("反馈序号或幂等键对应的内容冲突")
                return {
                    "through_sequence": int(job["feedback_sequence"]),
                    "created": 0,
                }
            now = utc_now()
            connection.execute(
                """
                INSERT INTO workflow_node_job_feedback_history(
                    uuid, create_time, update_time, deleted_at, description,
                    meta_data, workflow_node_job_uuid, sequence, feedback_type,
                    data, observed_at, received_at, published_at, idempotency_key
                ) VALUES (?, ?, ?, NULL, NULL, '{}', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    now,
                    now,
                    job_uuid,
                    sequence,
                    normalized_type,
                    _json(normalized_data),
                    observed_at.strip(),
                    now,
                    now,
                    normalized_key,
                ),
            )
            through = max(int(job["feedback_sequence"]), sequence)
            connection.execute(
                """
                UPDATE workflow_node_job
                SET feedback_sequence = ?, feedback_data = ?,
                    status = CASE WHEN status = 'dispatched' THEN 'running' ELSE status END,
                    started_at = CASE
                        WHEN started_at IS NULL THEN ? ELSE started_at END,
                    update_time = ?
                WHERE uuid = ? AND deleted_at IS NULL
                """,
                (through, _json(normalized_data), now, now, job_uuid),
            )
            WorkflowStore._append_runtime_event(
                connection,
                task_uuid=str(job["workflow_task_uuid"]),
                job_uuid=job_uuid,
                kind="feedback_committed",
                from_status=str(job["status"]),
                to_status=("running" if job["status"] == "dispatched" else str(job["status"])),
                now=now,
            )
            return {"through_sequence": through, "created": 1}

    def list_feedback(self, *, job_uuid: str, page: int, page_size: int) -> dict[str, Any]:
        """按 sequence 升序分页返回作业反馈历史。"""

        offset = (page - 1) * page_size
        with self._store._lock:
            job = self._store._conn.execute(
                "SELECT 1 FROM workflow_node_job WHERE uuid = ? AND deleted_at IS NULL",
                (job_uuid,),
            ).fetchone()
            if job is None:
                raise StoreNotFound(f"workflow node job {job_uuid} not found")
            total = int(
                self._store._conn.execute(
                    """
                    SELECT COUNT(*) FROM workflow_node_job_feedback_history
                    WHERE workflow_node_job_uuid = ? AND deleted_at IS NULL
                    """,
                    (job_uuid,),
                ).fetchone()[0]
            )
            rows = self._store._conn.execute(
                """
                SELECT * FROM workflow_node_job_feedback_history
                WHERE workflow_node_job_uuid = ? AND deleted_at IS NULL
                ORDER BY sequence ASC, uuid ASC LIMIT ? OFFSET ?
                """,
                (job_uuid, page_size, offset),
            ).fetchall()
        return {
            "items": [_feedback_row(row) for row in rows],
            "has_more": page * page_size < total,
            "page": page,
            "page_size": page_size,
        }


def _result_row(row: sqlite3.Row) -> dict[str, Any]:
    """投影不可变作业结果。"""

    return {
        "uuid": row["uuid"],
        "workflow_node_job_uuid": row["workflow_node_job_uuid"],
        "edge_command_uuid": row["edge_command_uuid"],
        "idempotency_key": row["idempotency_key"],
        "outcome": row["outcome"],
        "return_info": _load(row["return_info"]),
        "error_info": _load(row["error_info"]),
        "committed_at": row["committed_at"],
    }


def _feedback_row(row: sqlite3.Row) -> dict[str, Any]:
    """投影单个反馈历史样本。"""

    return {
        "uuid": row["uuid"],
        "create_time": row["create_time"],
        "update_time": row["update_time"],
        "meta_data": _load(row["meta_data"]),
        "workflow_node_job_uuid": row["workflow_node_job_uuid"],
        "sequence": row["sequence"],
        "feedback_type": row["feedback_type"],
        "data": _load(row["data"]),
        "observed_at": row["observed_at"],
        "received_at": row["received_at"],
        "published_at": row["published_at"],
        "idempotency_key": row["idempotency_key"],
    }


__all__ = ["JobEvidenceStore", "ensure_job_evidence_schema", "record_job_result"]
