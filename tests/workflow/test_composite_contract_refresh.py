"""已发布子工作流合同自动替换的纯领域回归测试。"""

from __future__ import annotations

from copy import deepcopy
from uuid import UUID, uuid5

import pytest

from unilabos.workflow.composite_contract_refresh import (
    CompositeContractRefreshPending,
    refresh_published_composite_invocations,
)
from unilabos.workflow.composite_invocation import expand_composite_invocation

# 下面的固定 UUID 分别代表子定义、父定义、调用根、上游节点、发布合同和边界连线；
# 固定身份使测试能够直接证明自动替换前后的节点、连接点（Handle）和边 UUID
# 是否按规则保持。
CHILD_UUID = "10000000-0000-4000-8000-000000000001"
PARENT_UUID = "10000000-0000-4000-8000-000000000002"
INVOCATION_UUID = "10000000-0000-4000-8000-000000000003"
PROVIDER_UUID = "10000000-0000-4000-8000-000000000004"
CHILD_NODE_UUID = "10000000-0000-4000-8000-000000000005"
OLD_CONTRACT_UUID = "10000000-0000-4000-8000-000000000006"
NEW_CONTRACT_UUID = "10000000-0000-4000-8000-000000000007"
OLD_TEMPLATE_UUID = "10000000-0000-4000-8000-000000000008"
NEW_TEMPLATE_UUID = "10000000-0000-4000-8000-000000000009"
PROVIDER_HANDLE_UUID = "10000000-0000-4000-8000-000000000010"
EDGE_UUID = "10000000-0000-4000-8000-000000000011"


def _input(name: str, *, required: bool) -> dict:
    """构造一个发布合同输入描述符。

    参数：``name`` 是参数名，``required`` 决定是否必填。返回：规范测试描述符；
    可选参数附带默认值。异常：无。
    """

    descriptor = {"name": name, "schema": {"type": "string"}, "required": required}
    if not required:
        descriptor["default"] = ""
    return descriptor


def _contract(
    *,
    identity: str,
    template_uuid: str,
    revision: int,
    inputs: list[dict],
) -> dict:
    """构造足以完成确定性展开的不可变发布合同。

    参数：身份、模板 UUID、修订和输入描述符共同描述一个版本。返回：含冻结图、
    边界映射和空设备要求的合同。异常：无；测试调用方负责传入规范 UUID。
    """

    return {
        "uuid": identity,
        "workflow_uuid": CHILD_UUID,
        "workflow_revision": revision,
        "node_template_uuid": template_uuid,
        "name": "子工作流",
        "source_hash": f"sha256:{revision:064x}",
        "contract_digest": f"sha256:{revision + 10:064x}",
        "input_contract": {"version": 1, "parameters": inputs},
        "output_contract": {"version": 1, "outputs": []},
        "executor_requirements": [],
        "executor_binding_mapping": {},
        "boundary_mapping": {
            "target_mappings": {},
            "source_mappings": {},
            "structural_mappings": {
                "entry_targets": [],
                "completion_sources": [],
            },
        },
        "graph_snapshot": {
            "workflow": {
                "uuid": CHILD_UUID,
                "revision": revision,
                "workflow_type": "experiment_operation",
            },
            "nodes": [
                {
                    "uuid": CHILD_NODE_UUID,
                    "name": f"动作 {revision}",
                    "type": "compute",
                    "pose": {"x": 0, "y": 0},
                    "param": {},
                    "execution_policy": {},
                    "disabled": False,
                    "minimized": False,
                    "meta_data": {},
                }
            ],
            "edges": [],
        },
    }


def _handle_uuid(template_uuid: str, name: str) -> str:
    """按生产发布规则计算一个输入连接点 UUID。

    参数：``template_uuid`` 是发布模板身份，``name`` 是输入参数名。返回：确定性
    连接点（Handle）UUID。异常：模板身份非法时由 ``UUID`` 构造器抛出
    ``ValueError``。
    """

    return str(uuid5(UUID(template_uuid), f"published-handle:target:{name}"))


def _parent_graph(contract: dict) -> dict:
    """构造含一个上游节点和一个已展开子工作流调用的父图。

    参数：``contract`` 是待插入的旧发布合同。返回：带一条外部参数连线的完整父
    图。异常：合同无法展开时传播 ``CompositeInvocationInvalid``。
    """

    base = {
        "workflow": {"uuid": PARENT_UUID, "revision": 2},
        "nodes": [
            {
                "uuid": PROVIDER_UUID,
                "name": "上游",
                "type": "compute",
                "pose": {"x": 0, "y": 0},
                "param": {},
                "execution_policy": {},
                "disabled": False,
                "minimized": False,
                "meta_data": {},
            }
        ],
        "edges": [],
    }
    nodes, edges = expand_composite_invocation(
        parent_graph=base,
        contract=contract,
        invocation_uuid=INVOCATION_UUID,
        pose={"x": 300, "y": 100},
        param={"sample": "manual"},
        device_bindings={},
    )
    return {
        **base,
        "nodes": [*base["nodes"], *nodes],
        "edges": [
            *edges,
            {
                "uuid": EDGE_UUID,
                "source_node_uuid": PROVIDER_UUID,
                "source_handle_uuid": PROVIDER_HANDLE_UUID,
                "target_node_uuid": INVOCATION_UUID,
                "target_handle_uuid": _handle_uuid(OLD_TEMPLATE_UUID, "sample"),
                "meta_data": {},
            },
        ],
    }


def test_refresh_preserves_invocation_and_remaps_boundary_by_parameter_name() -> None:
    """兼容更新须保留调用身份和填写值，并按参数名迁移外部连线。

    参数：无。返回：无。异常：稳定身份、填写值、位置、合同 pin 或连线连接点
    任一未正确迁移时由断言暴露。
    """

    previous = _contract(
        identity=OLD_CONTRACT_UUID,
        template_uuid=OLD_TEMPLATE_UUID,
        revision=1,
        inputs=[_input("sample", required=True)],
    )
    current = _contract(
        identity=NEW_CONTRACT_UUID,
        template_uuid=NEW_TEMPLATE_UUID,
        revision=2,
        inputs=[_input("sample", required=True), _input("note", required=False)],
    )
    refreshed = refresh_published_composite_invocations(
        parent_graph=_parent_graph(previous),
        current_contract=current,
        load_contract=lambda _identity: previous,
        validate_bindings=lambda _requirements, _bindings: True,
    )

    root = next(
        node for node in refreshed.graph["nodes"] if node["uuid"] == INVOCATION_UUID
    )
    boundary = next(
        edge
        for edge in refreshed.graph["edges"]
        if edge["target_node_uuid"] == INVOCATION_UUID
    )
    assert refreshed.invocation_uuids == (INVOCATION_UUID,)
    assert root["param"] == {"sample": "manual", "note": ""}
    assert root["pose"] == {"x": 300, "y": 100}
    assert root["meta_data"]["unilab"]["composite"]["contract_uuid"] == (
        NEW_CONTRACT_UUID
    )
    assert boundary["target_handle_uuid"] == _handle_uuid(NEW_TEMPLATE_UUID, "sample")
    assert boundary["uuid"] != EDGE_UUID


def test_refresh_rejects_new_required_input_without_mutating_parent() -> None:
    """新增必填参数没有提供者时须保留父图原样并给出稳定诊断。

    参数：无。返回：无。异常：没有关闭式拒绝、诊断码变化或输入父图被就地修改
    时由断言暴露。
    """

    previous = _contract(
        identity=OLD_CONTRACT_UUID,
        template_uuid=OLD_TEMPLATE_UUID,
        revision=1,
        inputs=[_input("sample", required=True)],
    )
    current = _contract(
        identity=NEW_CONTRACT_UUID,
        template_uuid=NEW_TEMPLATE_UUID,
        revision=2,
        inputs=[_input("sample", required=True), _input("operator", required=True)],
    )
    parent = _parent_graph(previous)
    original = deepcopy(parent)

    with pytest.raises(CompositeContractRefreshPending) as raised:
        refresh_published_composite_invocations(
            parent_graph=parent,
            current_contract=current,
            load_contract=lambda _identity: previous,
            validate_bindings=lambda _requirements, _bindings: True,
        )

    assert raised.value.code == "composite_input_required"
    assert parent == original
