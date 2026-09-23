"""时间计划适配层在派发门、超时估时、占用归属与完成目标上的行为。

适配层负责把运行期事实整理成可建模的快照，并按候选的预测时间决定本轮放行哪些
就绪节点。这几条分支决定"任务什么时候真的被放行"和"超时作业是继续等还是降级成
未知占用"，此前没有任何断言。
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from unilabos.app.scheduler.dag_state import WorkflowRun
from unilabos.app.scheduler.device_target import DeviceTargetUnavailable, ResolvedDeviceTarget
from unilabos.app.scheduler.dispatch import RecordingDispatcher
from unilabos.app.scheduler.models import (
    DispatchedJob,
    ReadyTask,
    WorkflowEdge,
    WorkflowState,
    WorkflowNode,
    WorkflowSpec,
    priority_weight,
)
from unilabos.app.scheduler.service import EdgeScheduler
from unilabos.app.scheduler.time_planning import (
    CandidatePlan,
    PlannedNode,
    PlanningAlternative,
    PlanningCompletionTarget,
    PlanningHold,
    PlanningNode,
    PlanningSnapshot,
    TimePlanningConfig,
)
from unilabos.app.scheduler.time_scheduling import TimeSchedulingAdapter, configured_time_planner
from unilabos.app.scheduler import time_scheduling as time_scheduling_module
from unilabos.app.scheduler.workflow_planning import PlanningInformationPrefix
from unilabos.workflow.resource_lock_key import device_lock_key

ARM = str(device_lock_key("arm-1"))
# 唤醒间隔取得足够大，以便把"预测开始时间未到"这条专门的唤醒与每轮固定的刷新
# 唤醒区分开。
REFRESH = 30.0


class _StubPlanner:
    """记录唤醒请求的最小规划器；候选由子类决定。"""

    def __init__(self) -> None:
        self.config = TimePlanningConfig(solve_seconds=0.5, refresh_seconds=REFRESH)
        self.wakes: list[float] = []

    def bind_wakeup(self, _callback: object) -> None:
        return None

    def wake_after(self, delay: float, *_args: object) -> None:
        self.wakes.append(round(float(delay), 3))

    def invalidate(self) -> None:
        return None

    def close(self) -> None:
        return None

    def request(self, _snapshot: object) -> None:
        return None


class _PlannerAt:
    """按给定毫秒偏移返回一个已验证候选。"""

    def __init__(self, start_ms: int) -> None:
        self.start_ms = start_ms
        self.config = TimePlanningConfig(solve_seconds=0.5, refresh_seconds=REFRESH)
        self.wakes: list[float] = []

    bind_wakeup = _StubPlanner.bind_wakeup
    wake_after = _StubPlanner.wake_after
    invalidate = _StubPlanner.invalidate
    close = _StubPlanner.close

    def request(self, snapshot: object) -> CandidatePlan:
        return CandidatePlan(
            str(getattr(snapshot, "revision")),
            float(getattr(snapshot, "time_origin")),
            "OPTIMAL",
            nodes=(PlannedNode(("wf", "step-1"), "arm-1", self.start_ms,
                               self.start_ms + 1000, (ARM,)),),
            validated=True,
        )


def _scheduler(planner: object, *, workflow_id: str = "wf", node_id: str = "step-1") -> tuple[EdgeScheduler, WorkflowNode]:
    scheduler = EdgeScheduler(dispatcher=RecordingDispatcher(), time_planner=planner)
    node = WorkflowNode(id=node_id, device_id="arm-1", action_name="run",
                        action_type="goal", param={})
    spec = WorkflowSpec(workflow_id=workflow_id, nodes=[node], edges=[])
    scheduler._workflows[workflow_id] = WorkflowRun(spec)
    return scheduler, node


def _ready(workflow_id: str, node: WorkflowNode) -> list[ReadyTask]:
    return [ReadyTask(workflow_id, node, priority_weight("normal"), 0.0)]


def test_order_withholds_a_task_until_its_predicted_start_and_arms_a_wakeup() -> None:
    """预测开始时间还没到就不放行，并按剩余等待时间安排一次专门唤醒。"""

    planner = _PlannerAt(start_ms=5000)
    scheduler, node = _scheduler(planner)
    try:
        released = scheduler._time_scheduling.order(scheduler, _ready("wf", node))
    finally:
        scheduler.close()

    assert released == [], "预测开始时间在未来时不能放行"
    # 一次是每轮固定的刷新唤醒，另一次是这条时间门专门安排的。
    assert REFRESH in planner.wakes
    gate_wakes = [delay for delay in planner.wakes if delay < REFRESH]
    assert gate_wakes, planner.wakes
    assert 4.0 <= max(gate_wakes) <= 5.0, planner.wakes


def test_order_does_not_arm_a_start_time_wakeup_once_the_predicted_start_has_passed() -> None:
    """对照：预测开始时间已到时不再安排时间门唤醒，只剩每轮刷新。"""

    planner = _PlannerAt(start_ms=0)
    scheduler, node = _scheduler(planner)
    try:
        scheduler._time_scheduling.order(scheduler, _ready("wf", node))
    finally:
        scheduler.close()

    assert planner.wakes and set(planner.wakes) == {REFRESH}, planner.wakes


def _inflight(scheduler: EdgeScheduler, *, job_id: str, workflow_id: str, node_id: str,
              dispatched_at: float, estimated_s: float, keys=(ARM,),
              interval_ids: list[str] | None = None) -> None:
    scheduler._inflight[job_id] = DispatchedJob(
        job_id=job_id,
        workflow_id=workflow_id,
        node_id=node_id,
        device_action_key="/devices/arm-1/run",
        device_id="arm-1",
        action_name="run",
        dispatched_at=dispatched_at,
        estimated_s=estimated_s,
        resource_lock_keys=set(keys),
        active_resource_lock_keys=set(keys),
        resource_interval_ids=list(interval_ids or []),
    )


def test_conditional_estimate_keeps_an_overdue_job_as_a_predictable_action() -> None:
    """超时作业若条件估时仍能给出剩余时间，就继续按剩余时长建模，不降级成未知占用。

    这是"继续等"与"放弃预测"的分界：给得出剩余时间时，时间轴仍有可信终点，竞争
    同资源的候选也不必整条被移出本轮模型。
    """

    planner = _StubPlanner()
    scheduler, node = _scheduler(planner)
    try:
        _inflight(scheduler, job_id="job-overdue", workflow_id="wf", node_id="step-1",
                  dispatched_at=0.0, estimated_s=1.0)
        scheduler._estimator = SimpleNamespace(
            estimate_remaining=lambda *_args, **_kwargs: SimpleNamespace(planned_s=7.0),
        )

        snapshot, _targets, _manual = scheduler._time_scheduling._build(scheduler, [], 100.0)
    finally:
        scheduler.close()

    assert not [hold for hold in snapshot.holds if hold.hold_id.startswith("unknown:")], (
        "条件估时给出剩余时间时不应退化成未知释放占用"
    )
    entry = {node_key.key: node_key for node_key in snapshot.nodes}[("wf", "step-1")]
    assert entry.fixed is True
    assert entry.alternatives[0].duration_ms == 7000
    assert not any(reason == "actual_completion_overdue_unknown" for *_, reason in snapshot.blocked)


def test_exhausted_estimate_degrades_the_job_into_an_unknown_occupancy() -> None:
    """对照：条件估时也给不出剩余时间时，作业降级成未知释放占用并记录阻塞原因。"""

    planner = _StubPlanner()
    scheduler, node = _scheduler(planner)
    try:
        _inflight(scheduler, job_id="job-overdue", workflow_id="wf", node_id="step-1",
                  dispatched_at=0.0, estimated_s=1.0)
        scheduler._estimator = SimpleNamespace(
            estimate_remaining=lambda *_args, **_kwargs: SimpleNamespace(planned_s=None),
        )

        snapshot, _targets, _manual = scheduler._time_scheduling._build(scheduler, [], 100.0)
    finally:
        scheduler.close()

    unknown = [hold for hold in snapshot.holds if hold.hold_id.startswith("unknown:job-overdue")]
    assert unknown
    assert all(hold.already_held and not hold.release_known for hold in unknown)
    assert ("wf", "step-1") not in {item.key for item in snapshot.nodes}
    assert any(reason == "actual_completion_overdue_unknown" for *_, reason in snapshot.blocked)


def test_unknown_occupancy_is_split_under_the_interval_that_actually_holds_it() -> None:
    """未知占用落在某个已持有区间内时，按父区间拆分并让父区间失去已知释放点。

    否则同一份物理占用会被计成两条彼此互斥的区间，父区间也会被误认为仍有可信的
    释放边界。
    """

    planner = _StubPlanner()
    scheduler, node = _scheduler(planner)
    try:
        scheduler._interval_resource_holders[("wf", "interval-1")] = {ARM}
        _inflight(scheduler, job_id="job-overdue", workflow_id="wf", node_id="step-1",
                  dispatched_at=0.0, estimated_s=1.0, interval_ids=["interval-1"])
        scheduler._estimator = SimpleNamespace(
            estimate_remaining=lambda *_args, **_kwargs: SimpleNamespace(planned_s=None),
        )

        snapshot, _targets, _manual = scheduler._time_scheduling._build(scheduler, [], 100.0)
    finally:
        scheduler.close()

    holds = {hold.hold_id: hold for hold in snapshot.holds}
    parent = holds.get("wf:interval-1")
    assert parent is not None, holds
    assert parent.already_held
    assert not parent.release_known, "父区间的释放点已不可信，必须跟着变未知"
    child = holds.get("unknown:job-overdue")
    assert child is not None, holds
    assert child.contained_in == ("wf:interval-1",)
    assert ARM in child.resources


def test_fully_visible_workflow_reports_a_task_completion_target() -> None:
    """可见节点齐全的任务报告 task_completion 目标，并覆盖全部节点。"""

    planner = _StubPlanner()
    scheduler, node = _scheduler(planner)
    try:
        snapshot, _targets, _manual = scheduler._time_scheduling._build(scheduler, [], 0.0)
    finally:
        scheduler.close()

    targets = {target.task_id: target for target in snapshot.completion_targets}
    assert "wf" in targets, snapshot.completion_targets
    assert targets["wf"].kind == "task_completion"
    assert ("wf", "step-1") in set(targets["wf"].node_keys)
    assert snapshot.partial_tasks == ()


def test_configured_time_planner_honors_enable_flag_and_numeric_settings(monkeypatch) -> None:
    monkeypatch.setenv("UNILABOS_TIME_SCHEDULING_ENABLED", "false")
    assert configured_time_planner() is None

    monkeypatch.setenv("UNILABOS_TIME_SCHEDULING_ENABLED", "maybe")
    with pytest.raises(ValueError, match="true 或 false"):
        configured_time_planner()

    monkeypatch.setenv("UNILABOS_TIME_SCHEDULING_ENABLED", "1")
    monkeypatch.setenv("UNILABOS_TIME_SCHEDULING_SOLVE_SECONDS", "0.1")
    monkeypatch.setenv("UNILABOS_TIME_SCHEDULING_MAX_NODES", "7")
    monkeypatch.setenv("ULAB_SCHEDULER_AGING_INTERVAL_SECONDS", "9")
    planner = configured_time_planner()
    assert planner is not None
    try:
        assert planner.config.solve_seconds == 0.1
        assert planner.config.max_nodes == 7
        assert planner.config.aging_seconds == 9.0
    finally:
        planner.close()


def test_devices_resolves_dynamic_targets_until_unavailable_or_duplicate() -> None:
    planner = _StubPlanner()
    scheduler, node = _scheduler(planner)
    node.device_id = ""
    first = ResolvedDeviceTarget("arm-a", "material-a")
    second = ResolvedDeviceTarget("arm-b", "material-b")
    calls = []

    def resolver(selector, action, seen):
        calls.append((selector, action, set(seen)))
        if len(calls) == 1:
            return first
        if len(calls) == 2:
            return second
        raise DeviceTargetUnavailable("none", "no more targets")

    scheduler._device_target_resolver = resolver
    adapter = TimeSchedulingAdapter(planner)
    try:
        assert adapter._devices(scheduler, node) == (first, second)
    finally:
        scheduler.close()

    duplicate_scheduler, duplicate_node = _scheduler(_StubPlanner())
    duplicate_node.device_id = ""
    duplicate_scheduler._device_target_resolver = lambda *_args: first
    try:
        assert adapter._devices(duplicate_scheduler, duplicate_node) == (first,)
    finally:
        duplicate_scheduler.close()


def test_devices_rejects_missing_resolver_and_empty_candidate_set() -> None:
    planner = _StubPlanner()
    scheduler, node = _scheduler(planner)
    node.device_id = ""
    adapter = TimeSchedulingAdapter(planner)
    try:
        with pytest.raises(ValueError, match="动态设备选择权威"):
            adapter._devices(scheduler, node)
        scheduler._device_target_resolver = lambda *_args: (_ for _ in ()).throw(
            DeviceTargetUnavailable("none", "empty")
        )
        with pytest.raises(ValueError, match="没有当前已注册"):
            adapter._devices(scheduler, node)
    finally:
        scheduler.close()


def test_inflight_without_a_definition_keeps_all_recorded_resource_keys() -> None:
    planner = _StubPlanner()
    scheduler, _node = _scheduler(planner)
    _inflight(
        scheduler,
        job_id="orphan",
        workflow_id="wf",
        node_id="missing-node",
        dispatched_at=0.0,
        estimated_s=5.0,
        keys=(ARM, "/resources/held"),
    )
    job = scheduler._inflight["orphan"]
    adapter = TimeSchedulingAdapter(planner)
    try:
        active = adapter._inflight_active_keys(scheduler, job)
    finally:
        scheduler.close()
    assert {ARM, "/resources/held"} <= active
    assert str(device_lock_key("arm-1")) in active


def test_invalidate_clears_targets_and_input_diagnostics() -> None:
    planner = _StubPlanner()
    scheduler, _node = _scheduler(planner)
    adapter = TimeSchedulingAdapter(planner)
    adapter._targets[("wf", "step-1", "arm-1")] = ResolvedDeviceTarget("arm-1", "")
    adapter._last_input = {"revision": "old"}
    adapter._last_error = "old error"
    try:
        adapter.invalidate()
    finally:
        scheduler.close()
    assert adapter._targets == {}
    assert adapter._last_input == {}
    assert adapter._last_error == ""


def test_order_converts_snapshot_errors_to_empty_result_and_wakeup() -> None:
    planner = _StubPlanner()
    scheduler, node = _scheduler(planner)
    adapter = TimeSchedulingAdapter(planner)
    adapter._build = lambda *_args: (_ for _ in ()).throw(ValueError("snapshot broken"))
    try:
        assert adapter.order(scheduler, _ready("wf", node)) == []
    finally:
        scheduler.close()
    assert adapter._last_error == "snapshot broken"
    assert planner.wakes == [REFRESH]


def test_manual_ready_node_bypasses_time_plan() -> None:
    planner = _StubPlanner()
    scheduler, node = _scheduler(planner)
    adapter = TimeSchedulingAdapter(planner)
    adapter._build = lambda *_args: (
        PlanningSnapshot("manual", 0.0, ()),
        {},
        {("wf", "step-1")},
    )
    try:
        result = adapter.order(scheduler, _ready("wf", node))
    finally:
        scheduler.close()
    assert result == _ready("wf", node)


def test_step_target_with_missing_or_unmapped_target_is_released() -> None:
    planner = _StubPlanner()
    scheduler, node = _scheduler(planner)
    scheduler._step_targets["wf"] = "step-1"
    adapter = TimeSchedulingAdapter(planner)
    snapshot = PlanningSnapshot("step", 0.0, ())
    adapter._build = lambda *_args: (snapshot, {}, set())
    try:
        assert adapter.order(scheduler, _ready("wf", node)) == _ready("wf", node)

        entry = PlannedNode(("wf", "step-1"), "arm-1", 0, 1000, (ARM,))
        planner.request = lambda _snapshot: CandidatePlan(
            "step", 0.0, "OPTIMAL", (entry,), validated=True
        )
        assert adapter.order(scheduler, _ready("wf", node)) == _ready("wf", node)
    finally:
        scheduler.close()


def test_fixed_or_tentative_step_candidate_stays_blocked() -> None:
    planner = _StubPlanner()
    scheduler, node = _scheduler(planner)
    scheduler._step_targets["wf"] = "step-1"
    adapter = TimeSchedulingAdapter(planner)
    snapshot = PlanningSnapshot("step-fixed", 0.0, ())
    adapter._build = lambda *_args: (snapshot, {}, set())
    fixed = PlannedNode(("wf", "step-1"), "arm-1", 0, 1000, (ARM,), fixed=True)
    planner.request = lambda _snapshot: CandidatePlan(
        "step-fixed", 0.0, "OPTIMAL", (fixed,), validated=True
    )
    try:
        assert adapter.order(scheduler, _ready("wf", node)) == []
        tentative = PlannedNode(
            ("wf", "step-1"), "arm-1", 0, 1000, (ARM,), parameters_final=False
        )
        planner.request = lambda _snapshot: CandidatePlan(
            "step-fixed", 0.0, "OPTIMAL", (tentative,), validated=True
        )
        assert adapter.order(scheduler, _ready("wf", node)) == []
    finally:
        scheduler.close()


def test_normal_candidate_without_target_is_withheld() -> None:
    planner = _PlannerAt(start_ms=0)
    scheduler, node = _scheduler(planner)
    adapter = TimeSchedulingAdapter(planner)
    adapter._build = lambda *_args: (PlanningSnapshot("unmapped", 0.0, ()), {}, set())
    try:
        assert adapter.order(scheduler, _ready("wf", node)) == []
    finally:
        scheduler.close()


def test_deferred_open_scopes_choose_one_conflicting_group_and_reject_fixed_conflict() -> None:
    planner = _StubPlanner()
    scheduler, first_node = _scheduler(planner, workflow_id="high", node_id="hold")
    second_spec = WorkflowSpec("low", [WorkflowNode("hold", "arm-1", "run", "goal", {})], [])
    scheduler._workflows["low"] = WorkflowRun(second_spec)
    adapter = TimeSchedulingAdapter(planner)
    high_key = ("high", "hold")
    low_key = ("low", "hold")
    nodes = {
        high_key: PlanningNode(high_key, (PlanningAlternative("arm", 1000, ("/shared",)),)),
        low_key: PlanningNode(low_key, (PlanningAlternative("arm", 1000, ("/shared",)),)),
    }
    holds = [
        PlanningHold("high-scope", "high", ("/shared",), (high_key,), False, False),
        PlanningHold("low-scope", "low", ("/shared",), (low_key,), False, False),
    ]
    try:
        deferred = adapter._deferred_open_scopes(scheduler, nodes, holds, {"high": 10.0, "low": 1.0})
        assert deferred == {low_key}
        nodes[low_key] = replace(nodes[low_key], fixed=True)
        with pytest.raises(ValueError, match="已持有区间证明"):
            adapter._deferred_open_scopes(scheduler, nodes, holds, {"high": 10.0, "low": 1.0})
    finally:
        scheduler.close()


def test_terminal_workflow_is_removed_from_snapshot_and_max_nodes_reports_partial_scope() -> None:
    planner = _StubPlanner()
    scheduler, node = _scheduler(planner)
    scheduler._workflows["wf"].state = WorkflowState.SUCCESS
    adapter = TimeSchedulingAdapter(planner)
    try:
        snapshot, _targets, _manual = adapter._build(scheduler, [], 0.0)
        assert snapshot.nodes == ()

        scheduler2, node2 = _scheduler(_StubPlanner())
        scheduler2._time_scheduling.planner.config = SimpleNamespace(
            max_nodes=0, max_alternatives=8, aging_seconds=30.0, refresh_seconds=30.0
        )
        try:
            snapshot2, _targets2, _manual2 = scheduler2._time_scheduling._build(
                scheduler2, [], 0.0
            )
        finally:
            scheduler2.close()
    finally:
        scheduler.close()
    assert snapshot2.partial_tasks == ("wf",)
    assert snapshot2.completion_targets[0].kind == "partial_scope"


def test_snapshot_includes_planner_state_and_adapter_diagnostics() -> None:
    planner = _StubPlanner()
    scheduler, _node = _scheduler(planner)
    planner.snapshot = lambda: {"latest": None}
    adapter = TimeSchedulingAdapter(planner)
    adapter._last_input = {"revision": "r"}
    adapter._last_error = ""
    try:
        projection = adapter.snapshot()
    finally:
        scheduler.close()
    assert projection == {
        "latest": None,
        "input": {"revision": "r"},
        "input_error": "",
        "guaranteed_hard_time_limits": False,
    }


def test_deferred_open_scope_merges_groups_that_share_members() -> None:
    planner = _StubPlanner()
    scheduler, _node = _scheduler(planner, workflow_id="high", node_id="a")
    scheduler._workflows["low"] = WorkflowRun(
        WorkflowSpec(
            "low",
            [WorkflowNode(id="b", device_id="arm-1", action_name="run", action_type="goal", param={})],
            [],
        )
    )
    adapter = TimeSchedulingAdapter(planner)
    high = ("high", "a")
    low = ("low", "b")
    nodes = {
        high: PlanningNode(high, (PlanningAlternative("arm", 1000, ("/r1",)),)),
        low: PlanningNode(low, (PlanningAlternative("arm", 1000, ("/r2",)),)),
    }
    holds = [
        PlanningHold("first", "high", ("/r1",), (high,), False, False),
        PlanningHold("merged", "low", ("/r2",), (high, low), False, False),
    ]
    try:
        assert adapter._deferred_open_scopes(
            scheduler, nodes, holds, {"high": 10.0, "low": 1.0}
        ) == set()
    finally:
        scheduler.close()


def test_build_reads_resource_plan_and_inflight_interval_ownership() -> None:
    planner = _StubPlanner()
    scheduler, node = _scheduler(planner)
    node.resource_interval_ids = ["interval-1"]
    resource = SimpleNamespace(resource_id="resource-1", canonical_key=ARM)
    interval = SimpleNamespace(interval_id="interval-1", resource_id="resource-1")
    scheduler._resource_plan_for_spec = lambda _spec: SimpleNamespace(
        resources=[resource], intervals=[interval]
    )
    _inflight(
        scheduler,
        job_id="job-interval",
        workflow_id="wf",
        node_id="step-1",
        dispatched_at=0.0,
        estimated_s=5.0,
        keys=(ARM,),
        interval_ids=["interval-1"],
    )
    adapter = TimeSchedulingAdapter(planner)
    adapter._inflight_active_keys = lambda *_args: {ARM}
    try:
        snapshot, _targets, _manual = adapter._build(scheduler, [], 0.0)
    finally:
        scheduler.close()
    assert any(node_key.key == ("wf", "step-1") for node_key in snapshot.nodes)


def test_tentative_parameter_path_blocks_when_no_timing_is_available() -> None:
    planner = _StubPlanner()
    scheduler, node = _scheduler(planner)
    run = scheduler._workflows["wf"]
    run.resolve_params = lambda _node_id: (_ for _ in ()).throw(
        time_scheduling_module.ParamResolveError("parent result missing")
    )
    scheduler._estimator = SimpleNamespace(
        estimate_timing=lambda *_args, **_kwargs: SimpleNamespace(planned_s=None)
    )
    adapter = TimeSchedulingAdapter(planner)
    try:
        snapshot, _targets, _manual = adapter._build(scheduler, [], 0.0)
    finally:
        scheduler.close()
    assert snapshot.nodes == ()
    assert any(reason == "parent result missing" for *_, reason in snapshot.blocked)


def test_tentative_parameter_prediction_errors_keep_original_blocked_semantics() -> None:
    planner = _StubPlanner()
    scheduler, node = _scheduler(planner)
    run = scheduler._workflows["wf"]
    run.resolve_params = lambda _node_id: (_ for _ in ()).throw(
        time_scheduling_module.ParamResolveError("parent result missing")
    )
    scheduler._estimator = SimpleNamespace(
        estimate_timing=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("tentative timing unavailable")
        )
    )
    adapter = TimeSchedulingAdapter(planner)
    try:
        snapshot, _targets, _manual = adapter._build(scheduler, [], 0.0)
    finally:
        scheduler.close()
    assert any(reason == "parent result missing" for *_, reason in snapshot.blocked)


def test_parameter_error_with_known_parent_creates_information_prefix() -> None:
    planner = _StubPlanner()
    scheduler, parent = _scheduler(planner, workflow_id="wf", node_id="parent")
    grandparent = WorkflowNode(
        id="grandparent", device_id="arm-1", action_name="grand", action_type="goal", param={}
    )
    child = WorkflowNode(
        id="child", device_id="arm-1", action_name="child", action_type="goal", param={}
    )
    scheduler._workflows["wf"] = WorkflowRun(
        WorkflowSpec(
            "wf",
            [grandparent, parent, child],
            [
                # The adapter reads dependencies from the run's workflow graph.
                    WorkflowEdge("parent-child", "parent", "child"),
                    WorkflowEdge("grand-parent", "grandparent", "parent"),
            ],
        )
    )
    run = scheduler._workflows["wf"]
    original_resolve = run.resolve_params

    def resolve(node_id: str):
        if node_id == "child":
            raise time_scheduling_module.ParamResolveError("parent result missing")
        return original_resolve(node_id)

    run.resolve_params = resolve
    scheduler._estimator = SimpleNamespace(
        estimate_timing=lambda action_key, *_args, **_kwargs: SimpleNamespace(
            planned_s=None if action_key.endswith("/child") else 1.0
        )
    )
    adapter = TimeSchedulingAdapter(planner)
    try:
        snapshot, _targets, _manual = adapter._build(scheduler, [], 0.0)
    finally:
        scheduler.close()
    assert snapshot.completion_targets
    assert any(target.prefix_id == "parameters:child" for target in snapshot.completion_targets)


def test_missing_inflight_node_is_projected_as_unknown_hold() -> None:
    planner = _StubPlanner()
    scheduler, _node = _scheduler(planner)
    _inflight(
        scheduler,
        job_id="orphan-job",
        workflow_id="wf",
        node_id="not-in-definition",
        dispatched_at=0.0,
        estimated_s=5.0,
    )
    adapter = TimeSchedulingAdapter(planner)
    try:
        snapshot, _targets, _manual = adapter._build(scheduler, [], 0.0)
    finally:
        scheduler.close()
    assert any(hold.hold_id.startswith("unknown:orphan-job") for hold in snapshot.holds)


def test_external_busy_key_is_preserved_as_unknown_hold() -> None:
    planner = _StubPlanner()
    scheduler, _node = _scheduler(planner)
    scheduler._busy_keys = lambda: {"/external/busy"}
    adapter = TimeSchedulingAdapter(planner)
    try:
        snapshot, _targets, _manual = adapter._build(scheduler, [], 0.0)
    finally:
        scheduler.close()
    external = next(hold for hold in snapshot.holds if hold.hold_id == "external")
    assert external.resources == ("/external/busy",)


def test_unknown_hold_excludes_its_successor_from_the_current_scope() -> None:
    planner = _StubPlanner()
    scheduler, first = _scheduler(planner, workflow_id="wf", node_id="first")
    second = WorkflowNode(
        id="second", device_id="arm-2", action_name="run", action_type="goal", param={}
    )
    scheduler._workflows["wf"] = WorkflowRun(
        WorkflowSpec(
            "wf",
            [first, second],
            [
                WorkflowEdge("first-second", "first", "second")
            ],
        )
    )
    _inflight(
        scheduler,
        job_id="overdue-first",
        workflow_id="wf",
        node_id="first",
        dispatched_at=0.0,
        estimated_s=1.0,
    )
    scheduler._estimator = SimpleNamespace(
        estimate_remaining=lambda *_args, **_kwargs: SimpleNamespace(planned_s=None),
        estimate_timing=lambda *_args, **_kwargs: SimpleNamespace(planned_s=1.0),
    )
    adapter = TimeSchedulingAdapter(planner)
    try:
        snapshot, _targets, _manual = adapter._build(scheduler, [], 100.0)
    finally:
        scheduler.close()
    assert ("wf", "second") not in {item.key for item in snapshot.nodes}
    assert any(item[:2] == ("wf", "second") for item in snapshot.blocked)


def test_information_prefix_wait_ages_and_selects_the_prefix(monkeypatch) -> None:
    planner = _StubPlanner()
    scheduler, node = _scheduler(planner)
    prefix = PlanningInformationPrefix("prefix-1", ("step-1",), ("step-1",))
    fake_view = SimpleNamespace(
        workflow_id="wf",
        node_states={},
        blocked={},
        full_tail_known=False,
        manual_gate_node_ids=(),
        nodes=(node,),
        dependencies={"step-1": ()},
        information_prefixes=(prefix,),
        continuous_intervals=(),
    )
    monkeypatch.setattr(time_scheduling_module, "build_workflow_planning_view", lambda *_args, **_kwargs: fake_view)
    adapter = TimeSchedulingAdapter(planner)
    try:
        first, _targets, _manual = adapter._build(scheduler, _ready("wf", node), 0.0)
        second, _targets, _manual = adapter._build(scheduler, _ready("wf", node), 10.0)
    finally:
        scheduler.close()
    assert first.partial_tasks == ("wf",)
    assert second.completion_targets == (
        # The target is chosen from the aged prefix and stops at its information boundary.
        second.completion_targets[0],
    )
    assert second.completion_targets[0].kind == "information"
    assert second.completion_targets[0].prefix_id == "prefix-1"


def test_unknown_job_successor_is_propagated_out_of_the_snapshot(monkeypatch) -> None:
    planner = _StubPlanner()
    scheduler, first = _scheduler(planner, workflow_id="wf", node_id="first")
    second = WorkflowNode(
        id="second", device_id="arm-1", action_name="run", action_type="goal", param={}
    )
    scheduler._workflows["wf"] = WorkflowRun(
        WorkflowSpec("wf", [first, second], [WorkflowEdge("e", "first", "second")])
    )
    _inflight(
        scheduler,
        job_id="unknown-first",
        workflow_id="wf",
        node_id="first",
        dispatched_at=0.0,
        estimated_s=1.0,
    )
    scheduler._estimator = SimpleNamespace(
        estimate_remaining=lambda *_args, **_kwargs: SimpleNamespace(planned_s=None),
        estimate_timing=lambda *_args, **_kwargs: SimpleNamespace(planned_s=1.0),
    )
    fake_view = SimpleNamespace(
        workflow_id="wf",
        node_states={},
        blocked={},
        full_tail_known=True,
        manual_gate_node_ids=(),
        nodes=(first, second),
        dependencies={"first": (), "second": ("first",)},
        information_prefixes=(),
        continuous_intervals=(),
    )
    monkeypatch.setattr(
        time_scheduling_module,
        "build_workflow_planning_view",
        lambda *_args, **_kwargs: fake_view,
    )
    adapter = TimeSchedulingAdapter(planner)
    try:
        snapshot, _targets, _manual = adapter._build(scheduler, [], 100.0)
    finally:
        scheduler.close()
    assert ("wf", "second") not in {item.key for item in snapshot.nodes}
    assert any(item[:2] == ("wf", "second") for item in snapshot.blocked)


def test_unavailable_information_prefix_is_skipped(monkeypatch) -> None:
    planner = _StubPlanner()
    scheduler, node = _scheduler(planner)
    fake_view = SimpleNamespace(
        workflow_id="wf",
        node_states={},
        blocked={},
        full_tail_known=False,
        manual_gate_node_ids=(),
        nodes=(node,),
        dependencies={"step-1": ()},
        information_prefixes=(
            PlanningInformationPrefix("missing-prefix", ("missing",), ("missing",)),
        ),
        continuous_intervals=(),
    )
    monkeypatch.setattr(
        time_scheduling_module,
        "build_workflow_planning_view",
        lambda *_args, **_kwargs: fake_view,
    )
    adapter = TimeSchedulingAdapter(planner)
    try:
        snapshot, _targets, _manual = adapter._build(scheduler, _ready("wf", node), 0.0)
    finally:
        scheduler.close()
    assert snapshot.completion_targets == (
        PlanningCompletionTarget("wf", (), "information"),
    )
