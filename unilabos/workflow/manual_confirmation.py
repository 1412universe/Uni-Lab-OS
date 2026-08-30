"""人工确认（ManualConfirmation）的持久事实与幂等决策。"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from unilabos.workflow.event_writer import append_frontend_event
from unilabos.workflow.json_codec import decode_json_bytes, encode_json
from unilabos.workflow.store import StoreConflict, StoreNotFound, WorkflowStore, utc_now

_FINAL_STATUSES = {"approved", "rejected", "timed_out", "canceled"}


def _json(value: Any) -> str:
    return encode_json(value, sort_keys=True).decode("utf-8")


def _load(value: str) -> Any:
    return decode_json_bytes(value.encode("utf-8"))


def ensure_manual_confirmation_schema(connection: sqlite3.Connection) -> None:
    """幂等创建人工确认表及查询索引。"""

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_manual_confirmation (
            uuid TEXT PRIMARY KEY,
            create_time TEXT NOT NULL,
            update_time TEXT NOT NULL,
            deleted_at TEXT,
            description TEXT,
            meta_data TEXT NOT NULL DEFAULT '{}',
            workflow_task_uuid TEXT NOT NULL,
            workflow_node_job_uuid TEXT NOT NULL,
            status TEXT NOT NULL CHECK (
                status IN ('pending', 'approved', 'rejected', 'timed_out', 'canceled')
            ),
            assignee_user_ids TEXT NOT NULL DEFAULT '[]',
            confirmed_by TEXT,
            comment TEXT,
            param TEXT NOT NULL DEFAULT '{}',
            decision_idempotency_key TEXT,
            opened_at TEXT NOT NULL,
            deadline_at TEXT,
            decided_at TEXT,
            FOREIGN KEY(workflow_task_uuid)
                REFERENCES workflow_task(uuid) ON DELETE RESTRICT,
            FOREIGN KEY(workflow_node_job_uuid)
                REFERENCES workflow_node_job(uuid) ON DELETE RESTRICT,
            UNIQUE(workflow_node_job_uuid)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_manual_confirmation_task
        ON workflow_manual_confirmation(workflow_task_uuid, opened_at DESC, uuid DESC)
        WHERE deleted_at IS NULL
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_manual_confirmation_pending_deadline
        ON workflow_manual_confirmation(deadline_at, uuid)
        WHERE deleted_at IS NULL AND status = 'pending'
        """
    )


def open_manual_confirmation(
    connection: sqlite3.Connection,
    *,
    job_row: sqlite3.Row,
    param: Mapping[str, Any],
) -> dict[str, Any]:
    """在作业派发事务中幂等开启一次人工确认。"""

    job_uuid = str(job_row["uuid"])
    existing = connection.execute(
        """
        SELECT * FROM workflow_manual_confirmation
        WHERE workflow_node_job_uuid = ? AND deleted_at IS NULL
        """,
        (job_uuid,),
    ).fetchone()
    if existing is not None:
        if _load(existing["param"]) != dict(param):
            raise StoreConflict(f"人工确认冻结参数冲突：{job_uuid}")
        return _row(existing)

    assignees = param.get("assignee_user_ids", [])
    if not isinstance(assignees, list) or any(
        not isinstance(value, str) or not value.strip() for value in assignees
    ):
        raise StoreConflict("人工确认 assignee_user_ids 必须是非空字符串数组")
    normalized_assignees = list(dict.fromkeys(value.strip() for value in assignees))
    timeout = param.get("timeout_seconds", 0)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout < 0:
        raise StoreConflict("人工确认 timeout_seconds 必须是非负数")
    opened_at = utc_now()
    deadline_at = None
    if timeout > 0:
        deadline_at = (
            datetime.now(timezone.utc) + timedelta(seconds=float(timeout))
        ).isoformat().replace("+00:00", "Z")
    confirmation_uuid = str(uuid4())
    connection.execute(
        """
        INSERT INTO workflow_manual_confirmation(
            uuid, create_time, update_time, deleted_at, description, meta_data,
            workflow_task_uuid, workflow_node_job_uuid, status,
            assignee_user_ids, confirmed_by, comment, param,
            decision_idempotency_key, opened_at, deadline_at, decided_at
        ) VALUES (?, ?, ?, NULL, NULL, '{}', ?, ?, 'pending', ?, NULL, NULL,
                  ?, NULL, ?, ?, NULL)
        """,
        (
            confirmation_uuid,
            opened_at,
            opened_at,
            str(job_row["workflow_task_uuid"]),
            job_uuid,
            _json(normalized_assignees),
            _json(dict(param)),
            opened_at,
            deadline_at,
        ),
    )
    row = connection.execute(
        "SELECT * FROM workflow_manual_confirmation WHERE uuid = ?",
        (confirmation_uuid,),
    ).fetchone()
    assert row is not None
    return _row(row)


def close_pending_manual_confirmation(
    connection: sqlite3.Connection,
    *,
    job_uuid: str,
    status: str,
    decided_at: str,
) -> None:
    """随 Job 终态原子关闭尚未决定的人工确认。"""

    if status not in {"timed_out", "canceled"}:
        raise StoreConflict("人工确认只能按 timed_out 或 canceled 自动关闭")
    connection.execute(
        """
        UPDATE workflow_manual_confirmation
        SET status = ?, decided_at = ?, update_time = ?
        WHERE workflow_node_job_uuid = ? AND deleted_at IS NULL
          AND status = 'pending'
        """,
        (status, decided_at, decided_at, job_uuid),
    )


class ManualConfirmationStore:
    """人工确认查询和决定的单一事务边界。"""

    def __init__(self, store: WorkflowStore) -> None:
        self._store = store
        with store.transaction() as connection:
            ensure_manual_confirmation_schema(connection)

    def get(self, confirmation_uuid: str) -> dict[str, Any]:
        with self._store.read() as connection:
            row = connection.execute(
                """
                SELECT * FROM workflow_manual_confirmation
                WHERE uuid = ? AND deleted_at IS NULL
                """,
                (confirmation_uuid,),
            ).fetchone()
        if row is None:
            raise StoreNotFound(f"manual confirmation {confirmation_uuid} not found")
        return _row(row)

    def get_by_job(self, job_uuid: str) -> dict[str, Any]:
        """按稳定 Job 身份读取唯一人工确认。"""

        with self._store.read() as connection:
            row = connection.execute(
                """
                SELECT * FROM workflow_manual_confirmation
                WHERE workflow_node_job_uuid = ? AND deleted_at IS NULL
                """,
                (job_uuid,),
            ).fetchone()
        if row is None:
            raise StoreNotFound(f"manual confirmation for job {job_uuid} not found")
        return _row(row)

    def list_by_task(self, task_uuid: str) -> list[dict[str, Any]]:
        with self._store.read() as connection:
            task = connection.execute(
                "SELECT 1 FROM workflow_task WHERE uuid = ? AND deleted_at IS NULL",
                (task_uuid,),
            ).fetchone()
            if task is None:
                raise StoreNotFound(f"workflow task {task_uuid} not found")
            rows = connection.execute(
                """
                SELECT * FROM workflow_manual_confirmation
                WHERE workflow_task_uuid = ? AND deleted_at IS NULL
                ORDER BY opened_at DESC, uuid DESC
                """,
                (task_uuid,),
            ).fetchall()
        return [_row(row) for row in rows]

    def decide(
        self,
        confirmation_uuid: str,
        *,
        action: str,
        confirmed_by: str,
        comment: str | None,
        idempotency_key: str,
        param: Mapping[str, Any] | None,
    ) -> tuple[dict[str, Any], bool]:
        """按幂等键批准或拒绝待处理确认，并返回是否首次决定。"""

        normalized_action = action.strip().lower()
        normalized_actor = confirmed_by.strip()
        normalized_key = idempotency_key.strip()
        if normalized_action not in {"approve", "reject"}:
            raise StoreConflict("人工确认 action 必须是 approve 或 reject")
        if not normalized_actor or not normalized_key:
            raise StoreConflict("confirmed_by 和 idempotency_key 不能为空")
        if normalized_action == "reject" and param is not None:
            raise StoreConflict("拒绝人工确认时不能提交 param")
        with self._store.transaction() as connection:
            row = connection.execute(
                """
                SELECT * FROM workflow_manual_confirmation
                WHERE uuid = ? AND deleted_at IS NULL
                """,
                (confirmation_uuid,),
            ).fetchone()
            if row is None:
                raise StoreNotFound(
                    f"manual confirmation {confirmation_uuid} not found"
                )
            target = "approved" if normalized_action == "approve" else "rejected"
            if row["status"] in _FINAL_STATUSES:
                if (
                    row["status"] == target
                    and row["decision_idempotency_key"] == normalized_key
                ):
                    return _row(row), False
                raise StoreConflict("人工确认已经由另一项决定关闭")
            now = utc_now()
            if row["deadline_at"] is not None and now >= str(row["deadline_at"]):
                connection.execute(
                    """
                    UPDATE workflow_manual_confirmation
                    SET status = 'timed_out', decided_at = ?, update_time = ?
                    WHERE uuid = ? AND status = 'pending'
                    """,
                    (now, now, confirmation_uuid),
                )
                raise StoreConflict("人工确认已经超时")
            approved_param = dict(param) if param is not None else _load(row["param"])
            connection.execute(
                """
                UPDATE workflow_manual_confirmation
                SET status = ?, confirmed_by = ?, comment = ?, param = ?,
                    decision_idempotency_key = ?, decided_at = ?, update_time = ?
                WHERE uuid = ? AND status = 'pending'
                """,
                (
                    target,
                    normalized_actor,
                    comment.strip() if isinstance(comment, str) and comment.strip() else None,
                    _json(approved_param),
                    normalized_key,
                    now,
                    now,
                    confirmation_uuid,
                ),
            )
            decided = connection.execute(
                "SELECT * FROM workflow_manual_confirmation WHERE uuid = ?",
                (confirmation_uuid,),
            ).fetchone()
            assert decided is not None
            append_frontend_event(
                connection,
                event="workflow.runtime.changed",
                data={"workflow_task_uuid": str(row["workflow_task_uuid"])},
                now=now,
            )
            return _row(decided), True


def _row(row: sqlite3.Row) -> dict[str, Any]:
    result = {
        "uuid": row["uuid"],
        "create_time": row["create_time"],
        "update_time": row["update_time"],
        "meta_data": _load(row["meta_data"]),
        "workflow_task_uuid": row["workflow_task_uuid"],
        "workflow_node_job_uuid": row["workflow_node_job_uuid"],
        "status": row["status"],
        "assignee_user_ids": _load(row["assignee_user_ids"]),
        "param": _load(row["param"]),
        "opened_at": row["opened_at"],
    }
    for field in ("description", "confirmed_by", "comment", "deadline_at", "decided_at"):
        if row[field] is not None:
            result[field] = row[field]
    return result


__all__ = [
    "ManualConfirmationStore",
    "close_pending_manual_confirmation",
    "ensure_manual_confirmation_schema",
    "open_manual_confirmation",
]
