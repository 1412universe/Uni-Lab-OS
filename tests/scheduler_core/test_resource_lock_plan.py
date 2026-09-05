"""资源占用区间深模块的调度内核模块测试。"""

from __future__ import annotations

import pytest

from unilabos.registry.action_resource_contract import (
    ActionResourceContractError,
    normalize_action_resource_contract,
)
from unilabos.workflow.resource_lock_plan import (
    CanonicalResource,
    RESOURCE_PLAN_CAPABILITY,
    ResourcePlan,
    ResourcePlanError,
    ResourceRelation,
    bind_station_resource_plan,
    compile_template_resource_plan,
    deserialize_resource_plan,
    resource_plan_for_node,
    serialize_resource_plan,
    validate_resource_plan,
)


def test_direct_successor_reuses_resource_and_adds_one_atomic_resource() -> None:
    """直接后继继续使用同一资源时只新增缺少成员。"""

    plan = compile_template_resource_plan(
        {
            "workflow_uuid": "workflow-resource-1",
            "nodes": [
                {"uuid": "pick", "resource_defaults": ["robot"]},
                {"uuid": "place", "resource_defaults": ["robot", "station:photo"]},
            ],
            "edges": [{"source_node_uuid": "pick", "target_node_uuid": "place"}],
        }
    )

    pick_set = next(item for item in plan.acquire_sets if item.node_uuid == "pick")
    place_set = next(item for item in plan.acquire_sets if item.node_uuid == "place")
    robot = next(item for item in plan.resources if item.alias == "robot")
    photo = next(item for item in plan.resources if item.alias == "station:photo")

    assert pick_set.atomic is True
    assert pick_set.resource_ids == (robot.resource_id,)
    assert place_set.preheld_resource_ids == (robot.resource_id,)
    assert place_set.resource_ids == (photo.resource_id,)
    assert any(
        relation.from_resource_id == robot.resource_id
        and relation.to_resource_id == photo.resource_id
        for relation in plan.relations
    )
    assert len(
        [item for item in plan.intervals if item.resource_id == robot.resource_id]
    ) == 1


def test_root_scope_is_hard_boundary_and_serializes_deterministically() -> None:
    """根资源覆盖整段工作流并保留显式范围身份。"""

    graph = {
        "workflow_uuid": "workflow-root-1",
        "resources": ["station:photo_scrape"],
        "nodes": [
            {"uuid": "load", "resource_defaults": ["robot"]},
            {"uuid": "capture", "resource_defaults": ["camera"]},
        ],
        "edges": [{"source_node_uuid": "load", "target_node_uuid": "capture"}],
    }
    plan = compile_template_resource_plan(graph)
    serialized = serialize_resource_plan(plan)
    restored = deserialize_resource_plan(serialized)

    root = next(scope for scope in plan.scopes if scope.kind == "root")
    photo = next(item for item in plan.resources if item.alias == "station:photo_scrape")
    photo_interval = next(item for item in plan.intervals if item.resource_id == photo.resource_id)

    assert root.hard_boundary is True
    assert root.entry_node_uuid == "load"
    assert root.exit_node_uuid == "capture"
    assert photo_interval.scope_id == root.scope_id
    assert serialized == serialize_resource_plan(restored)
    assert resource_plan_for_node(plan, "capture")["plan_id"] == plan.plan_id


def test_parallel_sibling_resource_identity_is_not_merged() -> None:
    """并行兄弟的同名资源保留分支区间，不按根任务合并。"""

    plan = compile_template_resource_plan(
        {
            "workflow_uuid": "workflow-parallel-1",
            "nodes": [
                {
                    "uuid": "branch-a",
                    "resource_defaults": ["robot"],
                    "meta_data": {"unilab": {"parallel_scope": "parallel-1", "parallel_order": 0}},
                },
                {
                    "uuid": "branch-b",
                    "resource_defaults": ["robot"],
                    "meta_data": {"unilab": {"parallel_scope": "parallel-1", "parallel_order": 1}},
                },
            ],
        }
    )

    robot = next(item for item in plan.resources if item.alias == "robot")
    intervals = [item for item in plan.intervals if item.resource_id == robot.resource_id]

    assert len(intervals) == 2
    assert {item.branch_id for item in intervals} == {
        "parallel:parallel-1:0",
        "parallel:parallel-1:1",
    }
    assert not plan.relations


def test_bound_plan_requires_every_alias_and_adds_static_dag_capability() -> None:
    """bound 计划必须由外部事实完成所有别名绑定。"""

    plan = compile_template_resource_plan(
        {
            "workflow_uuid": "workflow-bind-1",
            "nodes": [{"uuid": "action", "resource_defaults": ["robot", "rail"]}],
        }
    )
    with pytest.raises(ResourcePlanError, match="资源别名未绑定"):
        bind_station_resource_plan(
            plan,
            {"robot": "00000000-0000-4000-8000-000000000001"},
        )

    bound = bind_station_resource_plan(
        plan,
        {
            "robot": {
                "instance_uuid": "00000000-0000-4000-8000-000000000001",
                "kind": "device",
            },
            "rail": {
                "instance_uuid": "00000000-0000-4000-8000-000000000002",
                "kind": "motion",
            },
        },
    )

    assert bound.binding_state == "bound"
    assert "static_resource_dag_v1" in bound.capabilities
    assert all(not item.canonical_key.startswith("symbol:") for item in bound.resources)


def test_action_contract_v2_normalizes_resource_roles_and_transfer_roles() -> None:
    """动作合同 v2 把旧设备参数与 rail/tool 角色冻结为稳定字段。"""

    contract = normalize_action_resource_contract(
        {
            "version": 2,
            "required_device_params": ["executor"],
            "resource_params": [{"param": "rail", "role": "motion"}],
            "transfer": {
                "material_param": "material",
                "target_owner_param": "target_owner",
                "target_site_uuid_param": "target_site",
                "gripper_site_role": "gripper",
                "motion_resource_roles": ["rail", "axis"],
                "tool_resource_roles": ["gripper"],
            },
        }
    )

    assert contract["version"] == 2
    assert contract["resource_params"] == [
        {"param": "executor", "role": "device"},
        {"param": "rail", "role": "motion"},
    ]
    assert contract["transfer"]["motion_resource_roles"] == ["rail", "axis"]
    assert contract["transfer"]["tool_resource_roles"] == ["gripper"]


def test_action_contract_v2_rejects_duplicate_resource_parameter() -> None:
    """同一动作参数不能被重复声明为不同资源角色。"""

    with pytest.raises(ActionResourceContractError) as caught:
        normalize_action_resource_contract(
            {
                "version": 2,
                "resource_params": [
                    {"param": "rail", "role": "motion"},
                    {"param": "rail", "role": "tool"},
                ],
            }
        )

    assert caught.value.code == "duplicate_resource_parameter"


def test_static_resource_cycle_is_rejected_with_stable_code() -> None:
    """关系图出现 A→B→A 时必须失败关闭。"""

    resources = (
        CanonicalResource("r-a", "device:a", "device", "a", "00000000-0000-4000-8000-000000000001"),
        CanonicalResource("r-b", "device:b", "device", "b", "00000000-0000-4000-8000-000000000002"),
    )
    plan = ResourcePlan(
        plan_id="plan-cycle",
        binding_state="bound",
        capabilities=(RESOURCE_PLAN_CAPABILITY, "static_resource_dag_v1"),
        resources=resources,
        relations=(
            ResourceRelation("edge-a-b", "r-a", "r-b"),
            ResourceRelation("edge-b-a", "r-b", "r-a"),
        ),
    )

    with pytest.raises(ResourcePlanError) as caught:
        validate_resource_plan(plan)

    assert caught.value.code == "resource_cycle"
    assert "r-a" in caught.value.message
    assert "r-b" in caught.value.message


def test_bound_plan_requires_static_dag_capability_at_module_boundary() -> None:
    """资源计划自身也不能把 bound 状态与静态证明能力拆开。"""

    resource = CanonicalResource(
        "r-a",
        "device:a",
        "device",
        "a",
        "00000000-0000-4000-8000-000000000001",
    )
    with pytest.raises(ResourcePlanError) as caught:
        validate_resource_plan(
            ResourcePlan(
                plan_id="bound-without-proof",
                binding_state="bound",
                resources=(resource,),
            )
        )

    assert caught.value.code == "unsupported_plan_capability"


def test_non_atomic_acquire_set_cannot_be_admitted() -> None:
    """任何非原子取得集合都必须在计划验证阶段拒绝。"""

    plan = compile_template_resource_plan(
        {
            "workflow_uuid": "workflow-atomic-1",
            "nodes": [{"uuid": "action", "resource_defaults": ["robot"]}],
        }
    )
    acquire_set = plan.acquire_sets[0]
    invalid = ResourcePlan(
        plan_id=plan.plan_id,
        resources=plan.resources,
        intervals=plan.intervals,
        acquire_sets=(
            type(acquire_set)(
                acquire_set_id=acquire_set.acquire_set_id,
                node_uuid=acquire_set.node_uuid,
                resource_ids=acquire_set.resource_ids,
                preheld_resource_ids=acquire_set.preheld_resource_ids,
                atomic=False,
            ),
        ),
    )

    with pytest.raises(ResourcePlanError) as caught:
        validate_resource_plan(invalid)

    assert caught.value.code == "non_atomic_acquire"


def test_root_and_lexical_scopes_compose_without_cross_boundary_merge() -> None:
    """根资源与词法资源可叠加，离开词法边界后不延长其区间。"""

    plan = compile_template_resource_plan(
        {
            "workflow_uuid": "workflow-nested-scope-1",
            "resources": ["batch"],
            "nodes": [
                {"uuid": "before", "resource_defaults": ["batch", "robot"]},
                {"uuid": "inside", "resource_defaults": ["batch", "robot"]},
                {"uuid": "after", "resource_defaults": ["batch", "robot"]},
            ],
            "edges": [
                {"source_node_uuid": "before", "target_node_uuid": "inside"},
                {"source_node_uuid": "inside", "target_node_uuid": "after"},
            ],
            "resource_scopes": [
                {
                    "scope_id": "lexical",
                    "kind": "with",
                    "resources": ["robot"],
                    "node_uuids": ["inside"],
                }
            ],
        }
    )

    robot = next(item for item in plan.resources if item.alias == "robot")
    robot_intervals = [
        item for item in plan.intervals if item.resource_id == robot.resource_id
    ]
    assert len(robot_intervals) == 3
    assert {item.node_uuids for item in robot_intervals} == {
        ("before",),
        ("inside",),
        ("after",),
    }
    robot_acquires = [
        item
        for item in plan.acquire_sets
        if robot.resource_id in item.resource_ids
    ]
    assert len(robot_acquires) == 3


def test_nested_same_resource_scope_keeps_one_outer_interval() -> None:
    """同一路径嵌套同名资源只保留外层实际占用区间。"""

    plan = compile_template_resource_plan(
        {
            "workflow_uuid": "workflow-nested-same-resource",
            "resource_scopes": [
                {
                    "scope_id": "outer",
                    "kind": "with",
                    "resources": ["robot"],
                    "node_uuids": ["a", "b", "c"],
                },
                {
                    "scope_id": "inner",
                    "kind": "with",
                    "resources": ["robot"],
                    "parent_scope_id": "outer",
                    "node_uuids": ["b"],
                },
            ],
            "nodes": [
                {"uuid": "a"},
                {"uuid": "b"},
                {"uuid": "c"},
            ],
            "edges": [
                {"source_node_uuid": "a", "target_node_uuid": "b"},
                {"source_node_uuid": "b", "target_node_uuid": "c"},
            ],
        }
    )

    robot = next(item for item in plan.resources if item.alias == "robot")
    intervals = [
        item for item in plan.intervals if item.resource_id == robot.resource_id
    ]
    assert len(intervals) == 1
    assert intervals[0].node_uuids == ("a", "b", "c")
    assert intervals[0].scope_id == "outer"
