"""预排程快照与候选的入参契约。

``_check_snapshot`` 与 ``validate_candidate`` 是预排程的两道闭式门：前者拒绝
无法安全建模的快照，后者独立重算候选、不信任求解器内部状态。这两道门此前几乎
没有断言——一旦哪条守卫被误删，错误输入会静默改变排程结果而不是报错。本套件
逐条钉住它们的拒绝理由。
"""

from __future__ import annotations

import math

import pytest

from unilabos.app.scheduler.time_planning import (
    CandidatePlan,
    PlannedNode,
    PlanningAlternative,
    PlanningCompletionTarget,
    PlanningHold,
    PlanningNode,
    PlanningSnapshot,
    TimePlanningConfig,
    solve_snapshot,
    validate_candidate,
)

CFG = TimePlanningConfig(solve_seconds=1.0)
DEVICE = "/devices/arm-1"


def _node(node_id: str, *, duration: int = 1000, resources=(DEVICE,), task: str = "wf",
          predecessors=(), fixed: bool = False, priority: float = 1.0,
          alternatives=None) -> PlanningNode:
    options = (PlanningAlternative("arm-1", duration, tuple(resources)),) if alternatives is None else alternatives
    return PlanningNode((task, node_id), options, tuple(predecessors), priority, fixed,
                        0.0 if fixed else None)


def _snapshot(nodes, holds=(), targets=(), *, revision: str = "r", time_origin: float = 0.0) -> PlanningSnapshot:
    return PlanningSnapshot(revision=revision, time_origin=time_origin, nodes=tuple(nodes),
                            holds=tuple(holds), completion_targets=tuple(targets))


def _plan(snapshot: PlanningSnapshot, entries) -> CandidatePlan:
    return CandidatePlan(revision=snapshot.revision, time_origin=snapshot.time_origin,
                         nodes=tuple(entries), status="OPTIMAL")


def _entry(node: PlanningNode, start: int, *, alternative: int = 0, end: int | None = None,
           fixed: bool | None = None) -> PlannedNode:
    option = node.alternatives[alternative]
    return PlannedNode(node.key, option.device_id, start,
                       option.duration_ms + start if end is None else end,
                       option.resources, node.fixed if fixed is None else fixed)


@pytest.mark.parametrize(
    ("reason", "snapshot"),
    [
        ("排程快照时间无效", _snapshot([_node("a")], time_origin=math.inf)),
        ("排程节点身份重复", _snapshot([_node("a"), _node("a")])),
        ("排程设备候选数量无效", _snapshot([_node("a", alternatives=())])),
        ("排程优先级必须是有限值", _snapshot([_node("a", priority=math.nan)])),
        (
            "已经开始的动作不能重新选择设备",
            _snapshot([_node("a", fixed=True, alternatives=(
                PlanningAlternative("arm-1", 1000, (DEVICE,)),
                PlanningAlternative("arm-2", 1000, ("/devices/arm-2",)),
            ))]),
        ),
        ("排程时长必须在允许范围内", _snapshot([_node("a", duration=0)])),
        ("排程时长必须在允许范围内", _snapshot([_node("a", duration=-1)])),
        ("排程时长必须在允许范围内", _snapshot([_node("a", alternatives=(
            PlanningAlternative("arm-1", True, (DEVICE,)),
        ))])),
        ("排程资源键不能为空", _snapshot([_node("a", resources=("",))])),
        ("排程资源键不能为空", _snapshot([_node("a", resources=("   ",))])),
        ("排程依赖不完整", _snapshot([_node("a", predecessors=(("wf", "missing"),))])),
        ("排程依赖不完整", _snapshot([_node("a", predecessors=(("wf", "a"),))])),
        (
            "排程依赖存在循环",
            _snapshot([
                _node("a", predecessors=(("wf", "b"),)),
                _node("b", predecessors=(("wf", "a"),)),
            ]),
        ),
        (
            "任务完成目标身份重复或类型无效",
            _snapshot([_node("a")], targets=(
                PlanningCompletionTarget("wf", (("wf", "a"),)),
                PlanningCompletionTarget("wf", (("wf", "a"),)),
            )),
        ),
        (
            "任务完成目标身份重复或类型无效",
            _snapshot([_node("a")], targets=(
                PlanningCompletionTarget("wf", (("wf", "a"),), "not_a_kind"),
            )),
        ),
        (
            "完成目标引用不属于当前任务的节点",
            _snapshot([_node("a"), _node("b", task="other")], targets=(
                PlanningCompletionTarget("wf", (("other", "b"),)),
            )),
        ),
        (
            "连续占用的节点范围不完整",
            _snapshot([_node("a")], holds=(
                PlanningHold("h1", "wf", (DEVICE,), (("wf", "missing"),)),
            )),
        ),
        (
            "排程资源键不能为空",
            _snapshot([_node("a")], holds=(PlanningHold("h1", "wf", ("",)),)),
        ),
        (
            "尚未开始的连续占用没有取得节点",
            _snapshot([_node("a")], holds=(
                PlanningHold("h1", "wf", (DEVICE,), (), False),
            )),
        ),
        (
            "连续占用身份重复，应先按实际同一持有记录合并",
            _snapshot([_node("a")], holds=(
                PlanningHold("h1", "wf", (DEVICE,)),
                PlanningHold("h1", "wf", (DEVICE,)),
            )),
        ),
        (
            "连续占用的父身份不存在或引用自身",
            _snapshot([_node("a")], holds=(
                PlanningHold("h1", "wf", (DEVICE,), (), True, False, ("h1",)),
            )),
        ),
        (
            "连续占用的父身份不存在或引用自身",
            _snapshot([_node("a")], holds=(
                PlanningHold("h1", "wf", (DEVICE,), (), True, False, ("missing",)),
            )),
        ),
        (
            "内层实际占用只能引用同任务已经持有的父区间",
            _snapshot([_node("a"), _node("b", task="other")], holds=(
                PlanningHold("parent", "other", (DEVICE,)),
                PlanningHold("child", "wf", (DEVICE,), (), True, False, ("parent",)),
            )),
        ),
        (
            "父占用没有覆盖内层占用的规范资源键",
            _snapshot([_node("a")], holds=(
                PlanningHold("parent", "wf", ("/devices/other",)),
                PlanningHold("child", "wf", (DEVICE,), (), True, False, ("parent",)),
            )),
        ),
    ],
)
def test_check_snapshot_rejects_unsafe_input(reason: str, snapshot: PlanningSnapshot) -> None:
    """无法安全建模的快照必须被拒绝，而不是静默改变排程结果。"""

    with pytest.raises(ValueError, match=reason):
        solve_snapshot(snapshot, CFG)


def test_scale_limits_are_enforced_instead_of_dropping_constraints() -> None:
    """超出规模限额时拒绝求解，不能靠丢弃资源约束换取一个可行解。"""

    snapshot = _snapshot([_node(f"n{index}") for index in range(4)])
    with pytest.raises(ValueError, match="排程快照超出规模限额"):
        solve_snapshot(snapshot, TimePlanningConfig(solve_seconds=1.0, max_nodes=3))

    holds = tuple(PlanningHold(f"h{index}", "wf", (DEVICE,)) for index in range(4))
    with pytest.raises(ValueError, match="排程快照超出规模限额"):
        solve_snapshot(_snapshot([_node("a")], holds), TimePlanningConfig(solve_seconds=1.0, max_holds=3))


def test_valid_candidate_passes_every_independent_recheck() -> None:
    """对照：合法候选不应触发任何一条校验错误。"""

    first, second = _node("a"), _node("b", predecessors=(("wf", "a"),))
    snapshot = _snapshot([first, second])
    plan = _plan(snapshot, [_entry(first, 0), _entry(second, 1000)])

    assert validate_candidate(snapshot, plan) == ()


def test_candidate_revision_and_time_origin_must_match_the_snapshot() -> None:
    """候选必须属于同一份快照，否则会把旧事实当成本轮结论。"""

    node = _node("a")
    snapshot = _snapshot([node])

    wrong_revision = CandidatePlan(revision="other", time_origin=0.0,
                                   nodes=(_entry(node, 0),), status="OPTIMAL")
    wrong_origin = CandidatePlan(revision="r", time_origin=17.0,
                                 nodes=(_entry(node, 0),), status="OPTIMAL")

    assert any("候选版本与快照不一致" in error for error in validate_candidate(snapshot, wrong_revision))
    assert any("候选版本与快照不一致" in error for error in validate_candidate(snapshot, wrong_origin))


def test_candidate_node_set_must_be_complete_and_unique() -> None:
    """候选节点集合缺项或重复时立即失败，不再继续逐项检查。"""

    first, second = _node("a"), _node("b")
    snapshot = _snapshot([first, second])

    missing = _plan(snapshot, [_entry(first, 0)])
    duplicated = _plan(snapshot, [_entry(first, 0), _entry(first, 0)])

    assert validate_candidate(snapshot, missing) == ("候选节点集合不完整或重复",)
    assert "候选节点集合不完整或重复" in validate_candidate(snapshot, duplicated)


@pytest.mark.parametrize(
    ("reason", "make_entry"),
    [
        ("动作设备、资源或时长不符合快照", lambda node: PlannedNode(
            node.key, "arm-9", 0, 1000, node.alternatives[0].resources, False)),
        ("动作设备、资源或时长不符合快照", lambda node: PlannedNode(
            node.key, "arm-1", 0, 1000, ("/devices/other",), False)),
        ("动作设备、资源或时长不符合快照", lambda node: PlannedNode(
            node.key, "arm-1", 0, 4321, node.alternatives[0].resources, False)),
        ("动作时间无效", lambda node: PlannedNode(
            node.key, "arm-1", -1000, 0, node.alternatives[0].resources, False)),
        ("动作时间无效", lambda node: PlannedNode(
            node.key, "arm-1", 500, 500, node.alternatives[0].resources, False)),
        ("动作时间无效", lambda node: PlannedNode(
            node.key, "arm-1", 10**9, 10**9 + 1000, node.alternatives[0].resources, False)),
    ],
)
def test_candidate_entry_must_match_a_snapshot_alternative(reason: str, make_entry) -> None:
    """设备、资源、时长与时间窗都独立重算，不信任求解器给出的区间。"""

    node = _node("a")
    snapshot = _snapshot([node])

    errors = validate_candidate(snapshot, _plan(snapshot, [make_entry(node)]))

    assert any(reason in error for error in errors), errors


def test_candidate_must_not_move_an_already_started_action() -> None:
    """已经开始的动作只能停在时间原点，也不能丢掉 fixed 标记。"""

    node = _node("a", fixed=True)
    snapshot = _snapshot([node])

    moved = _plan(snapshot, [_entry(node, 500)])
    unmarked = _plan(snapshot, [_entry(node, 0, fixed=False)])

    assert any("已经开始的动作被移动" in error for error in validate_candidate(snapshot, moved))
    assert any("已经开始的动作被移动" in error for error in validate_candidate(snapshot, unmarked))


def test_candidate_must_respect_declared_dependencies() -> None:
    """父动作没有结束就开始子动作属于违反依赖，必须被独立重算发现。"""

    first, second = _node("a"), _node("b", predecessors=(("wf", "a"),))
    snapshot = _snapshot([first, second])

    overlapping = _plan(snapshot, [_entry(first, 0), _entry(second, 500)])
    back_to_back = _plan(snapshot, [_entry(first, 0), _entry(second, 1000)])

    assert any("违反动作依赖" in error for error in validate_candidate(snapshot, overlapping))
    # 端点互斥：父动作结束的同一刻允许子动作开始。
    assert validate_candidate(snapshot, back_to_back) == ()


def test_candidate_must_not_overlap_conflicting_resource_intervals() -> None:
    """同资源区间重叠必须被判为冲突；错开即合法。"""

    first, second = _node("a"), _node("b")
    snapshot = _snapshot([first, second])

    overlapping = _plan(snapshot, [_entry(first, 0), _entry(second, 500)])
    serialized = _plan(snapshot, [_entry(first, 0), _entry(second, 1000)])

    assert any("冲突的资源占用区间" in error for error in validate_candidate(snapshot, overlapping))
    assert validate_candidate(snapshot, serialized) == ()


def test_candidate_parent_interval_must_contain_the_inner_occupancy() -> None:
    """父资源区间必须完整包含内层实际占用时间。"""

    member = _node("a")
    inner = _node("b")
    snapshot = _snapshot(
        [member, inner],
        holds=(
            PlanningHold("parent", "wf", (DEVICE,), (("wf", "a"),), True, True),
            PlanningHold("child", "wf", (DEVICE,), (("wf", "b"),), True, True, ("parent",)),
        ),
    )
    # 父区间由成员 a 决定为 [0, 1000)，内层 child 由成员 b 决定为 [2000, 3000)，
    # 落在父区间之外。
    plan = _plan(snapshot, [_entry(member, 0), _entry(inner, 2000)])

    assert any("父资源区间未完整包含内层实际占用时间" in error
               for error in validate_candidate(snapshot, plan))
