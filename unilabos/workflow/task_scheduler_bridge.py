"""把标准工作流任务（WorkflowTask）委托给既有本地调度器。"""

from __future__ import annotations

import logging
import threading
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from unilabos.app.scheduler.dag_state import WorkflowRun
from unilabos.app.scheduler.material_source_resolution import (
    MaterialSourceResolutionCoordinator,
)
from unilabos.app.scheduler.service import EdgeScheduler
from unilabos.workflow.manual_confirmation import ManualConfirmationStore
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
        scheduler.add_job_pre_dispatch_listener(self._on_job_pre_dispatch)
        scheduler.add_job_execution_wait_listener(self._on_job_execution_wait)
        scheduler.add_job_dispatch_accepted_listener(
            self._on_job_dispatch_accepted
        )
        scheduler.add_job_dispatch_uncertain_listener(
            self._on_job_dispatch_uncertain
        )
        scheduler.add_job_cancel_accepted_listener(self._on_job_cancel_accepted)
        scheduler.add_job_cancel_uncertain_listener(self._on_job_cancel_uncertain)
        scheduler.add_job_cancel_no_send_listener(self._on_job_cancel_no_send)
        scheduler.add_job_feedback_listener(self._on_job_feedback)
        scheduler.add_job_finished_listener(self._on_job_finished)
        scheduler.add_job_settled_listener(self._on_job_settled)

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
            material_resolution = self._material_sources.reconcile(
                persisted_task,
                jobs,
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

    def recover_active_tasks(self) -> list[dict[str, Any]]:
        """恢复可安全重建的任务，并冻结存在执行未知作业的任务。

        参数：无。返回：已恢复任务的标准聚合列表；包括运行中任务和尚未执行
        首步的 ``pending + paused`` 单步任务。已成功作业仅
        恢复 DAG 返回值，待处理作业才可派发；发现 ``dispatched`` 或
        ``running`` 作业时转为 ``execution_unknown``、保留持久执行占用并禁止
        物理重放，返回的聚合可直接用于告警和对账。
        异常：冻结计划或持久事实不一致时记录后跳过，不阻止其他
        可证明安全的任务恢复。
        """

        if self._closed:
            raise TaskSchedulerBridgeError("工作流任务调度桥已经关闭")
        # 先恢复持久 Job→Task 路由，再重放 Edge 已提交的反馈/结果。
        # 这一顺序可以收敛“Edge 已落盘，工作流库未投影”的崩溃点，
        # 且不会把结果重放误当成物理执行重试。
        self._register_active_recovery_routes()
        self._scheduler.replay_persisted_edge_projections(
            feedback_listener=self._on_job_feedback,
            finished_listener=self._replay_persisted_job_finished,
        )
        recovered: list[dict[str, Any]] = []
        for status in ("running", "pending", "canceling"):
            page = 1
            while True:
                task_page = self._store.list_tasks(
                    page=page,
                    page_size=200,
                    status=status,
                )
                tasks = task_page["items"]
                for task in tasks:
                    if status == "pending" and not (
                        task.get("run_mode") == "step"
                        and task.get("control_status") == "paused"
                    ):
                        continue
                    try:
                        aggregate = self._recover_running_task(task)
                    except Exception:  # noqa: BLE001 - 单任务损坏不影响其他恢复
                        logger.exception(
                            "活动工作流任务无法安全恢复：%s",
                            task.get("uuid"),
                        )
                        continue
                    if aggregate is not None:
                        recovered.append(aggregate)
                if page * 200 >= int(task_page["total"]):
                    break
                page += 1
        return recovered

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
                            "execution_unknown",
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

    def _recover_running_task(
        self,
        task: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """恢复单个可证明无结果不明作业的运行中任务。"""

        task_uuid = self._required_text(task.get("uuid"), field="task.uuid")
        jobs = self._store.list_jobs(task_uuid)
        # pending 人工确认尚未下发物理动作：重启后可以用同一
        # Job 和同一截止时间安全恢复等待，不应误标 execution_unknown。
        for job in jobs:
            if (
                job.get("executor_kind") == "manual_confirm"
                and job.get("status") == "dispatched"
            ):
                try:
                    confirmation = self._manual_confirmations.get_by_job(
                        self._required_text(job.get("uuid"), field="job.uuid")
                    )
                except StoreNotFound:
                    continue
                if confirmation.get("status") == "pending":
                    self._projection.recover_pending_manual_confirmation(
                        self._required_text(job.get("uuid"), field="job.uuid")
                    )
        jobs = self._store.list_jobs(task_uuid)
        uncertain_jobs = [
            job
            for job in jobs
            if job.get("status") in {
                "dispatched",
                "running",
                "cancel_requested",
                "execution_unknown",
            }
        ]
        if uncertain_jobs:
            for job in uncertain_jobs:
                if job.get("status") == "execution_unknown":
                    continue
                self._projection.project_execution_unknown(
                    self._required_text(job.get("uuid"), field="job.uuid"),
                    reason=(
                        "local_cancel_restart_missing_terminal"
                        if job.get("status") == "cancel_requested"
                        else "local_process_restart_missing_result"
                    ),
                )
            logger.error(
                "工作流任务 %s 存在 %d 个执行未知作业，已保留占用并禁止自动恢复",
                task_uuid,
                len(uncertain_jobs),
            )
            return self._aggregate(task_uuid)
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
                job
                for job in jobs
                if job.get("executor_kind") != "material_source"
            ]
        if any(
            job.get("status") not in {"pending", "succeeded"}
            for job in ordinary_jobs
        ):
            raise TaskSchedulerBridgeError("运行中任务包含不可恢复的作业终态")
        pending_jobs = [job for job in ordinary_jobs if job.get("status") == "pending"]
        if not pending_jobs:
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
            str(job.get("workflow_node_uuid") or ""): job
            for job in ordinary_jobs
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
        for job in pending_jobs:
            job_uuid = self._required_text(job.get("uuid"), field="job.uuid")
            self._task_by_job[job_uuid] = task_uuid
        self._submitted_tasks.add(task_uuid)
        try:
            self._scheduler.restore_workflow(spec, completed_results)
        except Exception:
            if not self._crossed_dispatch_boundary(jobs):
                self._submitted_tasks.discard(task_uuid)
                for job in pending_jobs:
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
        self._scheduler.remove_admission_retry_listener(
            self._retry_pending_admissions
        )
        self._scheduler.remove_job_pre_dispatch_listener(self._on_job_pre_dispatch)
        self._scheduler.remove_job_execution_wait_listener(
            self._on_job_execution_wait
        )
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
        self._scheduler.remove_job_cancel_no_send_listener(
            self._on_job_cancel_no_send
        )
        self._scheduler.remove_job_feedback_listener(self._on_job_feedback)
        self._scheduler.remove_job_finished_listener(self._on_job_finished)
        self._scheduler.remove_job_settled_listener(self._on_job_settled)
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

    def _retry_pending_admissions(self) -> None:
        """重试全部尚未注册到旧调度器的物料来源准入。

        参数：无。返回无；每个工作流任务（WorkflowTask）只在本轮重试一次，仍
        受阻时由 ``submit`` 重新登记。异常：存储、准入或调度失败原样传播，禁止
        在公开重排失败时继续派发其他普通动作。
        """

        for task_uuid in tuple(self._admission_pending_tasks):
            self._admission_pending_tasks.discard(task_uuid)
            self.submit(self._store.get_task(task_uuid))

    def _on_job_pre_dispatch(self, dispatching: Mapping[str, Any]) -> bool | None:
        """在物理派发前提交标准作业派发意图。

        参数：``dispatching`` 是既有调度器即将越过执行边界的作业摘要。返回：
        非本桥作业返回 ``None`` 保持旧路径；标准作业取得全部持久执行锁时为真，
        锁冲突保持 ``pending`` 时为假。持久转换冲突原样抛出。
        """

        job_uuid = str(dispatching.get("job_id") or "")
        task_uuid = self._task_by_job.get(job_uuid)
        if task_uuid is None:
            return None
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
        aggregate = self._projection.project_pre_dispatch(
            task_uuid=task_uuid,
            job_uuid=job_uuid,
            resolved_param=resolved_args,
            execution_locks=execution_locks,
        )
        projected_job = next(
            (job for job in aggregate["jobs"] if job["uuid"] == job_uuid),
            None,
        )
        if projected_job is None:
            raise StoreConflict(f"派发作业投影后消失：{job_uuid}")
        if (
            projected_job.get("executor_kind") == "manual_confirm"
            and projected_job.get("status") == "dispatched"
        ):
            self._schedule_manual_confirmation_timeout(job_uuid)
        return projected_job["status"] == "dispatched"

    def _on_job_execution_wait(self, waiting: Mapping[str, Any]) -> None:
        """把旧调度器先命中的内存占用投影为持久等待事实。"""

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
        )

    def _on_job_dispatch_accepted(self, job_uuid: str) -> None:
        """把本桥作业的本地执行接受事实投影为 running。"""

        if job_uuid not in self._task_by_job:
            return
        self._projection.project_dispatch_accepted(job_uuid)

    def _on_job_dispatch_uncertain(self, job_uuid: str, reason: str) -> None:
        """把派发边界异常保守投影为 execution_unknown。"""

        if job_uuid not in self._task_by_job:
            return
        self._projection.project_execution_unknown(job_uuid, reason=reason)

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
            job
            for job in aggregate["jobs"]
            if str(job.get("uuid")) == job_uuid
        )
        self._schedule_cancel_timeout(accepted_job)

    def _on_job_cancel_uncertain(self, job_uuid: str, reason: str) -> None:
        """把取消拒绝或取消能力缺失保守投影为执行未知。"""

        if job_uuid not in self._task_by_job:
            return
        self._cancel_cancel_timer(job_uuid)
        job = self._store.get_job(job_uuid)
        if job.get("status") in {"succeeded", "failed", "canceled", "timeout"}:
            return
        self._projection.project_execution_unknown(job_uuid, reason=reason)

    def _on_job_cancel_no_send(self, job_uuid: str) -> None:
        """用 No-send Proof 立即结算一个尚未执行的取消作业。"""

        if job_uuid not in self._task_by_job:
            return
        self._cancel_cancel_timer(job_uuid)
        job = self._store.get_job(job_uuid)
        if job.get("status") != "cancel_requested":
            return
        self._projection.project_job_finished(
            job_uuid=job_uuid,
            scheduler_state="canceled",
            return_info={"cancel_reason": "local_no_send_proof"},
            error_info=[],
        )

    def _schedule_cancel_timeout(self, job: Mapping[str, Any]) -> None:
        """按持久截止时间为一个取消中作业安排单次本地检查。

        参数：``job`` 是最新持久作业投影。返回无。异常：缺少或损坏截止时间时
        立即转为执行未知，避免取消状态永久悬挂。受理前使用 ACK 截止时间，受理后
        使用设备完成截止时间。
        """

        job_uuid = self._required_text(job.get("uuid"), field="job.uuid")
        accepted = bool(job.get("cancel_accepted_at"))
        field = (
            "cancel_complete_deadline_at"
            if accepted
            else "cancel_ack_deadline_at"
        )
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
        """把到期且仍未收敛的取消作业冻结为执行未知。

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
            self._projection.project_execution_unknown(job_uuid, reason=reason)
        except Exception:  # noqa: BLE001 - 后台检查不能终止调用线程
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
            except Exception:  # noqa: BLE001 - 后台截止检查不能终止调用线程
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
        aggregate = self._projection.project_job_finished(
            job_uuid=job_uuid,
            scheduler_state=(
                "success" if success else ("canceled" if canceled else "failed")
            ),
            return_info=return_info,
            error_info=error_info,
            manual_confirmation_status=(
                "timed_out"
                if suc_type == "manual_confirmation_timeout"
                else None
            ),
        )
        self._cancel_cancel_timer(job_uuid)
        self._cancel_manual_confirmation_timer(job_uuid)
        terminal_status = aggregate["task"]["status"]
        if terminal_status == "succeeded" and any(
            job.get("executor_kind") == "material_source"
            for job in aggregate["jobs"]
        ):
            # 成功表示最后一个动作已完成，可立即释放来源层短期预留。失败任务必须
            # 等所有在途设备动作结算，由 ``_on_job_settled`` 推进 cleanup_status。
            self._material_sources.release_terminal_reservations(
                task_uuid,
                reason=f"workflow_{terminal_status}",
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
            if not active_for_task:
                self._projection.project_cleanup_settled(task_uuid)
                if any(
                    job.get("executor_kind") == "material_source"
                    for job in aggregate["jobs"]
                ):
                    self._material_sources.release_terminal_reservations(
                        task_uuid,
                        reason=f"workflow_{aggregate['task']['status']}",
                    )
        if task_uuid not in self._task_by_job.values():
            self._submitted_tasks.discard(task_uuid)

    def _aggregate(self, task_uuid: str) -> dict[str, Any]:
        """读取一个标准任务/作业聚合。

        参数：``task_uuid`` 是父任务稳定身份。返回：当前任务投影和有序作业列表。
        异常：任务不存在时传播工作流存储（WorkflowStore）异常。
        """

        return {
            "task": self._store.get_task(task_uuid),
            "jobs": self._store.list_jobs(task_uuid),
        }

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
                "execution_unknown",
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
