"""本地模式的持久作业执行占用（ExecutionLockLease）。

该模块只管理工作流数据库内的锁租约与等待顺序。库存数量、物料状态和库位
实体仍由库存库负责；两库之间没有被伪装成同一个原子事务。
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from unilabos.app.scheduler.models import priority_weight
from unilabos.app.scheduler.ordering import (
    DEFAULT_AGING_INTERVAL_SECONDS,
    aged_priority,
)
from unilabos.app.scheduler.resource_lock import conflicting_resource_lock_keys
from unilabos.workflow.execution_claim import (
    ensure_execution_claim,
    next_fencing_token,
    required_active_claim,
)
from unilabos.workflow.json_codec import decode_json_bytes, encode_json
from unilabos.workflow.store import StoreConflict, utc_now

_SCOPES = frozenset({"device", "material", "material_site"})


@dataclass(frozen=True, slots=True)
class ExecutionLockRequest:
    """一个作业需要全有或全无取得的设备、物料或库位执行占用。"""

    lock_key: str
    scope: str
    material_uuid: str | None = None
    site_uuid: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutionLockDecision:
    """持久占用准入结果、Claim 身份、栅栏与阻塞作业身份。"""

    acquired: bool
    blocking_task_uuid: str | None = None
    blocking_job_uuid: str | None = None
    claim_uuid: str | None = None
    fencing_tokens: tuple[tuple[str, int], ...] = ()


def normalize_execution_lock_requests(
    values: Sequence[Mapping[str, Any]] | None,
) -> tuple[ExecutionLockRequest, ...]:
    """校验、去重并稳定排序一组执行锁请求。

    参数：``values`` 是调度器产生的完整占用声明。返回：稳定、不可变的请求
    元组。异常：字段缺失、访问区域键非规范小写 token、UUID 非法或同键定义
    冲突时抛 ``StoreConflict``。该函数只规范业务合同，不写数据库。
    """

    normalized: dict[str, ExecutionLockRequest] = {}
    for value in values or ():
        if not isinstance(value, Mapping):
            raise StoreConflict("执行锁请求必须是对象")
        lock_key = str(value.get("lock_key") or "").strip()
        scope = str(value.get("scope") or "").strip()
        if not lock_key or scope not in _SCOPES:
            raise StoreConflict("执行锁请求缺少合法 lock_key 或 scope")
        material_uuid = str(value.get("material_uuid") or "").strip() or None
        site_uuid = str(value.get("site_uuid") or "").strip() or None
        request = ExecutionLockRequest(
            lock_key=lock_key,
            scope=scope,
            material_uuid=material_uuid,
            site_uuid=site_uuid,
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
    claim_uuid: str | None = None,
    fencing_tokens: Mapping[str, int] | None = None,
    aging_interval_seconds: float = DEFAULT_AGING_INTERVAL_SECONDS,
) -> ExecutionLockDecision:
    """在当前写事务中投影一个作业全有或全无的库存执行占用。

    参数：工作流事务、Task/Job 身份、完整锁请求，以及可选库存 Claim UUID 与
    Fence 映射。返回：取得/等待决定。异常：库存 Permit 与锁集合不一致、同次
    尝试漂移或数据库冲突时抛 ``StoreConflict``。未提供 Permit 仅用于隔离干跑
    和遗留投影测试；物理派发必须由组合根传入库存签发凭据。
    """

    normalized = normalize_execution_lock_requests(requests)
    provided_fences = dict(fencing_tokens or {})
    normalized_keys = {request.lock_key for request in normalized}
    if claim_uuid is not None and set(provided_fences) != normalized_keys:
        raise StoreConflict("库存 Permit Fence 与完整执行锁集合不一致")
    if any(token <= 0 for token in provided_fences.values()):
        raise StoreConflict("库存 Permit Fence 必须是正整数")
    if not normalized:
        claim = ensure_execution_claim(
            connection,
            task_uuid=task_uuid,
            job_uuid=job_uuid,
            resource_keys=(),
            claim_uuid=claim_uuid,
        )
        _clear_wait_reason(connection, task_uuid=task_uuid, job_uuid=job_uuid)
        return ExecutionLockDecision(
            acquired=True,
            claim_uuid=str(claim["claim_uuid"]),
        )

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
        if _lease_acquired_by(row) == job_uuid
    }
    requested_keys = {request.lock_key for request in normalized}
    if own_keys:
        if own_keys != requested_keys:
            raise StoreConflict(f"作业持久执行锁集合发生变化：{job_uuid}")
        claim = required_active_claim(connection, job_uuid=job_uuid)
        if claim_uuid is not None and str(claim["claim_uuid"]) != claim_uuid:
            raise StoreConflict(f"持久 Claim 与库存 Permit 不一致：{job_uuid}")
        fencing_tokens = tuple(
            sorted(
                (
                    str(row["lock_key"]),
                    int(row["fencing_token"]),
                )
                for row in active_rows
                if _lease_acquired_by(row) == job_uuid
            )
        )
        _release_waiters(connection, job_uuid=job_uuid)
        _clear_wait_reason(connection, task_uuid=task_uuid, job_uuid=job_uuid)
        return ExecutionLockDecision(
            acquired=True,
            claim_uuid=str(claim["claim_uuid"]),
            fencing_tokens=fencing_tokens,
        )

    enqueued_at = _ensure_waiters(
        connection,
        task_uuid=task_uuid,
        job_uuid=job_uuid,
        requests=normalized,
    )
    blockers = [
        row
        for row in active_rows
        if _lease_acquired_by(row) != job_uuid
        and row["workflow_node_job_uuid"] != job_uuid
        and conflicting_resource_lock_keys(
            requested_keys,
            {str(row["lock_key"])},
        )
    ]
    device_keys = tuple(
        request.lock_key for request in normalized if request.scope == "device"
    )
    tenancy_blocker = None
    owned_tenancy_keys: set[str] = set()
    if device_keys:
        placeholders = ",".join("?" for _ in device_keys)
        owned_tenancy_keys = {
            str(row["device_lock_key"])
            for row in connection.execute(
                f"""
                SELECT device_lock_key FROM task_device_tenancy
                WHERE state = 'active'
                  AND device_lock_key IN ({placeholders})
                  AND workflow_task_uuid = ?
                """,
                (*device_keys, task_uuid),
            ).fetchall()
        }
        tenancy_blocker = connection.execute(
            f"""
            SELECT workflow_task_uuid, acquired_by_job_uuid
            FROM task_device_tenancy
            WHERE state = 'active'
              AND device_lock_key IN ({placeholders})
              AND workflow_task_uuid <> ?
            ORDER BY acquired_at, uuid LIMIT 1
            """,
            (*device_keys, task_uuid),
        ).fetchone()
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
    if tenancy_blocker is not None:
        return _record_wait(
            connection,
            task_uuid=task_uuid,
            job_uuid=job_uuid,
            requests=normalized,
            blocking_task_uuid=str(tenancy_blocker["workflow_task_uuid"]),
            blocking_job_uuid=str(tenancy_blocker["acquired_by_job_uuid"]),
            waiting_since=enqueued_at,
        )

    current_task = connection.execute(
        "SELECT create_time, priority FROM workflow_task WHERE uuid = ?",
        (task_uuid,),
    ).fetchone()
    if current_task is None:
        raise StoreConflict(f"执行锁所属任务不存在：{task_uuid}")
    older = _older_conflicting_waiter(
        connection,
        job_uuid=job_uuid,
        current_enqueued_at=enqueued_at,
        current_task_create_time=str(current_task["create_time"]),
        current_task_uuid=task_uuid,
        current_priority=priority_weight(current_task["priority"]),
        requested_keys=requested_keys - owned_tenancy_keys,
        aging_interval_seconds=aging_interval_seconds,
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
    claim = ensure_execution_claim(
        connection,
        task_uuid=task_uuid,
        job_uuid=job_uuid,
        resource_keys=tuple(sorted(requested_keys)),
        acquired_at=acquired_at,
        claim_uuid=claim_uuid,
    )
    claim_uuid = str(claim["claim_uuid"])
    fencing_tokens: list[tuple[str, int]] = []
    for request in normalized:
        fencing_token = provided_fences.get(request.lock_key)
        if fencing_token is None:
            fencing_token = next_fencing_token(
                connection,
                lock_key=request.lock_key,
                now=acquired_at,
            )
        fencing_tokens.append((request.lock_key, fencing_token))
        metadata = {
            "semantic_scope": request.scope,
            "acquired_by_job_uuid": job_uuid,
        }
        connection.execute(
            """
            INSERT INTO execution_lock_lease(
                uuid, create_time, update_time, deleted_at, description,
                meta_data, workflow_task_uuid, workflow_node_job_uuid,
                lock_key, scope, material_uuid, site_uuid, state,
                acquired_at, released_at, claim_uuid, fencing_token
            ) VALUES (?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?, ?,
                      'reserved', ?, NULL, ?, ?)
            """,
            (
                str(uuid4()),
                acquired_at,
                acquired_at,
                encode_json(metadata, sort_keys=True).decode("utf-8"),
                task_uuid,
                job_uuid,
                request.lock_key,
                request.scope,
                request.material_uuid,
                request.site_uuid,
                acquired_at,
                claim_uuid,
                fencing_token,
            ),
        )
    _release_waiters(connection, job_uuid=job_uuid, released_at=acquired_at)
    _clear_wait_reason(connection, task_uuid=task_uuid, job_uuid=job_uuid)
    return ExecutionLockDecision(
        acquired=True,
        claim_uuid=claim_uuid,
        fencing_tokens=tuple(fencing_tokens),
    )


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
    connection.execute(
        """
        UPDATE execution_claim
        SET state = 'running', update_time = ?
        WHERE workflow_node_job_uuid = ? AND state = 'reserved'
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
    connection.execute(
        """
        UPDATE execution_claim
        SET state = 'uncertain', update_time = ?
        WHERE workflow_node_job_uuid = ?
          AND state IN ('reserved', 'running')
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
    connection.execute(
        """
        UPDATE execution_claim
        SET state = 'released', released_at = ?, update_time = ?
        WHERE workflow_node_job_uuid = ?
          AND state IN ('reserved', 'running', 'uncertain')
        """,
        (now, now, job_uuid),
    )


def release_task_execution_locks(
    connection: sqlite3.Connection,
    *,
    task_uuid: str,
    now: str,
) -> None:
    """在物理清理完成时回收一个任务残留的全部占用与等待事实。

    参数：数据库连接、工作流任务（WorkflowTask）身份和统一结算时间。返回无。
    异常：SQLite 写入错误原样传播。该操作只应在调用方已证明没有在途物理作业
    后执行；它覆盖由后续释放作业持有的访问区域长锁，并保持幂等。
    """

    connection.execute(
        """
        UPDATE execution_lock_lease
        SET state = 'released', released_at = ?, update_time = ?
        WHERE workflow_task_uuid = ?
          AND state IN ('reserved', 'running', 'uncertain')
          AND deleted_at IS NULL
        """,
        (now, now, task_uuid),
    )
    connection.execute(
        """
        UPDATE execution_claim
        SET state = 'released', released_at = ?, update_time = ?
        WHERE workflow_task_uuid = ?
          AND state IN ('reserved', 'running', 'uncertain')
        """,
        (now, now, task_uuid),
    )
    connection.execute(
        """
        UPDATE execution_lock_waiter
        SET state = 'released', released_at = ?, update_time = ?
        WHERE workflow_task_uuid = ? AND state = 'waiting'
          AND deleted_at IS NULL
        """,
        (now, now, task_uuid),
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
    result: list[dict[str, Any]] = []
    for row in connection.execute(query, parameters).fetchall():
        item = dict(row)
        metadata = _lease_metadata(row)
        semantic_scope = str(metadata.get("semantic_scope") or "").strip()
        if semantic_scope in _SCOPES:
            item["scope"] = semantic_scope
        result.append(item)
    return result


def _ensure_waiters(
    connection: sqlite3.Connection,
    *,
    task_uuid: str,
    job_uuid: str,
    requests: tuple[ExecutionLockRequest, ...],
) -> str:
    """为一组未取得的执行锁建立稳定排队事实。

    参数：工作流事务、Task/Job 身份与规范锁请求。返回：该 Job 首次排队时间；
    重放复用相同时间并用唯一约束避免重复 waiter。异常：数据库错误原样传播，
    所有写入随调用方事务提交或回滚。
    """

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
        metadata = {
            "semantic_scope": request.scope,
        }
        connection.execute(
            """
            INSERT OR IGNORE INTO execution_lock_waiter(
                uuid, create_time, update_time, deleted_at, description,
                meta_data, workflow_task_uuid, workflow_node_job_uuid,
                lock_key, scope, material_uuid, site_uuid, state,
                enqueued_at, released_at
            ) VALUES (?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?, ?,
                      'waiting', ?, NULL)
            """,
            (
                str(uuid4()),
                enqueued_at,
                enqueued_at,
                encode_json(metadata, sort_keys=True).decode("utf-8"),
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
    current_enqueued_at: str,
    current_task_create_time: str,
    current_task_uuid: str,
    current_priority: float,
    requested_keys: set[str],
    aging_interval_seconds: float,
) -> tuple[str, str] | None:
    """查找有效优先级领先且与尚未托管资源冲突的等待作业。

    参数：``connection`` 是工作流权威事务；``job_uuid`` 是当前作业身份；
    当前等待、任务创建、稳定身份和基础优先级共同构成排序；``requested_keys``
    已排除当前任务长期托管的设备键。返回：领先的阻塞任务和作业 UUID，或无冲突
    时返回 ``None``。异常：时间或数据库事实非法时原样传播。长期托管设备必须让
    所属任务优先完成操作或卸载，不能被外部等待者反向阻塞。
    """

    rows = connection.execute(
        """
        SELECT waiter.workflow_task_uuid, waiter.workflow_node_job_uuid,
               waiter.lock_key, waiter.enqueued_at,
               task.create_time AS task_create_time, task.priority
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
    grouped: dict[tuple[str, str, str, str, float], set[str]] = defaultdict(set)
    for row in rows:
        order = (
            str(row["enqueued_at"]),
            str(row["task_create_time"]),
            str(row["workflow_task_uuid"]),
            str(row["workflow_node_job_uuid"]),
            priority_weight(row["priority"]),
        )
        grouped[order].add(str(row["lock_key"]))
    now_seconds = _rfc3339_seconds(utc_now())
    current_rank = _waiter_rank(
        priority=current_priority,
        enqueued_at=current_enqueued_at,
        task_create_time=current_task_create_time,
        task_uuid=current_task_uuid,
        job_uuid=job_uuid,
        now_seconds=now_seconds,
        aging_interval_seconds=aging_interval_seconds,
    )
    candidates: list[tuple[tuple[float, str, str, str, str], str, str]] = []
    for order, lock_keys in grouped.items():
        enqueued_at, task_create_time, task_uuid, waiter_job_uuid, priority = order
        if not conflicting_resource_lock_keys(requested_keys, lock_keys):
            continue
        rank = _waiter_rank(
            priority=priority,
            enqueued_at=enqueued_at,
            task_create_time=task_create_time,
            task_uuid=task_uuid,
            job_uuid=waiter_job_uuid,
            now_seconds=now_seconds,
            aging_interval_seconds=aging_interval_seconds,
        )
        if rank < current_rank:
            candidates.append((rank, task_uuid, waiter_job_uuid))
    if candidates:
        _, blocking_task_uuid, blocking_job_uuid = min(candidates)
        return blocking_task_uuid, blocking_job_uuid
    return None


def _waiter_rank(
    *,
    priority: float,
    enqueued_at: str,
    task_create_time: str,
    task_uuid: str,
    job_uuid: str,
    now_seconds: float,
    aging_interval_seconds: float,
) -> tuple[float, str, str, str, str]:
    """把持久等待事实转换为与内存调度一致的稳定升序键。"""

    effective = aged_priority(
        priority,
        waited_seconds=now_seconds - _rfc3339_seconds(enqueued_at),
        aging_interval_seconds=aging_interval_seconds,
    )
    return (
        -effective,
        enqueued_at,
        task_create_time,
        task_uuid,
        job_uuid,
    )


def _rfc3339_seconds(value: str) -> float:
    """解析带时区 RFC3339 时间并返回 UTC epoch 秒。"""

    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("持久等待时间缺少时区")
    return parsed.astimezone(timezone.utc).timestamp()


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
        "resources": [_wait_resource(request) for request in requests],
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


def _wait_resource(request: ExecutionLockRequest) -> dict[str, str]:
    """把内部锁键转换为可展示但不依赖锁键语法的等待资源。"""

    resource = {"scope": request.scope}
    if request.scope == "device":
        device_prefix = "/devices/"
        if request.lock_key.startswith(device_prefix):
            resource["device_id"] = request.lock_key.removeprefix(device_prefix)
        return resource
    if request.material_uuid is not None:
        resource["material_uuid"] = request.material_uuid
    if request.scope == "material_site" and request.site_uuid is not None:
        resource["site_uuid"] = request.site_uuid
    return resource


def _lease_metadata(row: sqlite3.Row) -> dict[str, Any]:
    """安全读取既有租约元数据；损坏事实按空对象保守处理。"""

    raw = row["meta_data"]
    try:
        decoded = decode_json_bytes(str(raw or "{}").encode("utf-8"))
    except (TypeError, ValueError, UnicodeError):
        return {}
    return dict(decoded) if isinstance(decoded, Mapping) else {}


def _lease_acquired_by(row: sqlite3.Row) -> str:
    """返回真正越过派发边界并取得租约的入口作业身份。"""

    metadata = _lease_metadata(row)
    return str(
        metadata.get("acquired_by_job_uuid") or row["workflow_node_job_uuid"]
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
    "release_task_execution_locks",
    "try_acquire_execution_locks",
]
