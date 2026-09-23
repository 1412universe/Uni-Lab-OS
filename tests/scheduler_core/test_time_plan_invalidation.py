"""任务控制变更对时间计划候选的失效契约。

暂停、改参、取消都改变了"哪些动作还会被派发"这一业务事实。候选必须随之作废，
否则已取消任务的后续节点会继续留在 latest 里被读出，直到下一次 reconcile 换掉
revision 才消失——期间时间轴上画着一批永远不会执行的动作。
"""

from __future__ import annotations

from unilabos.app.scheduler.dag_state import WorkflowRun
from unilabos.app.scheduler.dispatch import RecordingDispatcher
from unilabos.app.scheduler.models import WorkflowNode, WorkflowSpec
from unilabos.app.scheduler.service import EdgeScheduler
from unilabos.app.scheduler.time_planning import RollingTimePlanner, TimePlanningConfig


class _RecordingPlanner(RollingTimePlanner):
    """真实滚动规划器，额外记录候选被作废的次数。"""

    def __init__(self) -> None:
        super().__init__(TimePlanningConfig(solve_seconds=0.5))
        self.invalidated = 0

    def invalidate(self) -> None:
        self.invalidated += 1
        super().invalidate()


def _scheduler(workflow_id: str) -> tuple[EdgeScheduler, _RecordingPlanner]:
    planner = _RecordingPlanner()
    scheduler = EdgeScheduler(dispatcher=RecordingDispatcher(), time_planner=planner)
    node = WorkflowNode(id="step-1", device_id="arm-1", action_name="run",
                        action_type="goal", param={})
    spec = WorkflowSpec(workflow_id=workflow_id, nodes=[node], edges=[])
    scheduler._workflows[workflow_id] = WorkflowRun(spec)
    return scheduler, planner


def test_cancel_invalidates_the_time_plan_candidate() -> None:
    """取消任务必须作废候选，与暂停同理。"""

    scheduler, planner = _scheduler("wf-cancel")
    try:
        assert planner.invalidated == 0

        assert scheduler.cancel_workflow("wf-cancel") is True

        assert planner.invalidated >= 1, "取消后候选仍然有效，被取消任务的节点会继续被读出"
    finally:
        scheduler.close()


def test_pause_invalidates_the_time_plan_candidate() -> None:
    """对照：暂停一直会作废候选，取消不应是例外。"""

    scheduler, planner = _scheduler("wf-pause")
    try:
        scheduler.pause_workflow("wf-pause")

        assert planner.invalidated >= 1
    finally:
        scheduler.close()


def test_unknown_workflow_cancel_does_not_touch_the_candidate() -> None:
    """不存在的任务取消是空操作，不应无故作废当前候选。"""

    scheduler, planner = _scheduler("wf-cancel")
    try:
        assert scheduler.cancel_workflow("wf-missing") is False

        assert planner.invalidated == 0
    finally:
        scheduler.close()
