"""资源占用区间的运行时连续取锁回归。"""

from __future__ import annotations

from unilabos.app.scheduler.dispatch import RecordingDispatcher
from unilabos.app.scheduler.models import WorkflowEdge, WorkflowNode, WorkflowSpec
from unilabos.app.scheduler.service import EdgeScheduler
from unilabos.workflow.resource_lock_plan import (
    bind_station_resource_plan,
    compile_template_resource_plan,
    serialize_resource_plan,
)


def _plan(*node_ids: str) -> dict[str, object]:
    """构造一个跨连续节点持有同一设备资源的 bound 计划。"""

    graph = {
        "workflow_uuid": "workflow-interval-runtime",
        "nodes": [
            {"uuid": node_id, "resource_defaults": ["robot"]}
            for node_id in node_ids
        ],
        "edges": [
            {
                "source_node_uuid": source,
                "target_node_uuid": target,
            }
            for source, target in zip(node_ids, node_ids[1:])
        ],
    }
    template = compile_template_resource_plan(graph)
    bound = bind_station_resource_plan(
        template,
        {"robot": {"canonical_key": "/devices/shared", "kind": "device"}},
    )
    return serialize_resource_plan(bound)


def _spec(
    workflow_id: str,
    *,
    node_ids: list[str],
    priority: str,
) -> WorkflowSpec:
    nodes = [
        WorkflowNode(
            id=node_id,
            device_id="shared",
            action_name="run",
            action_type="goal",
            param={},
        )
        for node_id in node_ids
    ]
    plan = _plan(*node_ids)
    interval_by_node = {
        node_id: [
            str(interval["interval_id"])
            for interval in plan["intervals"]
            if node_id in interval["node_uuids"]
        ]
        for node_id in node_ids
    }
    plan_id = str(plan["plan_id"])
    for node in nodes:
        node.resource_plan_id = plan_id
        node.resource_interval_ids = interval_by_node[node.id]
        node.resource_acquire_set_id = next(
            (
                str(acquire_set["acquire_set_id"])
                for acquire_set in plan["acquire_sets"]
                if acquire_set["node_uuid"] == node.id
            ),
            "",
        )
    edges = [
        WorkflowEdge(
            uuid=f"{source}->{target}",
            source_node_id=source,
            target_node_id=target,
        )
        for source, target in zip(node_ids, node_ids[1:])
    ]
    return WorkflowSpec(
        workflow_id=workflow_id,
        nodes=nodes,
        edges=edges,
        priority=priority,
        resource_plan=plan,
    )


def test_continuous_interval_wins_over_higher_priority_waiter() -> None:
    """同一连续区间的后继 Job 必须先于其他 Task 插入。"""

    dispatcher = RecordingDispatcher()
    scheduler = EdgeScheduler(dispatcher=dispatcher)
    first = scheduler.submit_workflow(
        _spec("task-a", node_ids=["a-1", "a-2"], priority="normal")
    )
    scheduler.submit_workflow(
        _spec("task-b", node_ids=["b-1"], priority="urgent")
    )

    assert [item["node_id"] for item in dispatcher.dispatched] == ["a-1"]

    continuation = scheduler.on_job_finished(
        first["dispatched"][0]["job_id"],
        success=True,
    )

    assert [item["node_id"] for item in continuation["dispatched"]] == ["a-2"]
    assert [item["node_id"] for item in dispatcher.dispatched] == ["a-1", "a-2"]


def test_parallel_continuation_branches_are_serialized_on_one_resource() -> None:
    """同一区间的并行分支不能在同一轮同时绕过设备互斥。"""

    dispatcher = RecordingDispatcher()
    scheduler = EdgeScheduler(dispatcher=dispatcher)
    graph = {
        "workflow_uuid": "workflow-interval-branch",
        "nodes": [
            {"uuid": node_id, "resource_defaults": ["robot"]}
            for node_id in ("root", "left", "right")
        ],
        "edges": [
            {"source_node_uuid": "root", "target_node_uuid": "left"},
            {"source_node_uuid": "root", "target_node_uuid": "right"},
        ],
    }
    plan = serialize_resource_plan(
        bind_station_resource_plan(
            compile_template_resource_plan(graph),
            {"robot": {"canonical_key": "/devices/shared", "kind": "device"}},
        )
    )
    nodes = [
        WorkflowNode(
            id=node_id,
            device_id="shared",
            action_name="run",
            action_type="goal",
            param={},
            resource_plan_id=str(plan["plan_id"]),
            resource_interval_ids=[
                str(interval["interval_id"])
                for interval in plan["intervals"]
                if node_id in interval["node_uuids"]
            ],
            resource_acquire_set_id=next(
                (
                    str(item["acquire_set_id"])
                    for item in plan["acquire_sets"]
                    if item["node_uuid"] == node_id
                ),
                "",
            ),
        )
        for node_id in ("root", "left", "right")
    ]
    spec = WorkflowSpec(
        workflow_id="task-branch",
        nodes=nodes,
        edges=[
            WorkflowEdge(uuid="root-left", source_node_id="root", target_node_id="left"),
            WorkflowEdge(uuid="root-right", source_node_id="root", target_node_id="right"),
        ],
        resource_plan=plan,
    )
    first = scheduler.submit_workflow(spec)
    scheduler.on_job_finished(first["dispatched"][0]["job_id"], success=True)

    continuation_nodes = [
        item["node_id"] for item in dispatcher.dispatched if item["node_id"] in {"left", "right"}
    ]
    assert len(continuation_nodes) == 1


def test_restore_rebuilds_persisted_interval_holder_before_successor_dispatch() -> None:
    """进程重启后从已成功 Job 的持久元数据恢复连续所有权。"""

    dispatcher = RecordingDispatcher()
    scheduler = EdgeScheduler(dispatcher=dispatcher)
    spec = _spec("task-restore", node_ids=["a-1", "a-2"], priority="normal")
    plan = spec.resource_plan or {}
    interval_ids = [str(item["interval_id"]) for item in plan["intervals"]]
    restored = scheduler.restore_workflow(
        spec,
        {"a-1": {"restored": True}},
        restored_interval_handoffs=[
            {
                "node_id": "a-1",
                "job_id": "job-a1",
                "resource_interval_ids": interval_ids,
                "resource_interval_ids_by_lock": {
                    "/devices/shared": interval_ids,
                },
            }
        ],
    )

    assert [item["node_id"] for item in restored["dispatched"]] == ["a-2"]
    assert [item["node_id"] for item in dispatcher.dispatched] == ["a-2"]
