"""资源计划进入执行计划和旧调度模型的模块测试。"""

from __future__ import annotations

import pytest

from unilabos.app.scheduler.models import node_from_dict, spec_from_dict
from unilabos.workflow.execution_plan import ExecutionPlanBuilder
from unilabos.workflow.resource_lock_plan import (
    RESOURCE_PLAN_CAPABILITY,
    ResourcePlan,
    bind_station_resource_plan,
    compile_template_resource_plan,
    serialize_resource_plan,
)
from unilabos.workflow.workflow_spec_compiler import (
    WorkflowSpecCompilationError,
    WorkflowSpecCompiler,
)


def _bound_plan() -> tuple[ResourcePlan, dict[str, object]]:
    """构造一个最小 bound 资源计划与序列化字典。"""

    template = compile_template_resource_plan(
        {
            "workflow_uuid": "workflow-spec-1",
            "nodes": [{"uuid": "action", "resource_defaults": ["robot"]}],
        }
    )
    bound = bind_station_resource_plan(
        template,
        {"robot": "00000000-0000-4000-8000-000000000001"},
    )
    return bound, serialize_resource_plan(bound)


def test_execution_plan_builder_projects_bound_resource_plan() -> None:
    """执行计划 seam 应把 bound 计划和节点区间身份一起投影。"""

    plan = ExecutionPlanBuilder._resource_plan(
        graph={
            "resources": ["robot"],
            "resource_bindings": {
                "robot": "00000000-0000-4000-8000-000000000001",
            },
        },
        planned_nodes=[{"uuid": "action", "resource_defaults": ["robot"]}],
        planned_edges=[],
    )

    assert plan is not None
    assert plan.binding_state == "bound"
    assert plan.plan_id
    assert plan.resources[0].canonical_key.startswith("resource:")


def test_execution_plan_builder_reads_v2_action_resource_params() -> None:
    """v2 Action 参数角色可通过任务绑定映射进入资源计划。"""

    plan = ExecutionPlanBuilder._resource_plan(
        graph={
            "resource_bindings": {
                "executor": "00000000-0000-4000-8000-000000000001"
            }
        },
        planned_nodes=[
            {
                "uuid": "action",
                "action_resource_contract": {
                    "version": 2,
                    "resource_params": [
                        {"param": "executor", "role": "device"}
                    ],
                },
            }
        ],
        planned_edges=[],
    )

    assert plan is not None
    assert plan.binding_state == "bound"
    assert plan.resources[0].alias == "executor"


def test_execution_plan_builder_accepts_root_and_lexical_resource_declarations() -> None:
    """执行计划 seam 同时接受工作流根资源与词法作用域。"""

    plan = ExecutionPlanBuilder._resource_plan(
        graph={
            "resources": ["batch"],
            "resource_scopes": [
                {
                    "scope_id": "lexical",
                    "kind": "with",
                    "resources": ["robot"],
                    "node_uuids": ["inside"],
                }
            ],
        },
        planned_nodes=[
            {"uuid": "before", "resource_defaults": ["batch"]},
            {"uuid": "inside", "resource_defaults": ["batch", "robot"]},
        ],
        planned_edges=[
            {"source_node_uuid": "before", "target_node_uuid": "inside"}
        ],
    )

    assert plan is not None
    assert {scope.scope_id for scope in plan.scopes} == {"root", "lexical"}


def test_execution_plan_scope_projection_ignores_authoring_group_nodes() -> None:
    """作者作用域包住展示 Group 时，计划只保留其真实执行子节点。"""

    plan = ExecutionPlanBuilder._resource_plan(
        graph={
            "workflow": {
                "meta_data": {
                    "unilab": {
                        "resource_scopes": [
                            {
                                "scope_id": "lexical",
                                "kind": "with",
                                "resources": ["robot"],
                                "entry_node_uuid": "group",
                                "exit_node_uuid": "inside",
                                "node_uuids": ["group", "inside"],
                            }
                        ]
                    }
                }
            }
        },
        planned_nodes=[{"uuid": "inside", "resource_defaults": ["robot"]}],
        planned_edges=[],
    )

    assert plan is not None
    scope = next(item for item in plan.scopes if item.scope_id == "lexical")
    assert scope.node_uuids == ("inside",)
    assert scope.entry_node_uuid == scope.exit_node_uuid == "inside"


def test_workflow_spec_compiler_rejects_template_resource_plan() -> None:
    """任务运行时不得绕过工站绑定直接消费 template 计划。"""

    template = compile_template_resource_plan(
        {
            "workflow_uuid": "workflow-spec-unbound",
            "nodes": [{"uuid": "action", "resource_defaults": ["robot"]}],
        }
    )
    task_snapshot = {
        "uuid": "00000000-0000-4000-8000-000000000099",
        "execution_plan": {
            "version": 1,
            "nodes": [],
            "edges": [],
            "handles": [],
            "resource_plan": serialize_resource_plan(template),
        },
    }

    with pytest.raises(WorkflowSpecCompilationError) as caught:
        WorkflowSpecCompiler().compile(task_snapshot, [])

    assert caught.value.code == "resource_plan_unbound"


def test_workflow_spec_compiler_rejects_dag_capability_without_plan() -> None:
    """静态无环能力不能脱离资源计划单独声明。"""

    task_snapshot = {
        "uuid": "00000000-0000-4000-8000-000000000097",
        "execution_plan": {
            "version": 1,
            "nodes": [],
            "edges": [],
            "handles": [],
            "capabilities": ["static_resource_dag_v1"],
        },
    }

    with pytest.raises(WorkflowSpecCompilationError) as caught:
        WorkflowSpecCompiler().compile(task_snapshot, [])

    assert caught.value.code == "invalid_resource_plan"


def test_workflow_spec_compiler_accepts_bound_resource_plan() -> None:
    """已绑定且含静态 DAG 能力的计划可进入 WorkflowSpec。"""

    _bound, serialized = _bound_plan()
    task_snapshot = {
        "uuid": "00000000-0000-4000-8000-000000000098",
        "execution_plan": {
            "version": 1,
            "nodes": [],
            "edges": [],
            "handles": [],
            "capabilities": [RESOURCE_PLAN_CAPABILITY],
            "resource_plan": serialized,
        },
    }

    spec = WorkflowSpecCompiler().compile(task_snapshot, [])

    assert spec.resource_plan == serialized


def test_scheduler_models_preserve_resource_plan_projection() -> None:
    """旧调度模型反序列化时保留节点与 WorkflowSpec 的计划身份。"""

    node = node_from_dict(
        {
            "id": "action",
            "resource_plan_id": "plan-1",
            "resource_interval_ids": ["interval-1"],
            "resource_acquire_set_id": "acquire-1",
        }
    )
    spec = spec_from_dict(
        {
            "workflow_id": "workflow-1",
            "nodes": [
                {
                    "id": "action",
                    "resource_plan_id": "plan-1",
                    "resource_interval_ids": ["interval-1"],
                    "resource_acquire_set_id": "acquire-1",
                }
            ],
            "resource_plan": {"plan_id": "plan-1", "binding_state": "bound"},
        }
    )

    assert node.resource_plan_id == "plan-1"
    assert node.resource_interval_ids == ["interval-1"]
    assert node.resource_acquire_set_id == "acquire-1"
    assert spec.resource_plan == {"plan_id": "plan-1", "binding_state": "bound"}
