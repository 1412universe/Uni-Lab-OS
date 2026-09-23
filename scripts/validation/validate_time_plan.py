#!/usr/bin/env python3
"""连续资源与时间计划的组合验证；只使用记录执行器，不连接设备。

独立运行此脚本，不导入或修改现有测试。重点区分区间保管与动作直接使用。
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import json
from pathlib import Path
import sys
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from unilabos.app.scheduler.dispatch import RecordingDispatcher
from unilabos.app.scheduler.models import WorkflowEdge, WorkflowNode, WorkflowSpec
from unilabos.app.scheduler.service import EdgeScheduler
from unilabos.app.scheduler.time_planning import (
    CandidatePlan, PlannedNode, PlanningAlternative, PlanningCompletionTarget,
    PlanningHold, PlanningNode, PlanningSnapshot,
    RollingTimePlanner, TimePlanningConfig, solve_snapshot, validate_candidate,
)
from unilabos.app.scheduler.dag_state import WorkflowRun
from unilabos.app.scheduler.workflow_planning import build_workflow_planning_view
from unilabos.workflow.resource_lock_plan import (
    bind_station_resource_plan, compile_template_resource_plan, serialize_resource_plan,
)


class Clock:
    def __init__(self) -> None:
        self.value = 1000.0

    def __call__(self) -> float:
        return self.value


@contextmanager
def environment(*, busy: set[str] | None = None) -> Iterator[tuple[EdgeScheduler, RecordingDispatcher, Clock]]:
    clock, dispatcher = Clock(), RecordingDispatcher()
    planner = RollingTimePlanner(TimePlanningConfig(solve_seconds=0.5, refresh_seconds=0.05))
    scheduler = EdgeScheduler(dispatcher=dispatcher, clock=clock, time_planner=planner, external_busy_keys=busy)
    try:
        yield scheduler, dispatcher, clock
    finally:
        scheduler.close()


def wait_for(scheduler: EdgeScheduler, predicate: Callable[[], bool], message: str,
             timeout: float = 8.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        scheduler.reschedule()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError(message + "\n" + json.dumps(scheduler.time_schedule_snapshot(), default=str, ensure_ascii=False))


def node_ids(dispatcher: RecordingDispatcher) -> list[str]:
    return [row["node_id"] for row in dispatcher.dispatched]


def job_id(dispatcher: RecordingDispatcher, node: str) -> str:
    return next(row["job_id"] for row in dispatcher.dispatched if row["node_id"] == node)


def workflow(identity: str, devices: Mapping[str, str | None],
             edges: Iterable[tuple[str, str]] = (), *, priority: int = 1,
             scope: bool = True, duration: float = 1) -> WorkflowSpec:
    edges = tuple(edges)
    graph = {
        "workflow_uuid": identity,
        "nodes": [{"uuid": node, "resource_defaults": [device] if device else []} for node, device in devices.items()],
        "edges": [{"source_node_uuid": a, "target_node_uuid": b} for a, b in edges],
    }
    if scope:
        graph["resources"] = [identity + "-batch"]
    plan = serialize_resource_plan(bind_station_resource_plan(
        compile_template_resource_plan(graph),
        {device: {"canonical_key": "/devices/" + device, "kind": "device"} for device in set(devices.values()) if device},
    ))
    nodes = [WorkflowNode(
        id=node, device_id=device or "", action_name="run", action_type="goal",
        param={"duration_s": duration}, resource_plan_id=str(plan["plan_id"]),
        resource_interval_ids=[str(item["interval_id"]) for item in plan["intervals"] if node in item["node_uuids"]],
        resource_acquire_set_id=next((str(item["acquire_set_id"]) for item in plan["acquire_sets"] if item["node_uuid"] == node), ""),
    ) for node, device in devices.items()]
    return WorkflowSpec(workflow_id=identity, nodes=nodes,
        edges=[WorkflowEdge(uuid=a + "->" + b, source_node_id=a, target_node_id=b) for a, b in edges],
        resource_plan=plan, submitted_at=1000, priority=priority)


def explicit_hold_containment() -> None:
    """只有显式身份关系可以豁免内外层重复占用；跨身份冲突仍保留。"""
    action = PlanningNode(("other", "free"), (PlanningAlternative("free", 100, ("/devices/free",)),))
    parent = PlanningHold("parent", "owner", ("/devices/A",))
    child = PlanningHold("child", "owner", ("/devices/A",), contained_in=("parent",))
    snapshot = PlanningSnapshot("contained", 1000, (action,), (parent, child))
    result = solve_snapshot(snapshot, TimePlanningConfig())
    assert result.validated and not validate_candidate(snapshot, result), result
    ordinary = replace(snapshot, revision="independent", holds=(parent, replace(child, contained_in=())))
    assert solve_snapshot(ordinary, TimePlanningConfig()).status == "INFEASIBLE"
    # 同一孩子可分别由多个实际持有父区间覆盖，不能携带未覆盖资源。
    parent_b = PlanningHold("parent-b", "owner", ("/devices/B",))
    merged_child = replace(child, resources=("/devices/A", "/devices/B"), contained_in=("parent", "parent-b"))
    merged = replace(snapshot, revision="two-parents", holds=(parent, parent_b, merged_child))
    assert solve_snapshot(merged, TimePlanningConfig()).validated
    bad = replace(snapshot, holds=(parent, replace(child, resources=("/devices/A", "/devices/B"))))
    try:
        solve_snapshot(bad, TimePlanningConfig())
    except ValueError:
        pass
    else:
        raise AssertionError("未覆盖的内层资源被误当作父区间共享")


def information_target_not_independent_tail() -> None:
    """长独立动作不应把尽早得到条件结果的 F_j 错换成整个可见子图 C_j。"""
    measure = PlanningNode(("partial", "measure"), (PlanningAlternative("shared", 10, ("/devices/shared",)),))
    independent = PlanningNode(("partial", "long-independent"), (PlanningAlternative("other", 100, ("/devices/other",)),))
    peer = PlanningNode(("peer", "peer"), (PlanningAlternative("shared", 20, ("/devices/shared",)),))
    snapshot = PlanningSnapshot("information-target", 1000, (measure, independent, peer),
        partial_tasks=("partial",), completion_targets=(
            PlanningCompletionTarget("partial", (measure.key,), "information", "if"),))
    plan = solve_snapshot(snapshot, TimePlanningConfig())
    assert plan.validated, plan
    entries = {node.key: node for node in plan.nodes}
    assert entries[measure.key].start_ms == 0 and entries[peer.key].start_ms == 10, plan
    assert independent.key in entries
    assert next(target for target in plan.completion_targets if target.task_id == "partial").node_keys == (measure.key,)
    legacy = solve_snapshot(replace(snapshot, revision="all-visible", completion_targets=()), TimePlanningConfig())
    assert next(node for node in legacy.nodes if node.key == peer.key).start_ms == 0


def condition_view_exposes_information_prefix_only() -> None:
    measure = WorkflowNode(id="measure", device_id="sensor", action_name="read", param={"duration_s": 1})
    condition = WorkflowNode(id="if", executor_kind="condition", param={
        "bindings": {"answer": {"kind": "node_result", "node_uuid": "measure"}},
        "branches": [
            {"label": "if", "condition": {"var": "answer"}, "node_uuids": ["yes"],
             "entry_node_uuids": ["yes"], "exit_node_uuids": ["yes"]},
            {"label": "else", "condition": None, "node_uuids": ["no"],
             "entry_node_uuids": ["no"], "exit_node_uuids": ["no"]},
        ]})
    run = WorkflowRun(WorkflowSpec(workflow_id="conditional", nodes=[measure, condition,
        WorkflowNode(id="yes", device_id="A", action_name="run"),
        WorkflowNode(id="no", device_id="A", action_name="run"),
        WorkflowNode(id="independent", device_id="B", action_name="run")], edges=[
        WorkflowEdge(uuid=a + b, source_node_id=a, target_node_id=b)
        for a, b in [("measure", "if"), ("if", "yes"), ("if", "no")]]))
    view = build_workflow_planning_view(run)
    assert {node.id for node in view.nodes} == {"measure", "independent"}
    assert len(view.information_prefixes) == 1
    assert view.information_prefixes[0].target_node_ids == ("measure",)
    assert view.information_prefixes[0].member_node_ids == ("measure",)


def prior_plan_is_soft_and_respects_absolute_time() -> None:
    a = PlanningNode(("a", "a"), (PlanningAlternative("shared", 1000, ("/devices/shared",)),))
    b = PlanningNode(("b", "b"), (PlanningAlternative("shared", 1000, ("/devices/shared",)),))
    c = PlanningNode(("c", "choice"), (
        PlanningAlternative("one", 1000, ("/devices/one",)),
        PlanningAlternative("two", 1000, ("/devices/two",))))
    long = PlanningNode(("c", "long"), (PlanningAlternative("long", 5000, ("/devices/long",)),))
    previous = CandidatePlan("old", 1000, "OPTIMAL", (
        PlannedNode(a.key, "shared", 1000, 2000, ("/devices/shared",)),
        PlannedNode(b.key, "shared", 0, 1000, ("/devices/shared",)),
        PlannedNode(c.key, "two", 2000, 3000, ("/devices/two",)),
        PlannedNode(long.key, "long", 0, 5000, ("/devices/long",)),
    ), validated=True)
    snapshot = PlanningSnapshot("new", 1000, (a, b, c, long), previous_plan=previous)
    result = solve_snapshot(snapshot, TimePlanningConfig(solve_seconds=1))
    assert result.validated and result.objective_stage == "stability_shift", result
    assert {node.key: (node.start_ms, node.device_id) for node in result.nodes} == {
        node.key: (node.start_ms, node.device_id) for node in previous.nodes}, result
    shifted = solve_snapshot(replace(snapshot, revision="shifted", time_origin=1000.5), TimePlanningConfig(solve_seconds=1))
    assert next(node for node in shifted.nodes if node.key == c.key).start_ms == 1500, shifted
    urgent = solve_snapshot(replace(snapshot, revision="urgent", nodes=(replace(a, priority=10), b, c, long)),
                            TimePlanningConfig(solve_seconds=1))
    assert next(node for node in urgent.nodes if node.key == a.key).start_ms == 0, urgent


def competing_new_open_scopes_are_deferred() -> None:
    """多个未开始且释放未知的区间竞争时，选择一组而非让整轮无解。"""
    def conditional(identity: str, priority: int) -> WorkflowSpec:
        measure, control, yes, no = [identity + suffix for suffix in ("-measure", "-if", "-yes", "-no")]
        spec = workflow(identity, {measure: "shared", control: None, yes: "shared", no: "shared"},
                        [(measure, control), (control, yes), (control, no)], priority=priority)
        decision = next(node for node in spec.nodes if node.id == control)
        decision.executor_kind = "condition"
        decision.param = {"bindings": {"answer": {"kind": "node_result", "node_uuid": measure}},
            "branches": [
                {"label": "if", "condition": {"var": "answer"}, "node_uuids": [yes],
                 "entry_node_uuids": [yes], "exit_node_uuids": [yes]},
                {"label": "else", "condition": None, "node_uuids": [no],
                 "entry_node_uuids": [no], "exit_node_uuids": [no]},
            ]}
        return spec
    busy = {"/devices/shared"}
    with environment(busy=busy) as (scheduler, dispatcher, clock):
        scheduler.submit_workflow(conditional("ordinary", 1))
        scheduler.submit_workflow(conditional("urgent", 10))
        scheduler.submit_workflow(workflow("free", {"independent": "independent"}, scope=False))
        assert "ordinary-measure" not in node_ids(dispatcher) and "urgent-measure" not in node_ids(dispatcher)
        busy.clear()
        wait_for(scheduler, lambda: {"urgent-measure", "independent"} <= set(node_ids(dispatcher)),
                 "开放区间竞争阻塞了高优先级前缀或独立工作")
        assert "ordinary-measure" not in node_ids(dispatcher)
        targets = scheduler.time_schedule_snapshot()["input"]["completion_targets"]
        target = next(item for item in targets if item["task_id"] == "urgent")
        assert target["kind"] == "information" and target["node_keys"] == (("urgent", "urgent-measure"),), target
        clock.value += 1
        scheduler.on_job_finished(job_id(dispatcher, "urgent-measure"), True, ret_value=True)
        wait_for(scheduler, lambda: "urgent-yes" in node_ids(dispatcher), "选路后未继续原连续区间")
        assert "ordinary-measure" not in node_ids(dispatcher)
        clock.value += 1
        scheduler.on_job_finished(job_id(dispatcher, "urgent-yes"), True)
        wait_for(scheduler, lambda: "ordinary-measure" in node_ids(dispatcher), "原连续区间释放后未重选等待前缀")


def close_suppresses_late_solver_and_timer_wakeup() -> None:
    """关闭等待有限求解退出，完成回调和已登记定时器都不能再次唤醒调度。"""
    entered, release = threading.Event(), threading.Event()
    wakes = []
    snapshot = PlanningSnapshot("closing", 1000, ())
    def pending_solve(snapshot: PlanningSnapshot, config: TimePlanningConfig) -> CandidatePlan:
        entered.set()
        assert release.wait(3)
        return CandidatePlan(snapshot.revision, snapshot.time_origin, "OPTIMAL", validated=True)
    with patch("unilabos.app.scheduler.time_planning.solve_snapshot", pending_solve):
        planner = RollingTimePlanner()
        planner.bind_wakeup(lambda: wakes.append("wake"))
        planner.request(snapshot)
        assert entered.wait(2)
        planner.wake_after(0.1)
        closing = threading.Thread(target=planner.close)
        closing.start()
        deadline = time.monotonic() + 2
        while not planner._closed and time.monotonic() < deadline:
            time.sleep(0.001)
        assert planner._closed
        release.set()
        closing.join(2)
        assert not closing.is_alive()
        planner.wake_after(0.02)
        time.sleep(0.12)
        assert not wakes, wakes


def two_tasks_hold_until_actual_release() -> None:
    """较高优先级的新任务不能插进已经取得的连续区间。"""
    with environment() as (scheduler, dispatcher, clock):
        scheduler.submit_workflow(workflow("owner", {"a1": "shared", "a2": "shared"}, [("a1", "a2")]))
        wait_for(scheduler, lambda: node_ids(dispatcher) == ["a1"], "首个连续动作未启动")
        scheduler.submit_workflow(workflow("urgent", {"b1": "shared"}, priority=10))
        clock.value += 1
        scheduler.on_job_finished(job_id(dispatcher, "a1"), True)
        wait_for(scheduler, lambda: "a2" in node_ids(dispatcher), "连续后继被外来急单阻塞")
        assert node_ids(dispatcher) == ["a1", "a2"], node_ids(dispatcher)
        assert scheduler._interval_resource_holders or scheduler._inflight
        # 已超预测结束仍不允许急单进入。实际完成报告才有权解除占用。
        clock.value += 3
        scheduler.reschedule()
        assert "b1" not in node_ids(dispatcher)
        scheduler.on_job_finished(job_id(dispatcher, "a2"), True)
        wait_for(scheduler, lambda: "b1" in node_ids(dispatcher), "实际释放后急单未恢复")


def same_scope_independent_actions_parallel() -> None:
    """固定的在途动作只占自己的直接设备，不独占整个作用域保管集合。"""
    with environment() as (scheduler, dispatcher, clock):
        scheduler.submit_workflow(workflow("parallel-scope",
            {"entry": "loader", "left": "A", "right": "B", "exit": "loader"},
            [("entry", "left"), ("entry", "right"), ("left", "exit"), ("right", "exit")], duration=10))
        wait_for(scheduler, lambda: "entry" in node_ids(dispatcher), "作用域入口未启动")
        clock.value += 10
        scheduler.on_job_finished(job_id(dispatcher, "entry"), True)
        wait_for(scheduler, lambda: {"left", "right"} <= set(node_ids(dispatcher)), "不同设备的兄弟动作被误串行")
        assert "exit" not in node_ids(dispatcher)
        # 再次快照时两者已固定；仍应得到合法计划，实际占用不包含兄弟设备。
        wait_for(scheduler, lambda: scheduler.time_schedule_snapshot()["latest"] is not None
                 and scheduler.time_schedule_snapshot()["latest"]["validated"], "并行在途动作使模型无解")
        for job in scheduler._inflight.values():
            if job.node_id in {"left", "right"}:
                keys = scheduler._time_scheduling._inflight_active_keys(scheduler, job)
                assert keys == {"/devices/A" if job.node_id == "left" else "/devices/B"}, keys
        assert all(node_ids(dispatcher).count(node) == 1 for node in ["entry", "left", "right"])


def overdue_continuous_holds_preserve_independent_progress() -> None:
    """超时动作的真实占用包含于原区间，阻塞共享资源但不阻塞独立任务。"""
    with environment() as (scheduler, dispatcher, clock):
        scheduler.submit_workflow(workflow("owner", {"slow": "A", "tail": "B"}, [("slow", "tail")]))
        wait_for(scheduler, lambda: "slow" in node_ids(dispatcher), "超时场景首动作未启动")
        clock.value += 5
        scheduler.submit_workflow(workflow("blocked", {"competitor": "B"}, scope=False))
        scheduler.submit_workflow(workflow("independent", {"free": "C"}, scope=False))
        wait_for(scheduler, lambda: "free" in node_ids(dispatcher), "超时连续区间阻塞了独立任务")
        assert "competitor" not in node_ids(dispatcher) and "tail" not in node_ids(dispatcher)
        view = scheduler.time_schedule_snapshot()["input"]
        unknown = [hold for hold in view["holds"] if hold["hold_id"].startswith("unknown:") and hold["owner_task"] == "owner"]
        assert unknown and any(hold["contained_in"] for hold in unknown), view
        assert any(hold["owner_task"] == "owner" and "/devices/B" in hold["resources"]
                   and not hold["release_known"] for hold in view["holds"]), view
        scheduler.on_job_finished(job_id(dispatcher, "slow"), True)
        wait_for(scheduler, lambda: "tail" in node_ids(dispatcher), "迟到的实际完成后连续尾部未恢复")
        assert "competitor" not in node_ids(dispatcher)
        clock.value += 1
        scheduler.on_job_finished(job_id(dispatcher, "tail"), True)
        wait_for(scheduler, lambda: "competitor" in node_ids(dispatcher), "区间实际退出后竞争任务未恢复")


def main() -> None:
    report: list[dict[str, str | float]] = []
    for case in (explicit_hold_containment, two_tasks_hold_until_actual_release,
                 same_scope_independent_actions_parallel, overdue_continuous_holds_preserve_independent_progress,
                 information_target_not_independent_tail, condition_view_exposes_information_prefix_only,
                 prior_plan_is_soft_and_respects_absolute_time, competing_new_open_scopes_are_deferred,
                 close_suppresses_late_solver_and_timer_wakeup):
        started = time.monotonic()
        case()
        report.append({"case": case.__name__, "status": "passed", "seconds": round(time.monotonic() - started, 3)})
    print(json.dumps({"status": "passed", "cases": report}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
