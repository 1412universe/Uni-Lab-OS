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
from unilabos.workflow.json_codec import decode_json_bytes, encode_json
from unilabos.workflow.manual_confirmation import close_pending_manual_confirmation
from unilabos.workflow.execution_lock_lease import release_task_execution_locks
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
_SUPPORTED_TASK_STATES = frozenset(
    {"pending", "running", "canceling", "succeeded", "failed", "canceled", "timeout"}
)


def fail_task_after_execution_process_restart(
    connection: sqlite3.Connection,
    *,
    task_uuid: str,
    now: str | None = None,
) -> bool:
    """原子终结 runtime 重启时仍非终态的工作流任务。

    参数：``connection`` 是调用方持有的工作流写事务；``task_uuid`` 是稳定任务
    UUID；``now`` 是可选统一结算时间。返回：任务自身非终态，或仍含未完成
    Job 并完成失败收敛时为 ``True``；无需收敛的终态任务返回 ``False``。异常：
    任务或作业缺失时抛
    ``StoreNotFound``/``StoreConflict``，SQLite 写入错误原样传播。

    已完成节点保留结果，在途节点失败，未开始节点取消。软件执行占用释放，
    历史不确定原因归档到控制证据，不生成对账要求或恢复命令；不修改实际库存。
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
    task_status = str(task["status"])
    unfinished_jobs = any(
        str(job["status"]) in _IN_FLIGHT_JOB_STATES or str(job["status"]) == "pending"
        for job in jobs
    )
    if task_status not in _SUPPORTED_TASK_STATES:
        raise StoreConflict(f"任务存在无法收敛的重启状态：{task_uuid}/{task_status}")
    if task_status not in {"pending", "running", "canceling"} and not unfinished_jobs:
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
    # 父 Task 可能已因另一个并行分支失败；此时只终结残留 Job 与锁，不覆盖
    # 原始失败证据。其他状态则由 runtime 重启统一收敛为失败。
    task_error_info = (
        str(task["error_info"] or "[]") if task_status == "failed" else failure_info
    )
    task_finished_at = (
        str(task["finished_at"] or failed_at) if task_status == "failed" else failed_at
    )

    for job in jobs:
        job_uuid = str(job["uuid"])
        status = str(job["status"])
        control_data = decode_json_bytes(
            str(job["control_data"] or "{}").encode("utf-8")
        )
        if not isinstance(control_data, dict):
            raise StoreConflict(f"重启作业控制证据已损坏：{job_uuid}")
        control_data["runtime_interruption"] = {
            "reason": EXECUTION_PROCESS_RESTARTED,
            "previous_uncertainty_reason": job["uncertainty_reason"],
            "at": failed_at,
        }
        connection.execute(
            "UPDATE workflow_node_job SET control_data=?, uncertainty_reason=NULL "
            "WHERE uuid=?",
            (encode_json(control_data, sort_keys=True).decode("utf-8"), job_uuid),
        )
        not_started = (
            "start_state" in job.keys() and job["start_state"] == "NOT_STARTED"
        )
        if status in _IN_FLIGHT_JOB_STATES and not not_started:
            changed = connection.execute(
                """
                UPDATE workflow_node_job
                SET status = 'failed', error_info = ?, wait_reason = '{}',
                    control_data = ?, uncertainty_reason = ?,
                    cancel_ack_deadline_at = NULL,
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
                    encode_json(control_data, sort_keys=True).decode("utf-8"),
                    None,
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
        if status == "pending" or (status in _IN_FLIGHT_JOB_STATES and not_started):
            changed = connection.execute(
                """
                UPDATE workflow_node_job
                SET status = 'canceled', error_info = ?, wait_reason = '{}',
                    cancel_ack_deadline_at=NULL, cancel_complete_deadline_at=NULL,
                    finished_at = ?, update_time = ?
                WHERE uuid = ? AND status = ? AND deleted_at IS NULL
                """,
                (canceled_info, failed_at, failed_at, job_uuid, status),
            ).rowcount
            if changed != 1:
                raise StoreConflict(f"作业重启取消状态发生并发变化：{job_uuid}")
            _append_job_transition(
                connection,
                task_uuid=task_uuid,
                job_uuid=job_uuid,
                from_status=status,
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

    release_task_execution_locks(connection, task_uuid=task_uuid, now=failed_at)
    connection.execute(
        "UPDATE workflow_task_material_claim SET status='released', released_at=?, "
        "revision=revision+1, update_time=? WHERE workflow_task_uuid=? AND status='active' "
        "AND deleted_at IS NULL",
        (failed_at, failed_at, task_uuid),
    )
    connection.execute(
        "UPDATE workflow_node_admission_hold SET status='canceled', released_at=?, "
        "update_time=? WHERE workflow_task_uuid=? AND status='open'",
        (failed_at, failed_at, task_uuid),
    )
    # 先保留软件清理待办，跨库释放完成后写 settled；不证明物料位置或设备状态。
    cleanup_status = "pending"
    attention_reason = None
    control_status = "active"
    reconciliation_resume_control_status = None

    changed_task = connection.execute(
        """
        UPDATE workflow_task
        SET status = 'failed', control_status = ?,
            cleanup_status = ?, attention_reason = ?,
            reconciliation_resume_control_status = ?, wait_reason = '{}',
            error_info = ?, finished_at = ?, update_time = ?
        WHERE uuid = ? AND status = ? AND deleted_at IS NULL
        """,
        (
            control_status,
            cleanup_status,
            attention_reason,
            reconciliation_resume_control_status,
            task_error_info,
            task_finished_at,
            failed_at,
            task_uuid,
            task_status,
        ),
    ).rowcount
    if changed_task != 1:
        raise StoreConflict(f"任务重启失败状态发生并发变化：{task_uuid}")
    if task_status != "failed":
        append_runtime_event(
            connection,
            task_uuid=task_uuid,
            kind="task_transition",
            from_status=task_status,
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
                "cleanup_status": cleanup_status,
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
