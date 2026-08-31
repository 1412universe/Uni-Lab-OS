"""作业执行声明（Claim）与资源栅栏令牌的持久化规则。"""

from __future__ import annotations

import sqlite3
from typing import Any
from uuid import uuid4

from unilabos.workflow.json_codec import decode_json_bytes, encode_json
from unilabos.workflow.store import StoreConflict, utc_now


def get_execution_claim(
    connection: sqlite3.Connection,
    *,
    job_uuid: str,
) -> dict[str, Any] | None:
    """读取作业当前尝试的稳定 Claim 与每个资源的栅栏令牌。

    参数：``connection`` 是工作流读事务；``job_uuid`` 是稳定作业身份。返回：
    不存在时为 ``None``，否则包含 Claim 状态和按锁键排序的 fences。异常：持久
    resource_keys 损坏时抛解码错误，关闭式阻止派发。
    """

    row = connection.execute(
        """
        SELECT * FROM execution_claim
        WHERE workflow_node_job_uuid = ?
        ORDER BY attempt DESC LIMIT 1
        """,
        (job_uuid,),
    ).fetchone()
    if row is None:
        return None
    fences = connection.execute(
        """
        SELECT lock_key, fencing_token FROM execution_lock_lease
        WHERE claim_uuid = ? AND deleted_at IS NULL
        ORDER BY lock_key ASC
        """,
        (row["claim_uuid"],),
    ).fetchall()
    return {
        "claim_uuid": row["claim_uuid"],
        "workflow_task_uuid": row["workflow_task_uuid"],
        "workflow_node_job_uuid": row["workflow_node_job_uuid"],
        "attempt": int(row["attempt"]),
        "resource_keys": decode_json_bytes(str(row["resource_keys"]).encode("utf-8")),
        "state": row["state"],
        "acquired_at": row["acquired_at"],
        "released_at": row["released_at"],
        "fences": [
            {
                "lock_key": fence["lock_key"],
                "fencing_token": int(fence["fencing_token"]),
            }
            for fence in fences
        ],
    }


def ensure_execution_claim(
    connection: sqlite3.Connection,
    *,
    task_uuid: str,
    job_uuid: str,
    resource_keys: tuple[str, ...],
    acquired_at: str | None = None,
    claim_uuid: str | None = None,
) -> sqlite3.Row:
    """为一个 Job 尝试幂等创建稳定 Claim，且禁止资源集合漂移。

    参数：工作流写事务、任务/作业身份、完整资源键集合、可选取得时间和库存
    权威已签发的 ``claim_uuid``。返回：已存在或新建的审计 Claim 行。异常：作业
    不存在、同一次尝试的身份/资源集合变化或已释放 Claim 被重用时抛
    ``StoreConflict``；SQLite 写入错误原样传播。
    """

    job = connection.execute(
        """
        SELECT attempt FROM workflow_node_job
        WHERE uuid = ? AND workflow_task_uuid = ? AND deleted_at IS NULL
        """,
        (job_uuid, task_uuid),
    ).fetchone()
    if job is None:
        raise StoreConflict(f"Claim 所属作业不存在：{job_uuid}")
    attempt = int(job["attempt"])
    resource_json = encode_json(list(resource_keys), sort_keys=True).decode("utf-8")
    existing = connection.execute(
        """
        SELECT * FROM execution_claim
        WHERE workflow_node_job_uuid = ? AND attempt = ?
        """,
        (job_uuid, attempt),
    ).fetchone()
    if existing is not None:
        if claim_uuid is not None and str(existing["claim_uuid"]) != claim_uuid:
            raise StoreConflict(f"Claim 身份与库存 Permit 不一致：{job_uuid}")
        if str(existing["resource_keys"]) != resource_json:
            raise StoreConflict(f"Claim 完整资源集合发生变化：{job_uuid}")
        if existing["state"] == "released":
            raise StoreConflict(f"已释放 Claim 不能重新派发：{job_uuid}")
        return existing
    now = acquired_at or utc_now()
    resolved_claim_uuid = claim_uuid or str(uuid4())
    connection.execute(
        """
        INSERT INTO execution_claim(
            claim_uuid, create_time, update_time, workflow_task_uuid,
            workflow_node_job_uuid, attempt, resource_keys, state,
            acquired_at, released_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'reserved', ?, NULL)
        """,
        (
            resolved_claim_uuid,
            now,
            now,
            task_uuid,
            job_uuid,
            attempt,
            resource_json,
            now,
        ),
    )
    row = connection.execute(
        "SELECT * FROM execution_claim WHERE claim_uuid = ?",
        (resolved_claim_uuid,),
    ).fetchone()
    assert row is not None
    return row


def required_active_claim(
    connection: sqlite3.Connection,
    *,
    job_uuid: str,
) -> sqlite3.Row:
    """读取作业未释放 Claim，缺失时关闭式失败。

    参数：工作流读事务和作业身份。返回：最新的 reserved、running 或 uncertain
    Claim 行。异常：缺失说明锁占用事实损坏，抛 ``StoreConflict``。
    """

    row = connection.execute(
        """
        SELECT * FROM execution_claim
        WHERE workflow_node_job_uuid = ?
          AND state IN ('reserved', 'running', 'uncertain')
        ORDER BY attempt DESC LIMIT 1
        """,
        (job_uuid,),
    ).fetchone()
    if row is None:
        raise StoreConflict(f"作业执行租约缺少 Claim：{job_uuid}")
    return row


def list_active_execution_claim_uuids(
    connection: sqlite3.Connection,
) -> tuple[str, ...]:
    """列出工作流库仍承担安全意义的全部 Claim 身份。

    参数：工作流读事务。返回：按取得时间与 UUID 稳定排序的 reserved、running、
    uncertain Claim UUID。异常：SQLite 读取错误原样传播。
    """

    rows = connection.execute(
        """
        SELECT claim_uuid FROM execution_claim
        WHERE state IN ('reserved', 'running', 'uncertain')
        ORDER BY acquired_at, claim_uuid
        """
    ).fetchall()
    return tuple(str(row["claim_uuid"]) for row in rows)


def next_fencing_token(
    connection: sqlite3.Connection,
    *,
    lock_key: str,
    now: str,
) -> int:
    """在当前事务内为单个资源分配严格递增的栅栏令牌。

    参数：工作流写事务、规范资源锁键和统一分配时间。返回：该资源新分配的正
    整数令牌。异常：SQLite 写入错误原样传播；调用方必须让本操作与租约插入
    位于同一事务中，避免令牌已发布但租约未落库。
    """

    connection.execute(
        """
        INSERT INTO execution_fence_counter(lock_key, last_fencing_token, update_time)
        VALUES (?, 1, ?)
        ON CONFLICT(lock_key) DO UPDATE SET
            last_fencing_token = last_fencing_token + 1,
            update_time = excluded.update_time
        """,
        (lock_key, now),
    )
    row = connection.execute(
        """
        SELECT last_fencing_token FROM execution_fence_counter
        WHERE lock_key = ?
        """,
        (lock_key,),
    ).fetchone()
    assert row is not None
    return int(row["last_fencing_token"])


__all__ = [
    "ensure_execution_claim",
    "get_execution_claim",
    "list_active_execution_claim_uuids",
    "next_fencing_token",
    "required_active_claim",
]
