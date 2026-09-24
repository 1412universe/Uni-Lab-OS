"""边界分支：时间刻度、快照关系、回退排程和后台生命周期。"""

from __future__ import annotations

import math
import threading
import time

import pytest

from unilabos.app.scheduler import time_planning
from unilabos.app.scheduler.time_planning import (
    CandidatePlan,
    PlannedNode,
    PlanningAlternative,
    PlanningHold,
    PlanningNode,
    PlanningSnapshot,
    RollingTimePlanner,
    TimePlanningConfig,
    _greedy_feasible_plan,
    solve_snapshot,
    ticks,
    validate_candidate,
)


RESOURCE = ("/devices/arm-1",)


def _node(node_id: str, *, task: str = "wf", duration: int = 1000,
          fixed: bool = False, predecessors=(), resources=RESOURCE) -> PlanningNode:
    return PlanningNode(
        (task, node_id),
        (PlanningAlternative("arm-1", duration, tuple(resources)),),
        tuple(predecessors),
        1.0,
        fixed,
        0.0 if fixed else None,
    )


def _snapshot(nodes=(), holds=(), *, revision="r", time_origin=0.0, previous_plan=None):
    return PlanningSnapshot(
        revision=revision,
        time_origin=time_origin,
        nodes=tuple(nodes),
        holds=tuple(holds),
        previous_plan=previous_plan,
    )


def _entry(node: PlanningNode, start: int = 0) -> PlannedNode:
    option = node.alternatives[0]
    return PlannedNode(node.key, option.device_id, start, start + option.duration_ms, option.resources)


@pytest.mark.parametrize("value", [-1.0, math.nan, math.inf, -math.inf])
def test_ticks_rejects_negative_and_nonfinite_seconds(value: float) -> None:
    with pytest.raises(ValueError, match="非负有限秒数"):
        ticks(value)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"solve_seconds": 0.0},
        {"aging_seconds": math.inf},
        {"retry_initial_seconds": 2.0, "retry_max_seconds": 1.0},
        {"max_nodes": True},
        {"max_alternatives": 1.5},
    ],
)
def test_time_planning_config_rejects_invalid_limits(kwargs) -> None:
    with pytest.raises(ValueError):
        TimePlanningConfig(**kwargs)


@pytest.mark.parametrize(
    "holds, reason",
    [
        (
            (
                PlanningHold("h", "wf", RESOURCE, (), True, False, ("p", "p")),
            ),
            "父身份重复",
        ),
        (
            (
                PlanningHold("p", "wf", RESOURCE, ()),
                PlanningHold("h", "wf", RESOURCE + ("/devices/arm-2",), (), True, False, ("p",)),
            ),
            "未被父区间覆盖",
        ),
        (
            (
                PlanningHold("p", "wf", RESOURCE, (), True, False, ("c",)),
                PlanningHold("c", "wf", RESOURCE, (), True, False, ("p",)),
            ),
            "包含关系存在循环",
        ),
    ],
)
def test_hold_relation_graph_rejects_duplicate_partial_and_cyclic_relations(holds, reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        solve_snapshot(_snapshot([_node("a")], holds), TimePlanningConfig(solve_seconds=1.0))


def test_validate_candidate_reports_invalid_hold_relations_before_entries() -> None:
    snapshot = _snapshot([_node("a")], (PlanningHold("h", "wf", RESOURCE),
                                         PlanningHold("h", "wf", RESOURCE)))
    assert validate_candidate(snapshot, CandidatePlan("r", 0.0, "OPTIMAL")) == (
        "连续占用身份重复，应先按实际同一持有记录合并",
    )


@pytest.mark.parametrize("holds", [
    (
        PlanningHold("parent", "wf", RESOURCE, (), True, False),
        PlanningHold("child", "wf", RESOURCE, (), True, False, ("parent",)),
    ),
    (
        PlanningHold("child", "wf", RESOURCE, (), True, False, ("parent",)),
        PlanningHold("parent", "wf", RESOURCE, (), True, False),
    ),
])
def test_nested_hold_pairs_are_independently_checked_in_candidate_validation(holds) -> None:
    snapshot = _snapshot([], holds)
    candidate = CandidatePlan("r", 0.0, "OPTIMAL")
    assert validate_candidate(snapshot, candidate) == ()
    assert solve_snapshot(snapshot, TimePlanningConfig(solve_seconds=1.0)).validated


def test_greedy_fallback_handles_future_scope_members_and_fixed_nodes() -> None:
    first = _node("first", fixed=True)
    second = _node("second")
    hold = PlanningHold("future", "wf", RESOURCE, (first.key, second.key), False, False)
    result = _greedy_feasible_plan(_snapshot([first, second], [hold]), horizon=5000)
    assert result is not None
    assert result[0].fixed is True


@pytest.mark.parametrize("release_known", [False, True])
def test_greedy_fallback_tracks_a_started_future_hold_while_waiting_for_a_member(
    release_known: bool,
) -> None:
    first = _node("first", fixed=True)
    blocked_member = _node("second", predecessors=(("wf", "missing"),))
    rival = _node("rival")
    hold = PlanningHold(
        "future",
        "wf",
        RESOURCE,
        (first.key, blocked_member.key),
        False,
        release_known,
    )
    # The rival is ready after the fixed member starts; the other member is
    # intentionally not ready, so fallback must conservatively track the hold
    # before discovering the unsatisfied dependency.
    assert _greedy_feasible_plan(_snapshot([first, blocked_member, rival], [hold]), 5000) is None


def test_greedy_fallback_returns_none_when_dependencies_never_become_ready() -> None:
    first = _node("first", predecessors=(("wf", "second"),))
    second = _node("second", predecessors=(("wf", "first"),))
    assert _greedy_feasible_plan(_snapshot([first, second]), horizon=5000) is None


def test_greedy_fallback_rejects_a_candidate_that_fails_independent_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(time_planning, "validate_candidate", lambda *_args: ("invalid",))
    assert _greedy_feasible_plan(_snapshot([_node("a")]), horizon=5000) is None


def test_stability_skips_a_previous_start_outside_the_integer_domain() -> None:
    node = _node("a")
    previous = CandidatePlan(
        "r",
        -2_000_000_000.0,
        "OPTIMAL",
        (_entry(node),),
        validated=True,
    )
    plan = solve_snapshot(
        _snapshot([node], previous_plan=previous),
        TimePlanningConfig(solve_seconds=1.0),
    )
    assert plan.validated


class _FeasibleThenUnknownSolver:
    """Small CP-SAT stand-in for deterministic budget/status edge tests."""

    created = 0

    def __init__(self) -> None:
        self.parameters = type("Parameters", (), {})()
        self.objective_value = 0.0
        self.best_objective_bound = 0.0
        type(self).created += 1

    def solve(self, _model):
        from ortools.sat.python import cp_model

        return cp_model.OPTIMAL if type(self).created == 1 else cp_model.UNKNOWN

    def boolean_value(self, _variable) -> bool:
        return True

    def value(self, variable) -> int:
        return 1000 if str(variable).startswith("e") else 0

    def status_name(self, _status) -> str:
        return "UNKNOWN"


def test_solve_snapshot_keeps_feasible_incumbent_when_budget_expires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ortools.sat.python import cp_model

    _FeasibleThenUnknownSolver.created = 0
    monkeypatch.setattr(cp_model, "CpSolver", _FeasibleThenUnknownSolver)
    moments = iter((0.0, 0.0, 1.0, 1.0))
    monkeypatch.setattr(time_planning.time, "monotonic", lambda: next(moments))
    plan = solve_snapshot(_snapshot([_node("a")]), TimePlanningConfig(solve_seconds=0.5))
    assert plan.status == "FEASIBLE"
    assert plan.validated


def test_solve_snapshot_keeps_incumbent_when_a_later_stage_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ortools.sat.python import cp_model

    _FeasibleThenUnknownSolver.created = 0
    monkeypatch.setattr(cp_model, "CpSolver", _FeasibleThenUnknownSolver)
    moments = iter((0.0, 0.0, 0.0, 0.0, 0.0, 0.0))
    monkeypatch.setattr(time_planning.time, "monotonic", lambda: next(moments))
    plan = solve_snapshot(_snapshot([_node("a")]), TimePlanningConfig(solve_seconds=0.5))
    assert plan.status == "FEASIBLE"
    assert plan.validated


class _FakeTimer:
    instances: list["_FakeTimer"] = []

    def __init__(self, delay: float, function) -> None:
        self.delay = delay
        self.function = function
        self.cancelled = False
        self.started = False
        self.instances.append(self)

    def start(self) -> None:
        self.started = True

    def cancel(self) -> None:
        self.cancelled = True


class _FailingExecutor:
    def submit(self, _function):
        raise RuntimeError("submit failed")

    def shutdown(self, **_kwargs) -> None:
        return None


def test_planner_handles_wakeup_callback_failure_and_closed_requests() -> None:
    planner = RollingTimePlanner(TimePlanningConfig(solve_seconds=0.1))
    planner.bind_wakeup(lambda: (_ for _ in ()).throw(RuntimeError("wake failed")))
    try:
        planner._notify_wakeup()
    finally:
        planner.close()

    assert planner.request(_snapshot()) is None
    planner._notify_wakeup()
    planner.invalidate()


def test_planner_arms_retry_when_same_revision_has_no_retry_timer() -> None:
    planner = RollingTimePlanner(TimePlanningConfig(retry_initial_seconds=0.01, retry_max_seconds=0.02))
    snapshot = _snapshot(revision="retry")
    try:
        with planner._lock:
            planner._requested_revision = snapshot.revision
            planner._latest = CandidatePlan(snapshot.revision, 0.0, "ERROR")
            planner._retry_revision = ""
        planner.request(snapshot)
        assert planner.snapshot()["retry"]["revision"] == "retry"
    finally:
        planner.close()


def test_retry_timer_guard_and_submit_failure_restore_single_flight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeTimer.instances.clear()
    monkeypatch.setattr(time_planning.threading, "Timer", _FakeTimer)
    snapshot = _snapshot(revision="retry")

    planner = RollingTimePlanner(TimePlanningConfig(retry_initial_seconds=0.1))
    try:
        with planner._lock:
            planner._requested_revision = snapshot.revision
            planner._arm_retry_locked(snapshot)
            timer = _FakeTimer.instances[-1]
            planner._arm_retry_locked(snapshot)
            assert timer.cancelled
            planner._retry_generation += 1
        timer.function()
    finally:
        planner.close()

    planner = RollingTimePlanner(TimePlanningConfig(retry_initial_seconds=0.1))
    planner._executor.shutdown(wait=True)
    planner._executor = _FailingExecutor()
    try:
        with planner._lock:
            planner._requested_revision = snapshot.revision
            planner._arm_retry_locked(snapshot)
            timer = _FakeTimer.instances[-1]
        with pytest.raises(RuntimeError, match="submit failed"):
            timer.function()
        assert planner._active is False
    finally:
        planner.close()


def test_planner_turns_solver_exception_into_error_result(monkeypatch: pytest.MonkeyPatch) -> None:
    done = threading.Event()

    def fail(*_args):
        done.set()
        raise RuntimeError("solver failed")

    monkeypatch.setattr(time_planning, "solve_snapshot", fail)
    planner = RollingTimePlanner(TimePlanningConfig(solve_seconds=0.1))
    try:
        planner.request(_snapshot(revision="error"))
        assert done.wait(1.0)
        deadline = time.monotonic() + 1.0
        while planner.snapshot()["latest"] is None and time.monotonic() < deadline:
            time.sleep(0.005)
        assert planner.snapshot()["latest"]["status"] == "ERROR"
    finally:
        planner.close()


def test_planner_discards_a_result_for_a_revision_replaced_while_solving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = threading.Event()
    release = threading.Event()

    def slow(snapshot, _config):
        entered.set()
        release.wait(1.0)
        return CandidatePlan(snapshot.revision, snapshot.time_origin, "OPTIMAL", validated=True)

    monkeypatch.setattr(time_planning, "solve_snapshot", slow)
    planner = RollingTimePlanner(TimePlanningConfig(solve_seconds=0.1))
    try:
        planner.request(_snapshot(revision="old"))
        assert entered.wait(1.0)
        planner.request(_snapshot(revision="new"))
        release.set()
        deadline = time.monotonic() + 1.0
        while planner.snapshot()["latest"] is None and time.monotonic() < deadline:
            time.sleep(0.005)
        assert planner.snapshot()["discarded"] >= 1
        assert planner.snapshot()["latest"]["revision"] == "new"
    finally:
        release.set()
        planner.close()


def test_wake_after_replaces_later_timer_and_ignores_stale_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeTimer.instances.clear()
    monkeypatch.setattr(time_planning.threading, "Timer", _FakeTimer)
    calls: list[str] = []
    planner = RollingTimePlanner(TimePlanningConfig(solve_seconds=0.1))
    planner.bind_wakeup(lambda: calls.append("wake"))
    try:
        planner.wake_after(1.0)
        first = _FakeTimer.instances[-1]
        planner.wake_after(5.0)
        assert len(_FakeTimer.instances) == 1
        planner.wake_after(0.5)
        second = _FakeTimer.instances[-1]
        assert first.cancelled
        first.function()
        assert calls == []
        second.function()
        assert calls == ["wake"]
    finally:
        planner.close()


def test_wake_after_rejects_nonfinite_delay_and_close_cancels_timer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeTimer.instances.clear()
    monkeypatch.setattr(time_planning.threading, "Timer", _FakeTimer)
    planner = RollingTimePlanner(TimePlanningConfig(solve_seconds=0.1))
    with pytest.raises(ValueError, match="有限秒数"):
        planner.wake_after(math.inf)
    planner.wake_after(0.1)
    timer = _FakeTimer.instances[-1]
    planner.close()
    assert timer.cancelled
    planner.wake_after(0.1)
