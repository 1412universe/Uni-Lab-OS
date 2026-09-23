"""时间计划适配层在派发门、超时估时、占用归属与完成目标上的行为。

适配层负责把运行期事实整理成可建模的快照，并按候选的预测时间决定本轮放行哪些
就绪节点。这几条分支决定"任务什么时候真的被放行"和"超时作业是继续等还是降级成
未知占用"，此前没有任何断言。
"""

from __future__ import annotations

from types import SimpleNamespace

from unilabos.app.scheduler.dag_state import WorkflowRun
from unilabos.app.scheduler.dispatch import RecordingDispatcher
from unilabos.app.scheduler.models import (
    DispatchedJob,
    ReadyTask,
    WorkflowNode,
    WorkflowSpec,
    priority_weight,
)
from unilabos.app.scheduler.service import EdgeScheduler
from unilabos.app.scheduler.time_planning import (
    CandidatePlan,
    PlannedNode,
    TimePlanningConfig,
)
from unilabos.app.scheduler.time_scheduling import TimeSchedulingAdapter
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
