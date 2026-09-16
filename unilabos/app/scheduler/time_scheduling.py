"""现有执行内核与后台时间规划之间的只读快照适配。

规划结果只决定当前候选的等待顺序和建议设备；既有参数、资源、库存、运行模式
以及持久派发授权仍须逐项复验。所有实体占用只按真实结果解除。
"""

from __future__ import annotations

import math
import os
from dataclasses import asdict, replace
from typing import TYPE_CHECKING, Any

from unilabos.app.scheduler.device_target import DeviceTargetUnavailable, ResolvedDeviceTarget
from unilabos.app.scheduler.models import ReadyTask, priority_weight
from unilabos.app.scheduler.param_resolver import ParamResolveError
from unilabos.app.scheduler.resource_lock import conflicting_resource_lock_keys
from unilabos.app.scheduler.time_planning import (
    PlanningAlternative, PlanningCompletionTarget, PlanningHold, PlanningNode, PlanningSnapshot,
    RollingTimePlanner, TimePlanningConfig, snapshot_revision, ticks,
)
from unilabos.app.scheduler.workflow_planning import PlanningInformationPrefix, build_workflow_planning_view
from unilabos.workflow.execution_resource_policy import resolve_execution_resource_policy
from unilabos.workflow.resource_lock_key import device_lock_key

if TYPE_CHECKING:
    from unilabos.app.scheduler.service import EdgeScheduler


def configured_time_planner() -> RollingTimePlanner | None:
    """生产组合根默认启用；显式禁用供迁移回退，隔离构造由调用方注入。"""
    enabled = os.environ.get("UNILABOS_TIME_SCHEDULING_ENABLED", "true").lower()
    if enabled not in {"true", "false", "1", "0"}:
        raise ValueError("UNILABOS_TIME_SCHEDULING_ENABLED 必须是 true 或 false")
    if enabled in {"false", "0"}:
        return None
    return RollingTimePlanner(TimePlanningConfig(
        solve_seconds=float(os.environ.get("UNILABOS_TIME_SCHEDULING_SOLVE_SECONDS", "0.5")),
        max_nodes=int(os.environ.get("UNILABOS_TIME_SCHEDULING_MAX_NODES", "256")),
        aging_seconds=float(os.environ.get("ULAB_SCHEDULER_AGING_INTERVAL_SECONDS", "30")),
    ))


class TimeSchedulingAdapter:
    """同一调度锁内复制当前事实，后台只接收不可变标量。"""

    def __init__(self, planner: RollingTimePlanner) -> None:
        self.planner = planner
        self._wait: dict[str, tuple[float, float, bool]] = {}
        self._targets: dict[tuple[str, str, str], ResolvedDeviceTarget] = {}
        self._last_input: dict[str, Any] = {}
        self._last_error = ""
        self._prefix_wait: dict[tuple[str, str], tuple[float, float, bool]] = {}

    def _priority(self, scheduler: EdgeScheduler, workflow_id: str, ready: list[ReadyTask], now: float) -> float:
        run = scheduler._workflows[workflow_id]
        eligible = (run.dispatch_gate == "active" and run.state.value == "running"
                    and any(task.workflow_id == workflow_id and not task.node.is_manual_confirm() for task in ready)
                    and not any(job.workflow_id == workflow_id for job in scheduler._inflight.values())
                    and (scheduler._station_control is None or scheduler._station_control.allows_task(workflow_id)))
        accumulated, previous, was_eligible = self._wait.get(workflow_id, (0.0, now, False))
        if was_eligible:
            accumulated += max(0.0, now - previous)
        self._wait[workflow_id] = (accumulated, now, eligible)
        return priority_weight(run.spec.priority) + math.floor(accumulated / self.planner.config.aging_seconds)

    def _devices(self, scheduler: EdgeScheduler, node: Any) -> tuple[ResolvedDeviceTarget, ...]:
        if node.device_id:
            return (ResolvedDeviceTarget(node.device_id, node.device_material_uuid),)
        if scheduler._device_target_resolver is None:
            raise ValueError("排程缺少动态设备选择权威")
        seen: set[str] = set()
        result = []
        for _ in range(self.planner.config.max_alternatives):
            try:
                target = scheduler._device_target_resolver(node.device_selector, node.action_name, seen)
            except DeviceTargetUnavailable:
                break
            if target in result:
                break
            result.append(target)
            seen.add(device_lock_key(target.material_uuid or target.local_device_id))
            seen.add(device_lock_key(target.local_device_id))
        if not result:
            raise ValueError("排程没有当前已注册且满足要求的设备候选")
        return tuple(result)

    @staticmethod
    def _active_keys(scheduler: EdgeScheduler, run: Any, node: Any,
                     requested: set[str], *, job_id: str | None = None) -> set[str]:
        """复用执行内核的直接使用语义，不把整段保管资源变成每个动作的独占。"""
        plan, plan_keys, _inherited, _intervals = scheduler._interval_projection(
            run, node, current_job_id=job_id,
        )
        return scheduler._active_use_resource_keys(
            run, node, plan=plan, plan_resource_keys=plan_keys,
            requested_lock_keys=requested | plan_keys,
        )

    def _inflight_active_keys(self, scheduler: EdgeScheduler, job: Any) -> set[str]:
        """旧计划缺少直接使用证明时，原内核仍将全部键视为独占。"""
        run = scheduler._workflows.get(job.workflow_id)
        node = run.node(job.node_id) if run is not None else None
        requested = set(job.resource_lock_keys) - set(job.operator_released_keys)
        if node is None:
            # 定义暂不可恢复时不猜测哪个占用只是保管，保留全部实际记录。
            active = requested | set(job.active_resource_lock_keys)
        else:
            active = self._active_keys(scheduler, run, node, requested, job_id=job.job_id)
        if node is None or not node.always_free or node.is_manual_confirm():
            active.add(device_lock_key(job.device_material_uuid or job.device_id))
        return active

    def order(self, scheduler: EdgeScheduler, ready: list[ReadyTask]) -> list[ReadyTask]:
        """未有同版本合格候选时等待；人工确认请求继续走原有执行前检查。"""
        now = scheduler._clock()
        try:
            snapshot, targets, manual = self._build(scheduler, ready, now)
            self._last_error = ""
        except (ValueError, TypeError, KeyError) as error:
            self._last_error = str(error)
            self.planner.wake_after(self.planner.config.refresh_seconds)
            return []
        self._targets = targets
        self._last_input = {"revision": snapshot.revision, "nodes": len(snapshot.nodes),
                            "partial_tasks": snapshot.partial_tasks, "blocked": snapshot.blocked,
                            "completion_targets": [asdict(target) for target in snapshot.completion_targets],
                            "holds": [asdict(hold) for hold in snapshot.holds]}
        plan = self.planner.request(snapshot)
        if snapshot.nodes or snapshot.holds or snapshot.blocked:
            self.planner.wake_after(self.planner.config.refresh_seconds)
        entries = {entry.key: entry for entry in plan.nodes} if plan is not None else {}
        result = []
        for task in ready:
            key = (task.workflow_id, task.node.id)
            if key in manual:
                result.append(task)
                continue
            entry = entries.get(key)
            if entry is None or entry.fixed:
                continue
            start_at = plan.time_origin + entry.start_ms / 1000
            if now + 0.001 < start_at:
                self.planner.wake_after(min(start_at - now, self.planner.config.refresh_seconds))
                continue
            target = targets.get((task.workflow_id, task.node.id, entry.device_id))
            if target is None:
                continue
            # 复制候选中的节点，不修改冻结的工作流定义。执行前仍按固定身份复查注册。
            result.append(replace(task, node=replace(task.node, device_id=target.local_device_id,
                                                     device_material_uuid=target.material_uuid)))
        result.sort(key=lambda task: (
            entries[(task.workflow_id, task.node.id)].start_ms
            if (task.workflow_id, task.node.id) in entries else 0,
            -self._priority_value(scheduler, task.workflow_id), task.submitted_at,
            task.workflow_id, task.node.id))
        return result

    def _priority_value(self, scheduler: EdgeScheduler, task_id: str) -> float:
        return priority_weight(scheduler._workflows[task_id].spec.priority) + math.floor(
            self._wait.get(task_id, (0, 0, False))[0] / self.planner.config.aging_seconds)

    def _deferred_open_scopes(self, scheduler: EdgeScheduler, nodes: dict[tuple[str, str], PlanningNode], holds: list[PlanningHold],
                              priorities: dict[str, float]) -> set[tuple[str, str]]:
        """本轮只选择互不冲突的新开放区间，不动任何已经发生的持有。

        同一取得动作关联的资源要一起选，避免只选半套资源。此处只是有限模型
        的范围选择；不替代现有联合资源计划与执行前的死锁/资源检查。
        """
        groups: list[tuple[set[tuple[str, str]], set[str], set[str]]] = []
        for hold in holds:
            if hold.already_held or hold.release_known:
                continue
            members, resources, identities = set(hold.members), set(hold.resources), {hold.hold_id}
            overlaps = [index for index, group in enumerate(groups) if group[0] & members]
            for index in reversed(overlaps):
                old_members, old_resources, old_ids = groups.pop(index)
                members.update(old_members)
                resources.update(old_resources)
                identities.update(old_ids)
            groups.append((members, resources, identities))
        def rank(group: tuple[set[tuple[str, str]], set[str], set[str]]) -> tuple[float, float, float, str, tuple[str, ...]]:
            task = min(key[0] for key in group[0])
            return (-priorities[task], -self._wait.get(task, (0, 0, False))[0],
                    scheduler._workflows[task].spec.submitted_at, task, tuple(sorted(group[2])))
        selected: set[str] = set()
        deferred: set[tuple[str, str]] = set()
        for members, resources, _ in sorted(groups, key=rank):
            if resources & selected or conflicting_resource_lock_keys(resources, selected):
                if any(nodes[key].fixed for key in members if key in nodes):
                    raise ValueError("实际运行节点缺少相应的已持有区间证明")
                deferred.update(members)
            else:
                selected.update(resources)
        return deferred

    def _build(self, scheduler: EdgeScheduler, ready: list[ReadyTask], now: float) -> tuple[
        PlanningSnapshot, dict[tuple[str, str, str], ResolvedDeviceTarget], set[tuple[str, str]],
    ]:
        inflight = {(job.workflow_id, job.node_id): job for job in scheduler._inflight.values()}
        priorities = {key: self._priority(scheduler, key, ready, now) for key in scheduler._workflows}
        nodes: dict[tuple[str, str], PlanningNode] = {}
        holds: list[PlanningHold] = []
        partial: set[str] = set()
        blocked: list[tuple[str, str, str]] = []
        manual: set[tuple[str, str]] = set()
        targets = {}
        facts = []
        views = []
        prefix_candidates: dict[str, list[PlanningInformationPrefix]] = {}
        for workflow_id, run in sorted(scheduler._workflows.items(), key=lambda item: (-priorities[item[0]], item[1].spec.submitted_at, item[0])):
            held = {identity: set(keys) for (owner, identity), keys in scheduler._interval_resource_holders.items() if owner == workflow_id}
            resource_plan = scheduler._resource_plan_for_spec(run.spec)
            interval_resources = {}
            if resource_plan is not None:
                resource_by_id = {resource.resource_id: resource for resource in resource_plan.resources}
                interval_resources = {interval.interval_id: resource_by_id[interval.resource_id].canonical_key
                                      for interval in resource_plan.intervals}
            for job in scheduler._inflight.values():
                if job.workflow_id == workflow_id:
                    for identity in job.resource_interval_ids:
                        resource_key = interval_resources.get(identity)
                        if resource_key is None:
                            held.setdefault(identity, set()).update(job.resource_lock_keys - job.operator_released_keys)
                        elif resource_key in job.resource_lock_keys and resource_key not in job.operator_released_keys:
                            held.setdefault(identity, set()).add(resource_key)
            if run.state.value in {"success", "failed", "canceled", "timeout"} and not held and not any(key[0] == workflow_id for key in inflight):
                self._wait.pop(workflow_id, None)
                continue
            view = build_workflow_planning_view(run,
                approved_manual_node_ids=[job.node_id for job in scheduler._inflight.values()
                                          if job.workflow_id == workflow_id and job.manual_confirmation_approved],
                held_interval_keys=held, step_target_node_id=scheduler._step_targets.get(workflow_id))
            views.append(view)
            prefix_candidates[workflow_id] = list(view.information_prefixes)
            facts.append((workflow_id, dict(view.node_states), dict(view.blocked), priorities[workflow_id], run.dispatch_gate))
            manual.update((workflow_id, node_id) for node_id in view.manual_gate_node_ids)
            if not view.full_tail_known:
                partial.add(workflow_id)
            blocked.extend((workflow_id, node_id, ";".join(reasons)) for node_id, reasons in view.blocked.items())
            if scheduler._station_control is not None and not scheduler._station_control.allows_task(workflow_id):
                blocked.extend((workflow_id, node.id, "task_control_blocked") for node in view.nodes)
                continue
            for node in view.nodes:
                key = (workflow_id, node.id)
                job = inflight.get(key)
                if job is None and len(nodes) >= self.planner.config.max_nodes:
                    partial.add(workflow_id)
                    blocked.append((*key, "planning_scope_budget"))
                    continue
                if job is not None:
                    remaining = job.estimated_s - max(0, now - job.dispatched_at)
                    if remaining <= 0:
                        conditional = scheduler._estimator.estimate_remaining(
                            job.device_action_key, job.resolved_args,
                            elapsed_s=max(0, now - job.dispatched_at),
                            device_id=job.device_material_uuid or job.device_id,
                        )
                        if conditional.planned_s is not None:
                            remaining = conditional.planned_s
                            facts.append((job.job_id, "conditional_remaining", int(now / self.planner.config.refresh_seconds)))
                    if remaining <= 0:
                        blocked.append((*key, "actual_completion_overdue_unknown"))
                        partial.add(workflow_id)
                        holds.append(PlanningHold("unknown:" + job.job_id, workflow_id,
                            tuple(sorted(self._inflight_active_keys(scheduler, job)))))
                        facts.append((job.job_id, "overdue", job.estimated_s))
                        continue
                    options = (PlanningAlternative(job.device_id, ticks(remaining),
                        tuple(sorted(self._inflight_active_keys(scheduler, job)))),)
                    nodes[key] = PlanningNode(key, options, (), priorities[workflow_id], True, job.dispatched_at)
                    facts.append((job.job_id, job.dispatched_at, job.estimated_s, options[0].resources))
                    continue
                try:
                    params = run.resolve_params(node.id)
                    policy = resolve_execution_resource_policy(node.execution_policy, params)
                    resource_keys = scheduler._resource_lock_keys(node, params) | set(policy.device_lock_keys)
                    resource_keys = self._active_keys(scheduler, run, node, resource_keys)
                    alternatives = []
                    for target in self._devices(scheduler, node):
                        action_key = f"/devices/{target.local_device_id}/{node.action_name}"
                        timing = scheduler._estimator.estimate_timing(action_key, params, device_id=target.material_uuid or target.local_device_id)
                        if timing.planned_s is None:
                            raise ValueError("动作耗时尚无可用估计")
                        keys = set(resource_keys)
                        if not node.always_free or node.is_manual_confirm():
                            keys.add(device_lock_key(target.material_uuid or target.local_device_id))
                        alternatives.append(PlanningAlternative(target.local_device_id, ticks(timing.planned_s), tuple(sorted(keys))))
                        targets[(*key, target.local_device_id)] = target
                    nodes[key] = PlanningNode(key, tuple(alternatives),
                        tuple((workflow_id, parent) for parent in view.dependencies[node.id]), priorities[workflow_id])
                    facts.append((key, params, asdict(nodes[key])))
                except (ValueError, TypeError, KeyError, ParamResolveError) as error:
                    blocked.append((*key, str(error)))
                    partial.add(workflow_id)
                    if isinstance(error, ParamResolveError):
                        # 路径已知但参数未产生时，先以产生输入的必要前驱为目标。
                        parents = view.dependencies[node.id]
                        members = set(parents)
                        pending_members = list(parents)
                        while pending_members:
                            for parent in view.dependencies[pending_members.pop()]:
                                if parent not in members:
                                    members.add(parent)
                                    pending_members.append(parent)
                        if parents:
                            prefix_candidates[workflow_id].append(PlanningInformationPrefix(
                                "parameters:" + node.id, parents, tuple(sorted(members))))
        # 在途人工等待等不在有限视图内，但实际占用仍必须投影到快照。
        known_holds = {hold.hold_id for hold in holds}
        for key, job in inflight.items():
            if key not in nodes and "unknown:" + job.job_id not in known_holds:
                holds.append(PlanningHold("unknown:" + job.job_id, job.workflow_id,
                    tuple(sorted(self._inflight_active_keys(scheduler, job)))))
        for view in views:
            for interval in view.continuous_intervals:
                members = tuple((view.workflow_id, node_id) for node_id in interval.member_node_ids if (view.workflow_id, node_id) in nodes)
                if interval.held or members:
                    holds.append(PlanningHold(view.workflow_id + ":" + interval.interval_id, view.workflow_id,
                        interval.resource_keys, members, interval.held,
                        interval.release_known and (view.workflow_id, interval.release_node_id) in nodes))
        parents = {hold.hold_id: hold for hold in holds if hold.already_held and not hold.hold_id.startswith("unknown:")}
        job_by_hold = {"unknown:" + job.job_id: job for job in inflight.values()}
        split_holds = []
        unknown_parents = set()
        for hold in holds:
            job = job_by_hold.get(hold.hold_id)
            if job is None:
                split_holds.append(hold)
                continue
            parent_ids = tuple(job.workflow_id + ":" + identity for identity in job.resource_interval_ids
                               if job.workflow_id + ":" + identity in parents
                               and set(parents[job.workflow_id + ":" + identity].resources) & set(hold.resources))
            parent_keys = {key for identity in parent_ids for key in parents[identity].resources}
            covered = set(hold.resources) & parent_keys
            uncovered = set(hold.resources) - covered
            if covered:
                split_holds.append(replace(hold, resources=tuple(sorted(covered)), contained_in=parent_ids))
                unknown_parents.update(parent_ids)
            if uncovered:
                split_holds.append(replace(hold, hold_id=hold.hold_id + ":unscoped", resources=tuple(sorted(uncovered))))
        holds = [replace(hold, release_known=False) if hold.hold_id in unknown_parents else hold for hold in split_holds]
        # 既有外部占用没有可靠结束证据，禁止用默认时长虚构释放。
        represented = {resource for node in nodes.values() if node.fixed for alt in node.alternatives for resource in alt.resources}
        represented.update(resource for hold in holds if hold.already_held for resource in hold.resources)
        external = scheduler._busy_keys() - represented
        external = {key for key in external if not any(key.startswith(resource + "/") for resource in represented)}
        if external:
            holds.append(PlanningHold("external", "", tuple(sorted(external))))
        # 未知占用对应的竞争节点及其依赖后继不进入本轮有限模型，其他任务照常推进。
        while True:
            deferred = set()
            excluded = set()
            for key, node in nodes.items():
                if node.fixed:
                    # 已经运行的事实不能因预测冲突从模型中消失；冲突必须使候选失败。
                    continue
                for hold in holds:
                    if hold.already_held and not hold.release_known and key not in hold.members and all(
                        conflicting_resource_lock_keys(set(alt.resources), set(hold.resources)) or set(alt.resources) & set(hold.resources)
                        for alt in node.alternatives):
                        excluded.add(key)
            for select_new_scopes in (False, True):
                if select_new_scopes:
                    if excluded:
                        # 先传播实际阻塞并缩小模型，下一轮才在可行候选中选新 scope。
                        break
                    deferred = self._deferred_open_scopes(scheduler, nodes, holds, priorities)
                    excluded.update(deferred)
                changed = True
                while changed:
                    changed = False
                    for key, node in nodes.items():
                        if key not in excluded and any(parent not in nodes or parent in excluded for parent in node.predecessors):
                            excluded.add(key)
                            changed = True
            if not excluded:
                break
            for key in excluded:
                nodes.pop(key, None)
                partial.add(key[0])
                blocked.append((*key, "new_open_scope_deferred" if key in deferred
                                else "resource_or_dependency_completion_unknown"))
            holds = [replace(hold, members=tuple(key for key in hold.members if key in nodes),
                             release_known=hold.release_known and all(key in nodes for key in hold.members))
                     for hold in holds if hold.already_held or any(key in nodes for key in hold.members)]
            # 去掉未知动作的后继可能让另一连续区间也失去已知退出点；继续排除
            # 竞争该区间的候选，直到不再产生新阻塞。实际持有始终保留。
        completion_targets = []
        current_prefixes: set[tuple[str, str]] = set()
        for workflow_id in sorted({key[0] for key in nodes}):
            task_keys = tuple(sorted(key for key in nodes if key[0] == workflow_id))
            if workflow_id not in partial:
                completion_targets.append(PlanningCompletionTarget(workflow_id, task_keys))
                continue
            available = []
            for prefix in prefix_candidates.get(workflow_id, ()):
                if not all((workflow_id, member) in nodes for member in prefix.member_node_ids):
                    continue
                identity = (workflow_id, prefix.prefix_id)
                current_prefixes.add(identity)
                waited, previous, was_eligible = self._prefix_wait.get(identity, (0.0, now, False))
                if was_eligible:
                    waited += max(0, now - previous)
                eligible = self._wait.get(workflow_id, (0, now, False))[2]
                self._prefix_wait[identity] = (waited, now, eligible)
                available.append((waited, prefix))
            if available:
                selected = min(available, key=lambda item: (-item[0], item[1].prefix_id))[1]
                completion_targets.append(PlanningCompletionTarget(workflow_id,
                    tuple((workflow_id, node_id) for node_id in selected.target_node_ids),
                    "information", selected.prefix_id))
            elif any(task == workflow_id and reason == "planning_scope_budget" for task, _, reason in blocked):
                completion_targets.append(PlanningCompletionTarget(workflow_id, task_keys, "partial_scope"))
            else:
                # 当前已到人工/未知物理边界时，不把独立动作末尾冒充整个任务完工。
                completion_targets.append(PlanningCompletionTarget(workflow_id, (), "information"))
        self._prefix_wait = {key: value for key, value in self._prefix_wait.items() if key in current_prefixes}
        facts.extend([("holds", [asdict(hold) for hold in holds]), ("blocked", sorted(blocked)),
                      ("completion_targets", [asdict(target) for target in completion_targets])])
        revision = snapshot_revision(facts)
        return PlanningSnapshot(revision, now, tuple(nodes.values()), tuple(holds), tuple(sorted(partial)),
                                tuple(sorted(blocked)), tuple(completion_targets)), targets, manual

    def snapshot(self) -> dict[str, Any]:
        return {**self.planner.snapshot(), "input": self._last_input, "input_error": self._last_error,
                "guaranteed_hard_time_limits": False}
