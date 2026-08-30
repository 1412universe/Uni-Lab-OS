"""把工站任务与作业状态变化投影到事务发件箱。"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Any

from unilabos.workflow.station_event_outbox import append_station_event
from unilabos.workflow.store import StoreConflict


def append_task_state_event(
    connection: sqlite3.Connection,
    *,
    task_uuid: str,
    status: str,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """在任务状态事务内追加一个可重放的 Backend 投影事件。

    参数：``connection`` 是当前工作流写事务；``task_uuid`` 是稳定任务身份；
    ``status`` 是已提交的主状态；``details`` 是清理状态、时间或失败码等补充事实。
    返回：持久事件。异常：身份或状态为空、补充字段试图覆盖核心身份时抛
    ``StoreConflict``；SQLite 写入错误原样传播并由外层回滚。
    """

    normalized_task_uuid = str(task_uuid or "").strip()
    normalized_status = str(status or "").strip()
    if not normalized_task_uuid or not normalized_status:
        raise StoreConflict("工站任务状态事件缺少任务身份或状态")
    payload = {"task_uuid": normalized_task_uuid, "status": normalized_status}
    _merge_details(payload, details)
    return append_station_event(
        connection,
        event_type=f"task.{normalized_status}",
        aggregate_type="workflow_task",
        aggregate_uuid=normalized_task_uuid,
        source_kind="task_transition",
        source_uuid=normalized_task_uuid,
        idempotency_key=(f"station:{normalized_task_uuid}:status:{normalized_status}"),
        payload=payload,
    )


def append_job_state_event(
    connection: sqlite3.Connection,
    *,
    job_row: sqlite3.Row,
    status: str,
    event_name: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """追加包含 Task/Job/Node/attempt 身份的作业状态事件。

    参数：当前工作流写事务、持久作业行、已提交的主状态、可选事件名称和补充
    事实。``event_name`` 用于 ``execution_attention`` 这类主状态仍为 running 的
    独立事件。返回：持久事件。异常：必填身份为空、尝试序号非法或补充字段覆盖
    核心身份时抛 ``StoreConflict``；数据库异常由调用方事务处理。
    """

    task_uuid = str(job_row["workflow_task_uuid"] or "").strip()
    job_uuid = str(job_row["uuid"] or "").strip()
    node_uuid = str(job_row["workflow_node_uuid"] or "").strip()
    normalized_status = str(status or "").strip()
    normalized_name = str(event_name or normalized_status).strip()
    attempt = int(job_row["attempt"])
    if (
        not task_uuid
        or not job_uuid
        or not node_uuid
        or not normalized_status
        or not normalized_name
        or attempt < 1
    ):
        raise StoreConflict("工站作业状态事件身份、状态或尝试序号非法")
    payload = {
        "task_uuid": task_uuid,
        "job_uuid": job_uuid,
        "workflow_node_uuid": node_uuid,
        "attempt": attempt,
        "status": normalized_status,
    }
    _merge_details(payload, details)
    return append_station_event(
        connection,
        event_type=f"job.{normalized_name}",
        aggregate_type="workflow_node_job",
        aggregate_uuid=job_uuid,
        source_kind="job_transition",
        source_uuid=job_uuid,
        idempotency_key=f"station:{job_uuid}:status:{normalized_name}:{attempt}",
        payload=payload,
    )


def _merge_details(
    payload: dict[str, Any],
    details: Mapping[str, Any] | None,
) -> None:
    """合并补充事实且禁止覆盖调用方已经确定的核心身份字段。

    参数：``payload`` 是待写入的核心事件对象；``details`` 是可选补充事实。返回
    无。异常：补充事实与核心字段同名时抛 ``StoreConflict``，避免调用处伪造
    Task/Job/attempt/status。
    """

    if details is None:
        return
    for key, value in details.items():
        normalized_key = str(key or "").strip()
        if not normalized_key or normalized_key in payload:
            raise StoreConflict(f"工站状态事件补充字段冲突：{normalized_key}")
        payload[normalized_key] = value


__all__ = ["append_job_state_event", "append_task_state_event"]
