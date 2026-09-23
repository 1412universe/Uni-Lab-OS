"""CP-SAT 模型对资源层级、优先级分层与规模边界的约束。

物料与库位的互斥语义（一个库位只能放一个物料，整物料与其任一子库位互斥，同一
物料下不同库位可并行）此前只在运行期锁引擎里被验证过，没有任何测试证明它真的
进了 CP-SAT 模型。本套件补齐这一层，并钉住优先级分层、既成事实的钉定，以及
规划范围（horizon）对完全串行化的充分性。
"""

from __future__ import annotations

from unilabos.app.scheduler.time_planning import (
    PlanningAlternative,
    PlanningNode,
    PlanningSnapshot,
    TimePlanningConfig,
    solve_snapshot,
)
from unilabos.workflow.resource_lock_key import material_lock_key, site_lock_key

CFG = TimePlanningConfig(solve_seconds=3.0)
OWNER = "11111111-1111-1111-1111-111111111111"
SITE_A = "22222222-2222-2222-2222-222222222222"
SITE_B = "33333333-3333-3333-3333-333333333333"
MATERIAL_KEY = str(material_lock_key(OWNER))
SITE_A_KEY = str(site_lock_key(OWNER, SITE_A))
SITE_B_KEY = str(site_lock_key(OWNER, SITE_B))


def _node(task: str, node_id: str, device: str, resources, *, duration: int = 1000,
          priority: float = 1.0, fixed: bool = False) -> PlanningNode:
    return PlanningNode((task, node_id), (PlanningAlternative(device, duration, tuple(resources)),),
                        (), priority, fixed, 0.0 if fixed else None)


def _spans(nodes) -> dict[str, tuple[int, int]]:
    return {node.key[1]: (node.start_ms, node.end_ms) for node in nodes}


def _solve(nodes, config: TimePlanningConfig = CFG):
    plan = solve_snapshot(PlanningSnapshot(revision="r", time_origin=0.0, nodes=tuple(nodes)), config)
    assert plan.validated, f"{plan.status} {plan.error}"
    return plan


def _disjoint(first: tuple[int, int], second: tuple[int, int]) -> bool:
    return first[1] <= second[0] or second[1] <= first[0]


def test_one_site_holds_one_material_so_two_actions_on_it_serialise() -> None:
    """同一个库位上的两个动作必须错开，即使它们用的是不同设备。"""

    spans = _spans(_solve([
        _node("wf-a", "load", "arm-1", (SITE_A_KEY,)),
        _node("wf-b", "pick", "arm-2", (SITE_A_KEY,)),
    ]).nodes)

    assert _disjoint(spans["load"], spans["pick"]), spans


def test_different_sites_under_one_material_run_in_parallel() -> None:
    """同一物料下的不同库位互不排斥，可以同时作业。"""

    spans = _spans(_solve([
        _node("wf-a", "load", "arm-1", (SITE_A_KEY,)),
        _node("wf-b", "pick", "arm-2", (SITE_B_KEY,)),
    ]).nodes)

    assert spans["load"][0] == 0
    assert spans["pick"][0] == 0


def test_whole_material_excludes_any_of_its_sites() -> None:
    """整物料占用与其任一子库位互斥：整架转运期间不能对某个孔位作业。"""

    spans = _spans(_solve([
        _node("wf-a", "transfer", "arm-1", (MATERIAL_KEY,)),
        _node("wf-b", "pipette", "arm-2", (SITE_A_KEY,)),
    ]).nodes)

    assert _disjoint(spans["transfer"], spans["pipette"]), spans


def test_higher_priority_task_is_completed_before_the_lower_tier() -> None:
    """优先级分层按层依次最小化完工时间：高优先级任务先做完。"""

    spans = _spans(_solve([
        _node("wf-low", "low", "arm-1", ("/devices/arm-1",), priority=1.0),
        _node("wf-high", "high", "arm-1", ("/devices/arm-1",), priority=10.0),
    ]).nodes)

    assert spans["high"] == (0, 1000), spans
    assert spans["low"][0] >= 1000, spans


def test_already_started_action_is_pinned_at_the_time_origin() -> None:
    """已经在跑的动作钉在时间原点，其余竞争同资源的动作排在它之后。

    已下发但尚未产生结果的动作以剩余时长进入模型，同样是不可重排的事实。
    """

    plan = _solve([
        _node("wf-a", "running", "arm-1", ("/devices/arm-1",), duration=4000, fixed=True),
        _node("wf-b", "waiting", "arm-1", ("/devices/arm-1",)),
    ])
    spans = _spans(plan.nodes)

    assert spans["running"] == (0, 4000)
    assert spans["waiting"][0] >= 4000, spans
    assert next(node for node in plan.nodes if node.key[1] == "running").fixed is True


def test_full_serialisation_fits_inside_the_planning_horizon() -> None:
    """同资源上的全部动作完全串行时仍落在规划范围内。

    horizon = 各动作最长候选时长之和 + 1000ms，因此最坏情况（全部串行）天然装得
    下；这条测试把这个充分性钉住，避免有人把 horizon 改成"最长动作 × 常数"之类
    不再覆盖串行化的公式。
    """

    count, duration = 8, 1500
    plan = _solve([
        _node("wf", f"n{index}", "arm-1", ("/devices/arm-1",), duration=duration)
        for index in range(count)
    ])
    spans = sorted(_spans(plan.nodes).values())

    assert len(spans) == count
    for earlier, later in zip(spans, spans[1:]):
        assert _disjoint(earlier, later), spans
    horizon = count * duration + 1000
    assert spans[-1][1] <= horizon
    assert spans[-1][1] == count * duration


def test_larger_model_with_many_alternatives_stays_validated_in_budget() -> None:
    """规模上去以后仍必须给出通过校验的计划，而不是一张空甘特。"""

    nodes = []
    for index in range(20):
        alternatives = tuple(
            PlanningAlternative(f"arm-{slot}", 1000 + index, (f"/devices/arm-{slot}",))
            for slot in range(4)
        )
        nodes.append(PlanningNode((f"wf-{index % 4}", f"n{index}"), alternatives))
    plan = solve_snapshot(
        PlanningSnapshot(revision="scale", time_origin=0.0, nodes=tuple(nodes)),
        TimePlanningConfig(solve_seconds=3.0, max_alternatives=8),
    )

    assert plan.status in {"OPTIMAL", "FEASIBLE"}
    assert plan.validated, plan.error
    assert len(plan.nodes) == 20
