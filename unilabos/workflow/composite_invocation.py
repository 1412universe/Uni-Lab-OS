"""已发布工作流合同在父图中的确定性组合展开。"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from unilabos.workflow.authoring_identity import (
    authoring_edge_uuid,
    expanded_node_uuid,
)


class CompositeInvocationInvalid(ValueError):
    """组合调用输入或冻结合同不满足安全展开条件。"""


def _remap_boundary_value(value: Any, node_uuid_map: Mapping[str, str]) -> Any:
    """递归替换边界映射里的来源节点 UUID。"""

    if isinstance(value, list):
        return [_remap_boundary_value(item, node_uuid_map) for item in value]
    if not isinstance(value, Mapping):
        return deepcopy(value)
    result = {
        str(key): _remap_boundary_value(item, node_uuid_map)
        for key, item in value.items()
    }
    node_uuid = result.get("workflow_node_uuid")
    if isinstance(node_uuid, str) and node_uuid in node_uuid_map:
        result["workflow_node_uuid"] = node_uuid_map[node_uuid]
    return result


def _remap_nested_composite_metadata(
    meta_data: Mapping[str, Any],
    node_uuid_map: Mapping[str, str],
) -> dict[str, Any]:
    """复制节点元数据，并重写嵌套组合调用的私有边界引用。"""

    result = deepcopy(dict(meta_data))
    unilab = result.get("unilab")
    composite = unilab.get("composite") if isinstance(unilab, dict) else None
    if not isinstance(composite, dict):
        return result
    for key in ("target_mappings", "source_mappings", "structural_mappings"):
        if key in composite:
            composite[key] = _remap_boundary_value(composite[key], node_uuid_map)
    return result


def _translated_pose(
    pose: Mapping[str, Any],
    *,
    minimum_x: float,
    minimum_y: float,
) -> dict[str, Any]:
    """把子图顶层节点平移到调用节点内部并保留其他布局字段。"""

    result = deepcopy(dict(pose))
    for key, minimum, offset in (
        ("x", minimum_x, 40.0),
        ("y", minimum_y, 64.0),
    ):
        raw = result.get(key, 0)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise CompositeInvocationInvalid(f"节点 pose.{key} 必须是数值")
        result[key] = raw - minimum + offset
    return result


def _top_level_origin(nodes: list[Mapping[str, Any]]) -> tuple[float, float]:
    """返回子图顶层节点坐标的左上原点。"""

    coordinates: list[tuple[float, float]] = []
    for node in nodes:
        if node.get("parent_uuid") is not None:
            continue
        pose = node.get("pose") or {}
        if not isinstance(pose, Mapping):
            raise CompositeInvocationInvalid("节点 pose 必须是对象")
        x = pose.get("x", 0)
        y = pose.get("y", 0)
        if (
            isinstance(x, bool)
            or isinstance(y, bool)
            or not isinstance(x, (int, float))
            or not isinstance(y, (int, float))
        ):
            raise CompositeInvocationInvalid("节点坐标必须是数值")
        coordinates.append((float(x), float(y)))
    if not coordinates:
        return 0.0, 0.0
    return min(item[0] for item in coordinates), min(item[1] for item in coordinates)


def _references_workflow(nodes: list[Mapping[str, Any]], workflow_uuid: str) -> bool:
    """判断冻结子树是否已经引用待插入的父工作流。"""

    for node in nodes:
        meta_data = node.get("meta_data")
        unilab = meta_data.get("unilab") if isinstance(meta_data, Mapping) else None
        composite = unilab.get("composite") if isinstance(unilab, Mapping) else None
        if (
            isinstance(composite, Mapping)
            and composite.get("child_workflow_uuid") == workflow_uuid
        ):
            return True
    return False


def expand_composite_invocation(
    *,
    parent_graph: Mapping[str, Any],
    contract: Mapping[str, Any],
    invocation_uuid: str,
    pose: Mapping[str, Any],
    param: Mapping[str, Any],
    device_bindings: Mapping[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """展开一个冻结发布合同并返回要追加到父图的节点与边。

    参数：``parent_graph`` 是当前父图，``contract`` 含私有冻结快照；调用身份、
    画布位置、参数和设备绑定来自公共命令。返回确定性调用根、内部节点和内部边；
    自引用、递归、身份碰撞或损坏引用抛 ``CompositeInvocationInvalid``。
    """

    parent_workflow = parent_graph["workflow"]
    parent_workflow_uuid = str(parent_workflow["uuid"])
    child_workflow_uuid = str(contract["workflow_uuid"])
    if child_workflow_uuid == parent_workflow_uuid:
        raise CompositeInvocationInvalid("工作流不能调用自身")
    snapshot = contract.get("graph_snapshot")
    if not isinstance(snapshot, Mapping):
        raise CompositeInvocationInvalid("发布合同缺少冻结图")
    source_nodes = snapshot.get("nodes")
    source_edges = snapshot.get("edges")
    if not isinstance(source_nodes, list) or not isinstance(source_edges, list):
        raise CompositeInvocationInvalid("发布合同冻结图损坏")
    if _references_workflow(source_nodes, parent_workflow_uuid):
        raise CompositeInvocationInvalid("组合调用会形成递归引用")

    node_uuid_map: dict[str, str] = {}
    for node in source_nodes:
        if not isinstance(node, Mapping) or not isinstance(node.get("uuid"), str):
            raise CompositeInvocationInvalid("发布合同包含无身份节点")
        source_uuid = str(node["uuid"])
        if source_uuid in node_uuid_map:
            raise CompositeInvocationInvalid("发布合同包含重复节点身份")
        node_uuid_map[source_uuid] = expanded_node_uuid(invocation_uuid, source_uuid)

    existing_node_uuids = {
        str(item["uuid"])
        for item in parent_graph.get("nodes", [])
        if isinstance(item, Mapping) and isinstance(item.get("uuid"), str)
    }
    insertion_node_uuids = {invocation_uuid, *node_uuid_map.values()}
    if existing_node_uuids & insertion_node_uuids:
        raise CompositeInvocationInvalid("组合调用节点身份与父图冲突")

    boundary_mapping = contract.get("boundary_mapping") or {}
    remapped_boundary = _remap_boundary_value(boundary_mapping, node_uuid_map)
    root = {
        "uuid": invocation_uuid,
        "workflow_node_template_uuid": contract["node_template_uuid"],
        "name": contract["name"],
        "type": "workflow",
        "pose": deepcopy(dict(pose)),
        "param": deepcopy(dict(param)),
        "execution_policy": {},
        "disabled": False,
        "minimized": False,
        "description": "引用不可变已发布工作流合同；来源更新不会自动覆盖本次调用。",
        "meta_data": {
            "unilab": {
                "composite": {
                    "version": 1,
                    "contract_uuid": contract["uuid"],
                    "child_workflow_uuid": child_workflow_uuid,
                    "child_workflow_revision": contract["workflow_revision"],
                    "child_source_hash": contract["source_hash"],
                    "contract_digest": contract["contract_digest"],
                    "contract_compatibility": {
                        "inputs": contract["input_contract"]["inputs"],
                        "outputs": contract["output_contract"]["outputs"],
                    },
                    "executor_requirements": deepcopy(
                        contract["executor_requirements"]
                    ),
                    "device_bindings": dict(device_bindings),
                    **remapped_boundary,
                }
            }
        },
    }

    minimum_x, minimum_y = _top_level_origin(source_nodes)
    expanded_nodes = [root]
    for source in source_nodes:
        copied = deepcopy(dict(source))
        source_uuid = str(source["uuid"])
        copied.pop("create_time", None)
        copied.pop("update_time", None)
        copied.pop("workflow_uuid", None)
        copied.pop("status", None)
        copied["uuid"] = node_uuid_map[source_uuid]
        raw_parent = source.get("parent_uuid")
        if raw_parent is None:
            copied["parent_uuid"] = invocation_uuid
            copied["pose"] = _translated_pose(
                source.get("pose") or {},
                minimum_x=minimum_x,
                minimum_y=minimum_y,
            )
        elif str(raw_parent) in node_uuid_map:
            copied["parent_uuid"] = node_uuid_map[str(raw_parent)]
        else:
            raise CompositeInvocationInvalid("子节点引用了冻结图外的父节点")
        copied["meta_data"] = _remap_nested_composite_metadata(
            source.get("meta_data") or {},
            node_uuid_map,
        )
        requirement_key = contract["executor_binding_mapping"].get(source_uuid)
        if requirement_key is not None:
            copied["material_uuid"] = device_bindings[requirement_key]
        expanded_nodes.append(copied)

    expanded_edges: list[dict[str, Any]] = []
    existing_edge_uuids = {
        str(item["uuid"])
        for item in parent_graph.get("edges", [])
        if isinstance(item, Mapping) and isinstance(item.get("uuid"), str)
    }
    for source in source_edges:
        if not isinstance(source, Mapping):
            raise CompositeInvocationInvalid("发布合同包含非法边")
        source_node_uuid = node_uuid_map.get(str(source.get("source_node_uuid")))
        target_node_uuid = node_uuid_map.get(str(source.get("target_node_uuid")))
        if source_node_uuid is None or target_node_uuid is None:
            raise CompositeInvocationInvalid("发布合同的边引用冻结图外节点")
        edge_uuid = authoring_edge_uuid(
            workflow_uuid=parent_workflow_uuid,
            source_node_uuid=source_node_uuid,
            source_handle_uuid=str(source["source_handle_uuid"]),
            target_node_uuid=target_node_uuid,
            target_handle_uuid=str(source["target_handle_uuid"]),
        )
        if edge_uuid in existing_edge_uuids:
            raise CompositeInvocationInvalid("组合调用边身份与父图冲突")
        copied = deepcopy(dict(source))
        copied.pop("create_time", None)
        copied.pop("update_time", None)
        copied["uuid"] = edge_uuid
        copied["source_node_uuid"] = source_node_uuid
        copied["target_node_uuid"] = target_node_uuid
        expanded_edges.append(copied)
    return expanded_nodes, expanded_edges


__all__ = ["CompositeInvocationInvalid", "expand_composite_invocation"]
