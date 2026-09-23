"""未知释放占用与已在跑事实的共存契约。

已经在跑的动作是既成事实：它的开始时间被钉在时间原点且不可重排。运行期报告
的忙碌键、以及超时在途作业退化成的未知释放占用，都不能反过来要求模型证明这
份事实"没有和自己重叠"——否则整盘退化成 INFEASIBLE，`order()` 拿不到合格候选，
连完全无关的任务都停在不派发状态。本套件把这条边界钉住，同时确认待执行候选
仍然被未知占用挡在本轮模型之外。
"""

from __future__ import annotations

from types import SimpleNamespace

from unilabos.app.scheduler.dag_state import WorkflowRun
from unilabos.app.scheduler.dispatch import RecordingDispatcher
from unilabos.app.scheduler.models import DispatchedJob, WorkflowNode, WorkflowSpec
from unilabos.app.scheduler.service import EdgeScheduler
from unilabos.app.scheduler.time_planning import (
    PlanningAlternative,
    PlanningHold,
    PlanningNode,
    PlanningSnapshot,
    TimePlanningConfig,
    solve_snapshot,
)
from unilabos.app.scheduler.time_scheduling import TimeSchedulingAdapter
from unilabos.workflow.resource_lock_key import (
    device_lock_key,
    material_lock_key,
    site_lock_key,
)

OWNER_MATERIAL = "11111111-1111-1111-1111-111111111111"
SITE = "22222222-2222-2222-2222-222222222222"
MATERIAL_KEY = str(material_lock_key(OWNER_MATERIAL))
SITE_KEY = str(site_lock_key(OWNER_MATERIAL, SITE))
ARM_KEY = str(device_lock_key("arm-1"))


def _planner() -> SimpleNamespace:
    return SimpleNamespace(
        config=SimpleNamespace(
            max_nodes=256,
            max_alternatives=8,
            aging_seconds=30.0,
            refresh_seconds=1.0,
        )
    )


def _register(scheduler: EdgeScheduler, workflow_id: str, node_id: str, device_id: str) -> None:
    node = WorkflowNode(
        id=node_id,
        device_id=device_id,
        action_name="run",
        action_type="goal",
        param={},
    )
    spec = WorkflowSpec(workflow_id=workflow_id, nodes=[node], edges=[])
    scheduler._workflows[workflow_id] = WorkflowRun(spec)


def _inflight(
    scheduler: EdgeScheduler,
    *,
    job_id: str,
    workflow_id: str,
    node_id: str,
    device_id: str,
    keys: set[str],
    dispatched_at: float,
    estimated_s: float,
) -> None:
    scheduler._inflight[job_id] = DispatchedJob(
        job_id=job_id,
        workflow_id=workflow_id,
        node_id=node_id,
        device_action_key=f"/devices/{device_id}/run",
        device_id=device_id,
        action_name="run",
        dispatched_at=dispatched_at,
        estimated_s=estimated_s,
        resource_lock_keys=set(keys),
        active_resource_lock_keys=set(keys),
    )


def _solve(scheduler: EdgeScheduler, now: float):
    adapter = TimeSchedulingAdapter(_planner())
    snapshot, _targets, _manual = adapter._build(scheduler, [], now)
    return snapshot, solve_snapshot(snapshot, TimePlanningConfig(solve_seconds=2.0))


def test_external_whole_material_key_does_not_duplicate_a_running_site_occupancy() -> None:
    """运行期报告的整物料忙键与在跑动作持有的子库位是同一份占用。

    一个库位只能放一个物料，整物料键与其任一子库位键因此互为互斥。字面前缀
    比较看不出这层关系，旧实现会把已经由在跑事实表示的占用再虚构成一条未知
    释放的外部占用，于是不可重排的在跑动作无处安放，整盘 INFEASIBLE。
    """

    scheduler = EdgeScheduler(dispatcher=RecordingDispatcher())
    try:
        _register(scheduler, "wf-running", "pick", "arm-1")
        _register(scheduler, "wf-peer", "weigh", "balance-1")
        _inflight(scheduler, job_id="job-running", workflow_id="wf-running", node_id="pick",
                  device_id="arm-1", keys={SITE_KEY, ARM_KEY}, dispatched_at=0.0, estimated_s=5.0)
        scheduler._external_busy_keys = {MATERIAL_KEY}

        snapshot, plan = _solve(scheduler, 1.0)
    finally:
        scheduler.close()

    external = [hold for hold in snapshot.holds if hold.hold_id == "external"]
    assert all(MATERIAL_KEY not in hold.resources for hold in external), (
        "整物料忙键已由在跑动作的子库位表示，不能再虚构成未知释放的外部占用"
    )
    assert plan.status in {"OPTIMAL", "FEASIBLE"}
    assert plan.validated
    assert ("wf-running", "pick") in {node.key for node in plan.nodes}


def test_unrelated_task_still_gets_a_plan_while_a_resource_is_held_unknown() -> None:
    """未知占用只该挡住竞争该资源的候选，不该冻结无关任务的派发。"""

    scheduler = EdgeScheduler(dispatcher=RecordingDispatcher())
    try:
        _register(scheduler, "wf-running", "pick", "arm-1")
        _register(scheduler, "wf-peer", "weigh", "balance-1")
        _inflight(scheduler, job_id="job-running", workflow_id="wf-running", node_id="pick",
                  device_id="arm-1", keys={SITE_KEY, ARM_KEY}, dispatched_at=0.0, estimated_s=5.0)
        scheduler._external_busy_keys = {MATERIAL_KEY}

        _snapshot, plan = _solve(scheduler, 1.0)
    finally:
        scheduler.close()

    assert plan.validated
    assert ("wf-peer", "weigh") in {node.key for node in plan.nodes}


def test_overdue_unknown_hold_keeps_a_conflicting_running_action_schedulable() -> None:
    """超时在途作业退化成未知释放占用后，同资源的另一条在跑事实仍可排程。

    事实不能被事实证伪：两条在跑动作都已经发生，要求模型证明它们不重叠只会
    让整盘不可行。占用区间本身不变，仍延伸到范围末尾。
    """

    scheduler = EdgeScheduler(dispatcher=RecordingDispatcher())
    try:
        _register(scheduler, "wf-overdue", "transfer", "arm-1")
        _register(scheduler, "wf-running", "pick", "arm-1")
        _inflight(scheduler, job_id="job-overdue", workflow_id="wf-overdue", node_id="transfer",
                  device_id="arm-1", keys={ARM_KEY}, dispatched_at=0.0, estimated_s=1.0)
        _inflight(scheduler, job_id="job-running", workflow_id="wf-running", node_id="pick",
                  device_id="arm-1", keys={ARM_KEY}, dispatched_at=90.0, estimated_s=30.0)

        snapshot, plan = _solve(scheduler, 100.0)
    finally:
        scheduler.close()

    unknown = [hold for hold in snapshot.holds if hold.hold_id.startswith("unknown:job-overdue")]
    assert unknown, "超时在途作业应退化成未知释放占用"
    assert all(not hold.release_known for hold in unknown)
    assert any(("wf-running", "pick") in set(hold.members) for hold in unknown), (
        "同资源的在跑事实应并入该占用，而不是与它互相排斥"
    )
    assert plan.status in {"OPTIMAL", "FEASIBLE"}
    assert plan.validated
    assert ("wf-running", "pick") in {node.key for node in plan.nodes}


def test_pending_competitor_is_still_excluded_from_the_round() -> None:
    """待执行候选仍被未知占用挡在本轮模型之外，占用的排除力没有被放宽。"""

    scheduler = EdgeScheduler(dispatcher=RecordingDispatcher())
    try:
        _register(scheduler, "wf-overdue", "transfer", "arm-1")
        _register(scheduler, "wf-waiting", "pick", "arm-1")
        _inflight(scheduler, job_id="job-overdue", workflow_id="wf-overdue", node_id="transfer",
                  device_id="arm-1", keys={ARM_KEY}, dispatched_at=0.0, estimated_s=1.0)

        snapshot, plan = _solve(scheduler, 100.0)
    finally:
        scheduler.close()

    assert ("wf-waiting", "pick") not in {node.key for node in snapshot.nodes}
    assert any(entry[:2] == ("wf-waiting", "pick")
               and entry[2] == "resource_or_dependency_completion_unknown"
               for entry in snapshot.blocked)
    assert "wf-waiting" in snapshot.partial_tasks
    assert plan.validated


def test_over_constrained_snapshot_is_reported_infeasible_and_never_validated() -> None:
    """模型层的不可行语义保持不变：诚实报 INFEASIBLE，绝不冒充成空计划。

    适配层保证事实与事实不会被送进同一条互斥约束，但真正无解的快照仍必须走
    诚实路径——不 validated、不落 greedy 回退（回退只对 UNKNOWN/ERROR 生效），
    这样派发侧的 `request()` 会拒绝返回，而不是把空计划当成"没有可排的事"。
    """

    snapshot = PlanningSnapshot(
        revision="over-constrained",
        time_origin=0.0,
        nodes=(
            PlanningNode(
                ("wf", "running"),
                (PlanningAlternative("arm-1", 5000, (SITE_KEY, ARM_KEY)),),
                (),
                1.0,
                True,
                0.0,
            ),
        ),
        # 与在跑事实层级互斥、且没有释放边界的占用；适配层不会构造这种组合，
        # 这里直接喂给模型以固定不可行分支的行为。
        holds=(PlanningHold("external", "", (MATERIAL_KEY,)),),
    )

    plan = solve_snapshot(snapshot, TimePlanningConfig(solve_seconds=2.0))

    assert plan.status == "INFEASIBLE"
    assert not plan.validated
    assert plan.nodes == ()
    assert plan.objective_stage == ""
