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
from dataclasses import asdict, dataclass, replace
from typing import Callable, Literal

from unilabos.app.scheduler.resource_lock import conflicting_resource_lock_keys


NodeKey = tuple[str, str]
CandidatePlanStatus = Literal[
    "OPTIMAL", "FEASIBLE", "UNKNOWN", "ERROR", "INFEASIBLE", "MODEL_INVALID",
]
CompletionTargetKind = Literal["task_completion", "information", "partial_scope"]


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
    kind: CompletionTargetKind = "task_completion"
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
    # 参数尚未由真实上游回执完全确认时，只能作为展示用预测，不能下发。
    parameters_final: bool = True


@dataclass(frozen=True)
class CandidatePlan:
    revision: str
    time_origin: float
    status: CandidatePlanStatus
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
    retry_initial_seconds: float = 1.0
    retry_max_seconds: float = 30.0

    def __post_init__(self) -> None:
        for value in (
            self.solve_seconds,
            self.aging_seconds,
            self.refresh_seconds,
            self.retry_initial_seconds,
            self.retry_max_seconds,
        ):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("排程时间预算必须是正有限秒数")
        if self.retry_max_seconds < self.retry_initial_seconds:
            raise ValueError("排程重试上限不能小于初始退避时间")
        for value in (self.max_nodes, self.max_alternatives, self.max_holds):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError("排程规模限额必须是正整数")


def snapshot_revision(facts: object) -> str:
    """对调用方选定的业务事实取指纹，禁止以对象地址标识快照。"""
    return hashlib.sha256(json.dumps(facts, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _conflicts(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    return bool(set(left) & set(right) or conflicting_resource_lock_keys(set(left), set(right)))


def _both_pinned_facts(snapshot: PlanningSnapshot, left: tuple, right: tuple) -> bool:
    """两个区间是否都是"已经发生且无法重排"的事实。

    已经开始的动作与已经取得的持续占用，开始时间都被钉在时间原点，规划器不能
    移动它们。给两个这样的区间加互斥析取只能得到"不可满足"——它表达的不是任何
    可选择的顺序，而是要求事实去证伪事实。真实运行期若出现两份层级冲突的活动
    占用，那是执行内核的异常，不能靠让整盘计划不可行来"报告"：那会连完全无关
    的任务一起冻结在不派发状态。参数：``left``/``right`` 是区间元组，第二项为
    ``None`` 表示持续占用，否则是动作身份。返回：两者是否都为既成事实。
    """

    fixed_keys = {node.key for node in snapshot.nodes if node.fixed}
    held_ids = {hold.hold_id for hold in snapshot.holds if hold.already_held}

    def pinned(span: tuple) -> bool:
        return span[0] in held_ids if span[1] is None else span[1] in fixed_keys

    return pinned(left) and pinned(right)


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
            # 资源键只按集合相交和层级关系判断互斥；空串会让两个毫不相关的动作
            # 被强行串行，而且无法从计划上看出原因。宁可拒绝快照，也不让一个无
            # 意义的键静默改变排程结果。
            if any(not isinstance(resource, str) or not resource.strip() for resource in option.resources):
                raise ValueError("排程资源键不能为空")
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
        if any(not isinstance(resource, str) or not resource.strip() for resource in hold.resources):
            raise ValueError("排程资源键不能为空")
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
            # 与建模一致：包含关系之外，两个既成事实之间不做互斥重算，否则候选会
            # 因为运行期已经存在、本就无法重排的占用被判为非法。
            if _both_pinned_facts(snapshot, left, right):
                continue
            if max(left[4], right[4]) < min(left[5], right[5]):
                errors.append("候选包含冲突的资源占用区间")
    return tuple(dict.fromkeys(errors))


def _greedy_feasible_plan(
    snapshot: PlanningSnapshot,
    horizon: int,
) -> tuple[PlannedNode, ...] | None:
    """Build a conservative incumbent when CP-SAT found no one.

    The CP-SAT model uses a guarded disjunction for every pair of alternative
    intervals. With dozens of nodes and eight dynamic device alternatives it
    is possible for a short budget to return ``UNKNOWN`` before finding an
    incumbent, even though a feasible schedule exists. This fallback uses
    deterministic list scheduling: independent resources still run in
    parallel, while every conflicting interval is delayed and the result is
    accepted only after :func:`validate_candidate` performs the full checks.
    """

    by_key = {node.key: node for node in snapshot.nodes}
    remaining = set(by_key)
    scheduled: dict[NodeKey, PlannedNode] = {}
    result: list[PlannedNode] = []
    held: list[tuple[tuple[str, ...], int, int, set[NodeKey]]] = [
        (hold.resources, 0, horizon, set(hold.members))
        for hold in snapshot.holds
        if hold.already_held and not hold.release_known
    ]

    def earliest_start(key: NodeKey, option: PlanningAlternative) -> int:
        start = max([0, *(scheduled[parent].end_ms for parent in by_key[key].predecessors)])
        while True:
            end = start + option.duration_ms
            conflicts = [
                other_end
                for resources, other_start, other_end, members in held
                if key not in members and _conflicts(option.resources, resources)
                and start < other_end and other_start < end
            ]
            conflicts.extend(
                other.end_ms
                for other in result
                if _conflicts(option.resources, other.resources)
                and start < other.end_ms and other.start_ms < end
            )
            if not conflicts:
                return start
            start = max(conflicts)

    # Topological order is deterministic and honours workflow dependencies.
    while remaining:
        ready = [key for key in remaining
                 if all(parent in scheduled for parent in by_key[key].predecessors)]
        if not ready:
            return None
        key = min(ready, key=lambda item: (-by_key[item].priority, item))
        node = by_key[key]
        if node.fixed:
            option = node.alternatives[0]
            start = 0
        else:
            choices = [
                (earliest_start(key, option), option)
                for option in node.alternatives
            ]
            start, option = min(
                choices,
                key=lambda item: (
                    item[0] + item[1].duration_ms,
                    item[0],
                    item[1].device_id,
                    item[1].resources,
                ),
            )
        entry = PlannedNode(key, option.device_id, start, start + option.duration_ms,
                            option.resources, node.fixed, node.parameters_final)
        scheduled[key] = entry
        result.append(entry)
        remaining.remove(key)

    candidate = CandidatePlan(snapshot.revision, snapshot.time_origin, "FEASIBLE",
                              tuple(result), partial_tasks=snapshot.partial_tasks)
    if validate_candidate(snapshot, candidate):
        return None
    return tuple(result)


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
            # 包含关系之外，两个既成事实之间不再互相排斥：它们的开始时间都被钉在
            # 时间原点、无法重排，析取只能得到不可满足。
            if _both_pinned_facts(snapshot, left, right):
                continue
            before = model.new_bool_var(f"order{index}_{j}")
            guards = [item for item in (left[6], right[6]) if item is not None]
            model.add(left[5] <= right[4]).only_enforce_if(guards + [before])
            model.add(right[5] <= left[4]).only_enforce_if(guards + [before.Not()])
    explicit_targets = {target.task_id: target for target in snapshot.completion_targets}
    task_completion = {}
    reported_targets = []
    # 显式目标可能来自当前范围之外的任务，或者其可见节点已经全部被过滤。
    # 不能只遍历 task_ends，否则 solve_snapshot 会把 information/partial_scope
    # 目标静默丢掉。
    target_tasks = sorted(set(task_ends) | set(explicit_targets))
    for ordinal, task in enumerate(target_tasks):
        target = explicit_targets.get(task)
        if target is None:
            target = PlanningCompletionTarget(task, tuple(node.key for node in snapshot.nodes if node.key[0] == task))
        reported_targets.append(target)
        values = [ends[key] for key in target.node_keys if key in ends]
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
        # 时间原点前进后，旧计划里已经流逝的开始时间会落到 starts 的定义域之外。
        # 未钳位时 ``starts == old_start`` 这一支恒不可行，changed 被强制为真，
        # 稳定性目标对所有"预定时间已经过去"的节点静默失效，计划因此更容易抖动。
        # 把目标钳进定义域后，稳定性继续按"尽量不改动"起作用：已经过期的预定
        # 时间等价于"能多早就多早"。
        target_start = max(0, min(horizon, old_start))
        shift = model.new_int_var(0, horizon, f"shift{ordinal}")
        delta = model.new_int_var(-horizon, horizon, f"delta{ordinal}")
        model.add(delta == starts[node.key] - target_start)
        # 独立差值变量避免所用 OR-Tools 版本对带常量表达式取反时改变偏移。
        model.add_abs_equality(shift, delta)
        changed = model.new_bool_var(f"start_changed{ordinal}")
        model.add(starts[node.key] == target_start).only_enforce_if(changed.Not())
        model.add(starts[node.key] != target_start).only_enforce_if(changed)
        changes.append(changed)
        shifts.append(shift)
        same_device = [choice for alternative, choice in zip(node.alternatives, choices[node.key])
                       if alternative.device_id == old.device_id]
        if same_device:
            changes.append(1 - sum(same_device))
        # 提示可以不满足新约束，CP-SAT 会重新验证；不会形成执行许可。
        model.add_hint(starts[node.key], target_start)
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

    def plan_from_solver(solver: object) -> tuple[PlannedNode, ...]:
        """Copy one solver assignment without making it an execution permit.

        A first-feasible CP-SAT pass below is deliberately separate from the
        lexicographic optimisation passes.  Large SZLab plans can spend the
        whole 500 ms budget proving the first objective before CP-SAT has
        returned any incumbent.  In that case the UI used to receive an empty
        plan even though the model was feasible.  Keeping the extraction in one
        helper ensures both passes produce the same validated representation.
        """

        return tuple(
            PlannedNode(
                node.key,
                option.device_id,
                solver.value(starts[node.key]),
                solver.value(ends[node.key]),
                option.resources,
                node.fixed,
                node.parameters_final,
            )
            for node in snapshot.nodes
            for option, choice in zip(node.alternatives, choices[node.key])
            if solver.boolean_value(choice)
        )

    # Always try to obtain a feasible incumbent before spending the bounded
    # budget on lexicographic optimisation.  ``stop_after_first_solution`` is
    # much faster for disjunctive models with many device alternatives (the
    # real two-task SZLab plan has 50 nodes); without it a 0.5 s budget often
    # returned UNKNOWN and no nodes at all.  The pass is capped so normal
    # plans still retain most of their budget for optimisation.
    feasibility_remaining = config.solve_seconds - (time.monotonic() - started)
    feasibility_budget = min(0.2, feasibility_remaining * 0.4)
    if feasibility_budget > 0.001 and snapshot.nodes:
        feasibility_solver = cp_model.CpSolver()
        feasibility_solver.parameters.max_time_in_seconds = feasibility_budget
        feasibility_solver.parameters.num_search_workers = 1
        feasibility_solver.parameters.random_seed = 0
        feasibility_solver.parameters.stop_after_first_solution = True
        feasibility_status = feasibility_solver.solve(model)
        if feasibility_status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            best = plan_from_solver(feasibility_solver)
            status_name = "FEASIBLE"

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
        # CpSolver.value()/boolean_value() are undefined when UNKNOWN has no
        # incumbent. Keep the feasibility-pass candidate and let the
        # deterministic fallback handle the genuinely empty case.
        best = plan_from_solver(solver)
        objective, bound = solver.objective_value, solver.best_objective_bound
        objective_stage = stage_name
        if status != cp_model.OPTIMAL:
            break
        completed_stages += 1
        if tier is not None:
            proven.append(tier)
        model.add(objective_expr == round(objective))
    if not best and status_name in {"UNKNOWN", "ERROR"} and snapshot.nodes:
        # A bounded optimisation run may time out before CP-SAT publishes an
        # incumbent. Keep the UI useful with a conservative list-scheduled
        # candidate; validate it below before exposing it to the adapter.
        fallback = _greedy_feasible_plan(snapshot, horizon)
        if fallback is not None:
            best = fallback
            status_name = "FEASIBLE"
            objective_stage = "fallback-feasible"
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
        self._retry_timer: threading.Timer | None = None
        self._retry_deadline: float | None = None
        self._retry_generation = 0
        self._retry_revision = ""
        self._retry_snapshot: PlanningSnapshot | None = None
        self._retry_attempt = 0
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
                self._cancel_retry_locked()
                # 旧候选仅作为软提示和稳定性参照；版本变化仍须完整重算与验证。
                self._pending = replace(snapshot, previous_plan=self._latest) if snapshot.previous_plan is None else snapshot
                self._submitted += 1
            elif (
                self._latest is not None
                and self._latest.revision == snapshot.revision
                and self._retryable_status(self._latest.status)
                and self._pending is None
                and self._retry_timer is None
                and not self._active
                and (
                    self._retry_revision != snapshot.revision
                    or self._retry_snapshot is None
                )
            ):
                # 专用重试计时器通常已经存在；这里兜底处理计时器被取消或
                # 系统时钟异常的情况，避免同一 revision 永久停在错误状态。
                self._arm_retry_locked(snapshot)
            if not self._active and self._pending is not None:
                self._active = True
                try:
                    self._executor.submit(self._run)
                except BaseException:
                    # 提交本身失败（线程池已关闭或拒绝）时必须复位单飞标志，
                    # 否则 _run 永远不会清除它，此后所有 request() 都不再提交
                    # 求解，计划永久停在上一轮候选上。
                    self._active = False
                    raise
            current = self._latest
            return current if current and current.revision == snapshot.revision and current.validated else None

    def invalidate(self) -> None:
        """同步撤销当前候选；暂停等控制变更不能继续消费旧快照。

        求解线程无法被可靠地中断，因此只撤销它提交前后的可见状态。正在运行的
        旧求解完成后会发现 ``_requested_revision`` 已被清空并丢弃结果；下一次
        ``request`` 会用新的业务事实重新提交快照。
        """
        with self._lock:
            if self._closed:
                return
            self._requested_revision = ""
            self._pending = None
            self._latest = None
            self._cancel_retry_locked()

    @staticmethod
    def _retryable_status(status: str) -> bool:
        """仅对瞬态求解失败重试，确定性的不可行模型仍交给上层处理。"""

        return status in {"ERROR", "UNKNOWN"}

    def _cancel_retry_locked(self) -> None:
        """取消旧版本的重试计时器；调用方必须持有 ``_lock``。"""

        if self._retry_timer is not None:
            self._retry_timer.cancel()
        self._retry_timer = None
        self._retry_deadline = None
        self._retry_generation += 1
        self._retry_revision = ""
        self._retry_snapshot = None
        self._retry_attempt = 0

    def _arm_retry_locked(self, snapshot: PlanningSnapshot) -> None:
        """为同一 revision 安排有上限的指数退避再次求解。"""

        if self._retry_timer is not None:
            self._retry_timer.cancel()
        self._retry_timer = None
        self._retry_deadline = None
        self._retry_generation += 1
        previous_attempt = self._retry_attempt if self._retry_revision == snapshot.revision else 0
        self._retry_revision = snapshot.revision
        self._retry_snapshot = snapshot
        self._retry_attempt = previous_attempt + 1
        delay = min(
            self.config.retry_max_seconds,
            self.config.retry_initial_seconds * (2 ** min(self._retry_attempt - 1, 20)),
        )
        self._retry_deadline = time.monotonic() + delay
        generation = self._retry_generation

        def retry() -> None:
            with self._lock:
                if (
                    self._closed
                    or generation != self._retry_generation
                    or self._retry_revision != snapshot.revision
                    or self._retry_snapshot is None
                    or self._requested_revision != snapshot.revision
                ):
                    return
                self._retry_timer = None
                self._retry_deadline = None
                # 计时器只负责把快照重新放入合并队列；若旧求解仍在收尾，
                # 该线程会在下一轮消费 pending，不会并发运行第二个 CP-SAT。
                self._pending = self._retry_snapshot
                self._retry_snapshot = None
                if not self._active:
                    self._active = True
                    # 与 request() 保持相同的锁顺序：提交发生在状态锁内，
                    # close() 不能在此处先关闭 executor 再让计时器迟到提交。
                    try:
                        self._executor.submit(self._run)
                    except BaseException:
                        # 同 request()：提交失败必须复位单飞标志，否则重试计时器
                        # 会把求解永久卡死在"已在运行"的假象里。
                        self._active = False
                        raise
                self._submitted += 1
            self._notify_wakeup()

        self._retry_timer = threading.Timer(delay, retry)
        self._retry_timer.daemon = True
        self._retry_timer.start()

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
                    if self._retryable_status(result.status):
                        self._arm_retry_locked(snapshot)
                    else:
                        self._cancel_retry_locked()
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
                    "retry": {"revision": self._retry_revision,
                              "attempt": self._retry_attempt,
                              "deadline": self._retry_deadline},
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
                self._cancel_retry_locked()
        self._executor.shutdown(wait=True, cancel_futures=True)
