"""Fallback 排程也必须尊重连续持有区间。"""

from __future__ import annotations

from unilabos.app.scheduler.time_planning import (
    PlanningAlternative,
    PlanningHold,
    PlanningNode,
    PlanningSnapshot,
    _greedy_feasible_plan,
)


SHARED = ("/devices/shared",)
AWAY = ("/devices/away",)


def test_greedy_fallback_finishes_known_hold_before_foreign_rival() -> None:
    """已持有但有释放节点时，fallback 不能把竞争动作插进成员之间。"""

    first = ("holder", "first")
    middle = ("holder", "middle")
    last = ("holder", "last")
    foreign = ("rival", "foreign")
    snapshot = PlanningSnapshot(
        revision="fallback-known-release",
        time_origin=0.0,
        nodes=(
            PlanningNode(
                first,
                (PlanningAlternative("shared", 1000, SHARED),),
                priority=10.0,
            ),
            PlanningNode(
                middle,
                (PlanningAlternative("away", 5000, AWAY),),
                predecessors=(first,),
                priority=1.0,
            ),
            PlanningNode(
                last,
                (PlanningAlternative("shared", 1000, SHARED),),
                predecessors=(middle,),
                priority=1.0,
            ),
            PlanningNode(
                foreign,
                (PlanningAlternative("shared", 1000, SHARED),),
                priority=5.0,
            ),
        ),
        holds=(
            PlanningHold(
                "holder:scope",
                "holder",
                SHARED,
                members=(first, middle, last),
                already_held=True,
                release_known=True,
            ),
        ),
    )

    plan = _greedy_feasible_plan(snapshot, horizon=9000)

    assert plan is not None
    spans = {entry.key: (entry.start_ms, entry.end_ms) for entry in plan}
    assert spans[first] == (0, 1000)
    assert spans[middle] == (1000, 6000)
    assert spans[last] == (6000, 7000)
    assert spans[foreign][0] >= spans[last][1]


def test_greedy_fallback_allows_foreign_action_before_future_hold_starts() -> None:
    """尚未开始的 scope 不应把时间原点到首成员之间的区间全部封死。"""

    first = ("holder", "first")
    last = ("holder", "last")
    foreign = ("rival", "foreign")
    snapshot = PlanningSnapshot(
        revision="fallback-future-scope",
        time_origin=0.0,
        nodes=(
            PlanningNode(
                first,
                (PlanningAlternative("shared", 1000, SHARED),),
                priority=1.0,
            ),
            PlanningNode(
                last,
                (PlanningAlternative("shared", 1000, SHARED),),
                predecessors=(first,),
                priority=1.0,
            ),
            PlanningNode(
                foreign,
                (PlanningAlternative("shared", 1000, SHARED),),
                priority=10.0,
            ),
        ),
        holds=(
            PlanningHold(
                "holder:future-scope",
                "holder",
                SHARED,
                members=(first, last),
                already_held=False,
                release_known=True,
            ),
        ),
    )

    plan = _greedy_feasible_plan(snapshot, horizon=5000)

    assert plan is not None
    spans = {entry.key: (entry.start_ms, entry.end_ms) for entry in plan}
    assert spans[foreign] == (0, 1000)
    assert spans[first][0] >= spans[foreign][1]
