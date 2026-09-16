"""有界时间排程与独立验证；候选计划不授予设备执行权。

输入只包含已固定的值，没有数据库连接、工作流可变对象或驱动。所有时间采用
向上取整的毫秒刻度。未知实际结束的占用不会因为预测到期而解除。
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field, replace
from typing import Callable

from unilabos.app.scheduler.resource_lock import conflicting_resource_lock_keys


NodeKey = tuple[str, str]


def ticks(seconds: float) -> int:
    """将非负有限秒数保守转换为整数毫秒。"""
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("排程时长必须是非负有限秒数")
    return max(1, math.ceil(seconds * 1000))


@dataclass(frozen=True)
class PlanningAlternative:
    """一次合法设备选择及其完整耗时、资源组合。"""

    device_id: str
    duration_ms: int
    resources: tuple[str, ...]


@dataclass(frozen=True)
class PlanningNode:
    """本轮已激活的动作，固定动作只规划剩余时间，不修改实际起点。"""

    key: NodeKey
    alternatives: tuple[PlanningAlternative, ...]
    predecessors: tuple[NodeKey, ...] = ()
    priority: float = 1.0
    fixed: bool = False
    actual_started_at: float | None = None
    parameters_final: bool = True


@dataclass(frozen=True)
class PlanningHold:
    """跨节点持续占用；没有可信释放边界时持续至本轮规划范围末尾。"""

    hold_id: str
    owner_task: str
    resources: tuple[str, ...]
    members: tuple[NodeKey, ...] = ()
    already_held: bool = True
    release_known: bool = False
    contained_in: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlanningCompletionTarget:
    """本轮目标终点；information 只到信息边界，不冒充 Task 完工。"""

    task_id: str
    node_keys: tuple[NodeKey, ...]
    kind: str = "task_completion"
    prefix_id: str = ""


@dataclass(frozen=True)
class PlanningSnapshot:
    """不可变快照；revision 由业务事实生成，不随墙上时钟反复失效。"""

    revision: str
    time_origin: float
    nodes: tuple[PlanningNode, ...]
    holds: tuple[PlanningHold, ...] = ()
    partial_tasks: tuple[str, ...] = ()
    blocked: tuple[tuple[str, str, str], ...] = ()
    completion_targets: tuple[PlanningCompletionTarget, ...] = ()
    previous_plan: CandidatePlan | None = None


@dataclass(frozen=True)
class PlannedNode:
    key: NodeKey
    device_id: str
    start_ms: int
    end_ms: int
    resources: tuple[str, ...]
    fixed: bool = False


@dataclass(frozen=True)
class CandidatePlan:
    revision: str
    time_origin: float
    status: str
    nodes: tuple[PlannedNode, ...] = ()
    proven_priority_tiers: tuple[float, ...] = ()
    objective_value: float | None = None
    objective_bound: float | None = None
    wall_seconds: float = 0.0
    partial_tasks: tuple[str, ...] = ()
    error: str = ""
    validated: bool = False
    objective_stage: str = ""
    completed_stages: int = 0
    completion_targets: tuple[PlanningCompletionTarget, ...] = ()


@dataclass(frozen=True)
class TimePlanningConfig:
    """后台计算限额不构成设备硬实时能力声明。"""

    solve_seconds: float = 0.5
    max_nodes: int = 256
    max_alternatives: int = 8
    max_holds: int = 512
    aging_seconds: float = 30.0
    refresh_seconds: float = 1.0

    def __post_init__(self) -> None:
        for value in (self.solve_seconds, self.aging_seconds, self.refresh_seconds):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("排程时间预算必须是正有限秒数")
        for value in (self.max_nodes, self.max_alternatives, self.max_holds):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError("排程规模限额必须是正整数")


def snapshot_revision(facts: object) -> str:
    """对调用方选定的业务事实取指纹，禁止以对象地址标识快照。"""
    return hashlib.sha256(json.dumps(facts, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _conflicts(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    return bool(set(left) & set(right) or conflicting_resource_lock_keys(set(left), set(right)))


def _hold_relations(snapshot: PlanningSnapshot) -> dict[str, PlanningHold]:
    """验证显式内外层持有身份，不能把同任务的不同持有自动视为共享。"""
    holds = {hold.hold_id: hold for hold in snapshot.holds}
    if len(holds) != len(snapshot.holds):
        raise ValueError("连续占用身份重复，应先按实际同一持有记录合并")
    for child in snapshot.holds:
        if len(set(child.contained_in)) != len(child.contained_in):
            raise ValueError("连续占用的父身份重复")
        covered: set[str] = set()
        for identity in child.contained_in:
            parent = holds.get(identity)
            if parent is None or identity == child.hold_id:
                raise ValueError("连续占用的父身份不存在或引用自身")
            if parent.owner_task != child.owner_task or not parent.already_held:
                raise ValueError("内层实际占用只能引用同任务已经持有的父区间")
            shared = set(parent.resources) & set(child.resources)
            if not shared:
                raise ValueError("父占用没有覆盖内层占用的规范资源键")
            covered.update(shared)
        if child.contained_in and not set(child.resources) <= covered:
            raise ValueError("内层占用包含未被父区间覆盖的资源，应单独保留")
    remaining = set(holds)
    while remaining:
        roots = {identity for identity in remaining
                 if not set(holds[identity].contained_in) & remaining}
        if not roots:
            raise ValueError("连续占用的包含关系存在循环")
        remaining -= roots
    return holds


def _check_snapshot(snapshot: PlanningSnapshot, config: TimePlanningConfig) -> None:
    if not math.isfinite(snapshot.time_origin):
        raise ValueError("排程快照时间无效")
    if len(snapshot.nodes) > config.max_nodes or len(snapshot.holds) > config.max_holds:
        raise ValueError("排程快照超出规模限额，不能丢弃资源约束")
    by_key = {node.key: node for node in snapshot.nodes}
    if len(by_key) != len(snapshot.nodes):
        raise ValueError("排程节点身份重复")
    for node in snapshot.nodes:
        if not node.alternatives or len(node.alternatives) > config.max_alternatives:
            raise ValueError("排程设备候选数量无效")
        if not math.isfinite(node.priority):
            raise ValueError("排程优先级必须是有限值")
        if node.fixed and len(node.alternatives) != 1:
            raise ValueError("已经开始的动作不能重新选择设备")
        for option in node.alternatives:
            if isinstance(option.duration_ms, bool) or not isinstance(option.duration_ms, int) or not 0 < option.duration_ms < 10**12:
                raise ValueError("排程时长必须在允许范围内")
        if any(parent not in by_key or parent == node.key for parent in node.predecessors):
            raise ValueError("排程依赖不完整")
    remaining = set(by_key)
    while remaining:
        roots = {key for key in remaining if not set(by_key[key].predecessors) & remaining}
        if not roots:
            raise ValueError("排程依赖存在循环")
        remaining -= roots
    _hold_relations(snapshot)
    seen_targets: set[str] = set()
    for target in snapshot.completion_targets:
        if target.task_id in seen_targets or target.kind not in {"task_completion", "information", "partial_scope"}:
            raise ValueError("任务完成目标身份重复或类型无效")
        seen_targets.add(target.task_id)
        if len(set(target.node_keys)) != len(target.node_keys) or any(
            key not in by_key or key[0] != target.task_id for key in target.node_keys
        ):
            raise ValueError("完成目标引用不属于当前任务的节点")
    for hold in snapshot.holds:
        if any(member not in by_key for member in hold.members):
            raise ValueError("连续占用的节点范围不完整")
        if not hold.already_held and not hold.members:
            raise ValueError("尚未开始的连续占用没有取得节点")


def _horizon(snapshot: PlanningSnapshot) -> int:
    return max(1000, sum(max(a.duration_ms for a in n.alternatives) for n in snapshot.nodes) + 1000)


def validate_candidate(snapshot: PlanningSnapshot, plan: CandidatePlan) -> tuple[str, ...]:
    """独立重算依赖、设备、区间及持续占用，不信任求解器内部状态。"""
    errors: list[str] = []
    try:
        holds_by_id = _hold_relations(snapshot)
    except ValueError as error:
        return (str(error),)
    expected = {node.key: node for node in snapshot.nodes}
    actual = {node.key: node for node in plan.nodes}
    if plan.revision != snapshot.revision or plan.time_origin != snapshot.time_origin:
        errors.append("候选版本与快照不一致")
    if len(actual) != len(plan.nodes) or set(actual) != set(expected):
        return tuple(errors + ["候选节点集合不完整或重复"])
    spans: list[tuple[str, NodeKey | None, set[NodeKey], tuple[str, ...], int, int]] = []
    for key, entry in actual.items():
        node = expected[key]
        if (entry.device_id, entry.end_ms - entry.start_ms, entry.resources) not in {
            (alt.device_id, alt.duration_ms, alt.resources) for alt in node.alternatives
        }:
            errors.append(f"动作设备、资源或时长不符合快照：{key}")
        if entry.start_ms < 0 or entry.end_ms <= entry.start_ms or entry.end_ms > _horizon(snapshot):
            errors.append(f"动作时间无效：{key}")
        if node.fixed != entry.fixed or (node.fixed and entry.start_ms != 0):
            errors.append(f"已经开始的动作被移动：{key}")
        if any(actual[parent].end_ms > entry.start_ms for parent in node.predecessors):
            errors.append(f"违反动作依赖：{key}")
        spans.append(("node", key, {key}, entry.resources, entry.start_ms, entry.end_ms))
    for hold in snapshot.holds:
        members = [actual[key] for key in hold.members]
        start = 0 if hold.already_held else min(item.start_ms for item in members)
        end = max((item.end_ms for item in members), default=_horizon(snapshot)) if hold.release_known else _horizon(snapshot)
        spans.append((hold.hold_id, None, set(hold.members), hold.resources, start, end))
    for index, left in enumerate(spans):
        for right in spans[index + 1:]:
            if not _conflicts(left[3], right[3]):
                continue
            # 保管区间包含自身动作；自身并行动作之间仍检查真正的互斥。
            if left[1] is None and right[1] in left[2]:
                continue
            if right[1] is None and left[1] in right[2]:
                continue
            # 同一 Task 内不同持续占用也可能互斥；只有区间自身的成员动作
            # 可以继承该持有，不能仅凭任务相同就放过两个独立区间。
            if left[1] is None and right[1] is None:
                parent, child = None, None
                if left[0] in holds_by_id[right[0]].contained_in:
                    parent, child = left, right
                elif right[0] in holds_by_id[left[0]].contained_in:
                    parent, child = right, left
                if parent is not None:
                    if parent[4] > child[4] or parent[5] < child[5]:
                        errors.append("父资源区间未完整包含内层实际占用时间")
                    continue
            if max(left[4], right[4]) < min(left[5], right[5]):
                errors.append("候选包含冲突的资源占用区间")
    return tuple(dict.fromkeys(errors))


def solve_snapshot(snapshot: PlanningSnapshot, config: TimePlanningConfig) -> CandidatePlan:
    """按有效优先级逐级优化任务完工时间，各阶段共用总预算。"""
    from ortools.sat.python import cp_model

    started = time.monotonic()
    _check_snapshot(snapshot, config)
    horizon = _horizon(snapshot)
    model = cp_model.CpModel()
    starts, ends, choices = {}, {}, {}
    spans = []
    holds_by_id = {hold.hold_id: hold for hold in snapshot.holds}
    task_ends: dict[str, list] = defaultdict(list)
    priorities: dict[str, float] = {}
    for ordinal, node in enumerate(snapshot.nodes):
        start = model.new_int_var(0, horizon, f"s{ordinal}")
        end = model.new_int_var(0, horizon, f"e{ordinal}")
        starts[node.key], ends[node.key] = start, end
        if node.fixed:
            model.add(start == 0)
        selected = []
        for index, option in enumerate(node.alternatives):
            present = model.new_bool_var(f"p{ordinal}_{index}")
            model.add(end == start + option.duration_ms).only_enforce_if(present)
            selected.append(present)
            spans.append(("node", node.key, {node.key}, option.resources, start, end, present))
        model.add_exactly_one(selected)
        choices[node.key] = selected
        task_ends[node.key[0]].append(end)
        priorities[node.key[0]] = max(priorities.get(node.key[0], node.priority), node.priority)
    for node in snapshot.nodes:
        for parent in node.predecessors:
            model.add(starts[node.key] >= ends[parent])
    for ordinal, hold in enumerate(snapshot.holds):
        start = model.new_int_var(0, horizon, f"hs{ordinal}")
        end = model.new_int_var(0, horizon, f"he{ordinal}")
        if hold.already_held:
            model.add(start == 0)
        else:
            model.add_min_equality(start, [starts[key] for key in hold.members])
        if hold.release_known and hold.members:
            model.add_max_equality(end, [ends[key] for key in hold.members])
        else:
            model.add(end == horizon)
        spans.append((hold.hold_id, None, set(hold.members), hold.resources, start, end, None))
    for index, left in enumerate(spans):
        for j, right in enumerate(spans[index + 1:], index + 1):
            if not _conflicts(left[3], right[3]):
                continue
            if left[1] is not None and left[1] == right[1]:
                continue
            if left[1] is None and right[1] in left[2]:
                continue
            if right[1] is None and left[1] in right[2]:
                continue
            if left[1] is None and right[1] is None:
                parent, child = None, None
                if left[0] in holds_by_id[right[0]].contained_in:
                    parent, child = left, right
                elif right[0] in holds_by_id[left[0]].contained_in:
                    parent, child = right, left
                if parent is not None:
                    model.add(parent[4] <= child[4])
                    model.add(parent[5] >= child[5])
                    continue
            before = model.new_bool_var(f"order{index}_{j}")
            guards = [item for item in (left[6], right[6]) if item is not None]
            model.add(left[5] <= right[4]).only_enforce_if(guards + [before])
            model.add(right[5] <= left[4]).only_enforce_if(guards + [before.Not()])
    explicit_targets = {target.task_id: target for target in snapshot.completion_targets}
    task_completion = {}
    reported_targets = []
    for ordinal, (task, values) in enumerate(sorted(task_ends.items())):
        target = explicit_targets.get(task)
        if target is None:
            target = PlanningCompletionTarget(task, tuple(node.key for node in snapshot.nodes if node.key[0] == task))
        reported_targets.append(target)
        values = [ends[key] for key in target.node_keys]
        if not values:
            continue
        completion = model.new_int_var(0, horizon, f"task{ordinal}")
        model.add_max_equality(completion, values)
        task_completion[task] = completion
    tiers = sorted({priorities[task] for task in task_completion}, reverse=True)
    stages = [(f"priority:{tier}", tier, sum(task_completion[task] for task in task_completion if priorities[task] == tier)) for tier in tiers]
    previous = snapshot.previous_plan
    previous_nodes = {node.key: node for node in previous.nodes} if previous and previous.validated else {}
    changes, shifts = [], []
    for ordinal, node in enumerate(snapshot.nodes):
        old = previous_nodes.get(node.key)
        if node.fixed or old is None:
            continue
        old_start = old.start_ms + round((previous.time_origin - snapshot.time_origin) * 1000)
        if abs(old_start) > 10**12:
            continue
        shift = model.new_int_var(0, horizon + abs(old_start), f"shift{ordinal}")
        delta = model.new_int_var(-abs(old_start), horizon + abs(old_start), f"delta{ordinal}")
        model.add(delta == starts[node.key] - old_start)
        # 独立差值变量避免所用 OR-Tools 版本对带常量表达式取反时改变偏移。
        model.add_abs_equality(shift, delta)
        changed = model.new_bool_var(f"start_changed{ordinal}")
        model.add(starts[node.key] == old_start).only_enforce_if(changed.Not())
        model.add(starts[node.key] != old_start).only_enforce_if(changed)
        changes.append(changed)
        shifts.append(shift)
        same_device = [choice for alternative, choice in zip(node.alternatives, choices[node.key])
                       if alternative.device_id == old.device_id]
        if same_device:
            changes.append(1 - sum(same_device))
        # 提示可以不满足新约束，CP-SAT 会重新验证；不会形成执行许可。
        hint_start = max(0, min(horizon, old_start))
        model.add_hint(starts[node.key], hint_start)
        for alternative, choice in zip(node.alternatives, choices[node.key]):
            model.add_hint(choice, int(alternative.device_id == old.device_id))
    if ends:
        makespan = model.new_int_var(0, horizon, "makespan")
        model.add_max_equality(makespan, list(ends.values()))
        stages.append(("makespan", None, makespan))
        if changes:
            # 先减少改动项数，再减少总时间偏移；不使用跨单位的大权重。
            stages.append(("stability_changes", None, sum(changes)))
            stages.append(("stability_shift", None, sum(shifts)))
        else:
            stages.append(("start_sum", None, sum(starts.values())))
    else:
        stages.append(("empty", None, 0))
    proven: list[float] = []
    best: tuple[PlannedNode, ...] = ()
    status_name, objective, bound = "UNKNOWN", None, None
    objective_stage = ""
    completed_stages = 0
    for stage_name, tier, objective_expr in stages:
        remaining = config.solve_seconds - (time.monotonic() - started)
        if remaining <= 0:
            if best:
                status_name = "FEASIBLE"
            break
        model.minimize(objective_expr)
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = remaining
        solver.parameters.num_search_workers = 1
        solver.parameters.random_seed = 0
        solver.parameters.relative_gap_limit = 0
        solver.parameters.absolute_gap_limit = 0
        status = solver.solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            if not best:
                status_name = solver.status_name(status)
            else:
                # 前一级最优不代表整套分层目标最优；保留已有合法候选即可。
                status_name = "FEASIBLE"
            break
        status_name = solver.status_name(status)
        best = tuple(PlannedNode(node.key, option.device_id, solver.value(starts[node.key]),
                                 solver.value(ends[node.key]), option.resources, node.fixed)
                     for node in snapshot.nodes
                     for option, choice in zip(node.alternatives, choices[node.key])
                     if solver.boolean_value(choice))
        objective, bound = solver.objective_value, solver.best_objective_bound
        objective_stage = stage_name
        if status != cp_model.OPTIMAL:
            break
        completed_stages += 1
        if tier is not None:
            proven.append(tier)
        model.add(objective_expr == round(objective))
    plan = CandidatePlan(snapshot.revision, snapshot.time_origin, status_name, best,
                         tuple(proven), objective, bound, time.monotonic() - started,
                         snapshot.partial_tasks)
    errors = validate_candidate(snapshot, plan) if status_name in {"OPTIMAL", "FEASIBLE"} else ()
    return CandidatePlan(**{**asdict(plan), "nodes": plan.nodes, "proven_priority_tiers": plan.proven_priority_tiers,
                            "partial_tasks": plan.partial_tasks,
                            "error": "; ".join(errors),
                            "objective_stage": objective_stage,
                            "completed_stages": completed_stages,
                            "completion_targets": tuple(reported_targets),
                            "validated": status_name in {"OPTIMAL", "FEASIBLE"} and not errors})


class RollingTimePlanner:
    """一个求解线程及一个合并的待处理快照，不在调度锁内运行 CP-SAT。"""

    def __init__(self, config: TimePlanningConfig | None = None) -> None:
        from ortools.sat.python import cp_model  # noqa: F401
        self.config = config or TimePlanningConfig()
        self._lock = threading.Lock()
        self._wake_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="time-planner")
        self._active = False
        self._pending: PlanningSnapshot | None = None
        self._latest: CandidatePlan | None = None
        self._requested_revision = ""
        self._closed = False
        self._wake: Callable[[], None] = lambda: None
        self._timer: threading.Timer | None = None
        self._timer_deadline: float | None = None
        self._timer_generation = 0
        self._submitted = 0
        self._discarded = 0

    def bind_wakeup(self, wake: Callable[[], None]) -> None:
        with self._lock:
            self._wake = wake

    def _notify_wakeup(self) -> None:
        """关闭与回调互斥；close 返回后不存在迟到的后台唤醒。"""
        with self._wake_lock:
            with self._lock:
                if self._closed:
                    return
                wake = self._wake
            # 不持有规划状态锁调用调度器，避免回调间接 request 时形成锁倒序。
            try:
                wake()
            except Exception:
                import logging
                logging.getLogger(__name__).exception("时间排程唤醒调度失败")

    def request(self, snapshot: PlanningSnapshot) -> CandidatePlan | None:
        """只返回同一快照的已验证候选；旧结果不会绕过版本校验。"""
        with self._lock:
            if self._closed:
                return None
            if snapshot.revision != self._requested_revision:
                self._requested_revision = snapshot.revision
                # 旧候选仅作为软提示和稳定性参照；版本变化仍须完整重算与验证。
                self._pending = replace(snapshot, previous_plan=self._latest) if snapshot.previous_plan is None else snapshot
                self._submitted += 1
            if not self._active and self._pending is not None:
                self._active = True
                self._executor.submit(self._run)
            current = self._latest
            return current if current and current.revision == snapshot.revision and current.validated else None

    def _run(self) -> None:
        while True:
            with self._lock:
                if self._closed or self._pending is None:
                    self._active = False
                    return
                snapshot, self._pending = self._pending, None
            try:
                result = solve_snapshot(snapshot, self.config)
            except Exception as error:
                result = CandidatePlan(snapshot.revision, snapshot.time_origin, "ERROR", error=str(error))
            with self._lock:
                if snapshot.revision == self._requested_revision and not self._closed:
                    self._latest = result
                else:
                    self._discarded += 1
            # 回调仅请求一轮重排；不把 Future 完成线程变成第二个调度写入口。
            self._notify_wakeup()

    def wake_after(self, seconds: float) -> None:
        """最多保留一个定时唤醒，处理计划起点和运行超时。"""
        if not math.isfinite(seconds):
            raise ValueError("排程唤醒延迟必须是有限秒数")
        delay = max(0.02, seconds)
        deadline = time.monotonic() + delay
        with self._lock:
            if self._closed:
                return
            if self._timer_deadline is not None and self._timer_deadline <= deadline:
                return
            if self._timer is not None:
                self._timer.cancel()
            self._timer_generation += 1
            generation = self._timer_generation
            self._timer_deadline = deadline
            def wake() -> None:
                with self._lock:
                    if generation != self._timer_generation:
                        return
                    self._timer = None
                    self._timer_deadline = None
                self._notify_wakeup()
            self._timer = threading.Timer(delay, wake)
            self._timer.daemon = True
            self._timer.start()

    def snapshot(self) -> dict:
        with self._lock:
            return {"enabled": True, "algorithm": "CP-SAT", "active_solves": int(self._active),
                    "pending_snapshots": int(self._pending is not None), "submitted": self._submitted,
                    "discarded": self._discarded, "config": asdict(self.config),
                    "latest": asdict(self._latest) if self._latest else None}

    def close(self) -> None:
        with self._wake_lock:
            with self._lock:
                self._closed = True
                self._pending = None
                if self._timer is not None:
                    self._timer.cancel()
                self._timer = None
                self._timer_deadline = None
                self._timer_generation += 1
        self._executor.shutdown(wait=True, cancel_futures=True)
