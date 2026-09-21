"""CP-SAT 预排程对多动作连续持有锁的约束测试。

运行时连续区间已有独立套件覆盖，本文件补的是「冻结资源计划 → planner 视图 →
PlanningHold → CP-SAT 约束」这段接缝：区间成员可以在自己的持有内连续执行，
而任何外部动作必须整体避开该持有，不得插进两次成员动作之间。
"""

from __future__ import annotations

from unilabos.app.scheduler.dispatch import RecordingDispatcher
from unilabos.app.scheduler.models import WorkflowEdge, WorkflowNode, WorkflowSpec
from unilabos.app.scheduler.service import EdgeScheduler
from unilabos.app.scheduler.time_planning import (
    PlanningAlternative,
    PlanningHold,
    PlanningNode,
    PlanningSnapshot,
    TimePlanningConfig,
    solve_snapshot,
)
from unilabos.app.scheduler.workflow_planning import build_workflow_planning_view
from unilabos.workflow.resource_lock_plan import (
    bind_station_resource_plan,
    compile_template_resource_plan,
    serialize_resource_plan,
)

CONFIG = TimePlanningConfig(solve_seconds=2.0)
SHARED = ("/devices/shared",)
OVEN = ("/devices/oven",)

HOLDER = "task-holder"
RIVAL = "task-rival"
FIRST = (HOLDER, "first")
AWAY = (HOLDER, "away")
LAST = (HOLDER, "last")
FOREIGN = (RIVAL, "foreign")

# 持有方优先级更高，会被先优化并紧贴时间原点排满，于是 first 与 last 之间留下
# 一段 5 秒空档：期间共享设备没有任何成员动作在执行。低优先级的外部动作恰好
# 能塞进这个空档，因此该空档能否被占用完全取决于连续持有约束本身。
HIGH_PRIORITY = 10.0
LOW_PRIORITY = 1.0


def _snapshot(*, with_hold: bool) -> PlanningSnapshot:
    """构造一个共享设备上带 5 秒空档的两任务快照。"""

    nodes = (
        PlanningNode(FIRST, (PlanningAlternative("shared", 1000, SHARED),), (), HIGH_PRIORITY),
        PlanningNode(AWAY, (PlanningAlternative("oven", 5000, OVEN),), (FIRST,), HIGH_PRIORITY),
        PlanningNode(LAST, (PlanningAlternative("shared", 1000, SHARED),), (AWAY,), HIGH_PRIORITY),
        PlanningNode(FOREIGN, (PlanningAlternative("shared", 1000, SHARED),), (), LOW_PRIORITY),
    )
    holds = ()
    if with_hold:
        holds = (
            PlanningHold(
                HOLDER + ":interval-1",
                HOLDER,
                SHARED,
                (FIRST, AWAY, LAST),
                already_held=False,
                release_known=True,
            ),
        )
    return PlanningSnapshot("revision", 0.0, nodes, holds)


def _spans(plan: object) -> dict[tuple[str, str], tuple[int, int]]:
    """把候选计划整理成 节点 -> (起, 止) 便于区间比较。"""

    return {item.key: (item.start_ms, item.end_ms) for item in plan.nodes}


def test_gap_inside_a_scope_is_reusable_without_a_continuous_hold() -> None:
    """对照组：没有连续持有时，外部动作会占用成员之间的空档。"""

    spans = _spans(solve_snapshot(_snapshot(with_hold=False), CONFIG))

    assert spans[FIRST] == (0, 1000)
    assert spans[LAST] == (6000, 7000)
    # 判别力前提：外部动作确实愿意且能够插进空档，否则下一个用例证明不了任何事。
    assert spans[FOREIGN] == (1000, 2000)
    assert spans[FIRST][0] <= spans[FOREIGN][0]
    assert spans[FOREIGN][1] <= spans[LAST][1]


def test_continuous_hold_keeps_a_foreign_action_out_of_the_interval() -> None:
    """连续持有必须把外部动作整体推出 [首个成员开始, 末个成员结束]。"""

    spans = _spans(solve_snapshot(_snapshot(with_hold=True), CONFIG))

    interval_start = spans[FIRST][0]
    interval_end = spans[LAST][1]
    foreign_start, foreign_end = spans[FOREIGN]

    assert foreign_start >= interval_end or foreign_end <= interval_start
    assert not (foreign_start >= interval_start and foreign_end <= interval_end)


def test_continuous_hold_does_not_delay_its_own_members() -> None:
    """成员动作不与自己的持有互斥，持有不得推迟本任务的排程。"""

    without_hold = _spans(solve_snapshot(_snapshot(with_hold=False), CONFIG))
    with_hold = _spans(solve_snapshot(_snapshot(with_hold=True), CONFIG))

    for key in (FIRST, AWAY, LAST):
        assert with_hold[key] == without_hold[key]


def test_frozen_resource_plan_reaches_the_planner_continuous_intervals() -> None:
    """冻结资源计划的区间必须出现在 planner 视图，否则排程看不到连续持有。"""

    node_ids = ["n1", "n2", "n3"]
    template = compile_template_resource_plan(
        {
            "workflow_uuid": "workflow-planner-hold",
            "nodes": [
                {"uuid": node_id, "resource_defaults": ["robot"]}
                for node_id in node_ids
            ],
            "edges": [
                {"source_node_uuid": source, "target_node_uuid": target}
                for source, target in zip(node_ids, node_ids[1:])
            ],
        }
    )
    plan = serialize_resource_plan(
        bind_station_resource_plan(
            template,
            {"robot": {"canonical_key": "/devices/shared", "kind": "device"}},
        )
    )

    nodes = []
    for node_id in node_ids:
        node = WorkflowNode(
            id=node_id,
            device_id="shared",
            action_name="run",
            action_type="goal",
            param={},
        )
        node.resource_plan_id = str(plan["plan_id"])
        node.resource_interval_ids = [
            str(interval["interval_id"])
            for interval in plan["intervals"]
            if node_id in interval["node_uuids"]
        ]
        node.resource_acquire_set_id = next(
            (
                str(acquire_set["acquire_set_id"])
                for acquire_set in plan["acquire_sets"]
                if acquire_set["node_uuid"] == node_id
            ),
            "",
        )
        nodes.append(node)

    spec = WorkflowSpec(
        workflow_id="workflow-planner-hold",
        nodes=nodes,
        edges=[
            WorkflowEdge(
                uuid=f"{source}->{target}",
                source_node_id=source,
                target_node_id=target,
            )
            for source, target in zip(node_ids, node_ids[1:])
        ],
        priority="normal",
        resource_plan=plan,
    )

    scheduler = EdgeScheduler(dispatcher=RecordingDispatcher())
    assert scheduler.submit_workflow(spec)["state"] == "running"

    view = build_workflow_planning_view(scheduler._workflows["workflow-planner-hold"])

    assert len(view.continuous_intervals) == 1
    interval = view.continuous_intervals[0]
    assert interval.resource_keys == ("/devices/shared",)
    assert interval.member_node_ids == tuple(node_ids)
    assert interval.release_known is True
