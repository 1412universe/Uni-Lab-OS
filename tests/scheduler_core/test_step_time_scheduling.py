"""CP-SAT 时间计划尚未就绪时的单步派发回归测试。"""

from __future__ import annotations

from types import SimpleNamespace

from unilabos.app.scheduler.dispatch import RecordingDispatcher
from unilabos.app.scheduler.models import WorkflowNode, WorkflowSpec
from unilabos.app.scheduler.service import EdgeScheduler
from unilabos.app.scheduler.time_planning import (
    CandidatePlan,
    PlannedNode,
    TimePlanningConfig,
)


class _PlanNotReady:
    """只模拟后台求解尚未产出当前版本计划的最小规划器。"""

    config = TimePlanningConfig()

    def bind_wakeup(self, _callback: object) -> None:
        return None

    def request(self, _snapshot: object) -> None:
        return None

    def wake_after(self, *_args: object) -> None:
        return None

    def invalidate(self) -> None:
        return None

    def close(self) -> None:
        return None


class _PlanStartsLater(_PlanNotReady):
    """返回一个把单步目标排到未来的候选，模拟后台排程时钟尚未追上。"""

    def request(self, snapshot: object) -> CandidatePlan:
        # ``TimeSchedulingAdapter`` 只需要不可变快照的两个字段；使用真实快照
        # 的 revision/origin，避免伪造一个会被规划器拒绝的版本。
        revision = str(getattr(snapshot, "revision"))
        time_origin = float(getattr(snapshot, "time_origin"))
        return CandidatePlan(
            revision,
            time_origin,
            "OPTIMAL",
            nodes=(
                PlannedNode(
                    ("step-starts-later", "node-a"),
                    "device-a",
                    60_000,
                    61_000,
                    (),
                ),
            ),
            validated=True,
        )


class _UnknownTimingEstimator:
    """返回未知 CP-SAT 时长，验证单步仍使用记录用的旧估时入口。"""

    def estimate_timing(self, *_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(planned_s=None, source="unknown")

    def estimate(self, *_args: object, **_kwargs: object) -> tuple[float, str]:
        return 42.0, "default"


def test_step_dispatches_selected_node_before_cp_sat_plan_is_ready() -> None:
    """单步命令不能因 CP-SAT 异步计划尚未返回而静默成功。

    提交单步任务时调度器会先保持暂停；操作员随后选择了唯一的 READY 节点。
    时间规划后台线程可能仍未返回当前快照的 CandidatePlan，但这个明确目标已
    通过 DAG 与执行前资源门禁确认，必须直接进入派发，而不是返回空结果。
    """

    dispatcher = RecordingDispatcher()
    scheduler = EdgeScheduler(dispatcher=dispatcher, time_planner=_PlanNotReady())
    spec = WorkflowSpec(
        workflow_id="step-before-plan-ready",
        nodes=[
            WorkflowNode(
                id="node-a",
                job_id="job-a",
                device_id="device-a",
                action_name="run",
                action_type="goal",
                param={},
            )
        ],
        run_mode="step",
    )

    try:
        submitted = scheduler.submit_workflow(spec)
        assert submitted["state"] == "paused"
        assert submitted["dispatched"] == []

        result = scheduler.step_workflow("step-before-plan-ready", "node-a")

        assert [item["node_id"] for item in result["dispatched"]] == ["node-a"]
        assert [item["job_id"] for item in dispatcher.dispatched] == ["job-a"]
    finally:
        scheduler.close()


def test_step_dispatches_selected_node_even_when_plan_start_is_in_future() -> None:
    """明确的单步目标不应受 CP-SAT 预测开始时间的等待门禁影响。"""

    dispatcher = RecordingDispatcher()
    scheduler = EdgeScheduler(
        dispatcher=dispatcher,
        time_planner=_PlanStartsLater(),
    )
    spec = WorkflowSpec(
        workflow_id="step-starts-later",
        nodes=[
            WorkflowNode(
                id="node-a",
                job_id="job-a",
                device_id="device-a",
                action_name="run",
                action_type="goal",
                param={},
            )
        ],
        run_mode="step",
    )

    try:
        scheduler.submit_workflow(spec)
        result = scheduler.step_workflow("step-starts-later", "node-a")

        assert [item["node_id"] for item in result["dispatched"]] == ["node-a"]
        assert [item["job_id"] for item in dispatcher.dispatched] == ["job-a"]
    finally:
        scheduler.close()


def test_step_dispatches_selected_node_when_timing_is_unknown() -> None:
    """未知预测时长不能吞掉用户明确选择的单步派发。"""

    dispatcher = RecordingDispatcher()
    scheduler = EdgeScheduler(dispatcher=dispatcher, time_planner=_PlanNotReady())
    scheduler._estimator = _UnknownTimingEstimator()  # type: ignore[assignment]
    spec = WorkflowSpec(
        workflow_id="step-unknown-timing",
        nodes=[
            WorkflowNode(
                id="node-a",
                job_id="job-a",
                device_id="device-a",
                action_name="run",
                action_type="goal",
                param={"duration": "not-a-number"},
            )
        ],
        run_mode="step",
    )

    try:
        scheduler.submit_workflow(spec)
        result = scheduler.step_workflow("step-unknown-timing", "node-a")

        assert [item["node_id"] for item in result["dispatched"]] == ["node-a"]
        assert [item["job_id"] for item in dispatcher.dispatched] == ["job-a"]
        assert result["dispatched"][0]["estimated_s"] == 42.0
    finally:
        scheduler.close()
