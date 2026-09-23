"""时间规划对已证明 ResourceSlot 透传的静态参数快照。"""

from unilabos.app.scheduler.dag_state import WorkflowRun
from unilabos.app.scheduler.dispatch import RecordingDispatcher
from unilabos.app.scheduler.models import (
    Handle,
    ReadyTask,
    WorkflowEdge,
    WorkflowNode,
    WorkflowSpec,
    node_from_dict,
    priority_weight,
)
from unilabos.app.scheduler.param_resolver import ParamResolveError
from unilabos.app.scheduler.service import EdgeScheduler
from unilabos.app.scheduler.time_planning import TimePlanningConfig, solve_snapshot
from unilabos.app.scheduler.time_scheduling import TimeSchedulingAdapter


def _chain(*, value_type: str = "ResourceSlot") -> WorkflowRun:
    nodes = [
        WorkflowNode(
            id="a",
            param={"resource": {"uuid": "material-1"}},
            material_passthrough_handles={"a-out": "a-in"},
        ),
        WorkflowNode(
            id="b",
            material_passthrough_handles={"b-out": "b-in"},
        ),
        WorkflowNode(id="c"),
    ]
    handles = [
        Handle("a-in", "", "resource", "resource", "a", "target", value_type),
        Handle("a-out", "executor", "resource", "resource", "a", "source", value_type),
        Handle("b-in", "", "resource", "resource", "b", "target", value_type),
        Handle("b-out", "executor", "resource", "resource", "b", "source", value_type),
        Handle("c-in", "", "resource", "resource", "c", "target", value_type),
    ]
    edges = [
        WorkflowEdge("a-b", "a", "b", "a-out", "b-in"),
        WorkflowEdge("b-c", "b", "c", "b-out", "c-in"),
    ]
    return WorkflowRun(WorkflowSpec("workflow", nodes, edges, handles))


def test_resource_slot_passthrough_resolves_entire_pending_chain() -> None:
    """未收到设备回执时，合同证明的 ResourceSlot 透传仍可用于时间快照。"""

    run = _chain()

    assert run.resolve_params("b") == {"resource": {"uuid": "material-1"}}
    assert run.resolve_params("c") == {"resource": {"uuid": "material-1"}}
    # 静态预测不写入真实回执，条件/运行态仍等待设备反馈。
    assert run.snapshot()["nodes"]["a"]["state"] == "pending"


def test_node_from_dict_reads_nested_and_top_level_passthrough_metadata() -> None:
    """执行计划两种元数据布局都能恢复静态透传合同。"""

    nested = node_from_dict(
        {
            "id": "nested",
            "meta_data": {
                "unilab": {
                    "material_passthrough_handles": {"output": "input"}
                }
            },
        }
    )
    top_level = node_from_dict(
        {
            "id": "top-level",
            "material_passthrough_handles": {"output": "input"},
            "meta_data": {
                "unilab": {
                    "material_passthrough_handles": {"old-output": "old-input"}
                }
            },
        }
    )

    assert nested.material_passthrough_handles == {"output": "input"}
    assert top_level.material_passthrough_handles == {"output": "input"}


def test_non_resource_edges_do_not_get_static_passthrough() -> None:
    """普通标量边不能因为同名字段而被猜测。"""

    run = _chain(value_type="string")

    try:
        run.resolve_params("b")
    except ParamResolveError:
        pass
    else:
        raise AssertionError("非 ResourceSlot 透传不应产生静态参数")


def test_unresolved_successor_is_kept_as_tentative_candidate_but_never_dispatched() -> None:
    """后继参数未回执时仍进入 CP-SAT 候选，但排序入口必须拒绝下发。"""

    nodes = [
        WorkflowNode(id="a", device_id="dev-a", action_name="run", action_type="goal"),
        WorkflowNode(id="b", device_id="dev-b", action_name="run", action_type="goal"),
    ]
    handles = [
        Handle("a-out", "executor", "resource", "resource", "a", "source", "ResourceSlot"),
        Handle("b-in", "", "resource", "resource", "b", "target", "ResourceSlot"),
    ]
    spec = WorkflowSpec(
        "tentative-workflow",
        nodes,
        [WorkflowEdge("a-b", "a", "b", "a-out", "b-in")],
        handles,
    )
    scheduler = EdgeScheduler(dispatcher=RecordingDispatcher())
    scheduler._workflows[spec.workflow_id] = WorkflowRun(spec)
    class _SynchronousPlanner:
        config = TimePlanningConfig()

        def wake_after(self, *_args):
            return None

        def request(self, snapshot):
            return solve_snapshot(snapshot, self.config)

    adapter = TimeSchedulingAdapter(_SynchronousPlanner())
    try:
        snapshot, _targets, _manual = adapter._build(scheduler, [], 0.0)
        by_key = {node.key: node for node in snapshot.nodes}
        assert (spec.workflow_id, "b") in by_key
        assert by_key[(spec.workflow_id, "b")].parameters_final is False
        assert snapshot.partial_tasks == (spec.workflow_id,)
        submitted = [
            ReadyTask(spec.workflow_id, node, priority_weight("normal"), 0.0)
            for node in nodes
        ]
        assert [task.node.id for task in adapter.order(scheduler, submitted)] == ["a"]
    finally:
        scheduler.close()
