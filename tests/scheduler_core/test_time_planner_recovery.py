"""时间规划目标、重试和等待老化的回归测试。"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from unilabos.app.scheduler import time_planning
from unilabos.app.scheduler.dispatch import RecordingDispatcher
from unilabos.app.scheduler.dag_state import WorkflowRun
from unilabos.app.scheduler.models import (
    ReadyTask,
    WorkflowNode,
    WorkflowSpec,
    priority_weight,
)
from unilabos.app.scheduler.service import EdgeScheduler
from unilabos.app.scheduler.time_planning import (
    CandidatePlan,
    PlanningAlternative,
    PlanningCompletionTarget,
    PlanningNode,
    PlanningSnapshot,
    RollingTimePlanner,
    TimePlanningConfig,
    solve_snapshot,
)
from unilabos.app.scheduler.time_scheduling import TimeSchedulingAdapter


def _empty_target_snapshot(revision: str = "empty-target") -> PlanningSnapshot:
    return PlanningSnapshot(
        revision=revision,
        time_origin=0.0,
        nodes=(),
        completion_targets=(
            PlanningCompletionTarget(
                "workflow-information",
                (),
                "information",
                "condition-input",
            ),
        ),
    )


def test_solve_snapshot_keeps_explicit_target_with_no_visible_nodes() -> None:
    """可见节点为空时仍返回 information 目标，不能被 task_ends 遍历丢弃。"""

    plan = solve_snapshot(_empty_target_snapshot(), TimePlanningConfig(solve_seconds=1.0))

    assert plan.completion_targets == (
        PlanningCompletionTarget(
            "workflow-information", (), "information", "condition-input"
        ),
    )


def test_adapter_keeps_target_when_every_visible_node_is_filtered() -> None:
    """站点控制过滤全部节点时，适配层仍报告信息边界。"""

    scheduler = EdgeScheduler(dispatcher=RecordingDispatcher())
    node = WorkflowNode(
        id="condition-tail",
        device_id="device-1",
        action_name="run",
        action_type="goal",
        param={},
    )
    spec = WorkflowSpec(workflow_id="filtered-workflow", nodes=[node], edges=[])
    scheduler._workflows[spec.workflow_id] = WorkflowRun(spec)

    class _BlockedStation:
        def allows_task(self, _workflow_id: str) -> bool:
            return False

    scheduler._station_control = _BlockedStation()
    planner = SimpleNamespace(
        config=SimpleNamespace(
            max_nodes=256,
            max_alternatives=8,
            aging_seconds=30.0,
            refresh_seconds=1.0,
        )
    )
    adapter = TimeSchedulingAdapter(planner)
    try:
        snapshot, _targets, _manual = adapter._build(scheduler, [], 0.0)
    finally:
        scheduler.close()

    assert snapshot.nodes == ()
    assert snapshot.partial_tasks == ("filtered-workflow",)
    assert snapshot.completion_targets == (
        PlanningCompletionTarget("filtered-workflow", (), "information"),
    )


def test_step_target_is_not_blocked_by_pending_cp_sat_plan() -> None:
    """单步明确选中的就绪节点不能等待异步 CP-SAT 结果。"""

    scheduler = EdgeScheduler(dispatcher=RecordingDispatcher())
    node = WorkflowNode(
        id="step-node",
        device_id="device-1",
        action_name="run",
        action_type="goal",
        param={},
    )
    spec = WorkflowSpec("step-workflow", [node], [], run_mode="step")
    scheduler._workflows[spec.workflow_id] = WorkflowRun(spec)
    scheduler._step_targets[spec.workflow_id] = node.id

    class _PendingPlanner:
        config = TimePlanningConfig()

        def wake_after(self, *_args):
            return None

        def request(self, _snapshot):
            # The real planner returns None until its worker publishes a plan.
            return None

    adapter = TimeSchedulingAdapter(_PendingPlanner())
    ready = [ReadyTask(spec.workflow_id, node, priority_weight("normal"), 0.0)]
    try:
        assert [task.node.id for task in adapter.order(scheduler, ready)] == [node.id]
    finally:
        scheduler.close()


def _wait_for_calls(calls: list[str], count: int, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while len(calls) < count and time.monotonic() < deadline:
        time.sleep(0.005)
    assert len(calls) >= count


@pytest.mark.parametrize("failure_status", ["ERROR", "UNKNOWN"])
def test_same_revision_transient_failure_is_retried_with_backoff(
    monkeypatch: pytest.MonkeyPatch,
    failure_status: str,
) -> None:
    """ERROR/UNKNOWN 不会把同一 revision 永久卡在旧结果上。"""

    calls: list[str] = []
    first_call = threading.Event()

    def fake_solve(snapshot: PlanningSnapshot, _config: TimePlanningConfig) -> CandidatePlan:
        calls.append(snapshot.revision)
        first_call.set()
        if len(calls) < 3:
            return CandidatePlan(snapshot.revision, snapshot.time_origin, failure_status)
        return CandidatePlan(
            snapshot.revision,
            snapshot.time_origin,
            "OPTIMAL",
            validated=True,
        )

    monkeypatch.setattr(time_planning, "solve_snapshot", fake_solve)
    planner = RollingTimePlanner(
        TimePlanningConfig(
            solve_seconds=0.1,
            retry_initial_seconds=0.05,
            retry_max_seconds=0.1,
        )
    )
    snapshot = _empty_target_snapshot("retry-same-revision")
    try:
        assert planner.request(snapshot) is None
        assert first_call.wait(1.0)
        time.sleep(0.015)
        assert len(calls) == 1
        _wait_for_calls(calls, 3)
        assert calls == [snapshot.revision] * 3
        assert planner.request(snapshot) is not None
    finally:
        planner.close()


def test_revision_change_cancels_old_revision_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """新事实版本到达后，旧版本的退避计时器不能再次提交求解。"""

    calls: list[str] = []
    first_call = threading.Event()

    def fake_solve(snapshot: PlanningSnapshot, _config: TimePlanningConfig) -> CandidatePlan:
        calls.append(snapshot.revision)
        first_call.set()
        return CandidatePlan(
            snapshot.revision,
            snapshot.time_origin,
            "ERROR" if snapshot.revision == "old" else "OPTIMAL",
            validated=snapshot.revision != "old",
        )

    monkeypatch.setattr(time_planning, "solve_snapshot", fake_solve)
    planner = RollingTimePlanner(
        TimePlanningConfig(retry_initial_seconds=0.05, retry_max_seconds=0.1)
    )
    old = _empty_target_snapshot("old")
    new = _empty_target_snapshot("new")
    try:
        planner.request(old)
        assert first_call.wait(1.0)
        planner.request(new)
        _wait_for_calls(calls, 2)
        time.sleep(0.15)
        assert calls == ["old", "new"]
    finally:
        planner.close()


def test_invalidate_discards_visible_candidate_before_next_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """暂停后旧排序立即不可见，迟到的旧求解结果也不能重新发布。"""

    calls: list[str] = []
    first_call = threading.Event()

    def fake_solve(snapshot: PlanningSnapshot, _config: TimePlanningConfig) -> CandidatePlan:
        calls.append(snapshot.revision)
        first_call.set()
        return CandidatePlan(
            snapshot.revision,
            snapshot.time_origin,
            "OPTIMAL",
            validated=True,
        )

    monkeypatch.setattr(time_planning, "solve_snapshot", fake_solve)
    planner = RollingTimePlanner(TimePlanningConfig(solve_seconds=0.1))
    old = _empty_target_snapshot("before-pause")
    new = _empty_target_snapshot("after-resume")
    try:
        assert planner.request(old) is None
        assert first_call.wait(1.0)
        _wait_for_calls(calls, 1)
        deadline = time.monotonic() + 1.0
        while planner.snapshot()["latest"] is None and time.monotonic() < deadline:
            time.sleep(0.005)
        assert planner.snapshot()["latest"]["revision"] == "before-pause"

        planner.invalidate()

        assert planner.snapshot()["latest"] is None
        assert planner.snapshot()["pending_snapshots"] == 0
        assert planner.request(new) is None
        _wait_for_calls(calls, 2)
        deadline = time.monotonic() + 1.0
        while (
            planner.snapshot()["latest"] is None
            or planner.snapshot()["latest"]["revision"] != "after-resume"
        ) and time.monotonic() < deadline:
            time.sleep(0.005)
        assert calls == ["before-pause", "after-resume"]
        assert planner.snapshot()["latest"]["revision"] == "after-resume"
    finally:
        planner.close()


def test_close_cancels_pending_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """关闭规划器后，退避计时器不能提交迟到的后台任务。"""

    calls: list[str] = []
    first_call = threading.Event()

    def fake_solve(snapshot: PlanningSnapshot, _config: TimePlanningConfig) -> CandidatePlan:
        calls.append(snapshot.revision)
        first_call.set()
        return CandidatePlan(snapshot.revision, snapshot.time_origin, "ERROR")

    monkeypatch.setattr(time_planning, "solve_snapshot", fake_solve)
    planner = RollingTimePlanner(
        TimePlanningConfig(retry_initial_seconds=0.05, retry_max_seconds=0.1)
    )
    try:
        planner.request(_empty_target_snapshot("close"))
        assert first_call.wait(1.0)
    finally:
        planner.close()
    time.sleep(0.15)
    assert calls == ["close"]


def test_inflight_workflow_sibling_still_accumulates_waiting_age() -> None:
    """同一 workflow 的独立 ready sibling 不应因另一 Job 在途而冻结老化。"""

    class _Run:
        dispatch_gate = "active"
        state = SimpleNamespace(value="running")
        spec = SimpleNamespace(priority="normal")

    class _Scheduler:
        _workflows = {"workflow": _Run()}
        _inflight = {"job": SimpleNamespace(workflow_id="workflow")}
        _station_control = None

    planner = SimpleNamespace(config=SimpleNamespace(aging_seconds=10.0))
    adapter = TimeSchedulingAdapter(planner)
    ready = [
        ReadyTask(
            "workflow",
            WorkflowNode(
                id="sibling",
                device_id="device",
                action_name="run",
                action_type="goal",
                param={},
            ),
            priority_weight=1.0,
            submitted_at=0.0,
        )
    ]

    adapter._priority(_Scheduler(), "workflow", ready, 0.0)
    aged = adapter._priority(_Scheduler(), "workflow", ready, 10.0)

    assert aged == priority_weight("normal") + 1


def test_short_budget_keeps_a_valid_serial_incumbent_for_large_alternative_model() -> None:
    """求解预算过短时仍返回可验证候选，避免甘特图整页空白。"""

    nodes = tuple(
        PlanningNode(
            (f"workflow-{index}", "node"),
            tuple(
                PlanningAlternative(
                    f"device-{alternative}",
                    1_000,
                    ("/devices/shared", f"/devices/device-{alternative}"),
                )
                for alternative in range(8)
            ),
        )
        for index in range(10)
    )
    plan = solve_snapshot(
        PlanningSnapshot("large-short-budget", 0.0, nodes),
        TimePlanningConfig(solve_seconds=0.01, max_alternatives=8),
    )

    assert plan.status == "FEASIBLE"
    assert plan.objective_stage == "fallback-feasible"
    assert plan.validated is True
    assert len(plan.nodes) == len(nodes)


def test_short_budget_fallback_keeps_disjoint_tasks_parallel() -> None:
    """兜底排程也不能把互不冲突的两个任务扩大成全局串行。"""

    nodes = tuple(
        PlanningNode(
            (task, "node"),
            tuple(
                PlanningAlternative(
                    f"{task}-device-{alternative}",
                    1_000,
                    (f"/devices/{task}", f"/devices/{task}-alt-{alternative}"),
                )
                for alternative in range(8)
            ),
        )
        for task in ("task-a", "task-b")
    )
    plan = solve_snapshot(
        PlanningSnapshot("parallel-short-budget", 0.0, nodes),
        TimePlanningConfig(solve_seconds=0.001, max_alternatives=8),
    )

    assert plan.validated is True
    assert {node.start_ms for node in plan.nodes} == {0}
