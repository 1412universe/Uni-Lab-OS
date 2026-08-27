"""把本地调度器（EdgeScheduler）状态投影到标准任务/作业聚合。"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from unilabos.workflow._execution_plan_graph import final_target_data_key
from unilabos.workflow.execution_lock_lease import (
    list_execution_locks,
    mark_execution_locks_running,
    mark_execution_locks_uncertain,
    record_execution_lock_wait,
    release_execution_locks,
    release_task_execution_locks,
    try_acquire_execution_locks,
)
from unilabos.workflow.intervention import (
    WorkflowInterventionStore,
    settle_intervention_for_job,
)
from unilabos.workflow.job_evidence import JobEvidenceStore, record_job_result
from unilabos.workflow.json_codec import decode_json_bytes, encode_json
from unilabos.workflow.manual_confirmation import (
    ManualConfirmationStore,
    close_pending_manual_confirmation,
    open_manual_confirmation,
)
from unilabos.workflow.material_source import MaterialCustodyPolicy
from unilabos.workflow.store import (
    StoreConflict,
    StoreNotFound,
    WorkflowStore,
    utc_now,
)
from unilabos.workflow.task_material_admission import (
    list_blocked_material_task_uuids,
    read_material_admission,
    record_admitted_materials,
    record_blocked_admission,
)

_ACTIVE_JOB_STATES = frozenset({"pending", "dispatched", "running"})
_TERMINAL_JOB_STATES = frozenset(
    {"succeeded", "failed", "skipped", "canceled", "timeout"}
)
_FINISHED_STATE_MAP = {
    "success": "succeeded",
    "failed": "failed",
    "canceled": "canceled",
    "timeout": "timeout",
}


def _encode_json_field(value: Any, *, field_name: str) -> str:
    """把一个结果字段编码成稳定 JSON 文本。

    参数：``value`` 是准备持久化的返回或错误信息；``field_name`` 是发生冲突时
    用于定位字段的代码标识。返回：键稳定排序的 JSON 文本。异常：值无法表达为
    JSON 时抛出 ``StoreConflict``，不允许部分状态写入。
    """

    try:
        return encode_json(value, sort_keys=True).decode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise StoreConflict(f"{field_name} 不是合法 JSON") from exc


def _decode_json_field(value: str | None, *, fallback: Any) -> Any:
    """把数据库 JSON 文本恢复为领域值。

    参数：``value`` 是数据库字段文本；``fallback`` 是空字段使用的默认值。
    返回：解码后的 JSON 值。异常：损坏的持久化文本异常原样传播，因为它代表
    工作流存储（WorkflowStore）事实已经不可解释。
    """

    if value is None or value == "":
        return fallback
    return decode_json_bytes(value.encode("utf-8"))


class TaskRuntimeProjection:
    """将短期本地状态安全写入标准工作流任务（WorkflowTask）聚合。

    该模块只承担兼容投影，不声明持久调度内核（Durable Scheduler Kernel）能力，
    也不写入遗留 ``workflow_runs`` 或 ``job_runs`` 表。
    """

    def __init__(self, store: WorkflowStore):
        """绑定唯一工作流存储（WorkflowStore）写权威。

        参数：``store`` 是持有任务和作业标准表的工作流存储。返回：无。
        异常：构造阶段幂等补齐作业证据表；DDL 或存储错误原样传播。
        """

        # ``_store`` 是本投影唯一允许写入的工作流任务（WorkflowTask）权威。
        self._store = store
        self._evidence = JobEvidenceStore(store)
        self._manual_confirmations = ManualConfirmationStore(store)
        self._interventions = WorkflowInterventionStore(store)

    @staticmethod
    def _append_invalidation(
        connection: sqlite3.Connection,
        *,
        task_uuid: str,
        now: str,
    ) -> None:
        """在状态事实同一事务内追加一次前端失效通知。"""

        WorkflowStore._append_event(
            connection,
            event="workflow.runtime.changed",
            data={"workflow_task_uuid": task_uuid},
            now=now,
        )

    @staticmethod
    def _task_row(
        connection: sqlite3.Connection,
        task_uuid: str,
    ) -> sqlite3.Row:
        """在当前事务读取一个工作流任务（WorkflowTask）数据库行。

        参数：``connection`` 是调用方持有的写事务；``task_uuid`` 是任务稳定身份。
        返回：未软删除的任务行。异常：身份不存在时抛出 ``StoreNotFound``。
        """

        # ``task_row`` 是后续作业状态聚合所属的父任务事实。
        task_row = connection.execute(
            "SELECT * FROM workflow_task WHERE uuid = ? AND deleted_at IS NULL",
            (task_uuid,),
        ).fetchone()
        if task_row is None:
            raise StoreNotFound(f"工作流任务不存在：{task_uuid}")
        return task_row

    @staticmethod
    def _job_row(
        connection: sqlite3.Connection,
        job_uuid: str,
    ) -> sqlite3.Row:
        """在当前事务读取一个工作流节点作业（WorkflowNodeJob）数据库行。

        参数：``connection`` 是调用方持有的写事务；``job_uuid`` 是作业稳定身份。
        返回：未软删除的作业行。异常：身份不存在时抛出 ``StoreNotFound``。
        """

        # ``job_row`` 是本轮要投影状态或核对重放的目标作业事实。
        job_row = connection.execute(
            "SELECT * FROM workflow_node_job WHERE uuid = ? AND deleted_at IS NULL",
            (job_uuid,),
        ).fetchone()
        if job_row is None:
            raise StoreNotFound(f"工作流节点作业不存在：{job_uuid}")
        return job_row

    @staticmethod
    def _job_rows(
        connection: sqlite3.Connection,
        task_uuid: str,
    ) -> list[sqlite3.Row]:
        """读取父任务拥有的完整工作流节点作业（WorkflowNodeJob）集合。

        参数：``connection`` 是当前事务；``task_uuid`` 是父任务稳定身份。返回：按
        拓扑序和 UUID 稳定排序的作业行。异常：任务没有作业时抛出
        ``StoreConflict``，防止空集合被错误聚合为成功。
        """

        # ``job_rows`` 是决定父任务业务终态的完整兄弟作业集合。
        job_rows = connection.execute(
            """
            SELECT * FROM workflow_node_job
            WHERE workflow_task_uuid = ? AND deleted_at IS NULL
            ORDER BY topological_index ASC, uuid ASC
            """,
            (task_uuid,),
        ).fetchall()
        if not job_rows:
            raise StoreConflict(f"工作流任务没有可投影作业：{task_uuid}")
        return list(job_rows)

    @classmethod
    def _aggregate(
        cls,
        connection: sqlite3.Connection,
        task_uuid: str,
    ) -> dict[str, Any]:
        """在同一事务生成标准任务/作业查询聚合。

        参数：``connection`` 是当前事务；``task_uuid`` 是父任务稳定身份。返回：
        包含公共任务投影和有序作业投影的字典。异常：任务或作业缺失时传播对应
        ``StoreNotFound`` 或 ``StoreConflict``。
        """

        # ``task_row`` 与 ``job_rows`` 来自同一 SQLite 快照，避免撕裂读取。
        task_row = cls._task_row(connection, task_uuid)
        job_rows = cls._job_rows(connection, task_uuid)
        return {
            "task": WorkflowStore._task_row(task_row),
            "jobs": [WorkflowStore._job_row(row) for row in job_rows],
        }

    def project_submission(
        self,
        task_uuid: str,
        scheduler_state: str,
    ) -> dict[str, Any]:
        """投影本地调度器（EdgeScheduler）的首次接收状态。

        参数：``task_uuid`` 是既有工作流任务（WorkflowTask）身份；
        ``scheduler_state`` 接受 ``waiting_for_material``、``running`` 或单步任务
        的 ``paused``。返回：不
        改写标准状态的任务/作业聚合。异常：未知本地状态或不合法的既有聚合抛出
        ``StoreConflict``；身份缺失抛出 ``StoreNotFound``。
        """

        if scheduler_state not in {"waiting_for_material", "running", "paused"}:
            raise StoreConflict(f"不支持的本地提交状态：{scheduler_state}")
        with self._store.transaction() as connection:
            # ``aggregate`` 是首次提交后可公开给 Backend-shaped 接口的标准事实。
            aggregate = self._aggregate(connection, task_uuid)
            task_status = aggregate["task"]["status"]
            job_statuses = {job["status"] for job in aggregate["jobs"]}
            # 协调器会在普通动作提交前完成 MaterialSource 作业，因此暂停的调试
            # 任务可以合法呈现 ``pending task + succeeded sources + pending actions``。
            # 物料来源成功不是物理派发，也不应迫使父任务提前进入 running。
            if task_status in {"pending", "running"} and job_statuses <= (
                _ACTIVE_JOB_STATES | {"succeeded"}
            ):
                return aggregate
            raise StoreConflict(
                f"本地提交状态与任务聚合冲突：{task_uuid}/{scheduler_state}"
            )

    def project_canceled(self, task_uuid: str) -> dict[str, Any]:
        """取消可证明尚未派发的任务；在途任务必须走持久取消状态机。"""

        now = utc_now()
        with self._store.transaction() as connection:
            task_row = self._task_row(connection, task_uuid)
            job_rows = self._job_rows(connection, task_uuid)
            if task_row["status"] == "canceled":
                return self._aggregate(connection, task_uuid)
            if task_row["status"] in {"succeeded", "failed", "timeout"}:
                raise StoreConflict(f"终态任务不能取消：{task_uuid}")
            if any(
                row["status"] in {
                    "dispatched",
                    "running",
                    "cancel_requested",
                    "execution_unknown",
                }
                for row in job_rows
            ):
                raise StoreConflict(f"在途任务不能直接投影为 canceled：{task_uuid}")
            for row in job_rows:
                if row["status"] in _TERMINAL_JOB_STATES:
                    release_execution_locks(
                        connection,
                        job_uuid=str(row["uuid"]),
                        now=now,
                    )
                    continue
                connection.execute(
                    """
                    UPDATE workflow_node_job
                    SET status = 'canceled', wait_reason = '{}',
                        finished_at = ?, update_time = ?
                    WHERE uuid = ? AND deleted_at IS NULL
                    """,
                    (now, now, row["uuid"]),
                )
                WorkflowStore._append_runtime_event(
                    connection,
                    task_uuid=task_uuid,
                    job_uuid=str(row["uuid"]),
                    kind="job_transition",
                    from_status=str(row["status"]),
                    to_status="canceled",
                    now=now,
                )
                release_execution_locks(
                    connection,
                    job_uuid=str(row["uuid"]),
                    now=now,
                )
            connection.execute(
                """
                UPDATE workflow_task
                SET status = 'canceled', control_status = 'active',
                    cleanup_status = 'settled',
                    wait_reason = '{}', finished_at = ?, update_time = ?
                WHERE uuid = ? AND deleted_at IS NULL
                """,
                (now, now, task_uuid),
            )
            WorkflowStore._append_runtime_event(
                connection,
                task_uuid=task_uuid,
                kind="task_transition",
                from_status=str(task_row["status"]),
                to_status="canceled",
                now=now,
            )
            self._append_invalidation(
                connection, task_uuid=task_uuid, now=now
            )
            return self._aggregate(connection, task_uuid)

    def project_cancel_requested(
        self,
        task_uuid: str,
        *,
        command_uuid: str,
        ack_deadline_at: str,
        complete_deadline_at: str,
    ) -> dict[str, Any]:
        """原子记录 Local 模式取消请求并区分未发送与在途作业。

        参数：``task_uuid`` 是任务身份；``command_uuid`` 是幂等控制命令身份；
        两个截止时间分别限制执行器受理和设备终态等待。返回任务/作业聚合。
        异常：终态冲突或缺少命令身份时抛 ``StoreConflict``。

        pending 作业可证明尚未越过物理边界，因此直接取消并释放预留锁；
        dispatched/running 作业只进入 ``cancel_requested``，继续持有执行占用。
        """

        normalized_command_uuid = str(command_uuid or "").strip()
        if not normalized_command_uuid:
            raise StoreConflict("取消命令 UUID 不能为空")
        requested_at = utc_now()
        with self._store.transaction() as connection:
            task_row = self._task_row(connection, task_uuid)
            job_rows = self._job_rows(connection, task_uuid)
            if task_row["status"] == "canceled":
                return self._aggregate(connection, task_uuid)
            if task_row["status"] in {"succeeded", "failed", "timeout"}:
                raise StoreConflict(f"终态任务不能取消：{task_uuid}")

            in_flight = False
            has_execution_unknown = False
            for row in job_rows:
                status = str(row["status"])
                job_uuid = str(row["uuid"])
                if status in _TERMINAL_JOB_STATES:
                    continue
                if status == "pending":
                    connection.execute(
                        """
                        UPDATE workflow_node_job
                        SET status = 'canceled', wait_reason = '{}',
                            cancel_command_uuid = ?, finished_at = ?, update_time = ?
                        WHERE uuid = ? AND status = 'pending' AND deleted_at IS NULL
                        """,
                        (
                            normalized_command_uuid,
                            requested_at,
                            requested_at,
                            job_uuid,
                        ),
                    )
                    release_execution_locks(
                        connection,
                        job_uuid=job_uuid,
                        now=requested_at,
                    )
                    WorkflowStore._append_runtime_event(
                        connection,
                        task_uuid=task_uuid,
                        job_uuid=job_uuid,
                        kind="job_transition",
                        from_status="pending",
                        to_status="canceled",
                        data={"reason": "cancel_no_send"},
                        now=requested_at,
                    )
                    continue
                if status in {"dispatched", "running"}:
                    changed = connection.execute(
                        """
                        UPDATE workflow_node_job
                        SET status = 'cancel_requested', cancel_command_uuid = ?,
                            cancel_ack_deadline_at = ?,
                            cancel_complete_deadline_at = ?,
                            cancel_accepted_at = NULL,
                            dispatch_deadline_at = NULL,
                            execution_deadline_at = NULL,
                            uncertainty_reason = 'local_cancel_requested',
                            wait_reason = '{}', update_time = ?
                        WHERE uuid = ? AND status IN ('dispatched', 'running')
                          AND deleted_at IS NULL
                        """,
                        (
                            normalized_command_uuid,
                            ack_deadline_at,
                            complete_deadline_at,
                            requested_at,
                            job_uuid,
                        ),
                    ).rowcount
                    if changed != 1:
                        raise StoreConflict(f"作业取消状态发生并发变化：{job_uuid}")
                    in_flight = True
                    WorkflowStore._append_runtime_event(
                        connection,
                        task_uuid=task_uuid,
                        job_uuid=job_uuid,
                        kind="job_transition",
                        from_status=status,
                        to_status="cancel_requested",
                        data={"command_uuid": normalized_command_uuid},
                        now=requested_at,
                    )
                    continue
                if status in {"cancel_requested", "execution_unknown"}:
                    in_flight = True
                    has_execution_unknown = (
                        has_execution_unknown or status == "execution_unknown"
                    )
                    continue
                raise StoreConflict(f"作业状态不能取消：{job_uuid}/{status}")

            target_status = "canceling" if in_flight else "canceled"
            cleanup_status = (
                "requires_attention"
                if has_execution_unknown
                else ("canceling" if in_flight else "settled")
            )
            control_status = (
                "waiting_reconciliation" if has_execution_unknown else "active"
            )
            finished_at = None if in_flight else requested_at
            connection.execute(
                """
                UPDATE workflow_task
                SET status = ?, cleanup_status = ?, control_status = ?,
                    wait_reason = '{}', finished_at = ?, update_time = ?
                WHERE uuid = ? AND deleted_at IS NULL
                """,
                (
                    target_status,
                    cleanup_status,
                    control_status,
                    finished_at,
                    requested_at,
                    task_uuid,
                ),
            )
            if str(task_row["status"]) != target_status:
                WorkflowStore._append_runtime_event(
                    connection,
                    task_uuid=task_uuid,
                    kind="task_transition",
                    from_status=str(task_row["status"]),
                    to_status=target_status,
                    data={"command_uuid": normalized_command_uuid},
                    now=requested_at,
                )
            self._append_invalidation(
                connection,
                task_uuid=task_uuid,
                now=requested_at,
            )
            return self._aggregate(connection, task_uuid)

    def project_cancel_accepted(self, job_uuid: str) -> dict[str, Any]:
        """记录本地执行器已接受取消，但继续持有执行占用。

        参数：``job_uuid`` 是在途作业身份。返回任务/作业聚合。异常：作业不是
        ``cancel_requested`` 时抛 ``StoreConflict``；重复受理幂等返回。
        """

        with self._store.transaction() as connection:
            job_row = self._job_row(connection, job_uuid)
            task_uuid = str(job_row["workflow_task_uuid"])
            if job_row["status"] in _TERMINAL_JOB_STATES:
                return self._aggregate(connection, task_uuid)
            if job_row["status"] != "cancel_requested":
                raise StoreConflict(f"作业不能确认取消受理：{job_uuid}")
            if job_row["cancel_accepted_at"] is not None:
                return self._aggregate(connection, task_uuid)
            accepted_at = utc_now()
            changed = connection.execute(
                """
                UPDATE workflow_node_job
                SET cancel_accepted_at = ?, cancel_ack_deadline_at = NULL,
                    update_time = ?
                WHERE uuid = ? AND status = 'cancel_requested'
                  AND cancel_accepted_at IS NULL AND deleted_at IS NULL
                """,
                (accepted_at, accepted_at, job_uuid),
            ).rowcount
            if changed != 1:
                raise StoreConflict(f"作业取消受理发生并发变化：{job_uuid}")
            WorkflowStore._append_runtime_event(
                connection,
                task_uuid=task_uuid,
                job_uuid=job_uuid,
                kind="job_transition",
                from_status="cancel_requested",
                to_status="cancel_requested",
                data={"cancel_accepted": True},
                now=accepted_at,
            )
            self._append_invalidation(
                connection,
                task_uuid=task_uuid,
                now=accepted_at,
            )
            return self._aggregate(connection, task_uuid)

    def project_material_source_blocked(
        self,
        task_uuid: str,
        *,
        reason: str = "任务所需物料暂不可用",
    ) -> dict[str, Any]:
        """持久化一次受阻的任务物料准入投影。

        参数：``task_uuid`` 是保持待处理的工作流任务（WorkflowTask）身份；
        ``reason`` 是可展示的受阻原因。返回：写入准入尝试和等待原因后的标准
        任务/作业聚合。异常：任务不为 ``pending``、没有物料来源
        解析作业（MaterialSourceResolutionJob），或来源作业已离开 ``pending`` 时
        抛出 ``StoreConflict``；身份不存在时传播 ``StoreNotFound``。
        """

        with self._store.transaction() as connection:
            aggregate = self._aggregate(connection, task_uuid)
            if aggregate["task"]["status"] != "pending":
                raise StoreConflict(f"任务不能保持准入受阻：{task_uuid}")
            # ``source_jobs`` 是本次全有或全无准入共同拥有的协调器作业。
            source_jobs = [
                job
                for job in aggregate["jobs"]
                if job.get("executor_kind") == "material_source"
            ]
            if not source_jobs or any(job["status"] != "pending" for job in source_jobs):
                raise StoreConflict(f"物料来源作业不能保持待处理：{task_uuid}")
            record_blocked_admission(
                connection,
                task_uuid=task_uuid,
                reason=reason,
                wait_reason={
                    "code": "material_unavailable",
                    "message": reason,
                },
            )
            self._append_invalidation(
                connection,
                task_uuid=task_uuid,
                now=utc_now(),
            )
            return self._aggregate(connection, task_uuid)

    def project_material_source_admission(
        self,
        task_uuid: str,
        bindings: Mapping[str, Mapping[str, str | None]],
    ) -> dict[str, Any]:
        """原子提交成功任务物料准入（TaskMaterialAdmission）的逐来源结果。

        参数：``task_uuid`` 是父工作流任务（WorkflowTask）身份；``bindings`` 按
        物料来源节点 UUID 提供已预留的 ``uuid``、``resource_template_uuid``，
        并可提供 ``site_uuid``、``flow_role``、``custody_policy``。返回：全部来源
        作业已直接变为 ``succeeded``
        的标准聚合；若没有普通动作，父任务也直接成功。异常：绑定集合、字段、
        状态或重放载荷冲突时抛出 ``StoreConflict``，整笔事务零部分写入。
        """

        if not isinstance(bindings, Mapping):
            raise StoreConflict("物料来源绑定必须是对象")
        # ``normalized_bindings`` 隔离调用方容器并验证有类型物料占位符身份。
        normalized_bindings: dict[str, dict[str, str]] = {}
        # ``material_facts`` 是 Backend-shaped 准入事实；旧调用方未提供新增字段时
        # 使用历史默认，仅用于兼容已持久化的本地执行计划。
        material_facts: dict[str, dict[str, str | None]] = {}
        for node_uuid, raw_binding in bindings.items():
            if not isinstance(raw_binding, Mapping):
                raise StoreConflict("物料来源绑定成员必须是对象")
            material_uuid = str(raw_binding.get("uuid") or "").strip()
            template_uuid = str(
                raw_binding.get("resource_template_uuid") or ""
            ).strip()
            custody_policy = str(raw_binding.get("custody_policy") or "").strip()
            if not node_uuid or not material_uuid or not template_uuid or not custody_policy:
                raise StoreConflict("物料来源绑定身份不能为空")
            if custody_policy not in {member.value for member in MaterialCustodyPolicy}:
                raise StoreConflict("物料来源保管策略不在规范闭集")
            normalized_bindings[str(node_uuid)] = {
                "uuid": material_uuid,
                "resource_template_uuid": template_uuid,
                "custody_policy": custody_policy,
            }
            material_facts[str(node_uuid)] = {
                "material_uuid": material_uuid,
                "resource_template_uuid": template_uuid,
                "site_uuid": str(raw_binding.get("site_uuid") or "").strip()
                or None,
                "flow_role": str(
                    raw_binding.get("flow_role") or "primary_sample"
                ).strip(),
                "custody_policy": str(
                    raw_binding.get("custody_policy") or "task_exclusive"
                ).strip(),
            }

        with self._store.transaction() as connection:
            task_row = self._task_row(connection, task_uuid)
            job_rows = self._job_rows(connection, task_uuid)
            # ``source_rows`` 必须与成功准入一次提交的完整绑定集合严格相等。
            source_rows = [
                row for row in job_rows if row["executor_kind"] == "material_source"
            ]
            source_node_uuids = {str(row["workflow_node_uuid"]) for row in source_rows}
            if not source_rows or set(normalized_bindings) != source_node_uuids:
                raise StoreConflict(f"物料来源绑定集合不完整：{task_uuid}")
            if task_row["status"] not in {"pending", "running", "succeeded"}:
                raise StoreConflict(f"任务不能提交物料来源结果：{task_uuid}")

            record_admitted_materials(
                connection,
                task_uuid=task_uuid,
                source_jobs=source_rows,
                bindings=material_facts,
            )
            self._project_material_binding_params(
                connection,
                task_row=task_row,
                job_rows=job_rows,
                bindings=normalized_bindings,
            )
            projected_at = utc_now()
            changed = False
            for row in source_rows:
                node_uuid = str(row["workflow_node_uuid"])
                return_info = {"material": normalized_bindings[node_uuid]}
                return_info_json = _encode_json_field(
                    return_info,
                    field_name="return_info",
                )
                if row["status"] == "succeeded":
                    if _decode_json_field(row["return_info"], fallback={}) != return_info:
                        raise StoreConflict(f"物料来源终态载荷冲突：{row['uuid']}")
                    continue
                if row["status"] != "pending":
                    raise StoreConflict(f"物料来源作业不能成功：{row['uuid']}")
                updated_jobs = connection.execute(
                    """
                    UPDATE workflow_node_job
                    SET status = 'succeeded', return_info = ?, error_info = '[]',
                        finished_at = ?, update_time = ?
                    WHERE uuid = ? AND status = 'pending' AND deleted_at IS NULL
                    """,
                    (return_info_json, projected_at, projected_at, row["uuid"]),
                ).rowcount
                if updated_jobs != 1:
                    raise StoreConflict(f"物料来源作业状态发生并发变化：{row['uuid']}")
                changed = True
                WorkflowStore._append_runtime_event(
                    connection,
                    task_uuid=task_uuid,
                    job_uuid=str(row["uuid"]),
                    kind="job_transition",
                    from_status="pending",
                    to_status="succeeded",
                    now=projected_at,
                )

            # 没有普通动作表示任务业务目标就是完成供料绑定；协调器工作不经历
            # ``running``，也不产生设备执行开始时间。
            ordinary_rows = [
                row for row in job_rows if row["executor_kind"] != "material_source"
            ]
            if not ordinary_rows and task_row["status"] == "pending":
                updated_tasks = connection.execute(
                    """
                    UPDATE workflow_task
                    SET status = 'succeeded', finished_at = ?, update_time = ?
                    WHERE uuid = ? AND status = 'pending' AND deleted_at IS NULL
                    """,
                    (projected_at, projected_at, task_uuid),
                ).rowcount
                if updated_tasks != 1:
                    raise StoreConflict(f"来源任务终态发生并发变化：{task_uuid}")
                changed = True
                WorkflowStore._append_runtime_event(
                    connection,
                    task_uuid=task_uuid,
                    kind="task_transition",
                    from_status="pending",
                    to_status="succeeded",
                    now=projected_at,
                )
            if changed:
                self._append_invalidation(
                    connection,
                    task_uuid=task_uuid,
                    now=projected_at,
                )
            return self._aggregate(connection, task_uuid)

    def get_material_admission(self, task_uuid: str) -> dict[str, Any] | None:
        """读取一个任务最近的物料准入事实。

        参数：``task_uuid`` 是稳定工作流任务（WorkflowTask）身份。返回：尚未评估
        时为 ``None``，否则返回状态、尝试号、修订和原因。异常：数据库错误原样
        传播；本方法只读，不改变准入或库存状态。
        """

        with self._store.transaction() as connection:
            self._task_row(connection, task_uuid)
            return read_material_admission(connection, task_uuid=task_uuid)

    def list_blocked_material_tasks(self) -> list[str]:
        """列出重启后仍应重试物料准入的任务。

        参数：无。返回：按最近评估时间稳定排序的待处理任务 UUID。异常：数据库
        读取错误原样传播；列表只包含持久 ``blocked`` 且任务仍为 ``pending`` 的
        任务，不依赖桥接层内存集合。
        """

        with self._store.transaction() as connection:
            return list_blocked_material_task_uuids(connection)

    def project_cleanup_settled(self, task_uuid: str) -> dict[str, Any]:
        """记录终态任务的物理清理已经结算。

        参数：``task_uuid`` 是已终止的工作流任务（WorkflowTask）身份。返回：更新
        后的任务/作业聚合。异常：任务尚非失败、取消或超时时抛 ``StoreConflict``；
        重放 ``settled`` 幂等返回。该状态转换会触发独占任务物料 claim 的释放。
        """

        with self._store.transaction() as connection:
            task_row = self._task_row(connection, task_uuid)
            if task_row["status"] not in {"failed", "canceled", "timeout"}:
                raise StoreConflict(f"非异常终态任务不能结算清理：{task_uuid}")
            if task_row["cleanup_status"] == "settled":
                return self._aggregate(connection, task_uuid)
            if task_row["cleanup_status"] not in {
                "none",
                "pending",
                "required",
                "canceling",
            }:
                raise StoreConflict(f"任务清理状态不能结算：{task_uuid}")
            settled_at = utc_now()
            release_task_execution_locks(
                connection,
                task_uuid=task_uuid,
                now=settled_at,
            )
            connection.execute(
                """
                UPDATE workflow_task
                SET cleanup_status = 'settled', update_time = ?
                WHERE uuid = ? AND deleted_at IS NULL
                """,
                (settled_at, task_uuid),
            )
            self._append_invalidation(
                connection,
                task_uuid=task_uuid,
                now=settled_at,
            )
            return self._aggregate(connection, task_uuid)

    @staticmethod
    def _project_material_binding_params(
        connection: sqlite3.Connection,
        *,
        task_row: sqlite3.Row,
        job_rows: Sequence[sqlite3.Row],
        bindings: Mapping[str, Mapping[str, str]],
    ) -> None:
        """把自动库存选择结果原子写入既有普通动作作业参数。

        参数：连接、任务行和作业行属于同一工作流存储（WorkflowStore）事务；
        ``bindings`` 是已整组占用的逐来源物料（Material）身份。返回无。异常：
        计划目标、作业状态或既有参数冲突时抛 ``StoreConflict``，来源成功状态与
        参数写入一起回滚。
        """

        plan = _decode_json_field(task_row["execution_plan"], fallback={})
        # 早期兼容任务没有冻结绑定目标；它们仍只投影来源结果，不补写动作参数。
        if plan == {}:
            return
        raw_nodes = plan.get("nodes") if isinstance(plan, Mapping) else None
        if not isinstance(raw_nodes, Sequence) or isinstance(raw_nodes, (str, bytes)):
            raise StoreConflict("执行计划节点必须是数组")
        jobs_by_node = {str(row["workflow_node_uuid"]): row for row in job_rows}
        raw_edges = plan.get("edges", [])
        if not isinstance(raw_edges, Sequence) or isinstance(raw_edges, (str, bytes)):
            raise StoreConflict("执行计划边必须是数组")
        inferred_targets: dict[str, list[dict[str, str]]] = {}
        for raw_edge in raw_edges:
            if not isinstance(raw_edge, Mapping):
                raise StoreConflict("执行计划边必须是对象")
            if (
                raw_edge.get("dependency_only") is True
                or raw_edge.get("source_type") != "ResourceSlot"
                or raw_edge.get("target_type") != "ResourceSlot"
            ):
                continue
            source_uuid = str(raw_edge.get("source_node_uuid") or "").strip()
            target_uuid = str(raw_edge.get("target_node_uuid") or "").strip()
            param_key = final_target_data_key(
                str(raw_edge.get("target_data_key") or "")
            )
            if source_uuid and target_uuid and param_key:
                inferred_targets.setdefault(source_uuid, []).append(
                    {"workflow_node_uuid": target_uuid, "param_key": param_key}
                )
        claimed_targets: set[tuple[str, str]] = set()
        for raw_node in raw_nodes:
            if not isinstance(raw_node, Mapping) or raw_node.get("kind") != "material_source":
                continue
            source_uuid = str(raw_node.get("uuid") or "")
            binding = bindings.get(source_uuid)
            if binding is None:
                raise StoreConflict(f"物料来源缺少运行绑定：{source_uuid}")
            raw_targets = raw_node.get("material_binding_targets", [])
            if not isinstance(raw_targets, Sequence) or isinstance(
                raw_targets, (str, bytes)
            ):
                raise StoreConflict("物料来源绑定目标必须是数组")
            combined_targets = [*raw_targets, *inferred_targets.get(source_uuid, [])]
            for raw_target in combined_targets:
                if not isinstance(raw_target, Mapping):
                    raise StoreConflict("物料来源绑定目标必须是对象")
                target_uuid = str(raw_target.get("workflow_node_uuid") or "").strip()
                param_key = str(raw_target.get("param_key") or "").strip()
                target = (target_uuid, param_key)
                if not target_uuid or not param_key:
                    raise StoreConflict("物料来源绑定目标不能为空")
                if target in claimed_targets:
                    continue
                claimed_targets.add(target)
                target_row = jobs_by_node.get(target_uuid)
                if target_row is None or target_row["executor_kind"] == "material_source":
                    raise StoreConflict(f"物料来源绑定目标不是普通动作：{target_uuid}")
                # 多个物料来源（MaterialSource）可以把不同参数绑定到同一个动作。
                # ``jobs_by_node`` 来自事务开始时的快照；每次写入前重新读取目标，
                # 否则后一个来源会用陈旧参数覆盖前一个来源刚提交的绑定。
                current_target_row = TaskRuntimeProjection._job_row(
                    connection,
                    str(target_row["uuid"]),
                )
                param = _decode_json_field(current_target_row["param"], fallback={})
                if not isinstance(param, Mapping):
                    raise StoreConflict(
                        f"工作流节点作业参数不是对象：{current_target_row['uuid']}"
                    )
                updated_param = dict(param)
                material_reference = {"uuid": str(binding["uuid"])}
                existing = updated_param.get(param_key)
                if existing is not None and existing != material_reference:
                    raise StoreConflict(
                        f"物料来源绑定与作业参数冲突：{current_target_row['uuid']}"
                    )
                if existing == material_reference:
                    continue
                updated_param[param_key] = material_reference
                changed = connection.execute(
                    "UPDATE workflow_node_job SET param = ?, update_time = ? "
                    "WHERE uuid = ? AND status = 'pending' AND deleted_at IS NULL",
                    (
                        _encode_json_field(updated_param, field_name="param"),
                        utc_now(),
                        current_target_row["uuid"],
                    ),
                ).rowcount
                if changed != 1:
                    raise StoreConflict(
                        "物料来源绑定目标状态发生并发变化："
                        f"{current_target_row['uuid']}"
                    )

    def project_pre_dispatch(
        self,
        *,
        task_uuid: str,
        job_uuid: str,
        resolved_param: Mapping[str, Any] | None = None,
        execution_locks: Sequence[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """在物理派发前原子推进目标作业及父任务。

        参数：``task_uuid`` 是父工作流任务（WorkflowTask）身份；``job_uuid`` 是
        即将派发的工作流节点作业（WorkflowNodeJob）身份；``resolved_param``
        是已投影全部父节点输出的最终参数；``execution_locks`` 是设备、物料与
        库位的全有或全无持久占用请求。返回：提交后的标准聚合；锁暂不可用时
        Job 保持 ``pending`` 并写入 ``wait_reason``。异常：身份不匹配或状态转换
        冲突时抛出 ``StoreConflict``；身份缺失时抛出 ``StoreNotFound``。同一
        派发意图重放时零写入。
        """

        with self._store.transaction() as connection:
            # ``job_row`` 是本次物理派发意图所指向的唯一作业。
            job_row = self._job_row(connection, job_uuid)
            if job_row["workflow_task_uuid"] != task_uuid:
                raise StoreConflict(f"作业不属于指定任务：{job_uuid}/{task_uuid}")
            task_row = self._task_row(connection, task_uuid)
            if job_row["status"] == "dispatched" and task_row["status"] == "running":
                return self._aggregate(connection, task_uuid)
            if job_row["status"] != "pending":
                raise StoreConflict(f"作业不能进入 dispatched：{job_uuid}")
            if task_row["status"] not in {"pending", "running"}:
                raise StoreConflict(f"任务不能开始派发：{task_uuid}")
            lock_decision = try_acquire_execution_locks(
                connection,
                task_uuid=task_uuid,
                job_uuid=job_uuid,
                requests=execution_locks,
            )
            if not lock_decision.acquired:
                self._append_invalidation(
                    connection,
                    task_uuid=task_uuid,
                    now=utc_now(),
                )
                return self._aggregate(connection, task_uuid)
            param_json = (
                job_row["param"]
                if resolved_param is None
                else _encode_json_field(resolved_param, field_name="resolved_param")
            )

            # ``projected_at`` 是同一事务内任务与作业共享的投影时间。
            projected_at = utc_now()
            updated_jobs = connection.execute(
                """
                UPDATE workflow_node_job
                SET status = 'dispatched', param = ?, update_time = ?
                WHERE uuid = ? AND status = 'pending' AND deleted_at IS NULL
                """,
                (param_json, projected_at, job_uuid),
            ).rowcount
            if updated_jobs != 1:
                raise StoreConflict(f"作业派发前状态发生并发变化：{job_uuid}")
            if task_row["status"] == "pending":
                updated_tasks = connection.execute(
                    """
                    UPDATE workflow_task
                    SET status = 'running', started_at = COALESCE(started_at, ?),
                        update_time = ?
                    WHERE uuid = ? AND status = 'pending' AND deleted_at IS NULL
                    """,
                    (projected_at, projected_at, task_uuid),
                ).rowcount
                if updated_tasks != 1:
                    raise StoreConflict(f"任务启动状态发生并发变化：{task_uuid}")
                WorkflowStore._append_runtime_event(
                    connection,
                    task_uuid=task_uuid,
                    kind="task_transition",
                    from_status="pending",
                    to_status="running",
                    now=projected_at,
                )
            WorkflowStore._append_runtime_event(
                connection,
                task_uuid=task_uuid,
                job_uuid=job_uuid,
                kind="job_transition",
                from_status="pending",
                to_status="dispatched",
                now=projected_at,
            )
            if str(job_row["executor_kind"]) == "manual_confirm":
                open_manual_confirmation(
                    connection,
                    job_row=job_row,
                    param=(
                        dict(resolved_param)
                        if resolved_param is not None
                        else _decode_json_field(job_row["param"], fallback={})
                    ),
                )
            self._append_invalidation(
                connection,
                task_uuid=task_uuid,
                now=projected_at,
            )
            return self._aggregate(connection, task_uuid)

    def project_execution_lock_wait(
        self,
        *,
        task_uuid: str,
        job_uuid: str,
        execution_locks: Sequence[Mapping[str, Any]] | None,
        blocking_task_uuid: str | None = None,
        blocking_job_uuid: str | None = None,
    ) -> dict[str, Any]:
        """在旧调度器内存占用先命中时保留持久等待身份与可解释原因。"""

        with self._store.transaction() as connection:
            job_row = self._job_row(connection, job_uuid)
            if job_row["workflow_task_uuid"] != task_uuid:
                raise StoreConflict(f"作业不属于指定任务：{job_uuid}/{task_uuid}")
            task_row = self._task_row(connection, task_uuid)
            if job_row["status"] != "pending":
                return self._aggregate(connection, task_uuid)
            if task_row["status"] not in {"pending", "running"}:
                raise StoreConflict(f"任务不能等待执行资源：{task_uuid}")
            record_execution_lock_wait(
                connection,
                task_uuid=task_uuid,
                job_uuid=job_uuid,
                requests=execution_locks,
                blocking_task_uuid=blocking_task_uuid,
                blocking_job_uuid=blocking_job_uuid,
            )
            self._append_invalidation(
                connection,
                task_uuid=task_uuid,
                now=utc_now(),
            )
            return self._aggregate(connection, task_uuid)

    def project_dispatch_accepted(self, job_uuid: str) -> dict[str, Any]:
        """记录本地执行适配器已经接受作业，并把持久占用推进为 running。"""

        with self._store.transaction() as connection:
            job_row = self._job_row(connection, job_uuid)
            task_uuid = str(job_row["workflow_task_uuid"])
            if job_row["status"] == "running":
                return self._aggregate(connection, task_uuid)
            if job_row["status"] != "dispatched":
                raise StoreConflict(f"作业不能确认进入 running：{job_uuid}")
            accepted_at = utc_now()
            changed = connection.execute(
                """
                UPDATE workflow_node_job
                SET status = 'running', started_at = COALESCE(started_at, ?),
                    update_time = ?
                WHERE uuid = ? AND status = 'dispatched' AND deleted_at IS NULL
                """,
                (accepted_at, accepted_at, job_uuid),
            ).rowcount
            if changed != 1:
                raise StoreConflict(f"作业执行确认发生并发变化：{job_uuid}")
            mark_execution_locks_running(
                connection,
                job_uuid=job_uuid,
                now=accepted_at,
            )
            WorkflowStore._append_runtime_event(
                connection,
                task_uuid=task_uuid,
                job_uuid=job_uuid,
                kind="job_transition",
                from_status="dispatched",
                to_status="running",
                now=accepted_at,
            )
            self._append_invalidation(
                connection,
                task_uuid=task_uuid,
                now=accepted_at,
            )
            return self._aggregate(connection, task_uuid)

    def project_execution_unknown(
        self,
        job_uuid: str,
        *,
        reason: str,
    ) -> dict[str, Any]:
        """保守记录无法证明是否执行的作业，并继续持有全部执行资源。"""

        normalized_reason = str(reason or "").strip()
        if not normalized_reason:
            raise StoreConflict("执行未知原因不能为空")
        with self._store.transaction() as connection:
            job_row = self._job_row(connection, job_uuid)
            task_uuid = str(job_row["workflow_task_uuid"])
            task_row = self._task_row(connection, task_uuid)
            if job_row["status"] == "execution_unknown":
                if job_row["uncertainty_reason"] != normalized_reason:
                    raise StoreConflict(f"作业执行未知原因冲突：{job_uuid}")
                return self._aggregate(connection, task_uuid)
            if job_row["status"] not in {
                "dispatched",
                "running",
                "cancel_requested",
            }:
                raise StoreConflict(f"作业不能进入 execution_unknown：{job_uuid}")
            opened_at = utc_now()
            changed = connection.execute(
                """
                UPDATE workflow_node_job
                SET status = 'execution_unknown', uncertainty_reason = ?,
                    wait_reason = '{}', update_time = ?
                WHERE uuid = ?
                  AND status IN ('dispatched', 'running', 'cancel_requested')
                  AND deleted_at IS NULL
                """,
                (normalized_reason, opened_at, job_uuid),
            ).rowcount
            if changed != 1:
                raise StoreConflict(f"作业执行未知状态发生并发变化：{job_uuid}")
            resume_control = (
                str(task_row["control_status"])
                if task_row["control_status"] != "waiting_reconciliation"
                else str(task_row["reconciliation_resume_control_status"] or "active")
            )
            connection.execute(
                """
                UPDATE workflow_task
                SET control_status = 'waiting_reconciliation',
                    cleanup_status = 'requires_attention',
                    attention_reason = ?,
                    reconciliation_resume_control_status = ?,
                    wait_reason = '{}', update_time = ?
                WHERE uuid = ? AND deleted_at IS NULL
                """,
                (normalized_reason, resume_control, opened_at, task_uuid),
            )
            mark_execution_locks_uncertain(
                connection,
                job_uuid=job_uuid,
                now=opened_at,
            )
            WorkflowStore._append_runtime_event(
                connection,
                task_uuid=task_uuid,
                job_uuid=job_uuid,
                kind="uncertainty_opened",
                from_status=str(job_row["status"]),
                to_status="execution_unknown",
                data={"reason": normalized_reason},
                now=opened_at,
            )
            self._append_invalidation(
                connection,
                task_uuid=task_uuid,
                now=opened_at,
            )
            return self._aggregate(connection, task_uuid)

    def project_uncertain_resolution_requested(
        self,
        job_uuid: str,
        *,
        command_uuid: str,
        reason: str,
        device_command_id: str,
    ) -> dict[str, Any]:
        """记录人工取消结论已请求 Edge 证明，但继续持有执行占用。"""

        normalized_command = str(command_uuid or "").strip()
        normalized_reason = str(reason or "").strip()
        normalized_device_command = str(device_command_id or "").strip()
        if not normalized_command or not normalized_reason:
            raise StoreConflict("UNKNOWN 处置命令和理由不能为空")
        with self._store.transaction() as connection:
            job = self._job_row(connection, job_uuid)
            task_uuid = str(job["workflow_task_uuid"])
            if job["status"] != "execution_unknown":
                raise StoreConflict("只有 execution_unknown 作业可以人工处置")
            leases = list_execution_locks(connection, job_uuid=job_uuid)
            if not leases or any(lease["state"] != "uncertain" for lease in leases):
                raise StoreConflict("作业缺少完整的 uncertain 执行占用")
            control = _decode_json_field(job["control_data"], fallback={})
            if not isinstance(control, Mapping):
                raise StoreConflict("作业 control_data 已损坏")
            expected = {
                "resolution": "canceled",
                "reason": normalized_reason,
                "command_uuid": normalized_command,
                "device_command_id": normalized_device_command,
                "status": "pending_edge_confirmation",
            }
            existing = control.get("manual_resolution")
            if existing is not None:
                if existing != expected:
                    raise StoreConflict("作业已有另一项 UNKNOWN 人工处置")
                return self._aggregate(connection, task_uuid)
            updated = dict(control)
            updated["manual_resolution"] = expected
            now = utc_now()
            connection.execute(
                """
                UPDATE workflow_node_job
                SET control_data = ?, update_time = ?
                WHERE uuid = ? AND status = 'execution_unknown'
                """,
                (
                    _encode_json_field(updated, field_name="control_data"),
                    now,
                    job_uuid,
                ),
            )
            self._append_invalidation(connection, task_uuid=task_uuid, now=now)
            return self._aggregate(connection, task_uuid)

    def list_execution_locks(self, job_uuid: str | None = None) -> list[dict[str, Any]]:
        """读取本地持久执行占用；可按作业身份过滤。"""

        with self._store.transaction() as connection:
            return list_execution_locks(connection, job_uuid=job_uuid)

    def project_feedback(
        self,
        *,
        job_uuid: str,
        sequence: int,
        feedback_type: str,
        data: Mapping[str, Any],
        observed_at: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """把 Edge 已提交的过程反馈幂等投影为有序历史和最新摘要。"""

        return self._evidence.commit_feedback(
            job_uuid=job_uuid,
            sequence=sequence,
            feedback_type=feedback_type,
            data=data,
            observed_at=observed_at,
            idempotency_key=idempotency_key,
        )

    def project_job_finished(
        self,
        *,
        job_uuid: str,
        scheduler_state: str,
        return_info: Mapping[str, Any] | None = None,
        error_info: Sequence[Any] | None = None,
        manual_confirmation_status: str | None = None,
    ) -> dict[str, Any]:
        """投影工作流节点作业（WorkflowNodeJob）的明确业务结果。

        参数：``job_uuid`` 是作业稳定身份；``scheduler_state`` 只接受本地
        ``success``、``failed``、设备明确 ``canceled`` 或 ``timeout``；
        ``return_info`` 是结果对象；``error_info`` 是错误详情序列；
        ``manual_confirmation_status`` 只供截止时间或取消收敛把关联确认原子关闭。
        返回：提交后的标准任务/作业聚合。异常：未知状态、结果类型、终态或载荷
        冲突抛出 ``StoreConflict``；身份缺失抛出
        ``StoreNotFound``。相同终态和载荷的投递重放（DeliveryReplay）零写入。
        """

        if scheduler_state not in _FINISHED_STATE_MAP:
            raise StoreConflict(f"不支持的本地完成状态：{scheduler_state}")
        if manual_confirmation_status not in {None, "timed_out", "canceled"}:
            raise StoreConflict("人工确认自动关闭状态非法")
        if return_info is not None and not isinstance(return_info, Mapping):
            raise StoreConflict("return_info 必须是对象")
        if error_info is not None and (
            not isinstance(error_info, Sequence)
            or isinstance(error_info, (str, bytes, bytearray))
        ):
            raise StoreConflict("error_info 必须是序列")

        # ``target_job_status`` 是 Backend-shaped 合同采用的标准作业终态。
        target_job_status = _FINISHED_STATE_MAP[scheduler_state]
        normalized_return_info = dict(return_info or {})
        normalized_error_info = list(error_info or [])
        return_info_json = _encode_json_field(
            normalized_return_info,
            field_name="return_info",
        )
        error_info_json = _encode_json_field(
            normalized_error_info,
            field_name="error_info",
        )

        with self._store.transaction() as connection:
            job_row = self._job_row(connection, job_uuid)
            task_uuid = job_row["workflow_task_uuid"]
            task_row = self._task_row(connection, task_uuid)
            current_job_status = job_row["status"]
            record_job_result(
                connection,
                job_row=job_row,
                outcome=target_job_status,
                return_info=normalized_return_info,
                error_info=normalized_error_info,
            )
            if current_job_status == target_job_status:
                same_return_info = (
                    _decode_json_field(
                        job_row["return_info"],
                        fallback={},
                    )
                    == normalized_return_info
                )
                same_error_info = (
                    _decode_json_field(
                        job_row["error_info"],
                        fallback=[],
                    )
                    == normalized_error_info
                )
                if same_return_info and same_error_info:
                    replayed_at = utc_now()
                    release_execution_locks(
                        connection,
                        job_uuid=job_uuid,
                        now=replayed_at,
                    )
                    close_pending_manual_confirmation(
                        connection,
                        job_uuid=job_uuid,
                        status=manual_confirmation_status or "canceled",
                        decided_at=replayed_at,
                    )
                    return self._aggregate(connection, task_uuid)
                raise StoreConflict(f"作业终态载荷冲突：{job_uuid}")
            if current_job_status in _TERMINAL_JOB_STATES:
                raise StoreConflict(f"作业终态冲突：{job_uuid}")
            if current_job_status not in {
                "dispatched",
                "running",
                "cancel_requested",
                "execution_unknown",
            }:
                raise StoreConflict(f"作业尚未派发，不能完成：{job_uuid}")
            if task_row["status"] not in {"running", "failed", "canceling"}:
                raise StoreConflict(f"父任务状态不接受作业结果：{task_uuid}")

            # ``finished_at`` 是明确作业结果落盘的统一完成时间。
            finished_at = utc_now()
            updated_jobs = connection.execute(
                """
                UPDATE workflow_node_job
                SET status = ?, return_info = ?, error_info = ?,
                    cancel_ack_deadline_at = NULL,
                    cancel_complete_deadline_at = NULL,
                    uncertainty_reason = NULL, finished_at = ?, update_time = ?
                WHERE uuid = ?
                  AND status IN (
                      'dispatched', 'running', 'cancel_requested', 'execution_unknown'
                  )
                  AND deleted_at IS NULL
                """,
                (
                    target_job_status,
                    return_info_json,
                    error_info_json,
                    finished_at,
                    finished_at,
                    job_uuid,
                ),
            ).rowcount
            if updated_jobs != 1:
                raise StoreConflict(f"作业完成状态发生并发变化：{job_uuid}")
            release_execution_locks(
                connection,
                job_uuid=job_uuid,
                now=finished_at,
            )
            settle_intervention_for_job(
                connection,
                job_uuid=job_uuid,
                now=finished_at,
            )
            close_pending_manual_confirmation(
                connection,
                job_uuid=job_uuid,
                status=manual_confirmation_status or "canceled",
                decided_at=finished_at,
            )
            WorkflowStore._append_runtime_event(
                connection,
                task_uuid=task_uuid,
                job_uuid=job_uuid,
                kind="job_transition",
                from_status=str(current_job_status),
                to_status=target_job_status,
                now=finished_at,
            )

            job_rows = self._job_rows(connection, task_uuid)
            if current_job_status == "execution_unknown" and not any(
                row["status"] == "execution_unknown" for row in job_rows
            ):
                connection.execute(
                    """
                    UPDATE workflow_task
                    SET control_status = COALESCE(
                            reconciliation_resume_control_status, 'active'
                        ),
                        cleanup_status = 'none', attention_reason = NULL,
                        reconciliation_resume_control_status = NULL,
                        update_time = ?
                    WHERE uuid = ? AND deleted_at IS NULL
                    """,
                    (finished_at, task_uuid),
                )
                WorkflowStore._append_runtime_event(
                    connection,
                    task_uuid=task_uuid,
                    job_uuid=job_uuid,
                    kind="uncertainty_resolved",
                    from_status="execution_unknown",
                    to_status=target_job_status,
                    now=finished_at,
                )
            # ``job_statuses`` 是决定父任务业务终态的完整兄弟作业状态集合。
            job_statuses = [row["status"] for row in job_rows]
            target_task_status: str | None = None
            if task_row["status"] == "canceling":
                if all(status in _TERMINAL_JOB_STATES for status in job_statuses):
                    target_task_status = "canceled"
            elif task_row["status"] != "failed":
                if "timeout" in job_statuses:
                    target_task_status = "timeout"
                elif "failed" in job_statuses:
                    target_task_status = "failed"
                elif "canceled" in job_statuses:
                    # execution_unknown 的人工取消证明终结的是整次 Task；后续节点
                    # 不得继续派发，物理清理由 settled 阶段统一完成。
                    target_task_status = "canceled"
                elif all(status == "succeeded" for status in job_statuses):
                    target_task_status = "succeeded"
            if target_task_status is not None:
                updated_tasks = connection.execute(
                    """
                    UPDATE workflow_task
                    SET status = ?, finished_at = ?, update_time = ?
                    WHERE uuid = ? AND status IN ('running', 'canceling')
                      AND deleted_at IS NULL
                    """,
                    (target_task_status, finished_at, finished_at, task_uuid),
                ).rowcount
                if updated_tasks != 1:
                    raise StoreConflict(f"任务终态发生并发变化：{task_uuid}")
                WorkflowStore._append_runtime_event(
                    connection,
                    task_uuid=task_uuid,
                    kind="task_transition",
                    from_status=str(task_row["status"]),
                    to_status=target_task_status,
                    now=finished_at,
                )
            self._append_invalidation(
                connection,
                task_uuid=task_uuid,
                now=finished_at,
            )
            return self._aggregate(connection, task_uuid)

    def recover_pending_manual_confirmation(self, job_uuid: str) -> dict[str, Any]:
        """把重启前尚未决策、也未下发设备的人工确认恢复为待调度。

        只允许 ``manual_confirm + pending confirmation + dispatched`` 这一可证明
        未越过物理派发边界的组合。已批准或已下发的作业不能借此
        退回 pending，应按结果不明处置。
        """

        with self._store.transaction() as connection:
            job = self._job_row(connection, job_uuid)
            if job["executor_kind"] != "manual_confirm":
                raise StoreConflict("作业不是人工确认节点")
            confirmation = connection.execute(
                """
                SELECT status FROM workflow_manual_confirmation
                WHERE workflow_node_job_uuid = ?
                ORDER BY opened_at DESC, uuid DESC LIMIT 1
                """,
                (job_uuid,),
            ).fetchone()
            if confirmation is None or confirmation["status"] != "pending":
                raise StoreConflict("人工确认已决策，不能安全重排")
            if job["status"] == "pending":
                return self._aggregate(connection, job["workflow_task_uuid"])
            if job["status"] != "dispatched":
                raise StoreConflict("人工确认作业状态不可安全恢复")
            now = utc_now()
            connection.execute(
                """
                UPDATE workflow_node_job
                SET status = 'pending', started_at = NULL,
                    cancel_ack_deadline_at = NULL,
                    cancel_complete_deadline_at = NULL,
                    wait_reason = '{}', update_time = ?
                WHERE uuid = ? AND status = 'dispatched'
                """,
                (now, job_uuid),
            )
            release_execution_locks(connection, job_uuid=job_uuid, now=now)
            WorkflowStore._append_runtime_event(
                connection,
                task_uuid=job["workflow_task_uuid"],
                job_uuid=job_uuid,
                kind="job_transition",
                from_status="dispatched",
                to_status="pending",
                now=now,
            )
            return self._aggregate(connection, job["workflow_task_uuid"])


__all__ = ["TaskRuntimeProjection"]
