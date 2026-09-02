"""把标准工作流任务（WorkflowTask）委托给既有本地调度器。"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from unilabos.app.scheduler.dag_state import WorkflowRun
from unilabos.app.scheduler.dispatch import CommittedJobOutcome
from unilabos.app.scheduler.inventory.dispatch_admission import (
    AliquotDispatchCondition,
    DispatchAdmissionRequest,
    DispatchFence,
    DispatchResource,
    OperateInPlaceCondition,
    TransferDispatchCondition,
)
from unilabos.app.scheduler.inventory.station_resource import (
    MaterialTransferCommand,
    StationResourceError,
)
from unilabos.app.scheduler.material_source_resolution import (
    MaterialSourceResolutionCoordinator,
)
from unilabos.app.scheduler.models import DispatchedJob
from unilabos.app.scheduler.resource_wait_policy import (
    is_temporary_resource_condition,
)
from unilabos.app.scheduler.service import EdgeScheduler
from unilabos.workflow.dispatch_permit_saga import (
    freeze_projected_dispatch_permit,
)
from unilabos.workflow.execution_lock_lease import list_execution_locks
from unilabos.workflow.manual_confirmation import ManualConfirmationStore
from unilabos.workflow.material_transfer_settlement import (
    MaterialTransferSettlement,
)
from unilabos.workflow.material_aliquot_settlement import MaterialAliquotSettlement
from unilabos.workflow.physical_settlement_policy import (
    MATERIAL_CONTENT_RECONCILIATION_REQUIRED,
    MATERIAL_TRANSFER_RECONCILIATION_REQUIRED,
)
from unilabos.workflow.quantity_inventory import WorkflowQuantityInventory
from unilabos.workflow.store import StoreConflict, StoreNotFound, WorkflowStore
from unilabos.workflow.task_runtime_projection import TaskRuntimeProjection
from unilabos.workflow.workflow_spec_compiler import WorkflowSpecCompiler

logger = logging.getLogger(__name__)

_DEFAULT_CANCEL_ACK_TIMEOUT_SECONDS = 10.0
_DEFAULT_CANCEL_COMPLETE_TIMEOUT_SECONDS = 60.0


class TaskSchedulerBridgeError(RuntimeError):
    """工作流任务不能安全进入本地调度器时使用的稳定桥接错误。"""


class TaskSchedulerBridge:
    """隐藏任务编译、短期物料门禁、生命周期投影和监听器清理。"""

    def __init__(
        self,
        store: WorkflowStore,
        *,
        scheduler: EdgeScheduler,
        compiler: WorkflowSpecCompiler | None = None,
        projection: TaskRuntimeProjection | None = None,
        cancel_ack_timeout_seconds: float = _DEFAULT_CANCEL_ACK_TIMEOUT_SECONDS,
        cancel_complete_timeout_seconds: float = (
            _DEFAULT_CANCEL_COMPLETE_TIMEOUT_SECONDS
        ),
    ) -> None:
        """装配唯一工作流任务调度桥（TaskSchedulerBridge）。

        参数：``store`` 是标准任务/作业写权威；``scheduler`` 是既有本地调度器；
        ``compiler`` 把冻结执行计划（ExecutionPlan）转换为遗留调度规格；
        ``projection`` 把调度生命周期写回标准事实；两个取消超时分别约束执行器
        受理与设备终态等待。返回无；初始化会只读恢复此前受阻的准入任务。异常：
        读取恢复事实或超时参数转换失败时原样传播；库存服务只能从
        ``scheduler.inventory_service`` 读取，不能另行注入。
        """

        # ``_store`` 是本桥唯一的工作流任务（WorkflowTask）持久事实来源。
        self._store = store
        # ``_scheduler`` 同时持有唯一允许复用的本地库存权威（Inventory Authority）。
        self._scheduler = scheduler
        self._compiler = compiler or WorkflowSpecCompiler()
        self._projection = projection or TaskRuntimeProjection(store)
        self._max_in_flight_jobs = int(getattr(scheduler, "max_in_flight_jobs", 100))
        self._max_active_tasks = int(getattr(scheduler, "max_active_tasks", 500))
        self._max_tasks_per_workflow = int(
            getattr(scheduler, "max_tasks_per_workflow", 100)
        )
        self._cancel_ack_timeout_seconds = max(
            0.01,
            float(cancel_ack_timeout_seconds),
        )
        self._cancel_complete_timeout_seconds = max(
            self._cancel_ack_timeout_seconds,
            float(cancel_complete_timeout_seconds),
        )
        self._cancel_timers: dict[str, threading.Timer] = {}
        self._cancel_timer_lock = threading.RLock()
        self._manual_confirmations = ManualConfirmationStore(store)
        self._manual_timers: dict[str, threading.Timer] = {}
        self._manual_timer_lock = threading.RLock()
        # 关闭时要等待已进入的超时收敛回调完成，避免回调在
        # 外部关闭 SQLite 后继续投影终态。
        self._manual_callback_lock = threading.RLock()
        # ``_material_sources`` 复用调度器持有的同一库存权威，先于任何普通派发
        # 协调整个任务的物料来源解析作业（MaterialSourceResolutionJob）。
        self._material_sources = MaterialSourceResolutionCoordinator(
            inventory=scheduler.inventory_service,
            projection=self._projection,
        )
        inventory_store = getattr(scheduler.inventory_service, "store", None)
        self._quantity_inventory = (
            WorkflowQuantityInventory(store, scheduler.inventory_service)
            if inventory_store is not None
            else None
        )
        self._material_transfer_settlement = MaterialTransferSettlement(
            scheduler.station_resource_inventory
        )
        self._material_aliquot_settlement = MaterialAliquotSettlement(
            scheduler.station_resource_inventory
        )
        # ``_task_by_job`` 只过滤本桥提交到共享调度器的作业，不承担持久恢复。
        self._task_by_job: dict[str, str] = {}
        # ``_submitted_tasks`` 标识仍可进行准入重试（AdmissionRetry）的本地运行。
        self._submitted_tasks: set[str] = set()
        # ``_admission_pending_tasks`` 从持久准入事实恢复；内存集合仅作本轮调度索引，
        # 进程重启不会丢失仍需重试的任务身份。
        self._admission_pending_tasks: set[str] = set(
            TaskRuntimeProjection(store).list_blocked_material_tasks()
        )
        self._closed = False
        scheduler.add_admission_retry_listener(self._retry_pending_admissions)
        scheduler.bind_dispatch_admission_authority(self._on_job_pre_dispatch)
        scheduler.add_job_execution_wait_listener(self._on_job_execution_wait)
        scheduler.add_job_dispatch_accepted_listener(self._on_job_dispatch_accepted)
        scheduler.add_job_dispatch_uncertain_listener(self._on_job_dispatch_uncertain)
        scheduler.add_job_cancel_accepted_listener(self._on_job_cancel_accepted)
        scheduler.add_job_cancel_uncertain_listener(self._on_job_cancel_uncertain)
        scheduler.add_job_cancel_no_send_listener(self._on_job_cancel_no_send)
        scheduler.add_job_feedback_listener(self._on_job_feedback)
        scheduler.add_job_outcome_listener(self._on_job_outcome)
        scheduler.add_job_finished_listener(self._on_job_finished)
        scheduler.add_job_settled_listener(self._on_job_settled)
        scheduler.add_execution_process_restarted_listener(
            self._on_execution_process_restarted
        )
        scheduler.set_drain_blocker_provider(self.active_or_uncertain_job_ids)

    def prepare_inventory_allocations(
        self,
        connection: Any,
        *,
        graph: Mapping[str, Any],
        prepared: Any,
        task_uuid: str,
        bindings: list[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        """在 Task 首次写入前准备数量型库存分配。

        参数：``connection`` 是工作流创建事务；``graph/prepared`` 是同一冻结图
        和执行计划；``task_uuid`` 是运行身份；``bindings`` 是 HTTP 显式绑定。
        返回：既有 ``workflow_inventory_allocation`` 表的待插入行。异常：本次执行
        有逻辑库存需求但未装配库存权威时关闭式失败；具体校验错误原样传播。
        """

        active_nodes = {str(job.get("workflow_node_uuid")) for job in prepared.jobs}
        active_requirements = [
            requirement
            for requirement in graph.get("inventory_requirements", [])
            if isinstance(requirement, Mapping)
            and str(requirement.get("consume_node_uuid")) in active_nodes
        ]
        if self._quantity_inventory is None:
            if active_requirements or bindings:
                raise StoreConflict("工作流数量型库存未装配本地库存权威")
            return []
        return self._quantity_inventory.prepare_task_allocations(
            connection,
            graph=graph,
            prepared=prepared,
            task_uuid=task_uuid,
            bindings=bindings,
            defer_inventory_reservation=True,
        )

    def preflight_inventory_allocations(
        self,
        *,
        graph: Mapping[str, Any],
        prepared: Any,
        bindings: list[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        """只读检查候选任务的共享数量库存准入。

        参数：图、冻结候选输入和显式库存绑定与正式提交相同。返回：已验证分配
        预览。异常：存在活动需求但本地库存权威未装配，或任一数量不足时失败关闭。
        本方法不创建任务、预留、台账或 Outbox。
        """

        active_nodes = {str(job.get("workflow_node_uuid")) for job in prepared.jobs}
        active_requirements = [
            requirement
            for requirement in graph.get("inventory_requirements", [])
            if isinstance(requirement, Mapping)
            and str(requirement.get("consume_node_uuid")) in active_nodes
        ]
        if self._quantity_inventory is None:
            if active_requirements or bindings:
                raise StoreConflict("工作流数量型库存未装配本地库存权威")
            return []
        return self._quantity_inventory.preflight_task_allocations(
            graph=graph,
            prepared=prepared,
            bindings=bindings,
        )

    def discard_uncommitted_inventory(self, task_uuid: str) -> None:
        """补偿未提交 Task 在库存权威中留下的数量预留。

        参数：``task_uuid`` 是创建事务最终回滚的任务身份。返回无。异常：库存补偿
        失败原样传播并由 API 报告内部错误；未装配数量库存或重复调用均安全无写入。
        """

        if self._quantity_inventory is None:
            return
        self._quantity_inventory.discard_uncommitted_task(task_uuid)

    def list_task_inventory_consumptions(self, task_uuid: str) -> list[dict[str, Any]]:
        """返回任务的数量型库存消费事实；未装配库存时返回空数组。"""

        if self._quantity_inventory is None:
            return []
        return self._quantity_inventory.list_task_consumptions(task_uuid)

    def list_job_inventory_consumptions(self, job_uuid: str) -> list[dict[str, Any]]:
        """返回作业的数量型库存消费事实；未装配库存时返回空数组。"""

        if self._quantity_inventory is None:
            return []
        return self._quantity_inventory.list_job_consumptions(job_uuid)

    def list_reagent_inventory_consumptions(
        self, reagent_uuid: str
    ) -> list[dict[str, Any]]:
        """返回试剂实例的数量型库存消费谱系；未装配库存时返回空数组。"""

        if self._quantity_inventory is None:
            return []
        return self._quantity_inventory.list_reagent_consumptions(reagent_uuid)

    def submit(self, task: Mapping[str, Any]) -> dict[str, Any]:
        """提交已经持久化的工作流任务（WorkflowTask）。

        参数：``task`` 是创建事务返回的标准任务投影。返回：调度同步推进后的标准
        任务/作业聚合。异常：桥关闭、任务身份非法、冻结计划编译失败、缺少库存权威
        或派发前投影冲突时失败关闭；失败不会创建新的任务/作业身份。
        """

        if self._closed:
            raise TaskSchedulerBridgeError("工作流任务调度桥已经关闭")
        # ``task_uuid`` 是本地调度运行、遗留预留和标准任务共用的稳定身份。
        task_uuid = self._required_text(task.get("uuid"), field="task.uuid")
        if task_uuid in self._submitted_tasks:
            return self._aggregate(task_uuid)
        if task_uuid in self._admission_pending_tasks:
            return self._aggregate(task_uuid)
        jobs: list[dict[str, Any]] = []
        registered = False
        try:
            persisted_task = self._store.get_task(task_uuid)
            # ``jobs`` 是创建事务已经确定的工作流节点作业（WorkflowNodeJob）集合。
            jobs = self._store.list_jobs(task_uuid)
            spec = self._compiler.compile(persisted_task, jobs)
            if (
                spec.material_requirements_by_node()
                and self._scheduler.inventory_service is None
            ):
                raise TaskSchedulerBridgeError(
                    "工作流声明了物料需求，但本地调度器没有装配库存权威"
                )
            # 物料来源解析必须先提交完整短期预留和逐来源结果；受阻时不注册普通
            # 动作，也不让任何作业越过物理派发边界。
            allocation_reader = getattr(
                self._quantity_inventory,
                "task_allocations",
                None,
            )
            quantity_allocations = (
                allocation_reader(task_uuid) if callable(allocation_reader) else ()
            )
            material_resolution = self._material_sources.reconcile(
                persisted_task,
                jobs,
                quantity_allocations=quantity_allocations,
            )
            if material_resolution.status == "blocked":
                self._admission_pending_tasks.add(task_uuid)
                return self._aggregate(task_uuid)
            self._admission_pending_tasks.discard(task_uuid)
            # 自动物料来源（MaterialSource）的准入结果已原子写入既有动作作业参数；
            # 重新读取同一作业身份后再编译，禁止派发准入前的空参数快照。
            jobs = self._store.list_jobs(task_uuid)
            spec = self._compiler.compile(persisted_task, jobs)
            if not spec.nodes:
                # 仅来源任务没有普通作业可触发调度器终态清理；协调器必须在返回成功
                # 前幂等释放仍活跃的短期预留，不能让测试或调用方承担内部清理。
                self._material_sources.release_terminal_reservations(
                    task_uuid,
                    reason="workflow_succeeded",
                )
                return self._aggregate(task_uuid)

            dispatch_job_uuids = {node.job_id for node in spec.nodes}
            for job in jobs:
                # ``job_uuid`` 是监听器回调与标准持久作业之间的稳定路由身份。
                job_uuid = self._required_text(job.get("uuid"), field="jobs[].uuid")
                if job_uuid not in dispatch_job_uuids:
                    continue
                self._task_by_job[job_uuid] = task_uuid
            self._submitted_tasks.add(task_uuid)
            registered = True
            submission = self._scheduler.submit_workflow(spec)
            self._project_scheduler_trace_context(
                task_uuid,
                submission.get("trace_context"),
            )
            # ``scheduler_state`` 是内部等料或运行状态；投影层负责限制 wire 状态。
            scheduler_state = self._required_text(
                submission.get("state"), field="scheduler.state"
            )
            return self._projection.project_submission(task_uuid, scheduler_state)
        except Exception as error:
            if self._crossed_dispatch_boundary(jobs):
                raise TaskSchedulerBridgeError(
                    "工作流任务派发结果不确定，已保留在途执行等待明确结果"
                ) from error
            if registered:
                self._cancel_failed_submission(task_uuid, jobs)
            if isinstance(error, TaskSchedulerBridgeError):
                raise
            raise TaskSchedulerBridgeError(
                "工作流任务无法安全提交到本地调度器"
            ) from error

    def retry_admission(self, task_uuid: str) -> dict[str, Any]:
        """对同一待处理任务触发准入重试（AdmissionRetry）。

        参数：``task_uuid`` 是此前已提交但因物料不足等待的稳定任务身份。返回：
        重排后的标准任务/作业聚合。异常：桥关闭、未知任务或调度重排失败时传播；
        本操作绝不创建新任务、作业或执行尝试身份。
        """

        if self._closed:
            raise TaskSchedulerBridgeError("工作流任务调度桥已经关闭")
        normalized_uuid = self._required_text(task_uuid, field="task_uuid")
        admission = TaskRuntimeProjection(self._store).get_material_admission(
            normalized_uuid
        )
        if normalized_uuid in self._admission_pending_tasks or (
            admission is not None and admission.get("status") == "blocked"
        ):
            # 显式准入重试复用同一持久任务/作业身份；先移除内存标记，让 ``submit``
            # 真正重做整图预留，若仍受阻会原样重新登记。
            self._admission_pending_tasks.discard(normalized_uuid)
            return self.submit(self._store.get_task(normalized_uuid))
        if normalized_uuid not in self._submitted_tasks:
            raise TaskSchedulerBridgeError("工作流任务尚未提交到本地调度器")
        self._scheduler.reschedule()
        return self._aggregate(normalized_uuid)

    def step(
        self,
        task_uuid: str,
        *,
        target_node_uuid: str | None = None,
    ) -> dict[str, Any]:
        """让已经提交且暂停的单步任务只派发一个就绪节点。"""

        if self._closed:
            raise TaskSchedulerBridgeError("工作流任务调度桥已经关闭")
        normalized_uuid = self._required_text(task_uuid, field="task_uuid")
        # ``_submitted_tasks`` 只是桥接层的监听路由账本，不是运行权威。工作区
        # 重组或监听器世代切换后它可能暂时缺少仍由同一个 EdgeScheduler 持有的
        # 运行；单步准入必须由调度器自身验证任务存在、单步模式和暂停状态。
        try:
            return self._scheduler.step_workflow(
                normalized_uuid,
                target_node_id=target_node_uuid,
            )
        except ValueError as error:
            raise TaskSchedulerBridgeError(str(error)) from error

    def cancel(
        self,
        task_uuid: str,
        *,
        command_uuid: str | None = None,
    ) -> dict[str, Any]:
        """持久化取消请求并请求本地执行器安全停止设备作业。

        参数：``task_uuid`` 是已提交任务身份；``command_uuid`` 是公开幂等控制命令
        身份，直接调用时自动生成。返回取消受理后的任务/作业聚合。异常：桥关闭、
        任务未提交或投影冲突时抛稳定错误。

        未发送作业同步取消；设备在途作业保持 ``cancel_requested`` 和执行锁，等待
        执行器受理及明确终态。取消请求成功不等于设备已经安全停止。
        """

        if self._closed:
            raise TaskSchedulerBridgeError("工作流任务调度桥已经关闭")
        normalized_uuid = self._required_text(task_uuid, field="task_uuid")
        if self._scheduler.workflow_snapshot(normalized_uuid) is None:
            raise TaskSchedulerBridgeError("工作流任务尚未提交到本地调度器")
        normalized_command_uuid = self._required_text(
            command_uuid or str(uuid4()),
            field="command_uuid",
        )
        now = datetime.now(timezone.utc)
        ack_deadline = now + timedelta(seconds=self._cancel_ack_timeout_seconds)
        complete_deadline = now + timedelta(
            seconds=self._cancel_complete_timeout_seconds
        )
        self._projection.project_cancel_requested(
            normalized_uuid,
            command_uuid=normalized_command_uuid,
            ack_deadline_at=self._format_time(ack_deadline),
            complete_deadline_at=self._format_time(complete_deadline),
        )
        if not self._scheduler.cancel_workflow(normalized_uuid):
            raise TaskSchedulerBridgeError("工作流任务尚未提交到本地调度器")
        self._store.stop_debug(normalized_uuid)
        self._admission_pending_tasks.discard(normalized_uuid)
        aggregate = self._aggregate(normalized_uuid)
        for job in aggregate["jobs"]:
            if job.get("status") == "cancel_requested":
                self._schedule_cancel_timeout(job)
        if aggregate["task"]["status"] == "canceled":
            self._submitted_tasks.discard(normalized_uuid)
        return aggregate

    def pause(self, task_uuid: str) -> dict[str, Any]:
        """Pause future dispatch while preserving already in-flight device work."""

        if self._closed:
            raise TaskSchedulerBridgeError("工作流任务调度桥已经关闭")
        normalized_uuid = self._required_text(task_uuid, field="task_uuid")
        try:
            result = self._scheduler.pause_workflow(normalized_uuid)
            task = self._store.set_task_control_status(
                normalized_uuid, control_status="paused"
            )
            return {"task": task, "scheduler": result}
        except (StoreConflict, ValueError) as error:
            raise TaskSchedulerBridgeError(str(error)) from error

    def resume(self, task_uuid: str) -> dict[str, Any]:
        """Resume a command-paused workflow under the same Task identity."""

        if self._closed:
            raise TaskSchedulerBridgeError("工作流任务调度桥已经关闭")
        normalized_uuid = self._required_text(task_uuid, field="task_uuid")
        try:
            result = self._scheduler.resume_workflow(normalized_uuid)
            task = self._store.set_task_control_status(
                normalized_uuid, control_status="active"
            )
            return {"task": task, "scheduler": result}
        except (StoreConflict, ValueError) as error:
            raise TaskSchedulerBridgeError(str(error)) from error

    def decide_manual_confirmation(
        self,
        job_uuid: str,
        *,
        approved: bool,
        param: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """让调度器按同一作业身份继续设备动作或明确拒绝。"""

        if self._closed:
            raise TaskSchedulerBridgeError("工作流任务调度桥已经关闭")
        normalized_job_uuid = self._required_text(job_uuid, field="job_uuid")
        try:
            result = self._scheduler.resolve_manual_confirmation(
                normalized_job_uuid,
                approved=approved,
                param=param,
            )
            # 人工决策一旦被调度器接受，原确认截止时间就不再有效。
            # 对“确认后继续设备动作”的节点也必须立即取消计时器，
            # 否则设备正在执行时，旧计时器会误触发超时收敛。
            self._cancel_manual_confirmation_timer(normalized_job_uuid)
            return result
        except (StoreConflict, ValueError) as error:
            raise TaskSchedulerBridgeError(str(error)) from error

    def request_uncertain_resolution(
        self,
        job_uuid: str,
        *,
        reason: str,
        device_command_id: str,
    ) -> dict[str, Any]:
        """创建 Edge UNKNOWN 处置命令并保持执行锁到提交证明到达。"""

        if self._closed:
            raise TaskSchedulerBridgeError("工作流任务调度桥已经关闭")
        normalized_job = self._required_text(job_uuid, field="job_uuid")
        normalized_reason = self._required_text(reason, field="reason")
        try:
            result = self._scheduler.request_uncertain_resolution(
                normalized_job,
                reason=normalized_reason,
            )
            command_uuid = self._required_text(
                result.get("command_uuid"),
                field="resolution.command_uuid",
            )
            aggregate = self._projection.project_uncertain_resolution_requested(
                normalized_job,
                command_uuid=command_uuid,
                reason=normalized_reason,
                device_command_id=str(device_command_id or "").strip(),
            )
            return {
                "job": next(
                    job for job in aggregate["jobs"] if job["uuid"] == normalized_job
                ),
                "resolution_command_uuid": command_uuid,
                "pending_edge_confirmation": True,
                "created": bool(result.get("created", True)),
            }
        except (StoreConflict, StoreNotFound, ValueError) as error:
            raise TaskSchedulerBridgeError(str(error)) from error

    def settle_failed_material_transfer(
        self,
        job_uuid: str,
        *,
        actual_change_set: Mapping[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        """提交失败转运的实际物料位置并释放物理占用。

        参数：``job_uuid`` 是等待物料位置对账的失败作业；
        ``actual_change_set`` 必须完整声明物料、实际父资源以及实际库位 UUID 或
        名称；``reason`` 是操作员说明。返回：保持失败主状态、已完成物理结算的
        作业。异常：身份、库存事实、停止证明或幂等载荷冲突时抛桥接错误，且不会
        提前释放库存 Claim/Fence。
        """

        if self._closed:
            raise TaskSchedulerBridgeError("工作流任务调度桥已经关闭")
        normalized_job = self._required_text(job_uuid, field="job_uuid")
        normalized_reason = self._required_text(reason, field="reason")
        inventory = self._scheduler.station_resource_inventory
        if inventory is None:
            raise TaskSchedulerBridgeError("物料位置对账缺少工站库存权威")
        try:
            persisted_job = self._store.get_job(normalized_job)
            expected_change = persisted_job.get("expected_change_set")
            control = persisted_job.get("control_data")
            if (
                isinstance(expected_change, Mapping)
                and expected_change.get("kind") == "material_content_aliquot"
            ):
                receipts = actual_change_set.get("receipts")
                if (
                    persisted_job.get("status")
                    not in {"failed", "canceled", "timeout"}
                    or persisted_job.get("uncertainty_reason")
                    != MATERIAL_CONTENT_RECONCILIATION_REQUIRED
                    or not isinstance(receipts, list)
                    or not isinstance(control, Mapping)
                    or not isinstance(
                        control.get("physical_settlement"), Mapping
                    )
                    or not control["physical_settlement"].get(
                        "execution_stopped"
                    )
                ):
                    raise StoreConflict(
                        "失败分装缺少完整目标回执或设备停止证明"
                    )
                claim = self._projection.get_execution_claim(normalized_job)
                if claim is None:
                    raise StoreConflict(
                        f"失败分装作业缺少库存 Claim：{normalized_job}"
                    )
                settled = self._material_aliquot_settlement.settle_success(
                    job=persisted_job,
                    execution_claim=claim,
                    receipts=receipts,
                )
                change_set = {
                    "kind": "material_content_aliquot",
                    "source_material_uuid": expected_change.get(
                        "source_material_uuid"
                    ),
                    "target_material_uuids": list(
                        expected_change.get("target_material_uuids") or []
                    ),
                    "receipts": [dict(item) for item in receipts],
                    "inventory_result": dict(settled or {}),
                }
                aggregate = self._projection.project_failed_job_inventory_reconciled(
                    normalized_job,
                    actual_change_set=change_set,
                    reason=normalized_reason,
                )
                inventory.transition_dispatch_permit(
                    str(claim["claim_uuid"]), target_state="released"
                )
                self._finish_settled_terminal_task(normalized_job)
                return next(
                    item
                    for item in aggregate["jobs"]
                    if item["uuid"] == normalized_job
                )
            change_set = self._normalize_actual_material_change_set(
                actual_change_set
            )
            if (
                persisted_job.get("status") not in {"failed", "canceled", "timeout"}
                or persisted_job.get("uncertainty_reason")
                != MATERIAL_TRANSFER_RECONCILIATION_REQUIRED
                or not isinstance(expected_change, Mapping)
                or expected_change.get("kind") != "material_transfer"
                or change_set["material_uuid"]
                != expected_change.get("material_uuid")
                or not isinstance(control, Mapping)
                or not isinstance(control.get("physical_settlement"), Mapping)
                or not control["physical_settlement"].get("execution_stopped")
            ):
                raise StoreConflict(
                    "失败转运缺少匹配的预期变化或设备停止证明"
                )
            claim = self._projection.get_execution_claim(normalized_job)
            if claim is None:
                raise StoreConflict(f"失败转运作业缺少库存 Claim：{normalized_job}")
            settled_material = inventory.settle_material_transfer(
                MaterialTransferCommand(
                    material_uuid=change_set["material_uuid"],
                    target_owner_material_uuid=change_set[
                        "target_owner_material_uuid"
                    ],
                    target_site_uuid=change_set.get("target_site_uuid", ""),
                    target_site_name=change_set.get("target_site_name", ""),
                    actor="physical_settlement",
                    causation_id=normalized_job,
                    effect_uuid=str(
                        persisted_job.get("dispatch_effect_uuid") or ""
                    ),
                    claim_uuid=str(claim["claim_uuid"]),
                    job_uuid=normalized_job,
                    attempt=int(persisted_job.get("attempt") or 0),
                    parameter_hash=str(
                        persisted_job.get("dispatch_parameter_hash") or ""
                    ),
                    expected_change_set=dict(expected_change),
                    fences=tuple(
                        DispatchFence(
                            lock_key=str(fence["lock_key"]),
                            fencing_token=int(fence["fencing_token"]),
                        )
                        for fence in claim.get("fences", [])
                    ),
                )
            )
            change_set["inventory_result"] = dict(settled_material)
            aggregate = self._projection.project_failed_job_inventory_reconciled(
                normalized_job,
                actual_change_set=change_set,
                reason=normalized_reason,
            )
            inventory.transition_dispatch_permit(
                str(claim["claim_uuid"]),
                target_state="released",
            )
            self._finish_settled_terminal_task(normalized_job)
            return next(
                job
                for job in aggregate["jobs"]
                if job["uuid"] == normalized_job
            )
        except (StoreConflict, StoreNotFound, StationResourceError, ValueError) as error:
            raise TaskSchedulerBridgeError(str(error)) from error

    def recover_active_tasks(self) -> list[dict[str, Any]]:
        """恢复未派发任务，并失败执行进程重启时仍在途的任务。

        参数：无。返回：已恢复任务的标准聚合列表；包括运行中任务和尚未执行
        首步的 ``pending + paused`` 单步任务。已成功作业仅
        恢复 DAG 返回值，待处理作业才可派发；发现 ``dispatched`` 或
        ``running`` 作业时将作业和父任务转为 ``failed``、跳过尚未开始的节点并
        保留不确定执行占用，返回的聚合可直接用于告警和物理结算。
        异常：冻结计划或持久事实不一致时记录后跳过，不阻止其他
        可证明安全的任务恢复。
        """

        if self._closed:
            raise TaskSchedulerBridgeError("工作流任务调度桥已经关闭")
        inventory = self._scheduler.station_resource_inventory
        if inventory is not None:
            inventory.release_unprojected_dispatch_permits(
                known_claim_uuids=(
                    self._projection.list_active_execution_claim_uuids()
                )
            )
        elif self._scheduler.physical_dispatch_enabled:
            raise TaskSchedulerBridgeError("物理部署缺少库存 DispatchPermit 权威")
        # 先补齐跨两个 SQLite 的库存消费意图，再恢复 Job→Task 路由并重放
        # Edge 已提交结果，避免结果投影领先于实际库存结算。
        if self._quantity_inventory is not None:
            self._quantity_inventory.recover_pending()
        self._recover_terminal_inventory_cleanup()
        # 先恢复持久 Job→Task 路由，再重放 Edge 已提交的反馈/结果。
        # 这一顺序可以收敛“Edge 已落盘，工作流库未投影”的崩溃点，
        # 且不会把结果重放误当成物理执行重试。
        self._register_active_recovery_routes()
        self._scheduler.replay_persisted_edge_projections(
            feedback_listener=self._on_job_feedback,
            outcome_listener=self._replay_persisted_job_outcome,
            finished_listener=self._replay_persisted_job_finished,
        )
        recovered: list[dict[str, Any]] = []
        # Backend 启动扫描包含普通 pending Task，并按 create_time/uuid 稳定排序。
        # Local 使用同一顺序恢复，保证创建已提交、调度调用尚未发生的崩溃窗口
        # 能以原 Task/Job 身份幂等重入。
        for task in self._store.list_recoverable_tasks():
            try:
                if task.get("status") == "pending" and not (
                    task.get("run_mode") == "step"
                    and task.get("control_status") == "paused"
                ):
                    aggregate = self.submit(task)
                else:
                    aggregate = self._recover_running_task(task)
            except Exception:  # 单任务损坏不影响其他恢复
                logger.exception(
                    "活动工作流任务无法安全恢复：%s",
                    task.get("uuid"),
                )
                continue
            if aggregate is not None:
                recovered.append(aggregate)
        return recovered

    def _recover_terminal_inventory_cleanup(self) -> None:
        """补齐终态 Task 在进程退出前未完成的库存释放与清理提交。

        参数与返回均为空。扫描成功、失败、取消和超时任务；仍存在 dispatched、
        running 或 cancel_requested 作业时保留全部占用。安全
        任务先幂等释放数量与实例物料预留，异常终态最后才写 cleanup settled；
        任一释放失败原样传播，禁止把未完成清理伪装成已结算。
        """

        unsafe_job_statuses = {
            "dispatched",
            "running",
            "cancel_requested",
        }
        for status in ("succeeded", "failed", "canceled", "timeout"):
            page = 1
            while True:
                task_page = self._store.list_tasks(
                    page=page,
                    page_size=200,
                    status=status,
                )
                for task in task_page["items"]:
                    task_uuid = str(task["uuid"])
                    jobs = self._store.list_jobs(task_uuid)
                    uncertain_jobs = [
                        job
                        for job in jobs
                        if str(job.get("uncertainty_reason") or "").strip()
                    ]
                    if uncertain_jobs:
                        self._mark_inventory_claims_uncertain(
                            {"task": task, "jobs": jobs}
                        )
                        continue
                    if any(
                        str(job.get("status")) in unsafe_job_statuses for job in jobs
                    ):
                        continue
                    reason = f"workflow_{status}_recovery"
                    if self._quantity_inventory is not None:
                        self._quantity_inventory.release_task(
                            task_uuid,
                            reason=reason,
                        )
                    if any(
                        job.get("executor_kind") == "material_source" for job in jobs
                    ):
                        self._material_sources.release_terminal_reservations(
                            task_uuid,
                            reason=reason,
                        )
                    if status != "succeeded" and task.get("cleanup_status") in {
                        "none",
                        "pending",
                        "required",
                        "canceling",
                    }:
                        self._projection.project_cleanup_settled(task_uuid)
                if page * 200 >= int(task_page["total"]):
                    break
                page += 1

    def _mark_inventory_claims_uncertain(
        self,
        aggregate: Mapping[str, Any],
    ) -> None:
        """把等待物理结算作业的库存 Claim 统一冻结为 uncertain。

        参数：``aggregate`` 是同一任务的持久 Task/Job 聚合。返回无；只处理带
        ``uncertainty_reason`` 且已有工作流 Claim 的作业。异常：物理部署缺少库存
        权威、Claim 或状态转换失败时原样传播；记录型干跑允许没有库存副本。
        """

        inventory = self._scheduler.station_resource_inventory
        jobs = aggregate.get("jobs")
        if not isinstance(jobs, list):
            raise StoreConflict("任务聚合缺少作业数组")
        for job in jobs:
            if not isinstance(job, Mapping) or not str(
                job.get("uncertainty_reason") or ""
            ).strip():
                continue
            job_uuid = self._required_text(job.get("uuid"), field="job.uuid")
            claim = self._projection.get_execution_claim(job_uuid)
            if claim is None:
                if self._scheduler.physical_dispatch_enabled:
                    raise StoreConflict(
                        f"等待物理结算的作业缺少库存 Claim：{job_uuid}"
                    )
                continue
            if inventory is None:
                if self._scheduler.physical_dispatch_enabled:
                    raise StoreConflict("物理部署缺少库存 DispatchPermit 权威")
                continue
            inventory.transition_dispatch_permit(
                str(claim["claim_uuid"]),
                target_state="uncertain",
            )

    def _register_active_recovery_routes(self) -> None:
        """从持久任务重建结果重放所需的稳定路由。"""

        for status in ("running", "pending", "canceling"):
            page = 1
            while True:
                task_page = self._store.list_tasks(
                    page=page,
                    page_size=200,
                    status=status,
                )
                for task in task_page["items"]:
                    task_uuid = self._required_text(task.get("uuid"), field="task.uuid")
                    for job in self._store.list_jobs(task_uuid):
                        if job.get("status") in {
                            "dispatched",
                            "running",
                            "cancel_requested",
                        }:
                            job_uuid = self._required_text(
                                job.get("uuid"), field="job.uuid"
                            )
                            self._task_by_job[job_uuid] = task_uuid
                if page * 200 >= int(task_page["total"]):
                    break
                page += 1

    def _replay_persisted_job_finished(
        self,
        job_uuid: str,
        success: bool,
        ret_value: Any,
        suc_type: str,
    ) -> None:
        """把已提交 Edge 结果先投影终态，再收敛任务清理。"""

        self._on_job_finished(job_uuid, success, ret_value, suc_type)
        self._on_job_settled(job_uuid, success, ret_value, suc_type)

    def _replay_persisted_job_outcome(
        self,
        job_uuid: str,
        outcome: CommittedJobOutcome,
    ) -> None:
        """把已提交保真结果投影终态并收敛任务清理。

        参数：``job_uuid`` 是持久作业身份；``outcome`` 是 Edge 发件箱重放的完整
        终态证据。返回无。异常：工作流投影或清理失败向上传播，结果保持待投影；
        本方法只做投递重放（DeliveryReplay），不会再次派发物理作业。
        """

        self._on_job_outcome(job_uuid, outcome)
        success = outcome.outcome == "succeeded"
        ret_value = outcome.return_info.get("return_value", outcome.return_info)
        self._on_job_settled(
            job_uuid,
            success,
            ret_value,
            "normal" if success else outcome.outcome,
        )

    def _on_execution_process_restarted(
        self,
        job_uuids: tuple[str, ...],
    ) -> None:
        """把动作进程重启收敛成受影响任务的失败终态。

        参数：``job_uuids`` 是动作账本保留下来的结果不确定作业。返回无。异常：
        单项投影故障在其余任务全部处理后聚合为 ``TaskSchedulerBridgeError``；陈旧
        终态任务幂等跳过。每个任务只投影一次；运行中作业进入失败，尚未物理
        执行的节点进入跳过，Claim/Fence 保留待人工对账。
        """

        task_uuids: list[str] = []
        for job_uuid in job_uuids:
            task_uuid = self._task_by_job.get(job_uuid)
            if task_uuid is None:
                try:
                    job = self._store.get_job(job_uuid)
                except StoreNotFound:
                    logger.warning("动作进程重启包含未知作业 %s", job_uuid)
                    continue
                task_uuid = self._required_text(
                    job.get("workflow_task_uuid"),
                    field="job.workflow_task_uuid",
                )
            if task_uuid not in task_uuids:
                task_uuids.append(task_uuid)
        failures: list[tuple[str, Exception]] = []
        for task_uuid in task_uuids:
            try:
                aggregate = self._projection.project_execution_process_restarted(
                    task_uuid
                )
            except Exception as error:
                # ``failures`` 延后报告单项持久化故障，保证同批其他任务仍能收敛。
                failures.append((task_uuid, error))
                logger.exception("动作进程重启收敛任务失败：%s", task_uuid)
                continue
            if aggregate is None:
                logger.info(
                    "动作进程重启跳过无需变化的陈旧任务：%s",
                    task_uuid,
                )
                continue
            try:
                self._mark_inventory_claims_uncertain(aggregate)
            except Exception as error:
                failures.append((task_uuid, error))
                logger.exception(
                    "动作进程重启冻结库存 Claim 失败：%s",
                    task_uuid,
                )
                continue
            logger.error(
                "工作流任务 %s 因动作执行进程重启整体失败，未开始节点不再推进",
                task_uuid,
            )
        if failures:
            failed_task_uuids = ",".join(task_uuid for task_uuid, _ in failures)
            raise TaskSchedulerBridgeError(
                "动作进程重启有任务未能提交失败事实：" + failed_task_uuids
            ) from failures[0][1]

    def _recover_running_task(
        self,
        task: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """恢复单个可证明无结果不明作业的运行中任务。"""

        task_uuid = self._required_text(task.get("uuid"), field="task.uuid")
        jobs = self._store.list_jobs(task_uuid)
        # pending 人工确认尚未下发物理动作：重启后保留原 Job、Claim、Fence
        # 和截止时间恢复等待，禁止释放后重新仲裁或生成新的派发凭据。
        pending_manual_jobs: list[dict[str, Any]] = []
        for job in jobs:
            if (
                job.get("executor_kind") == "manual_confirm"
                and job.get("status") in {"dispatched", "running"}
            ):
                try:
                    confirmation = self._manual_confirmations.get_by_job(
                        self._required_text(job.get("uuid"), field="job.uuid")
                    )
                except StoreNotFound:
                    continue
                if confirmation.get("status") == "pending":
                    pending_manual_jobs.append(dict(job))
        safe_manual_ids = {
            self._required_text(job.get("uuid"), field="job.uuid")
            for job in pending_manual_jobs
        }
        if task.get("status") in {"running", "canceling"} and not pending_manual_jobs:
            aggregate = self._projection.project_execution_process_restarted(task_uuid)
            if aggregate is None:
                raise TaskSchedulerBridgeError("运行中任务未能提交进程重启失败事实")
            self._mark_inventory_claims_uncertain(aggregate)
            logger.error(
                "工作流任务 %s 因工站调度进程重启整体失败，未开始节点不再推进",
                task_uuid,
            )
            return aggregate
        interrupted_jobs = [
            job
            for job in jobs
            if (
                job.get("status")
                in {"dispatched", "running", "cancel_requested"}
                and str(job.get("uuid") or "") not in safe_manual_ids
            )
        ]
        if interrupted_jobs or task.get("status") == "canceling":
            aggregate = self._projection.project_execution_process_restarted(task_uuid)
            if aggregate is None:
                raise TaskSchedulerBridgeError("在途作业未能提交进程重启失败事实")
            self._mark_inventory_claims_uncertain(aggregate)
            logger.error(
                "工作流任务 %s 有 %d 个作业被动作进程重启中断，已失败并保留占用",
                task_uuid,
                len(interrupted_jobs),
            )
            return aggregate
        source_jobs = [
            job for job in jobs if job.get("executor_kind") == "material_source"
        ]
        ordinary_jobs = [
            job for job in jobs if job.get("executor_kind") != "material_source"
        ]
        if any(job.get("status") != "succeeded" for job in source_jobs):
            raise TaskSchedulerBridgeError("运行中任务存在未完成的物料来源作业")
        if source_jobs:
            # 旧冻结计划可能只记录第一个物理消费者；幂等重放同一准入结果会从
            # 计划边补齐复合工作流隐式透传的其他待处理动作参数，不再次查询或
            # 占用库存（Inventory）。
            bindings: dict[str, Mapping[str, str]] = {}
            for source_job in source_jobs:
                return_info = source_job.get("return_info")
                material = (
                    return_info.get("material")
                    if isinstance(return_info, Mapping)
                    else None
                )
                if not isinstance(material, Mapping):
                    raise TaskSchedulerBridgeError("物料来源成功作业缺少绑定结果")
                source_node_uuid = self._required_text(
                    source_job.get("workflow_node_uuid"),
                    field="material_source_job.workflow_node_uuid",
                )
                bindings[source_node_uuid] = material
            self._projection.project_material_source_admission(task_uuid, bindings)
            jobs = self._store.list_jobs(task_uuid)
            ordinary_jobs = [
                job for job in jobs if job.get("executor_kind") != "material_source"
            ]
        if any(
            job.get("status") not in {"pending", "succeeded"}
            and str(job.get("uuid") or "") not in safe_manual_ids
            for job in ordinary_jobs
        ):
            raise TaskSchedulerBridgeError("运行中任务包含不可恢复的作业终态")
        pending_jobs = [job for job in ordinary_jobs if job.get("status") == "pending"]
        if not pending_jobs and not pending_manual_jobs:
            raise TaskSchedulerBridgeError("运行中任务没有待处理作业")

        spec = self._compiler.compile(task, jobs)
        plan = task.get("execution_plan")
        raw_nodes = plan.get("nodes") if isinstance(plan, Mapping) else None
        if not isinstance(raw_nodes, list):
            raise TaskSchedulerBridgeError("执行计划节点必须是数组")
        nodes_by_uuid = {
            str(node.get("uuid") or ""): node
            for node in raw_nodes
            if isinstance(node, Mapping)
        }
        jobs_by_node = {
            str(job.get("workflow_node_uuid") or ""): job for job in ordinary_jobs
        }
        completed_results: dict[str, Any] = {}
        recovery_run = WorkflowRun(spec)
        for node in spec.nodes:
            job = jobs_by_node.get(node.id)
            if job is None or job.get("status") != "succeeded":
                continue
            # 按冻结拓扑顺序重建当时最终参数；这只读取已成功
            # 父节点事实，不派发任何动作。
            resolved_param = recovery_run.resolve_params(node.id)
            recovered_result = self._recover_simulated_action_return(
                job,
                nodes_by_uuid.get(node.id, {}),
                resolved_param=resolved_param,
            )
            completed_results[node.id] = recovered_result
            recovery_run.mark_finished(node.id, recovered_result)
        spec_nodes = {node.id: node for node in spec.nodes}
        restored_manual_jobs: list[DispatchedJob] = []
        for persisted_job in pending_manual_jobs:
            job_uuid = self._required_text(
                persisted_job.get("uuid"), field="job.uuid"
            )
            node_uuid = self._required_text(
                persisted_job.get("workflow_node_uuid"),
                field="job.workflow_node_uuid",
            )
            node = spec_nodes.get(node_uuid)
            if node is None or not node.is_manual_confirm():
                raise TaskSchedulerBridgeError("人工确认恢复节点与冻结计划不一致")
            claim = self._projection.get_execution_claim(job_uuid)
            if claim is None or claim.get("state") not in {"reserved", "running"}:
                raise TaskSchedulerBridgeError("人工确认恢复缺少活动 Claim")
            control_data = persisted_job.get("control_data")
            actual_executor = (
                control_data.get("actual_executor")
                if isinstance(control_data, Mapping)
                else None
            )
            device_id = str(
                (
                    actual_executor.get("local_device_id")
                    if isinstance(actual_executor, Mapping)
                    else ""
                )
                or node.device_id
            ).strip()
            device_material_uuid = str(
                (
                    actual_executor.get("material_uuid")
                    if isinstance(actual_executor, Mapping)
                    else ""
                )
                or node.device_material_uuid
            ).strip()
            persisted_execution_locks = (
                control_data.get("execution_locks")
                if isinstance(control_data, Mapping)
                else None
            )
            restored_resource_locks = {
                str(item.get("lock_key") or "").strip()
                for item in (
                    persisted_execution_locks
                    if isinstance(persisted_execution_locks, list)
                    else ()
                )
                if isinstance(item, Mapping)
                and str(item.get("lock_key") or "").strip()
            }
            credentials = {
                "attempt": int(persisted_job.get("attempt") or 0),
                "command_uuid": str(
                    persisted_job.get("edge_command_uuid") or ""
                ),
                "claim_uuid": str(claim.get("claim_uuid") or ""),
                "fences": [dict(item) for item in claim.get("fences", ())],
                "effect_uuid": str(
                    persisted_job.get("dispatch_effect_uuid") or ""
                ),
                "parameter_hash": str(
                    persisted_job.get("dispatch_parameter_hash") or ""
                ),
                "expected_change_set": dict(
                    persisted_job.get("expected_change_set") or {}
                ),
            }
            if node.manual_continues_device_action and (
                credentials["attempt"] <= 0
                or any(
                    not credentials[field]
                    for field in (
                        "command_uuid",
                        "claim_uuid",
                        "fences",
                        "effect_uuid",
                        "parameter_hash",
                    )
                )
            ):
                raise TaskSchedulerBridgeError(
                    "继续设备动作的人工确认缺少可恢复派发凭据"
                )
            restored_manual_jobs.append(
                DispatchedJob(
                    job_id=job_uuid,
                    workflow_id=task_uuid,
                    node_id=node_uuid,
                    device_action_key=f"/devices/{device_id}/{node.action_name}",
                    device_id=device_id,
                    device_material_uuid=device_material_uuid,
                    action_name=node.action_name,
                    resolved_args=dict(persisted_job.get("param") or {}),
                    dispatch_credentials=credentials,
                    resource_lock_keys=restored_resource_locks,
                )
            )
        recoverable_jobs = [*pending_jobs, *pending_manual_jobs]
        for job in recoverable_jobs:
            job_uuid = self._required_text(job.get("uuid"), field="job.uuid")
            self._task_by_job[job_uuid] = task_uuid
        self._submitted_tasks.add(task_uuid)
        try:
            restored = self._scheduler.restore_workflow(
                spec,
                completed_results,
                restored_manual_jobs,
            )
            self._project_scheduler_trace_context(
                task_uuid,
                restored.get("trace_context"),
            )
            for restored_job in restored_manual_jobs:
                self._schedule_manual_confirmation_timeout(restored_job.job_id)
        except Exception:
            if not self._crossed_dispatch_boundary(jobs):
                self._submitted_tasks.discard(task_uuid)
                for job in recoverable_jobs:
                    self._task_by_job.pop(str(job.get("uuid") or ""), None)
            raise
        return self._aggregate(task_uuid)

    @staticmethod
    def _recover_simulated_action_return(
        job: Mapping[str, Any],
        plan_node: Mapping[str, Any],
        *,
        resolved_param: Mapping[str, Any] | None = None,
    ) -> Any:
        """为模拟动作回执重建同名输入的物料透传并兼容历史标记。"""

        return_info = job.get("return_info")
        if not isinstance(return_info, Mapping) or not (
            return_info.get("action_mode") == "simulate"
            or return_info.get("test_mode") is True
        ):
            return deepcopy(return_info)
        recovered = deepcopy(dict(return_info))
        param = resolved_param if resolved_param is not None else job.get("param")
        param_schema = plan_node.get("param_schema")
        properties = (
            param_schema.get("properties")
            if isinstance(param_schema, Mapping)
            else None
        )
        result_schema = (
            properties.get("result") if isinstance(properties, Mapping) else None
        )
        result_properties = (
            result_schema.get("properties")
            if isinstance(result_schema, Mapping)
            else None
        )
        if isinstance(param, Mapping) and isinstance(result_properties, Mapping):
            for output_key in result_properties:
                if output_key not in recovered and output_key in param:
                    recovered[output_key] = deepcopy(param[output_key])
        return recovered

    def close(self) -> None:
        """幂等注销本桥的调度生命周期监听器。

        参数：无。返回：无；重复调用不重复注销，关闭后清除仅用于回调过滤的内存
        路由，但不修改任何持久任务、作业或物料预留事实。
        """

        if self._closed:
            return
        self._closed = True
        self._scheduler.remove_admission_retry_listener(self._retry_pending_admissions)
        self._scheduler.unbind_dispatch_admission_authority(self._on_job_pre_dispatch)
        self._scheduler.remove_job_execution_wait_listener(self._on_job_execution_wait)
        self._scheduler.remove_job_dispatch_accepted_listener(
            self._on_job_dispatch_accepted
        )
        self._scheduler.remove_job_dispatch_uncertain_listener(
            self._on_job_dispatch_uncertain
        )
        self._scheduler.remove_job_cancel_accepted_listener(
            self._on_job_cancel_accepted
        )
        self._scheduler.remove_job_cancel_uncertain_listener(
            self._on_job_cancel_uncertain
        )
        self._scheduler.remove_job_cancel_no_send_listener(self._on_job_cancel_no_send)
        self._scheduler.remove_job_feedback_listener(self._on_job_feedback)
        self._scheduler.remove_job_outcome_listener(self._on_job_outcome)
        self._scheduler.remove_job_finished_listener(self._on_job_finished)
        self._scheduler.remove_job_settled_listener(self._on_job_settled)
        self._scheduler.remove_execution_process_restarted_listener(
            self._on_execution_process_restarted
        )
        self._scheduler.set_drain_blocker_provider(None)
        self._task_by_job.clear()
        self._submitted_tasks.clear()
        self._admission_pending_tasks.clear()
        with self._cancel_timer_lock:
            timers = tuple(self._cancel_timers.values())
            self._cancel_timers.clear()
        for timer in timers:
            timer.cancel()
        with self._manual_timer_lock:
            manual_timers = tuple(self._manual_timers.values())
            self._manual_timers.clear()
        for timer in manual_timers:
            timer.cancel()
        # 与在途超时回调建立关闭栅栏；新回调会看到 ``_closed``
        # 并立即返回，已进入的回调则在此完成持久化后再退出。
        with self._manual_callback_lock:
            pass

    def active_or_uncertain_job_ids(self) -> set[str]:
        """返回阻止调度器安全排空的持久作业身份。

        参数：无。返回：已经派发、运行、请求取消，或业务已失败但仍持有不确定
        执行占用的 Job UUID 集合；尚未批准的人工确认与内部物料来源解析不属于设备
        在途作业。异常：持久库读取失败原样传播，使排空关闭式失败，禁止把读取失败
        解释为空闲。
        """

        unsafe_statuses = {
            "dispatched",
            "running",
            "cancel_requested",
        }
        result: set[str] = set()
        page = 1
        page_size = 200
        while True:
            task_page = self._store.list_tasks(page=page, page_size=page_size)
            for task in task_page["items"]:
                task_uuid = self._required_text(task.get("uuid"), field="task.uuid")
                for job in self._store.list_jobs(task_uuid):
                    if job.get("executor_kind") == "material_source":
                        continue
                    job_uuid = self._required_text(job.get("uuid"), field="job.uuid")
                    status_is_unsafe = job.get("status") in unsafe_statuses
                    with self._store.transaction() as connection:
                        claim_is_uncertain = any(
                            lease.get("state") == "uncertain"
                            for lease in list_execution_locks(
                                connection,
                                job_uuid=job_uuid,
                            )
                        )
                    if not status_is_unsafe and not claim_is_uncertain:
                        continue
                    if (
                        job.get("executor_kind") == "manual_confirm"
                        and job.get("status") == "dispatched"
                        and self._manual_confirmation_is_waiting(job_uuid)
                    ):
                        continue
                    result.add(job_uuid)
            if page * page_size >= int(task_page["total"]):
                return result
            page += 1

    def _manual_confirmation_is_waiting(self, job_uuid: str) -> bool:
        """判断人工确认 Job 是否仍停留在尚未派发设备动作的等待阶段。"""

        try:
            confirmation = self._manual_confirmations.get_by_job(job_uuid)
        except StoreNotFound:
            return False
        return confirmation.get("status") == "pending"

    def _retry_pending_admissions(self) -> None:
        """按持久顺序重试明确处于物料来源准入等待的 Task。

        参数：无。返回无；每个工作流任务（WorkflowTask）只在本轮重试一次，仍
        受阻时由 ``submit`` 重新登记。异常：存储、准入或调度失败原样传播，禁止
        在公开重排失败时继续派发其他普通动作。
        """

        pending = set(self._admission_pending_tasks)
        for task in self._store.list_recoverable_tasks(statuses=("pending",)):
            task_uuid = self._required_text(task.get("uuid"), field="task.uuid")
            if task_uuid not in pending:
                continue
            self._admission_pending_tasks.discard(task_uuid)
            self.submit(task)

    def _on_job_pre_dispatch(self, dispatching: dict[str, Any]) -> bool:
        """在物理派发前取得库存 Permit 并提交标准派发意图。

        参数：``dispatching`` 是既有调度器即将越过执行边界的作业摘要。返回：
        标准作业在库存事务中复验全部条件、取得 Claim/Fence，并把同一 Permit
        投影到工作流库后为真；资源竞争保持 ``pending``
        时为假。异常：缺库存权威、请求损坏或跨库投影冲突关闭式阻止物理派发。
        """

        job_uuid = str(dispatching.get("job_id") or "")
        task_uuid = self._task_by_job.get(job_uuid)
        if task_uuid is None:
            raise StoreConflict(f"派发作业不属于当前持久调度权威：{job_uuid}")
        dispatch_task_uuid = self._required_text(
            dispatching.get("workflow_id"), field="dispatching.workflow_id"
        )
        if dispatch_task_uuid != task_uuid:
            raise StoreConflict(f"派发作业与任务身份不一致：{job_uuid}")
        resolved_args = dispatching.get("resolved_args")
        if not isinstance(resolved_args, Mapping):
            raise StoreConflict(f"派发作业缺少最终解析参数：{job_uuid}")
        execution_locks = dispatching.get("execution_locks")
        if not isinstance(execution_locks, list):
            raise StoreConflict(f"派发作业缺少执行锁集合：{job_uuid}")
        device_tenancy = dispatching.get("device_tenancy")
        if device_tenancy is not None and not isinstance(device_tenancy, Mapping):
            raise StoreConflict(f"派发作业设备托管转换不是对象：{job_uuid}")
        raw_operate = dispatching.get("operate_in_place_condition")
        required_device_tenancy = None
        if raw_operate is not None:
            if not isinstance(raw_operate, Mapping):
                raise StoreConflict(f"派发作业原位操作条件不是对象：{job_uuid}")
            required_device_tenancy = {
                "material_uuid": str(raw_operate.get("material_uuid") or ""),
                "device_lock_key": (
                    "/devices/" + str(raw_operate.get("device_material_uuid") or "")
                ),
            }
        actual_executor = dispatching.get("actual_executor")
        if actual_executor is not None and not isinstance(actual_executor, Mapping):
            raise StoreConflict(f"派发作业实际执行器不是对象：{job_uuid}")
        inventory_authority = self._scheduler.station_resource_inventory
        permit = None
        permit_committed = False
        permit_projected = False
        if inventory_authority is None and self._scheduler.physical_dispatch_enabled:
            raise StoreConflict("物理派发未装配库存 DispatchPermit 权威")
        if inventory_authority is not None:
            request = self._dispatch_admission_request(
                dispatching=dispatching,
                task_uuid=task_uuid,
                job_uuid=job_uuid,
                resolved_args=resolved_args,
                execution_locks=execution_locks,
            )
            try:
                decision = inventory_authority.acquire_dispatch_permit(request)
            except StationResourceError as error:
                if not is_temporary_resource_condition(error.code):
                    raise
                self._projection.project_execution_lock_wait(
                    task_uuid=task_uuid,
                    job_uuid=job_uuid,
                    execution_locks=[],
                    wait_code=error.code,
                    wait_message=error.message,
                    wait_resources=error.resources,
                    max_active_tasks=self._max_active_tasks,
                    max_tasks_per_workflow=self._max_tasks_per_workflow,
                )
                return False
            if not decision.acquired:
                self._projection.project_execution_lock_wait(
                    task_uuid=task_uuid,
                    job_uuid=job_uuid,
                    execution_locks=execution_locks,
                    blocking_task_uuid=(decision.blocking_task_uuid or None),
                    blocking_job_uuid=(decision.blocking_job_uuid or None),
                    wait_code=decision.wait_code,
                    wait_message=decision.wait_message,
                    max_active_tasks=self._max_active_tasks,
                    max_tasks_per_workflow=self._max_tasks_per_workflow,
                )
                return False
            permit = decision.permit
            assert permit is not None
        try:
            projection_kwargs: dict[str, Any] = {}
            if required_device_tenancy is not None:
                projection_kwargs["required_device_tenancy"] = required_device_tenancy
            aggregate = self._projection.project_pre_dispatch(
                task_uuid=task_uuid,
                job_uuid=job_uuid,
                resolved_param=resolved_args,
                execution_locks=execution_locks,
                device_tenancy=device_tenancy,
                actual_executor=actual_executor,
                dispatch_permit=(permit.as_mapping() if permit is not None else None),
                max_active_tasks=self._max_active_tasks,
                max_tasks_per_workflow=self._max_tasks_per_workflow,
                max_in_flight_jobs=self._max_in_flight_jobs,
                aging_interval_seconds=self._scheduler.aging_interval_seconds,
                **projection_kwargs,
            )
            projected_job = next(
                (job for job in aggregate["jobs"] if job["uuid"] == job_uuid),
                None,
            )
            if projected_job is None:
                raise StoreConflict(f"派发作业投影后消失：{job_uuid}")
            if projected_job["status"] != "dispatched":
                if inventory_authority is not None and permit is not None:
                    inventory_authority.transition_dispatch_permit(
                        permit.claim_uuid,
                        target_state="released",
                    )
                return False
            # 工作流库已持久化派发意图和 Claim。从这一行起，
            # 即使库存库的 reserved 确认返回异常，也不能再把
            # prepared Permit 当成“未投影”释放。
            permit_projected = True
            if inventory_authority is not None and permit is not None:
                inventory_authority.transition_dispatch_permit(
                    permit.claim_uuid,
                    target_state="reserved",
                )
                permit_committed = True
        except BaseException:
            if (
                inventory_authority is not None
                and permit is not None
                and not permit_committed
            ):
                if permit_projected:
                    freeze_projected_dispatch_permit(
                        inventory=inventory_authority,
                        projection=self._projection,
                        job_uuid=job_uuid,
                        claim_uuid=permit.claim_uuid,
                    )
                else:
                    inventory_authority.transition_dispatch_permit(
                        permit.claim_uuid,
                        target_state="released",
                    )
            raise
        if projected_job["status"] == "dispatched":
            claim = self._projection.get_execution_claim(job_uuid)
            if claim is None:
                raise StoreConflict(f"派发作业缺少持久 Claim：{job_uuid}")
            if permit is not None and str(claim["claim_uuid"]) != permit.claim_uuid:
                raise StoreConflict(f"工作流 Claim 与库存 Permit 不一致：{job_uuid}")
            dispatching.update(
                {
                    "attempt": int(projected_job["attempt"]),
                    "command_uuid": str(projected_job["edge_command_uuid"]),
                    "claim_uuid": str(claim["claim_uuid"]),
                    "fences": [dict(fence) for fence in claim["fences"]],
                    "effect_uuid": (
                        permit.effect_uuid
                        if permit is not None
                        else str(projected_job.get("dispatch_effect_uuid") or "")
                    ),
                    "parameter_hash": (
                        permit.parameter_hash
                        if permit is not None
                        else str(projected_job.get("dispatch_parameter_hash") or "")
                    ),
                    "expected_change_set": (
                        dict(permit.expected_change_set)
                        if permit is not None
                        else dict(projected_job.get("expected_change_set") or {})
                    ),
                }
            )
        if (
            projected_job.get("executor_kind") == "manual_confirm"
            and projected_job.get("status") == "dispatched"
        ):
            self._schedule_manual_confirmation_timeout(job_uuid)
        return projected_job["status"] == "dispatched"

    def _dispatch_admission_request(
        self,
        *,
        dispatching: Mapping[str, Any],
        task_uuid: str,
        job_uuid: str,
        resolved_args: Mapping[str, Any],
        execution_locks: list[Mapping[str, Any]],
    ) -> DispatchAdmissionRequest:
        """从最终动作参数和完整资源集构造不可变库存准入请求。

        参数：派发摘要、Task/Job 身份、最终参数和全部执行锁。返回：带确定性
        ``effect_uuid``、参数哈希、预期 ChangeSet 与可选转运条件的请求。异常：
        JSON 参数、锁字段或转运条件损坏时抛 ``StoreConflict``，不得猜测默认值。
        """

        try:
            parameter_bytes = json.dumps(
                resolved_args,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeError) as error:
            raise StoreConflict(f"派发最终参数不能稳定哈希：{job_uuid}") from error
        parameter_hash = "sha256:" + hashlib.sha256(parameter_bytes).hexdigest()
        job = self._store.get_job(job_uuid)
        attempt = int(job["attempt"])
        effect_uuid = str(
            uuid5(
                NAMESPACE_URL,
                f"unilabos-dispatch:{job_uuid}:{attempt}:{parameter_hash}",
            )
        )
        resources: list[DispatchResource] = []
        for raw in execution_locks:
            if not isinstance(raw, Mapping):
                raise StoreConflict("派发执行锁必须是对象")
            resources.append(
                DispatchResource(
                    lock_key=str(raw.get("lock_key") or ""),
                    scope=str(raw.get("scope") or ""),
                    material_uuid=str(raw.get("material_uuid") or ""),
                    site_uuid=str(raw.get("site_uuid") or ""),
                )
            )
        raw_transfer = dispatching.get("transfer_dispatch_condition")
        raw_operate = dispatching.get("operate_in_place_condition")
        raw_aliquot = dispatching.get("aliquot_dispatch_condition")
        transfer = None
        operate_in_place = None
        aliquot = None
        expected_change_set: dict[str, Any] = {"kind": "no_inventory_change"}
        if raw_transfer is not None:
            if not isinstance(raw_transfer, Mapping):
                raise StoreConflict("派发转运条件必须是对象")
            required = {
                "material_uuid",
                "source_owner_material_uuid",
                "source_site_uuid",
                "target_owner_material_uuid",
                "target_site_uuid",
                "executor_material_uuid",
                "gripper_site_uuid",
            }
            if set(raw_transfer) != required or any(
                not str(raw_transfer[field] or "").strip() for field in required
            ):
                raise StoreConflict("派发转运条件字段不完整")
            transfer = TransferDispatchCondition(
                **{field: str(raw_transfer[field]).strip() for field in required}
            )
            expected_change_set = {
                "kind": "material_transfer",
                "material_uuid": transfer.material_uuid,
                "source_site_uuid": transfer.source_site_uuid,
                "target_site_uuid": transfer.target_site_uuid,
            }
        if raw_operate is not None:
            if not isinstance(raw_operate, Mapping):
                raise StoreConflict("派发原位操作条件必须是对象")
            required_operate = {
                "material_uuid",
                "site_owner_material_uuid",
                "site_uuid",
                "device_material_uuid",
            }
            if set(raw_operate) != required_operate or any(
                not str(raw_operate[field] or "").strip()
                for field in required_operate
            ):
                raise StoreConflict("派发原位操作条件字段不完整")
            if transfer is not None:
                raise StoreConflict("同一作业不能同时声明转运和原位操作")
            operate_in_place = OperateInPlaceCondition(
                **{
                    field: str(raw_operate[field]).strip()
                    for field in required_operate
                }
            )
        if raw_aliquot is not None:
            if not isinstance(raw_aliquot, Mapping) or set(raw_aliquot) != {
                "source_material_uuid",
                "target_material_uuids",
            }:
                raise StoreConflict("派发分装条件字段不完整")
            source_uuid = str(raw_aliquot.get("source_material_uuid") or "").strip()
            raw_targets = raw_aliquot.get("target_material_uuids")
            if (
                not source_uuid
                or not isinstance(raw_targets, list)
                or not raw_targets
                or any(not str(value or "").strip() for value in raw_targets)
            ):
                raise StoreConflict("派发分装来源或目标集合非法")
            if transfer is not None or operate_in_place is not None:
                raise StoreConflict("同一作业不能同时声明分装、转运或原位操作")
            targets = tuple(str(value).strip() for value in raw_targets)
            aliquot = AliquotDispatchCondition(
                source_material_uuid=source_uuid,
                target_material_uuids=targets,
            )
            expected_change_set = {
                "kind": "material_content_aliquot",
                "source_material_uuid": source_uuid,
                "target_material_uuids": list(targets),
            }
        return DispatchAdmissionRequest(
            effect_uuid=effect_uuid,
            task_uuid=task_uuid,
            job_uuid=job_uuid,
            attempt=attempt,
            parameter_hash=parameter_hash,
            expected_change_set=expected_change_set,
            resources=tuple(resources),
            transfer=transfer,
            operate_in_place=operate_in_place,
            aliquot=aliquot,
        )

    def _on_job_execution_wait(self, waiting: Mapping[str, Any]) -> None:
        """把调度循环先命中的内存占用投影为持久等待事实。"""

        job_uuid = str(waiting.get("job_id") or "")
        task_uuid = self._task_by_job.get(job_uuid)
        if task_uuid is None:
            return
        dispatch_task_uuid = self._required_text(
            waiting.get("workflow_id"), field="waiting.workflow_id"
        )
        if dispatch_task_uuid != task_uuid:
            raise StoreConflict(f"等待作业与任务身份不一致：{job_uuid}")
        execution_locks = waiting.get("execution_locks")
        if not isinstance(execution_locks, list):
            raise StoreConflict(f"等待作业缺少执行锁集合：{job_uuid}")
        candidate_site_uuids = waiting.get("candidate_site_uuids")
        if candidate_site_uuids is not None and not isinstance(
            candidate_site_uuids, list
        ):
            raise StoreConflict(f"等待作业候选库位集合不是数组：{job_uuid}")
        raw_wait_resources = waiting.get("wait_resources")
        if raw_wait_resources is not None and not isinstance(
            raw_wait_resources, list
        ):
            raise StoreConflict(f"等待作业资源集合不是数组：{job_uuid}")
        wait_resources = list(raw_wait_resources or [])
        wait_resources.extend(
            {
                "scope": "material_site",
                "site_uuid": self._required_text(
                    site_uuid,
                    field="waiting.candidate_site_uuids",
                ),
            }
            for site_uuid in candidate_site_uuids or []
        )
        self._projection.project_execution_lock_wait(
            task_uuid=task_uuid,
            job_uuid=job_uuid,
            execution_locks=execution_locks,
            blocking_task_uuid=(
                str(waiting["blocking_workflow_id"])
                if waiting.get("blocking_workflow_id")
                else None
            ),
            blocking_job_uuid=(
                str(waiting["blocking_job_id"])
                if waiting.get("blocking_job_id")
                else None
            ),
            wait_code=(str(waiting["wait_code"]) if waiting.get("wait_code") else None),
            wait_message=(
                str(waiting["wait_message"]) if waiting.get("wait_message") else None
            ),
            wait_resources=wait_resources,
            max_active_tasks=self._max_active_tasks,
            max_tasks_per_workflow=self._max_tasks_per_workflow,
        )

    def _on_job_dispatch_accepted(self, job_uuid: str) -> None:
        """把库存 Claim 与工作流投影一起推进为 running。

        参数：``job_uuid`` 是执行适配器已明确接受的作业。返回：无。异常：库存
        Claim 缺失或任一持久转换失败时原样传播，调用方会转入不确定对账。
        """

        if job_uuid not in self._task_by_job:
            return
        self._transition_inventory_claim(job_uuid, target_state="running")
        self._projection.project_dispatch_accepted(job_uuid)

    def _on_job_dispatch_uncertain(self, job_uuid: str, reason: str) -> None:
        """让库存与工作流 Claim 同时冻结为物理不确定。

        参数：``job_uuid`` 是派发接受结果不明的作业；``reason`` 是稳定原因。
        返回：无。异常：库存或工作流持久化失败原样传播，不能释放任何资源。
        """

        if job_uuid not in self._task_by_job:
            return
        self._transition_inventory_claim(job_uuid, target_state="uncertain")
        self._projection.project_execution_attention(job_uuid, reason=reason)

    def _on_job_cancel_accepted(self, job_uuid: str) -> None:
        """记录本地执行器已受理取消并切换到设备终态截止时间。

        参数：``job_uuid`` 是在途作业身份。返回无。异常：持久投影冲突传播给
        执行边界，防止受理事实被静默丢失。终态先于受理回调到达时幂等忽略。
        """

        if job_uuid not in self._task_by_job:
            return
        job = self._store.get_job(job_uuid)
        if job.get("status") in {"succeeded", "failed", "canceled", "timeout"}:
            self._cancel_cancel_timer(job_uuid)
            return
        aggregate = self._projection.project_cancel_accepted(job_uuid)
        accepted_job = next(
            job for job in aggregate["jobs"] if str(job.get("uuid")) == job_uuid
        )
        self._schedule_cancel_timeout(accepted_job)

    def _on_job_cancel_uncertain(self, job_uuid: str, reason: str) -> None:
        """让取消拒绝或能力缺失的作业保持运行并等待物理对账。"""

        if job_uuid not in self._task_by_job:
            return
        self._cancel_cancel_timer(job_uuid)
        job = self._store.get_job(job_uuid)
        if job.get("status") in {"succeeded", "failed", "canceled", "timeout"}:
            return
        self._projection.project_execution_attention(job_uuid, reason=reason)

    def _on_job_cancel_no_send(self, job_uuid: str) -> None:
        """用 No-send Proof 立即结算一个尚未执行的取消作业。"""

        if job_uuid not in self._task_by_job:
            return
        self._cancel_cancel_timer(job_uuid)
        job = self._store.get_job(job_uuid)
        if job.get("status") != "cancel_requested":
            return
        task_uuid = self._required_text(
            job.get("workflow_task_uuid"),
            field="job.workflow_task_uuid",
        )
        self._project_job_result(
            task_uuid=task_uuid,
            job_uuid=job_uuid,
            scheduler_state="canceled",
            return_info={"cancel_reason": "local_no_send_proof"},
            error_info=[],
            manual_confirmation_status=None,
        )

    def _transition_inventory_claim(
        self,
        job_uuid: str,
        *,
        target_state: str,
    ) -> None:
        """按工作流审计 Claim 身份推进库存权威生命周期。

        参数：``job_uuid`` 是工作流节点作业；``target_state`` 是库存 Claim 目标
        状态。返回：无。异常：物理部署缺库存权威、Claim 缺失或转换非法时抛
        ``StoreConflict``/库存异常；干跑模式没有库存 Claim 时直接返回。
        """

        inventory_authority = self._scheduler.station_resource_inventory
        claim = self._projection.get_execution_claim(job_uuid)
        if inventory_authority is None:
            if self._scheduler.physical_dispatch_enabled:
                raise StoreConflict("物理作业缺少库存 DispatchPermit 权威")
            return
        if claim is None:
            raise StoreConflict(f"作业缺少库存 Claim 投影：{job_uuid}")
        inventory_authority.transition_dispatch_permit(
            str(claim["claim_uuid"]),
            target_state=target_state,
        )

    def _schedule_cancel_timeout(self, job: Mapping[str, Any]) -> None:
        """按持久截止时间为一个取消中作业安排单次本地检查。

        参数：``job`` 是最新持久作业投影。返回无。异常：缺少或损坏截止时间时
        立即让主状态恢复为 running 并进入物理对账，避免取消状态永久悬挂。受理前使用 ACK 截止时间，受理后
        使用设备完成截止时间。
        """

        job_uuid = self._required_text(job.get("uuid"), field="job.uuid")
        accepted = bool(job.get("cancel_accepted_at"))
        field = "cancel_complete_deadline_at" if accepted else "cancel_ack_deadline_at"
        raw_deadline = job.get(field)
        try:
            deadline = self._parse_time(raw_deadline)
        except (TypeError, ValueError):
            self._on_job_cancel_uncertain(
                job_uuid,
                f"invalid_{field}",
            )
            return
        delay = max(
            0.0,
            (deadline - datetime.now(timezone.utc)).total_seconds(),
        )
        timer = threading.Timer(
            delay,
            self._on_cancel_timeout,
            kwargs={
                "job_uuid": job_uuid,
                "expected_accepted": accepted,
            },
        )
        timer.daemon = True
        with self._cancel_timer_lock:
            previous = self._cancel_timers.pop(job_uuid, None)
            self._cancel_timers[job_uuid] = timer
        if previous is not None:
            previous.cancel()
        timer.start()

    def _on_cancel_timeout(
        self,
        *,
        job_uuid: str,
        expected_accepted: bool,
    ) -> None:
        """把到期且仍未收敛的取消作业冻结为运行中物理不确定事实。

        参数：``job_uuid`` 是作业身份；``expected_accepted`` 标识本计时器观察的是
        受理阶段还是设备终态阶段。返回无。异常：存储关闭或并发终态只记录日志，
        不释放任何执行占用。
        """

        with self._cancel_timer_lock:
            self._cancel_timers.pop(job_uuid, None)
        if self._closed:
            return
        try:
            job = self._store.get_job(job_uuid)
            if job.get("status") != "cancel_requested":
                return
            accepted = bool(job.get("cancel_accepted_at"))
            if accepted != expected_accepted:
                return
            reason = (
                "local_cancel_completion_timeout"
                if accepted
                else "local_cancel_acceptance_timeout"
            )
            self._projection.project_execution_attention(job_uuid, reason=reason)
        except Exception:
            logger.exception("本地取消超时检查失败：%s", job_uuid)

    def _cancel_cancel_timer(self, job_uuid: str) -> None:
        """幂等取消一个作业的本地截止时间计时器。"""

        with self._cancel_timer_lock:
            timer = self._cancel_timers.pop(job_uuid, None)
        if timer is not None:
            timer.cancel()

    def _schedule_manual_confirmation_timeout(self, job_uuid: str) -> None:
        """按持久确认截止时间安排一次到期检查；无截止时间则不建计时器。"""

        confirmation = self._manual_confirmations.get_by_job(job_uuid)
        raw_deadline = confirmation.get("deadline_at")
        if confirmation.get("status") != "pending" or raw_deadline is None:
            return
        deadline = self._parse_time(raw_deadline)
        delay = max(
            0.0,
            (deadline - datetime.now(timezone.utc)).total_seconds(),
        )
        timer = threading.Timer(
            delay,
            self._on_manual_confirmation_timeout,
            kwargs={"job_uuid": job_uuid},
        )
        timer.daemon = True
        with self._manual_timer_lock:
            previous = self._manual_timers.pop(job_uuid, None)
            self._manual_timers[job_uuid] = timer
        if previous is not None:
            previous.cancel()
        timer.start()

    def _on_manual_confirmation_timeout(self, *, job_uuid: str) -> None:
        """到期后让调度器以明确超时证据结算人工确认 Job。"""

        with self._manual_callback_lock:
            with self._manual_timer_lock:
                self._manual_timers.pop(job_uuid, None)
            if self._closed:
                return
            try:
                confirmation = self._manual_confirmations.get_by_job(job_uuid)
                if confirmation.get("status") != "pending":
                    return
                deadline = self._parse_time(confirmation.get("deadline_at"))
                if deadline > datetime.now(timezone.utc):
                    self._schedule_manual_confirmation_timeout(job_uuid)
                    return
                self._scheduler.expire_manual_confirmation(job_uuid)
            except Exception:
                logger.exception("人工确认超时收敛失败：%s", job_uuid)

    def _cancel_manual_confirmation_timer(self, job_uuid: str) -> None:
        """幂等取消一个人工确认截止计时器。"""

        with self._manual_timer_lock:
            timer = self._manual_timers.pop(job_uuid, None)
        if timer is not None:
            timer.cancel()

    @staticmethod
    def _parse_time(value: Any) -> datetime:
        """解析持久 UTC 时间；非法或无时区输入抛 ``ValueError``。"""

        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("截止时间缺少时区")
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _format_time(value: datetime) -> str:
        """把带时区时间规范为本地工作流库使用的 UTC RFC3339 文本。"""

        if value.tzinfo is None:
            raise ValueError("截止时间缺少时区")
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def _on_job_feedback(self, job_uuid: str, sample: Mapping[str, Any]) -> None:
        """把 Edge 已提交反馈投影为工作流作业有序历史。"""

        # 反馈可能在进程重启后的重放阶段到达；持久作业表才是归属权威，不能用
        # 进程内 ``_task_by_job`` 过滤，否则恢复中的合法反馈会被静默丢弃。
        self._projection.project_feedback(
            job_uuid=job_uuid,
            sequence=sample.get("sequence"),
            feedback_type=sample.get("feedback_type"),
            data=sample.get("data") or {},
            observed_at=sample.get("observed_at"),
            idempotency_key=sample.get("idempotency_key"),
        )

    def _on_job_finished(
        self,
        job_uuid: str,
        success: bool,
        ret_value: Any,
        suc_type: str,
    ) -> None:
        """把既有调度器明确结果投影为标准任务/作业终态。

        参数：``job_uuid`` 是稳定作业身份；``success`` 是明确成功标志；
        ``ret_value`` 是设备结果；``suc_type`` 是遗留人工处理分类。返回无；非本桥
        作业忽略，投影冲突传播给调度器记录，绝不触发物理重做。
        """

        task_uuid = self._task_by_job.get(job_uuid)
        if task_uuid is None:
            return
        # ``return_info`` 保持标准对象字段；标量结果使用明确包装键。
        return_info = (
            dict(ret_value)
            if isinstance(ret_value, Mapping)
            else ({"return_value": ret_value} if ret_value is not None else {})
        )
        # ``error_info`` 只在明确失败时记录稳定代码与人工决策来源。
        error_info: list[dict[str, Any]] = []
        canceled = not success and suc_type == "canceled"
        if not success and not canceled:
            error_info = [
                {
                    "code": "legacy_edge_scheduler_action_failed",
                    "message": "设备动作执行失败",
                    "suc_type": suc_type,
                }
            ]
        self._project_job_result(
            task_uuid=task_uuid,
            job_uuid=job_uuid,
            scheduler_state=(
                "success" if success else ("canceled" if canceled else "failed")
            ),
            return_info=return_info,
            error_info=error_info,
            manual_confirmation_status=(
                "timed_out" if suc_type == "manual_confirmation_timeout" else None
            ),
        )

    def _on_job_outcome(
        self,
        job_uuid: str,
        outcome: CommittedJobOutcome,
    ) -> None:
        """把 Edge HTTP 保真结果投影为标准任务/作业终态。

        参数：``job_uuid`` 是稳定作业身份；``outcome`` 保留 Backend wire 终态、
        返回值与错误证据。返回无。异常：身份路由或持久投影冲突向上传播；结果不会
        被降级成旧 ``success/suc_type`` 组合，也不会触发新的物理执行。
        """

        task_uuid = self._task_by_job.get(job_uuid)
        if task_uuid is None:
            return
        if outcome.unknown_command_ids:
            self._projection.project_execution_attention(
                job_uuid,
                reason=(
                    "edge_reported_unknown_commands:"
                    + ",".join(outcome.unknown_command_ids)
                ),
            )
            return
        persisted_job = self._store.get_job(job_uuid)
        if (
            persisted_job.get("status") == "failed"
            and str(persisted_job.get("uncertainty_reason") or "").strip()
        ):
            claim = self._projection.get_execution_claim(job_uuid)
            aggregate = self._projection.project_failed_job_execution_stopped(
                job_uuid,
                outcome=outcome.outcome,
                return_info=outcome.return_info,
                error_info=outcome.error_info,
            )
            settled_job = next(
                item for item in aggregate["jobs"] if item["uuid"] == job_uuid
            )
            if not settled_job.get("uncertainty_reason"):
                inventory = self._scheduler.station_resource_inventory
                if inventory is not None and claim is not None:
                    inventory.transition_dispatch_permit(
                        str(claim["claim_uuid"]),
                        target_state="released",
                    )
                elif self._scheduler.physical_dispatch_enabled:
                    raise StoreConflict(
                        f"失败作业物理结算缺少库存 Claim：{job_uuid}"
                    )
            self._cancel_cancel_timer(job_uuid)
            self._cancel_manual_confirmation_timer(job_uuid)
            return
        scheduler_state = {
            "succeeded": "success",
            "failed": "failed",
            "canceled": "canceled",
            "timeout": "timeout",
        }.get(outcome.outcome)
        if scheduler_state is None:
            raise StoreConflict(f"不支持的 Edge 作业终态：{outcome.outcome}")
        if outcome.outcome == "succeeded":
            task = self._store.get_task(task_uuid)
            claim = self._projection.get_execution_claim(job_uuid)
            self._material_transfer_settlement.settle_success(
                job=self._store.get_job(job_uuid),
                execution_plan=(
                    task.get("execution_plan")
                    if isinstance(task.get("execution_plan"), Mapping)
                    else None
                ),
                execution_claim=claim,
            )
            self._material_aliquot_settlement.settle_success(
                job=self._store.get_job(job_uuid),
                execution_claim=claim,
                receipts=outcome.material_aliquot_receipts,
            )
        if outcome.outcome == "succeeded" and self._quantity_inventory is not None:
            self._quantity_inventory.consume_successful_job(
                task_uuid=task_uuid,
                job_uuid=job_uuid,
                consumptions=outcome.inventory_consumptions,
            )
        self._project_job_result(
            task_uuid=task_uuid,
            job_uuid=job_uuid,
            scheduler_state=scheduler_state,
            return_info=outcome.return_info,
            error_info=outcome.error_info,
            manual_confirmation_status=None,
        )

    def _project_job_result(
        self,
        *,
        task_uuid: str,
        job_uuid: str,
        scheduler_state: str,
        return_info: Mapping[str, Any],
        error_info: list[Any],
        manual_confirmation_status: str | None,
    ) -> None:
        """提交一次标准作业结果并完成桥接层终态收尾。

        参数：任务/作业身份、规范调度终态、返回对象、错误数组和可选人工确认
        关闭状态。返回无。异常：持久结果冲突或物料来源释放失败原样传播；计时器
        仅在结果提交成功后取消，相同投递依赖投影层幂等处理。
        """

        claim = self._projection.get_execution_claim(job_uuid)
        aggregate = self._projection.project_job_finished(
            job_uuid=job_uuid,
            scheduler_state=scheduler_state,
            return_info=return_info,
            error_info=error_info,
            manual_confirmation_status=manual_confirmation_status,
        )
        settled_job = next(
            item for item in aggregate["jobs"] if item["uuid"] == job_uuid
        )
        inventory_claim_state = (
            "uncertain"
            if str(settled_job.get("uncertainty_reason") or "").strip()
            else "released"
        )
        inventory_authority = self._scheduler.station_resource_inventory
        if inventory_authority is not None and claim is not None:
            inventory_authority.transition_dispatch_permit(
                str(claim["claim_uuid"]),
                target_state=inventory_claim_state,
            )
        elif self._scheduler.physical_dispatch_enabled:
            raise StoreConflict(f"物理作业结果缺少库存 Claim：{job_uuid}")
        self._cancel_cancel_timer(job_uuid)
        self._cancel_manual_confirmation_timer(job_uuid)
        terminal_status = aggregate["task"]["status"]
        if terminal_status == "succeeded" and any(
            job.get("executor_kind") == "material_source" for job in aggregate["jobs"]
        ):
            # 成功表示最后一个动作已完成，可立即释放来源层短期预留。失败任务必须
            # 等所有在途设备动作结算，由 ``_on_job_settled`` 推进 cleanup_status。
            self._material_sources.release_terminal_reservations(
                task_uuid,
                reason=f"workflow_{terminal_status}",
            )
        if terminal_status == "succeeded" and self._quantity_inventory is not None:
            self._quantity_inventory.release_task(
                task_uuid,
                reason="workflow_succeeded",
            )

    def _on_job_settled(
        self,
        job_uuid: str,
        success: bool,
        ret_value: Any,
        suc_type: str,
    ) -> None:
        """在调度 DAG 已结算当前节点后推进调试与异常终态清理。

        参数：``job_uuid`` 是已结算作业身份；其余参数是调度器完成事实，本方法不
        改写结果载荷。返回无。异常：调试推进或清理投影冲突原样传播，避免仍在途
        的物料被静默释放。
        """

        task_uuid = self._task_by_job.get(job_uuid)
        if task_uuid is None:
            return
        # 正常完成回调来自当前调度运行；重启后的 Edge 结果重放则可能只有持久
        # Job→Task 路由而没有对应内存运行。记录这一区别，用于在释放持久派发
        # 容量后主动唤醒其他已经恢复、仍在调度器中等待的作业。
        replayed_without_runtime = self._scheduler.workflow_snapshot(task_uuid) is None
        # ``continue`` 只在下一个节点没有断点时复用既有单步派发原语；断点或
        # 显式 ``step`` 会先创建新 Hold，绝不越过物理派发边界。
        debug_action = self._store.advance_debug_after_job_finished(task_uuid)
        if debug_action.get("type") == "step":
            next_node_uuid = self._required_text(
                debug_action.get("workflow_node_uuid"),
                field="debug.workflow_node_uuid",
            )
            try:
                self._scheduler.step_workflow(
                    task_uuid,
                    target_node_id=next_node_uuid,
                )
            except ValueError as error:
                raise TaskSchedulerBridgeError(str(error)) from error
        self._task_by_job.pop(job_uuid, None)
        aggregate = self._aggregate(task_uuid)
        if aggregate["task"]["status"] in {"failed", "canceled", "timeout"}:
            scheduler_snapshot = self._scheduler.snapshot()
            active_for_task = any(
                inflight.get("workflow_id") == task_uuid
                for inflight in scheduler_snapshot.get("inflight_jobs", {}).values()
            )
            unsettled_jobs = [
                job
                for job in aggregate["jobs"]
                if str(job.get("uncertainty_reason") or "").strip()
            ]
            if not active_for_task and not unsettled_jobs:
                if self._quantity_inventory is not None:
                    self._quantity_inventory.release_task(
                        task_uuid,
                        reason=f"workflow_{aggregate['task']['status']}",
                    )
                if any(
                    job.get("executor_kind") == "material_source"
                    for job in aggregate["jobs"]
                ):
                    self._material_sources.release_terminal_reservations(
                        task_uuid,
                        reason=f"workflow_{aggregate['task']['status']}",
                    )
                # ``settled`` 是两类库存预留均已安全释放后的最终承诺。任何释放
                # 失败都会保留非 settled 状态，供恢复扫描以同一身份幂等重试。
                self._projection.project_cleanup_settled(task_uuid)
        if task_uuid not in self._task_by_job.values():
            self._submitted_tasks.discard(task_uuid)
        if replayed_without_runtime:
            # Edge 发件箱重放发生在旧调度运行已经丢失之后；此时不会经过
            # ``EdgeScheduler._on_job_finished`` 尾部的自动重排，需要显式唤醒
            # 其他已恢复运行。容量仍由数据库状态判定，不依赖这个内存触发器。
            self._scheduler.reschedule()

    def _finish_settled_terminal_task(self, job_uuid: str) -> None:
        """在显式物理结算完成后推进父任务资源清理。

        参数：``job_uuid`` 是刚刚清除不确定事实的失败作业。返回无；复用标准
        settled 回调以统一释放任务级预留和调度容量。异常原样传播。
        """

        task_uuid = str(self._store.get_job(job_uuid)["workflow_task_uuid"])
        self._task_by_job.setdefault(job_uuid, task_uuid)
        self._on_job_settled(job_uuid, False, None, "physical_settlement")

    @staticmethod
    def _normalize_actual_material_change_set(
        value: Mapping[str, Any],
    ) -> dict[str, Any]:
        """验证操作员提交的实际物料位置，拒绝模糊或额外字段。"""

        if not isinstance(value, Mapping):
            raise ValueError("实际物料位置必须是对象")
        allowed = {
            "kind",
            "material_uuid",
            "target_owner_material_uuid",
            "target_site_uuid",
            "target_site_name",
        }
        if set(value) - allowed:
            raise ValueError("实际物料位置包含未定义字段")
        normalized = {
            field: str(value.get(field) or "").strip()
            for field in allowed
        }
        if normalized["kind"] != "material_transfer":
            raise ValueError("实际变化类型必须是 material_transfer")
        if not normalized["material_uuid"] or not normalized[
            "target_owner_material_uuid"
        ]:
            raise ValueError("实际物料位置缺少物料或父资源 UUID")
        if bool(normalized["target_site_uuid"]) == bool(
            normalized["target_site_name"]
        ):
            raise ValueError("实际物料位置必须且只能指定库位 UUID 或名称")
        return {key: item for key, item in normalized.items() if item}

    def _aggregate(self, task_uuid: str) -> dict[str, Any]:
        """读取一个标准任务/作业聚合。

        参数：``task_uuid`` 是父任务稳定身份。返回：当前任务投影和有序作业列表。
        异常：任务不存在时传播工作流存储（WorkflowStore）异常。
        """

        return {
            "task": self._store.get_task(task_uuid),
            "jobs": self._store.list_jobs(task_uuid),
        }

    def _project_scheduler_trace_context(
        self,
        task_uuid: str,
        trace_context: Any,
    ) -> None:
        """把 Scheduler 根 span carrier 写入 Task，关闭观测时保持业务 no-op。"""

        if not isinstance(trace_context, Mapping) or not trace_context:
            return
        projector = getattr(self._projection, "project_trace_context", None)
        if not callable(projector):
            logger.warning(
                "任务运行投影未实现 Trace Context 接口，忽略 task=%s",
                task_uuid,
            )
            return
        try:
            projector(task_uuid, trace_context)
        except Exception:  # noqa: BLE001 - 观测元数据失败不得改变物理调度结论
            logger.exception(
                "工作流任务 Trace Context 持久化失败，业务继续 task=%s",
                task_uuid,
            )

    def _cancel_failed_submission(
        self,
        task_uuid: str,
        jobs: list[dict[str, Any]],
    ) -> None:
        """封闭一次未越过执行边界的失败提交。

        参数：``task_uuid`` 是旧调度运行身份；``jobs`` 是本次路由的持久作业集合。
        返回无；尽力取消遗留内存运行并清除监听路由，原始异常由调用方保留。
        """

        try:
            self._scheduler.cancel_workflow(task_uuid)
        except Exception:  # 清理失败不能覆盖原始安全错误
            logger.exception("失败的工作流任务提交无法清理遗留调度运行")
        self._submitted_tasks.discard(task_uuid)
        self._admission_pending_tasks.discard(task_uuid)
        for job in jobs:
            self._task_by_job.pop(str(job.get("uuid") or ""), None)

    def _crossed_dispatch_boundary(self, jobs: list[dict[str, Any]]) -> bool:
        """判断标准作业是否已经越过持久派发边界。

        参数：``jobs`` 是本次提交的既有工作流节点作业（WorkflowNodeJob）集合。
        返回：任一作业已为 ``dispatched`` 或 ``running`` 时为真。异常：存储读取
        故障视为不能证明未派发，保守返回真并禁止取消或清除回调路由。
        """

        try:
            # ``persisted_statuses`` 是物理派发前投影提交后的标准状态集合。
            persisted_statuses = {
                self._store.get_job(str(job.get("uuid") or ""))["status"]
                for job in jobs
            }
        except Exception:  # noqa: BLE001 - 无法证明未派发时必须保守保留在途事实
            return True
        return bool(
            persisted_statuses
            & {
                "dispatched",
                "running",
                "cancel_requested",
            }
        )

    @staticmethod
    def _required_text(value: Any, *, field: str) -> str:
        """校验桥接必填文本。

        参数：``value`` 是未知输入，``field`` 是稳定诊断字段。返回：去空白文本。
        异常：空值抛出 ``TaskSchedulerBridgeError``。
        """

        normalized = str(value or "").strip()
        if not normalized:
            raise TaskSchedulerBridgeError(f"{field} 不能为空")
        return normalized


__all__ = ["TaskSchedulerBridge", "TaskSchedulerBridgeError"]
