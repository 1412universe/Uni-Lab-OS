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
TASK_ABORTED_BY_RUNTIME_RESTART = "task_aborted_by_runtime_restart"
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
    """原子终结 runtime 重启时仍非终态的工作流任务。

    参数：``connection`` 是调用方持有的工作流写事务；``task_uuid`` 是稳定任务
    UUID；``now`` 是可选统一结算时间。返回：任务处于 ``pending``、``running``
    或 ``canceling`` 并完成失败收敛时为 ``True``；终态任务返回 ``False``。
    异常：任务或作业缺失时抛
    ``StoreNotFound``/``StoreConflict``，SQLite 写入错误原样传播。

    已完成节点保持终态；在途节点失败；未开始节点取消且永不恢复。所有旧 Claim、
    Lease 与 Fence 通过释放租约失效，任务清理直接收敛为 ``settled``。
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
    if str(task["status"]) not in {"pending", "running", "canceling"}:
        return False

    failed_at = now or utc_now()
    failure_details = [
        {
            "code": EXECUTION_PROCESS_RESTARTED,
            "message": "设备执行进程重启，无法继续推进原工作流任务",
        }
    ]
    failure_info = _json(failure_details)
    canceled_details = [
        {
            "code": TASK_ABORTED_BY_RUNTIME_RESTART,
            "message": "runtime 重启导致工作流任务终止，节点未执行",
        }
    ]
    canceled_info = _json(canceled_details)

    for job in jobs:
        job_uuid = str(job["uuid"])
        status = str(job["status"])
        if status in _IN_FLIGHT_JOB_STATES:
            changed = connection.execute(
                """
                UPDATE workflow_node_job
                SET status = 'failed', error_info = ?, wait_reason = '{}',
                    uncertainty_reason = NULL, cancel_ack_deadline_at = NULL,
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
                SET status = 'canceled', error_info = ?, wait_reason = '{}',
                    finished_at = ?, update_time = ?
                WHERE uuid = ? AND status = 'pending' AND deleted_at IS NULL
                """,
                (canceled_info, failed_at, failed_at, job_uuid),
            ).rowcount
            if changed != 1:
                raise StoreConflict(f"作业重启取消状态发生并发变化：{job_uuid}")
            _append_job_transition(
                connection,
                task_uuid=task_uuid,
                job_uuid=job_uuid,
                from_status="pending",
                to_status="canceled",
                now=failed_at,
                data={"reason": TASK_ABORTED_BY_RUNTIME_RESTART},
            )
            append_job_state_event(
                connection,
                job_row=job,
                status="canceled",
                details={
                    "error_info": canceled_details,
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

    connection.execute(
        """
        UPDATE execution_lock_lease
        SET state = 'released', released_at = ?, update_time = ?
        WHERE workflow_task_uuid = ?
          AND state IN ('reserved', 'running', 'uncertain')
          AND deleted_at IS NULL
        """,
        (
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
        SET state = 'released', released_at = ?, update_time = ?
        WHERE workflow_task_uuid = ?
          AND state IN ('reserved', 'running', 'uncertain')
        """,
        (
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
            "settled",
            None,
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
            "cleanup_status": "settled",
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
    "TASK_ABORTED_BY_RUNTIME_RESTART",
    "fail_task_after_execution_process_restart",
]
