"""设备执行进程重启后的工作流任务（WorkflowTask）失败收敛。"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from typing import Any

from unilabos.workflow.event_writer import (
    append_frontend_event,
    append_runtime_event,
)
from unilabos.workflow.job_evidence import record_job_result
from unilabos.workflow.json_codec import encode_json
from unilabos.workflow.manual_confirmation import close_pending_manual_confirmation
from unilabos.workflow.station_status_projection import (
    append_job_state_event,
    append_task_state_event,
)
from unilabos.workflow.store import StoreConflict, StoreNotFound, utc_now

EXECUTION_PROCESS_RESTARTED = "execution_process_restarted"
_IN_FLIGHT_JOB_STATES = frozenset({"dispatched", "running", "cancel_requested"})
_TERMINAL_JOB_STATES = frozenset(
    {"succeeded", "failed", "skipped", "canceled", "timeout"}
)


def fail_task_after_execution_process_restart(
    connection: sqlite3.Connection,
    *,
    task_uuid: str,
    now: str | None = None,
) -> bool:
    """原子终结仍有设备侧在途作业的工作流任务。

    参数：``connection`` 是调用方持有的工作流写事务；``task_uuid`` 是稳定任务
    UUID；``now`` 是可选统一结算时间。返回：任务已经进入 ``running`` 或
    ``canceling`` 并完成失败收敛时为 ``True``；仍是 ``pending`` 时为 ``False``，
    允许以原身份恢复。异常：任务或作业缺失时抛
    ``StoreNotFound``/``StoreConflict``，SQLite 写入错误原样传播。

    该事务只确定业务失败，不声称设备已经停止。仍可能对应物理动作的占用转为
    ``uncertain``，任务清理进入 ``requires_attention``；若崩溃发生在两个作业
    之间且没有物理在途事实，则任务同样失败，但清理可直接为 ``settled``。
    """

    task = connection.execute(
        "SELECT * FROM workflow_task WHERE uuid = ? AND deleted_at IS NULL",
        (task_uuid,),
    ).fetchone()
    if task is None:
        raise StoreNotFound(f"工作流任务不存在：{task_uuid}")
    jobs = connection.execute(
        """
        SELECT * FROM workflow_node_job
        WHERE workflow_task_uuid = ? AND deleted_at IS NULL
        ORDER BY topological_index ASC, uuid ASC
        """,
        (task_uuid,),
    ).fetchall()
    if not jobs:
        raise StoreConflict(f"工作流任务没有可恢复作业：{task_uuid}")
    if str(task["status"]) not in {"running", "canceling"}:
        return False
    in_flight = [row for row in jobs if str(row["status"]) in _IN_FLIGHT_JOB_STATES]

    failed_at = now or utc_now()
    failure_details = [
        {
            "code": EXECUTION_PROCESS_RESTARTED,
            "message": "设备执行进程重启，无法继续推进原工作流任务",
        }
    ]
    failure_info = _json(failure_details)
    skipped_details = [
        {
            "code": "upstream_execution_process_restarted",
            "message": "工作流任务已因设备执行进程重启终止，节点未执行",
        }
    ]
    skipped_info = _json(skipped_details)

    for job in jobs:
        job_uuid = str(job["uuid"])
        status = str(job["status"])
        if status in _IN_FLIGHT_JOB_STATES:
            physical_may_be_in_flight = (
                str(job["executor_kind"]) != "manual_confirm"
                or _manual_confirmation_was_approved(
                    connection,
                    job_uuid=job_uuid,
                )
            )
            changed = connection.execute(
                """
                UPDATE workflow_node_job
                SET status = 'failed', error_info = ?, wait_reason = '{}',
                    uncertainty_reason = ?, cancel_ack_deadline_at = NULL,
                    cancel_complete_deadline_at = NULL, finished_at = ?,
                    update_time = ?
                WHERE uuid = ?
                  AND status IN (
                      'dispatched', 'running', 'cancel_requested'
                  )
                  AND deleted_at IS NULL
                """,
                (
                    failure_info,
                    (
                        EXECUTION_PROCESS_RESTARTED
                        if physical_may_be_in_flight
                        else None
                    ),
                    failed_at,
                    failed_at,
                    job_uuid,
                ),
            ).rowcount
            if changed != 1:
                raise StoreConflict(f"作业重启失败状态发生并发变化：{job_uuid}")
            record_job_result(
                connection,
                job_row=job,
                outcome="failed",
                return_info={},
                error_info=failure_details,
            )
            _append_job_transition(
                connection,
                task_uuid=task_uuid,
                job_uuid=job_uuid,
                from_status=status,
                to_status="failed",
                now=failed_at,
                data={"failure_code": EXECUTION_PROCESS_RESTARTED},
            )
            close_pending_manual_confirmation(
                connection,
                job_uuid=job_uuid,
                status="canceled",
                decided_at=failed_at,
                resolution_reason="runtime_restarted",
            )
            continue
        if status == "pending":
            changed = connection.execute(
                """
                UPDATE workflow_node_job
                SET status = 'skipped', error_info = ?, wait_reason = '{}',
                    finished_at = ?, update_time = ?
                WHERE uuid = ? AND status = 'pending' AND deleted_at IS NULL
                """,
                (skipped_info, failed_at, failed_at, job_uuid),
            ).rowcount
            if changed != 1:
                raise StoreConflict(f"作业重启跳过状态发生并发变化：{job_uuid}")
            _append_job_transition(
                connection,
                task_uuid=task_uuid,
                job_uuid=job_uuid,
                from_status="pending",
                to_status="skipped",
                now=failed_at,
                data={"reason": "upstream_execution_process_restarted"},
            )
            append_job_state_event(
                connection,
                job_row=job,
                status="skipped",
                details={
                    "error_info": skipped_details,
                    "finished_at": failed_at,
                },
            )
            close_pending_manual_confirmation(
                connection,
                job_uuid=job_uuid,
                status="canceled",
                decided_at=failed_at,
                resolution_reason="runtime_restarted",
            )
            continue
        if status not in _TERMINAL_JOB_STATES:
            raise StoreConflict(f"作业存在无法收敛的重启状态：{job_uuid}/{status}")

    # 只有仍可能存在物理动作的任务才保留不确定占用。纯调度窗口（前一节点已
    # 结算、后一节点尚未派发）同样业务失败，但不伪造需要人工清理的设备事实。
    physical_in_flight = any(
        str(row["executor_kind"]) != "manual_confirm"
        or _manual_confirmation_was_approved(connection, job_uuid=str(row["uuid"]))
        for row in in_flight
    )
    requires_attention = physical_in_flight
    target_lease_state = "uncertain" if requires_attention else "released"
    connection.execute(
        """
        UPDATE execution_lock_lease
        SET state = ?, released_at = CASE WHEN ? = 'released' THEN ? ELSE released_at END,
            update_time = ?
        WHERE workflow_task_uuid = ?
          AND state IN ('reserved', 'running', 'uncertain')
          AND deleted_at IS NULL
        """,
        (
            target_lease_state,
            target_lease_state,
            failed_at,
            failed_at,
            task_uuid,
        ),
    )
    connection.execute(
        """
        UPDATE execution_lock_waiter
        SET state = 'released', released_at = ?, update_time = ?
        WHERE workflow_task_uuid = ? AND state = 'waiting'
          AND deleted_at IS NULL
        """,
        (failed_at, failed_at, task_uuid),
    )
    connection.execute(
        """
        UPDATE execution_claim
        SET state = ?, released_at = CASE WHEN ? = 'released' THEN ? ELSE released_at END,
            update_time = ?
        WHERE workflow_task_uuid = ?
          AND state IN ('reserved', 'running', 'uncertain')
        """,
        (
            target_lease_state,
            target_lease_state,
            failed_at,
            failed_at,
            task_uuid,
        ),
    )

    changed_task = connection.execute(
        """
        UPDATE workflow_task
        SET status = 'failed', control_status = 'active',
            cleanup_status = ?, attention_reason = ?,
            reconciliation_resume_control_status = NULL, wait_reason = '{}',
            error_info = ?, finished_at = ?, update_time = ?
        WHERE uuid = ? AND status IN ('pending', 'running', 'canceling')
          AND deleted_at IS NULL
        """,
        (
            "requires_attention" if requires_attention else "settled",
            EXECUTION_PROCESS_RESTARTED if requires_attention else None,
            failure_info,
            failed_at,
            failed_at,
            task_uuid,
        ),
    ).rowcount
    if changed_task != 1:
        raise StoreConflict(f"任务重启失败状态发生并发变化：{task_uuid}")
    append_runtime_event(
        connection,
        task_uuid=task_uuid,
        kind="task_transition",
        from_status=str(task["status"]),
        to_status="failed",
        data={"failure_code": EXECUTION_PROCESS_RESTARTED},
        now=failed_at,
    )
    append_task_state_event(
        connection,
        task_uuid=task_uuid,
        status="failed",
        details={
            "failure_code": EXECUTION_PROCESS_RESTARTED,
            "cleanup_status": (
                "requires_attention" if requires_attention else "settled"
            ),
            "finished_at": failed_at,
        },
    )
    append_frontend_event(
        connection,
        event="workflow.runtime.changed",
        data={"workflow_task_uuid": task_uuid},
        now=failed_at,
    )
    return True


def _manual_confirmation_was_approved(
    connection: sqlite3.Connection,
    *,
    job_uuid: str,
) -> bool:
    """判断人工确认节点是否已经越过纯等待阶段。

    参数：当前工作流事务与节点作业 UUID。返回：确认状态为 ``approved`` 时为真；
    记录不存在或仍 pending 时为假。异常：SQLite 查询错误原样传播。批准后可能已
    派发连续设备动作，因此重启必须保守要求物理清理。
    """

    row = connection.execute(
        """
        SELECT status FROM workflow_manual_confirmation
        WHERE workflow_node_job_uuid = ?
        """,
        (job_uuid,),
    ).fetchone()
    return row is not None and str(row["status"]) == "approved"


def _append_job_transition(
    connection: sqlite3.Connection,
    *,
    task_uuid: str,
    job_uuid: str,
    from_status: str,
    to_status: str,
    now: str,
    data: dict[str, Any],
) -> None:
    """追加单个重启导致的作业状态转换日志。

    参数：``connection`` 是当前事务；Task/Job UUID 定位聚合与节点作业；两个状态
    是转换前后 wire 值；``now`` 是统一时间；``data`` 是稳定诊断对象。返回无。
    异常：日志写入错误原样传播并回滚调用方事务。
    """

    append_runtime_event(
        connection,
        task_uuid=task_uuid,
        job_uuid=job_uuid,
        kind="job_transition",
        from_status=from_status,
        to_status=to_status,
        data=data,
        now=now,
    )


def _json(value: Sequence[dict[str, str]]) -> str:
    """把失败详情编码为键稳定的 JSON 文本。

    参数：``value`` 是结构化错误对象序列。返回：UTF-8 JSON 文本。异常：不可编码
    值由 JSON 编码器原样抛出，禁止写入部分恢复事实。
    """

    return encode_json(value, sort_keys=True).decode("utf-8")


__all__ = [
    "EXECUTION_PROCESS_RESTARTED",
    "fail_task_after_execution_process_restart",
]
