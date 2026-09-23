"""重解稳定性与候选投影契约。

预排程每隔若干秒就按新快照重解一次。若稳定性目标失效，同一批动作会在时间轴上
无故左右横跳；而候选投影一旦把未通过校验的计划当成可用计划递出去，消费端就会
展示调度器自己拒绝执行的安排。本套件同时钉住这两件事。
"""

from __future__ import annotations

import time

from unilabos.app.scheduler.time_planning import (
    CandidatePlan,
    PlannedNode,
    PlanningAlternative,
    PlanningHold,
    PlanningNode,
    PlanningSnapshot,
    RollingTimePlanner,
    TimePlanningConfig,
    solve_snapshot,
)

CFG = TimePlanningConfig(solve_seconds=2.0)
D1 = "/devices/arm-1"
D2 = "/devices/oven-1"


def _shared(node_id: str, duration: int = 1000) -> PlanningNode:
    return PlanningNode(("wf", node_id), (PlanningAlternative("arm-1", duration, (D1,)),))


def _long() -> PlanningNode:
    """独占另一台设备的长动作；它把 makespan 钉住，让其余动作的位置由稳定性决定。"""

    return PlanningNode(("wf", "long"), (PlanningAlternative("oven-1", 10000, (D2,)),))


def _entry(node: PlanningNode, start: int) -> PlannedNode:
    option = node.alternatives[0]
    return PlannedNode(node.key, option.device_id, start, start + option.duration_ms,
                       option.resources, False)


def _previous(nodes, time_origin: float = 0.0) -> CandidatePlan:
    return CandidatePlan(revision="prev", time_origin=time_origin, nodes=tuple(nodes),
                         status="OPTIMAL", validated=True)


def test_unchanged_input_reproduces_the_previous_starts() -> None:
    """输入没变时重解必须落回同一组开始时间，而不是另一个等价解。"""

    first, second, long_action = _shared("a"), _shared("b"), _long()
    previous = _previous([_entry(first, 3000), _entry(second, 5000), _entry(long_action, 0)])
    snapshot = PlanningSnapshot(
        revision="same", time_origin=0.0,
        nodes=(first, second, long_action), previous_plan=previous,
    )

    plan = solve_snapshot(snapshot, CFG)
    starts = {node.key[1]: node.start_ms for node in plan.nodes}

    assert plan.validated
    assert plan.objective_stage.startswith("stability"), plan.objective_stage
    assert starts["a"] == 3000
    assert starts["b"] == 5000


def test_elapsed_previous_start_still_anchors_stability() -> None:
    """时间原点前进后，已经流逝的预定开始时间必须钳进定义域继续参与稳定性。

    未钳位时 ``starts == old_start`` 这一支恒不可行，该节点被强制记为"已变动"，
    于是求解器会为了少改一项而去挪动本来可以原地开始的动作：动作 a 明明可以
    立刻开始（钳位后目标为 0），却被推到 b 之后。
    """

    first, second, long_action = _shared("a"), _shared("b"), _long()
    # 旧计划：a 在 0、b 在 2000、长动作在 0。
    previous = _previous([_entry(first, 0), _entry(second, 2000), _entry(long_action, 0)])
    # 新快照的时间原点前进 1.2 秒：a 与长动作的旧开始时间落到 -1200，b 落到 800。
    snapshot = PlanningSnapshot(
        revision="advanced", time_origin=1.2,
        nodes=(first, second, long_action), previous_plan=previous,
    )

    plan = solve_snapshot(snapshot, CFG)
    starts = {node.key[1]: node.start_ms for node in plan.nodes}

    assert plan.validated
    assert starts["a"] == 0, f"已过期的预定时间应钳到最早可行点，实际 {starts}"
    assert starts["long"] == 0
    assert starts["b"] == 1000, f"b 应紧随 a 之后，实际 {starts}"


def test_unvalidated_previous_plan_is_not_used_as_an_anchor() -> None:
    """上一轮候选没通过校验时不能当稳定性锚点，只能退回按最早开始排。"""

    first = _shared("a")
    previous = CandidatePlan(revision="prev", time_origin=0.0,
                             nodes=(_entry(first, 5000),), status="UNKNOWN", validated=False)
    snapshot = PlanningSnapshot(revision="r", time_origin=0.0, nodes=(first,),
                                previous_plan=previous)

    plan = solve_snapshot(snapshot, CFG)

    assert plan.objective_stage == "start_sum"
    assert plan.nodes[0].start_ms == 0


def _await_latest(planner: RollingTimePlanner, revision: str, timeout: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        projection = planner.snapshot()
        latest = projection.get("latest")
        if latest and latest.get("revision") == revision and projection["active_solves"] == 0:
            return projection
        time.sleep(0.02)
    raise AssertionError(f"{revision} 的求解结果未在 {timeout}s 内出现")


def test_projection_reports_an_infeasible_plan_honestly_and_withholds_it() -> None:
    """不可行的候选必须以 validated=False 暴露给消费端，并且不被派发侧取用。

    投影仍保留 status 与 error 供诊断——消费端据此显示"暂无可用计划"而不是把
    调度器自己拒绝执行的安排画到时间轴上。
    """

    running = PlanningNode(
        ("wf", "running"), (PlanningAlternative("arm-1", 5000, (D1,)),), (), 1.0, True, 0.0,
    )
    snapshot = PlanningSnapshot(
        revision="infeasible", time_origin=0.0, nodes=(running,),
        holds=(PlanningHold("external", "", (D1,)),),
    )
    planner = RollingTimePlanner(TimePlanningConfig(solve_seconds=1.0))
    try:
        assert planner.request(snapshot) is None
        projection = _await_latest(planner, "infeasible")

        assert projection["latest"]["status"] == "INFEASIBLE"
        assert projection["latest"]["validated"] is False
        assert projection["latest"]["nodes"] == ()
        # 派发侧只接受同版本且已验证的候选。
        assert planner.request(snapshot) is None
    finally:
        planner.close()


def test_validated_plan_is_handed_to_the_dispatch_side() -> None:
    """对照：通过校验的同版本候选必须能被派发侧取用。"""

    node = _shared("a")
    snapshot = PlanningSnapshot(revision="feasible", time_origin=0.0, nodes=(node,))
    planner = RollingTimePlanner(TimePlanningConfig(solve_seconds=1.0))
    try:
        assert planner.request(snapshot) is None
        _await_latest(planner, "feasible")

        plan = planner.request(snapshot)
        assert plan is not None
        assert plan.validated
        assert plan.nodes[0].start_ms == 0
    finally:
        planner.close()


def test_failed_submit_does_not_strand_the_single_flight_flag() -> None:
    """提交失败必须复位单飞标志，否则此后所有求解都被永久挡住。"""

    node = _shared("a")
    snapshot = PlanningSnapshot(revision="submit-fail", time_origin=0.0, nodes=(node,))
    planner = RollingTimePlanner(TimePlanningConfig(solve_seconds=1.0))

    class _RejectingExecutor:
        def submit(self, *_args, **_kwargs):
            raise RuntimeError("executor rejected")

        def shutdown(self, **_kwargs) -> None:
            return None

    healthy = planner._executor
    planner._executor = _RejectingExecutor()
    try:
        try:
            planner.request(snapshot)
        except RuntimeError:
            pass
        assert planner.snapshot()["active_solves"] == 0, "提交失败后不能留下'正在求解'的假象"

        planner._executor = healthy
        assert planner.request(snapshot) is None
        _await_latest(planner, "submit-fail")
        assert planner.request(snapshot) is not None
    finally:
        planner._executor = healthy
        planner.close()
