"""本地模式的持久作业执行占用（ExecutionLockLease）。

该模块只管理工作流数据库内的锁租约与等待顺序。库存数量、物料状态和库位
实体仍由库存库负责；两库之间没有被伪装成同一个原子事务。
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from unilabos.app.scheduler.resource_lock import conflicting_resource_lock_keys
from unilabos.workflow.json_codec import encode_json
from unilabos.workflow.store import StoreConflict, utc_now

_SCOPES = frozenset({"device", "material", "material_site"})


@dataclass(frozen=True, slots=True)
class ExecutionLockRequest:
    """一个作业需要全有或全无取得的执行锁。"""

    lock_key: str
    scope: str
    material_uuid: str | None = None
    site_uuid: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutionLockDecision:
    """持久锁准入结果及可展示的阻塞作业身份。"""

    acquired: bool
    blocking_task_uuid: str | None = None
    blocking_job_uuid: str | None = None


def normalize_execution_lock_requests(
    values: Sequence[Mapping[str, Any]] | None,
) -> tuple[ExecutionLockRequest, ...]:
    """校验、去重并稳定排序一组执行锁请求。"""

    normalized: dict[str, ExecutionLockRequest] = {}
    for value in values or ():
        if not isinstance(value, Mapping):
            raise StoreConflict("执行锁请求必须是对象")
        lock_key = str(value.get("lock_key") or "").strip()
        scope = str(value.get("scope") or "").strip()
        if not lock_key or scope not in _SCOPES:
            raise StoreConflict("执行锁请求缺少合法 lock_key 或 scope")
        request = ExecutionLockRequest(
            lock_key=lock_key,
            scope=scope,
            material_uuid=str(value.get("material_uuid") or "").strip() or None,
            site_uuid=str(value.get("site_uuid") or "").strip() or None,
        )
        previous = normalized.get(lock_key)
        if previous is not None and previous != request:
            raise StoreConflict(f"同一执行锁键的请求定义冲突：{lock_key}")
        normalized[lock_key] = request
    return tuple(normalized[key] for key in sorted(normalized))


def try_acquire_execution_locks(
    connection: sqlite3.Connection,
    *,
    task_uuid: str,
    job_uuid: str,
    requests: Sequence[Mapping[str, Any]] | None,
) -> ExecutionLockDecision:
    """在当前写事务中为一个待处理作业全有或全无取得执行锁。"""

    normalized = normalize_execution_lock_requests(requests)
    if not normalized:
        _clear_wait_reason(connection, task_uuid=task_uuid, job_uuid=job_uuid)
        return ExecutionLockDecision(acquired=True)

    active_rows = connection.execute(
        """
        SELECT * FROM execution_lock_lease
        WHERE deleted_at IS NULL
          AND state IN ('reserved', 'running', 'uncertain')
        ORDER BY acquired_at ASC, uuid ASC
        """
    ).fetchall()
    own_keys = {
        str(row["lock_key"])
        for row in active_rows
        if row["workflow_node_job_uuid"] == job_uuid
    }
    requested_keys = {request.lock_key for request in normalized}
    if own_keys:
        if own_keys != requested_keys:
            raise StoreConflict(f"作业持久执行锁集合发生变化：{job_uuid}")
        _release_waiters(connection, job_uuid=job_uuid)
        _clear_wait_reason(connection, task_uuid=task_uuid, job_uuid=job_uuid)
        return ExecutionLockDecision(acquired=True)

    enqueued_at = _ensure_waiters(
        connection,
        task_uuid=task_uuid,
        job_uuid=job_uuid,
        requests=normalized,
    )
    blockers = [
        row
        for row in active_rows
        if row["workflow_node_job_uuid"] != job_uuid
        and conflicting_resource_lock_keys(
            requested_keys,
            {str(row["lock_key"])},
        )
    ]
    if blockers:
        blocker = blockers[0]
        return _record_wait(
            connection,
            task_uuid=task_uuid,
            job_uuid=job_uuid,
            requests=normalized,
            blocking_task_uuid=str(blocker["workflow_task_uuid"]),
            blocking_job_uuid=str(blocker["workflow_node_job_uuid"]),
            waiting_since=enqueued_at,
        )

    current_task = connection.execute(
        "SELECT create_time FROM workflow_task WHERE uuid = ?",
        (task_uuid,),
    ).fetchone()
    if current_task is None:
        raise StoreConflict(f"执行锁所属任务不存在：{task_uuid}")
    current_order = (enqueued_at, str(current_task["create_time"]), task_uuid, job_uuid)
    older = _older_conflicting_waiter(
        connection,
        job_uuid=job_uuid,
        current_order=current_order,
        requested_keys=requested_keys,
    )
    if older is not None:
        return _record_wait(
            connection,
            task_uuid=task_uuid,
            job_uuid=job_uuid,
            requests=normalized,
            blocking_task_uuid=older[0],
            blocking_job_uuid=older[1],
            waiting_since=enqueued_at,
        )

    acquired_at = utc_now()
    for request in normalized:
        connection.execute(
            """
            INSERT INTO execution_lock_lease(
                uuid, create_time, update_time, deleted_at, description,
                meta_data, workflow_task_uuid, workflow_node_job_uuid,
                lock_key, scope, material_uuid, site_uuid, state,
                acquired_at, released_at
            ) VALUES (?, ?, ?, NULL, NULL, '{}', ?, ?, ?, ?, ?, ?,
                      'reserved', ?, NULL)
            """,
            (
                str(uuid4()),
                acquired_at,
                acquired_at,
                task_uuid,
                job_uuid,
                request.lock_key,
                request.scope,
                request.material_uuid,
                request.site_uuid,
                acquired_at,
            ),
        )
    _release_waiters(connection, job_uuid=job_uuid, released_at=acquired_at)
    _clear_wait_reason(connection, task_uuid=task_uuid, job_uuid=job_uuid)
    return ExecutionLockDecision(acquired=True)


def record_execution_lock_wait(
    connection: sqlite3.Connection,
    *,
    task_uuid: str,
    job_uuid: str,
    requests: Sequence[Mapping[str, Any]] | None,
    blocking_task_uuid: str | None = None,
    blocking_job_uuid: str | None = None,
) -> ExecutionLockDecision:
    """在物理执行尚被内存占用阻止时持久化同一组锁的等待顺序。"""

    normalized = normalize_execution_lock_requests(requests)
    if not normalized:
        _clear_wait_reason(connection, task_uuid=task_uuid, job_uuid=job_uuid)
        return ExecutionLockDecision(acquired=True)
    waiting_since = _ensure_waiters(
        connection,
        task_uuid=task_uuid,
        job_uuid=job_uuid,
        requests=normalized,
    )
    return _record_wait(
        connection,
        task_uuid=task_uuid,
        job_uuid=job_uuid,
        requests=normalized,
        blocking_task_uuid=blocking_task_uuid,
        blocking_job_uuid=blocking_job_uuid,
        waiting_since=waiting_since,
    )


def mark_execution_locks_running(
    connection: sqlite3.Connection,
    *,
    job_uuid: str,
    now: str,
) -> None:
    """把已被执行适配器接受的作业锁从 reserved 推进为 running。"""

    connection.execute(
        """
        UPDATE execution_lock_lease
        SET state = 'running', update_time = ?
        WHERE workflow_node_job_uuid = ? AND state = 'reserved'
          AND deleted_at IS NULL
        """,
        (now, job_uuid),
    )


def mark_execution_locks_uncertain(
    connection: sqlite3.Connection,
    *,
    job_uuid: str,
    now: str,
) -> None:
    """保留结果不明作业的全部锁，并标记为 uncertain。"""

    connection.execute(
        """
        UPDATE execution_lock_lease
        SET state = 'uncertain', update_time = ?
        WHERE workflow_node_job_uuid = ?
          AND state IN ('reserved', 'running')
          AND deleted_at IS NULL
        """,
        (now, job_uuid),
    )


def release_execution_locks(
    connection: sqlite3.Connection,
    *,
    job_uuid: str,
    now: str,
) -> None:
    """在明确结果提交后幂等释放一个作业的全部持久执行锁。"""

    connection.execute(
        """
        UPDATE execution_lock_lease
        SET state = 'released', released_at = ?, update_time = ?
        WHERE workflow_node_job_uuid = ?
          AND state IN ('reserved', 'running', 'uncertain')
          AND deleted_at IS NULL
        """,
        (now, now, job_uuid),
    )


def list_execution_locks(
    connection: sqlite3.Connection,
    *,
    job_uuid: str | None = None,
) -> list[dict[str, Any]]:
    """按作业可选过滤并返回稳定排序的执行锁事实。"""

    query = "SELECT * FROM execution_lock_lease WHERE deleted_at IS NULL"
    parameters: tuple[str, ...] = ()
    if job_uuid is not None:
        query += " AND workflow_node_job_uuid = ?"
        parameters = (job_uuid,)
    query += " ORDER BY create_time ASC, uuid ASC"
    return [dict(row) for row in connection.execute(query, parameters).fetchall()]


def _ensure_waiters(
    connection: sqlite3.Connection,
    *,
    task_uuid: str,
    job_uuid: str,
    requests: tuple[ExecutionLockRequest, ...],
) -> str:
    existing = connection.execute(
        """
        SELECT MIN(enqueued_at) AS enqueued_at
        FROM execution_lock_waiter
        WHERE workflow_node_job_uuid = ? AND state = 'waiting'
          AND deleted_at IS NULL
        """,
        (job_uuid,),
    ).fetchone()
    enqueued_at = str(existing["enqueued_at"] or utc_now())
    for request in requests:
        connection.execute(
            """
            INSERT OR IGNORE INTO execution_lock_waiter(
                uuid, create_time, update_time, deleted_at, description,
                meta_data, workflow_task_uuid, workflow_node_job_uuid,
                lock_key, scope, material_uuid, site_uuid, state,
                enqueued_at, released_at
            ) VALUES (?, ?, ?, NULL, NULL, '{}', ?, ?, ?, ?, ?, ?,
                      'waiting', ?, NULL)
            """,
            (
                str(uuid4()),
                enqueued_at,
                enqueued_at,
                task_uuid,
                job_uuid,
                request.lock_key,
                request.scope,
                request.material_uuid,
                request.site_uuid,
                enqueued_at,
            ),
        )
    return enqueued_at


def _older_conflicting_waiter(
    connection: sqlite3.Connection,
    *,
    job_uuid: str,
    current_order: tuple[str, str, str, str],
    requested_keys: set[str],
) -> tuple[str, str] | None:
    rows = connection.execute(
        """
        SELECT waiter.workflow_task_uuid, waiter.workflow_node_job_uuid,
               waiter.lock_key, waiter.enqueued_at,
               task.create_time AS task_create_time
        FROM execution_lock_waiter AS waiter
        JOIN workflow_node_job AS job
          ON job.uuid = waiter.workflow_node_job_uuid
         AND job.deleted_at IS NULL AND job.status = 'pending'
        JOIN workflow_task AS task
          ON task.uuid = waiter.workflow_task_uuid
         AND task.deleted_at IS NULL
         AND task.status IN ('pending', 'running')
        WHERE waiter.deleted_at IS NULL AND waiter.state = 'waiting'
          AND waiter.workflow_node_job_uuid <> ?
        ORDER BY waiter.enqueued_at ASC, task.create_time ASC,
                 waiter.workflow_task_uuid ASC,
                 waiter.workflow_node_job_uuid ASC, waiter.lock_key ASC
        """,
        (job_uuid,),
    ).fetchall()
    grouped: dict[tuple[str, str, str, str], set[str]] = defaultdict(set)
    for row in rows:
        order = (
            str(row["enqueued_at"]),
            str(row["task_create_time"]),
            str(row["workflow_task_uuid"]),
            str(row["workflow_node_job_uuid"]),
        )
        grouped[order].add(str(row["lock_key"]))
    for order in sorted(grouped):
        if order >= current_order:
            break
        if conflicting_resource_lock_keys(requested_keys, grouped[order]):
            return order[2], order[3]
    return None


def _record_wait(
    connection: sqlite3.Connection,
    *,
    task_uuid: str,
    job_uuid: str,
    requests: tuple[ExecutionLockRequest, ...],
    blocking_task_uuid: str | None,
    blocking_job_uuid: str | None,
    waiting_since: str,
) -> ExecutionLockDecision:
    reason = {
        "code": "operation_lease",
        "message": "执行资源正在被其他作业使用",
        "scopes": sorted({request.scope for request in requests}),
        "waiting_since": waiting_since,
    }
    if blocking_task_uuid is not None:
        reason["blocking_task_uuid"] = blocking_task_uuid
    if blocking_job_uuid is not None:
        reason["blocking_job_uuid"] = blocking_job_uuid
    reason_json = encode_json(reason, sort_keys=True).decode("utf-8")
    now = utc_now()
    connection.execute(
        "UPDATE workflow_node_job SET wait_reason = ?, update_time = ? WHERE uuid = ?",
        (reason_json, now, job_uuid),
    )
    connection.execute(
        "UPDATE workflow_task SET wait_reason = ?, update_time = ? WHERE uuid = ?",
        (reason_json, now, task_uuid),
    )
    return ExecutionLockDecision(
        acquired=False,
        blocking_task_uuid=blocking_task_uuid,
        blocking_job_uuid=blocking_job_uuid,
    )


def _release_waiters(
    connection: sqlite3.Connection,
    *,
    job_uuid: str,
    released_at: str | None = None,
) -> None:
    settled_at = released_at or utc_now()
    connection.execute(
        """
        UPDATE execution_lock_waiter
        SET state = 'released', released_at = ?, update_time = ?
        WHERE workflow_node_job_uuid = ? AND state = 'waiting'
          AND deleted_at IS NULL
        """,
        (settled_at, settled_at, job_uuid),
    )


def _clear_wait_reason(
    connection: sqlite3.Connection,
    *,
    task_uuid: str,
    job_uuid: str,
) -> None:
    now = utc_now()
    connection.execute(
        "UPDATE workflow_node_job SET wait_reason = '{}', update_time = ? WHERE uuid = ?",
        (now, job_uuid),
    )
    remaining = connection.execute(
        """
        SELECT wait_reason FROM workflow_node_job
        WHERE workflow_task_uuid = ? AND uuid <> ? AND status = 'pending'
          AND deleted_at IS NULL AND wait_reason <> '{}'
        ORDER BY update_time ASC, uuid ASC LIMIT 1
        """,
        (task_uuid, job_uuid),
    ).fetchone()
    task_wait_reason = str(remaining["wait_reason"]) if remaining is not None else "{}"
    connection.execute(
        "UPDATE workflow_task SET wait_reason = ?, update_time = ? WHERE uuid = ?",
        (task_wait_reason, now, task_uuid),
    )


__all__ = [
    "ExecutionLockDecision",
    "ExecutionLockRequest",
    "list_execution_locks",
    "mark_execution_locks_running",
    "mark_execution_locks_uncertain",
    "normalize_execution_lock_requests",
    "record_execution_lock_wait",
    "release_execution_locks",
    "try_acquire_execution_locks",
]
