"""隐式并行归一化：不连线即并行的图语义测试。"""

from __future__ import annotations

import itertools
from typing import Any

import pytest

from unilabos.workflow.parallel_normalization import implicit_parallel_nodes

_GROUP_TEMPLATE = "1b7c3bb4-63bc-570d-b03b-8828a74e9f75"


def _uuid_factory():
    """返回确定性 UUID 生成器，便于断言结构而不依赖随机值。"""

    counter = itertools.count(1)

    def make() -> str:
        value = next(counter)
        return f"9000000{value % 10}-0000-4000-8000-0000000000{value:02d}"

    return make


def _action(uuid: str, order: int, name: str = "execute_command", **extra: Any) -> dict[str, Any]:
    """构造一个顶层动作节点写模型。"""

    node: dict[str, Any] = {
        "uuid": uuid,
        "name": name,
        "type": "ILab",
        "workflow_node_template_uuid": "aaaa0000-0000-4000-8000-000000000001",
        "param": {},
        "description": name,
        "disabled": False,
        "minimized": False,
        "pose": {"position": {"x": 0, "y": 0}},
        "meta_data": {
            "unilab": {
                "authoring_source_order": order,
                "authoring_result_name": name,
            }
        },
    }
    node.update(extra)
    return node


def _edge(source: str, target: str) -> dict[str, Any]:
    """构造一条顺序依赖边。"""

    return {
        "uuid": f"e-{source[:4]}-{target[:4]}",
        "source_node_uuid": source,
        "target_node_uuid": target,
        "source_handle_uuid": "h-src",
        "target_handle_uuid": "h-tgt",
        "meta_data": {},
    }


def _groups(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """取出结果里的分组节点。"""

    return [node for node in nodes if node.get("type") == "group"]


def _by_uuid(nodes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """按 UUID 索引节点。"""

    return {str(node.get("uuid")): node for node in nodes}


def test_two_isolated_nodes_become_parallel_branches() -> None:
    """两个互不相连的顶层动作应被包成同一并行作用域下的两个分支。"""

    a = _action("10000000-0000-4000-8000-000000000001", 0, "pick")
    b = _action("10000000-0000-4000-8000-000000000002", 1, "place")

    result, changed = implicit_parallel_nodes(
        nodes=[a, b], edges=[], make_uuid=_uuid_factory()
    )

    assert changed is True
    groups = _groups(result)
    assert len(groups) == 2

    # 两个分支共享同一 parallel_scope —— 这是后端识别并行的唯一依据
    scopes = {group["meta_data"]["unilab"]["parallel_scope"] for group in groups}
    assert len(scopes) == 1
    assert next(iter(scopes))

    # 分支顺序稳定
    assert [group["meta_data"]["unilab"]["parallel_order"] for group in groups] == [0, 1]

    # 动作被挂到各自分支下，且带上同一作用域
    indexed = _by_uuid(result)
    action_a = indexed[a["uuid"]]
    action_b = indexed[b["uuid"]]
    assert action_a["parent_uuid"] == groups[0]["uuid"]
    assert action_b["parent_uuid"] == groups[1]["uuid"]
    assert action_a["meta_data"]["unilab"]["parallel_scope"] == next(iter(scopes))
    assert action_b["meta_data"]["unilab"]["parallel_scope"] == next(iter(scopes))


def test_connected_nodes_are_left_alone() -> None:
    """有连线的节点不属于孤立集合，保持原有串行语义。"""

    a = _action("10000000-0000-4000-8000-000000000011", 0, "pick")
    b = _action("10000000-0000-4000-8000-000000000012", 1, "place")

    result, changed = implicit_parallel_nodes(
        nodes=[a, b], edges=[_edge(a["uuid"], b["uuid"])], make_uuid=_uuid_factory()
    )

    assert changed is False
    assert result == [a, b]
    assert _groups(result) == []


def test_single_isolated_node_is_not_wrapped() -> None:
    """只有一个孤立节点时没有并行伙伴，不应生成分组。"""

    a = _action("10000000-0000-4000-8000-000000000021", 0, "pick")

    result, changed = implicit_parallel_nodes(
        nodes=[a], edges=[], make_uuid=_uuid_factory()
    )

    assert changed is False
    assert result == [a]


def test_group_and_control_nodes_are_excluded() -> None:
    """分组、条件、循环节点不参与动作并行判定。"""

    group = {
        "uuid": "10000000-0000-4000-8000-000000000031",
        "name": "G",
        "type": "group",
        "param": {"name": "g"},
        "meta_data": {},
    }
    condition = {
        "uuid": "10000000-0000-4000-8000-000000000032",
        "name": "C",
        "type": "condition",
        "meta_data": {},
    }
    a = _action("10000000-0000-4000-8000-000000000033", 0, "pick")

    result, changed = implicit_parallel_nodes(
        nodes=[group, condition, a], edges=[], make_uuid=_uuid_factory()
    )

    # 只有一个真正的孤立动作 → 不包裹
    assert changed is False
    assert result == [group, condition, a]


def test_already_wrapped_nodes_are_idempotent() -> None:
    """已在分组下的节点不再是顶层节点，重复保存不会叠加包裹。"""

    parent = "10000000-0000-4000-8000-000000000041"
    a = _action(
        "10000000-0000-4000-8000-000000000042",
        0,
        "pick",
        parent_uuid=parent,
        meta_data={"unilab": {"authoring_source_order": 0, "parallel_scope": "scope-x"}},
    )
    b = _action(
        "10000000-0000-4000-8000-000000000043",
        1,
        "place",
        parent_uuid=parent,
        meta_data={"unilab": {"authoring_source_order": 1, "parallel_scope": "scope-x"}},
    )

    result, changed = implicit_parallel_nodes(
        nodes=[a, b], edges=[], make_uuid=_uuid_factory()
    )

    assert changed is False
    assert result == [a, b]


def test_group_order_starts_after_existing_orders() -> None:
    """分组序号必须落在既有动作序号之后，否则后端报「节点顺序不能重复」。"""

    a = _action("10000000-0000-4000-8000-000000000051", 0, "pick")
    b = _action("10000000-0000-4000-8000-000000000052", 7, "place")

    result, changed = implicit_parallel_nodes(
        nodes=[a, b], edges=[], make_uuid=_uuid_factory()
    )

    assert changed is True
    group_orders = [
        group["meta_data"]["unilab"]["authoring_source_order"] for group in _groups(result)
    ]
    assert all(order > 7 for order in group_orders)


def test_group_carries_static_name_for_renderer() -> None:
    """渲染器据 param.name 生成 group(name=...)，缺它会报展示分组缺少静态 name。"""

    a = _action("10000000-0000-4000-8000-000000000061", 0, "pick")
    b = _action("10000000-0000-4000-8000-000000000062", 1, "place")

    result, _ = implicit_parallel_nodes(
        nodes=[a, b], edges=[], make_uuid=_uuid_factory()
    )

    names = [group["param"]["name"] for group in _groups(result)]
    assert names == ["branch_1", "branch_2"]


def test_no_edges_are_added() -> None:
    """并行分支之间不得产生任何依赖边，否则运行期会串行化。"""

    a = _action("10000000-0000-4000-8000-000000000071", 0, "pick")
    b = _action("10000000-0000-4000-8000-000000000072", 1, "place")

    result, _ = implicit_parallel_nodes(
        nodes=[a, b], edges=[], make_uuid=_uuid_factory()
    )

    # 函数只返回节点；这里确认分组节点没有自带任何边结构
    assert all("source_node_uuid" not in group for group in _groups(result))


def test_missing_uuid_raises() -> None:
    """身份生成失败时必须失败关闭，不能写出损坏的图。"""

    a = _action("10000000-0000-4000-8000-000000000081", 0, "pick")
    b = _action("10000000-0000-4000-8000-000000000082", 1, "place")

    with pytest.raises(ValueError):
        implicit_parallel_nodes(nodes=[a, b], edges=[], make_uuid=lambda: "")


def test_isolated_nodes_mixed_with_chain_only_wrap_isolated() -> None:
    """孤立节点与既有链并存时，只并行化孤立节点，链保持串行。"""

    chain_a = _action("10000000-0000-4000-8000-000000000091", 0, "pick")
    chain_b = _action("10000000-0000-4000-8000-000000000092", 1, "place")
    lone_1 = _action("10000000-0000-4000-8000-000000000093", 2, "stir")
    lone_2 = _action("10000000-0000-4000-8000-000000000094", 3, "weigh")

    result, changed = implicit_parallel_nodes(
        nodes=[chain_a, chain_b, lone_1, lone_2],
        edges=[_edge(chain_a["uuid"], chain_b["uuid"])],
        make_uuid=_uuid_factory(),
    )

    assert changed is True
    indexed = _by_uuid(result)
    # 链上的节点保持顶层、无父级
    assert indexed[chain_a["uuid"]].get("parent_uuid") is None
    assert indexed[chain_b["uuid"]].get("parent_uuid") is None
    # 两个孤立动作被包裹
    assert indexed[lone_1["uuid"]]["parent_uuid"] is not None
    assert indexed[lone_2["uuid"]]["parent_uuid"] is not None
    assert len(_groups(result)) == 2


# ── 作者源码顺序号缺失时的兜底 ───────────────────────────────────────────


def _order_node(uuid: str, order=None, node_type: str = "ILab") -> dict[str, Any]:
    """构造一个可选带 `authoring_source_order` 的节点。"""

    node: dict[str, Any] = {"uuid": uuid, "name": uuid, "type": node_type, "meta_data": {}}
    if order is not None:
        node["meta_data"]["unilab"] = {"authoring_source_order": order}
    return node


def test_missing_source_order_is_backfilled_for_structured_graph() -> None:
    """含分组的图里有节点缺顺序号时必须补号，而不是直接拒绝保存。

    这曾是「并行结构 + 编辑器新建节点」无法保存的直接原因：新建节点不写
    `authoring_source_order`，而结构化图要求全量具备。
    """

    from unilabos.workflow.authoring_python import _authoring_ordered_nodes

    nodes = {
        "g": _order_node("g", 0, "group"),
        "a": _order_node("a"),          # 缺号
        "b": _order_node("b"),          # 缺号
    }

    ordered = _authoring_ordered_nodes(nodes, [])

    # 全部节点都被排进来，没有抛错
    assert {node["uuid"] for node in ordered} == {"g", "a", "b"}


def test_backfill_never_reverses_execution_dependency() -> None:
    """上游节点缺号、下游已有靠后序号时，补齐不能把顺序颠倒。"""

    from unilabos.workflow.authoring_python import _authoring_ordered_nodes

    nodes = {"a": _order_node("a"), "b": _order_node("b", 5)}
    edges = [{"source_node_uuid": "a", "target_node_uuid": "b"}]

    ordered = _authoring_ordered_nodes(nodes, edges)

    assert [node["uuid"] for node in ordered] == ["a", "b"]


def test_backfill_preserves_existing_relative_order() -> None:
    """已有顺序号的节点相对次序必须保持不变。"""

    from unilabos.workflow.authoring_python import _authoring_ordered_nodes

    nodes = {
        "x": _order_node("x", 0),
        "y": _order_node("y", 1),
        "z": _order_node("z"),          # 缺号，应排在已有序号之后
    }

    ordered = _authoring_ordered_nodes(nodes, [])

    assert [node["uuid"] for node in ordered] == ["x", "y", "z"]


def test_duplicate_source_order_still_rejected() -> None:
    """顺序号重复仍属非法——那是真正的数据冲突，不该被兜底掩盖。"""

    from unilabos.workflow.authoring_python import _authoring_ordered_nodes

    nodes = {"a": _order_node("a", 0), "b": _order_node("b", 0)}

    with pytest.raises(Exception) as excinfo:
        _authoring_ordered_nodes(nodes, [])
    assert "顺序不能重复" in str(excinfo.value)


def test_cycle_still_detected_during_backfill() -> None:
    """兜底路径同样要做环检测，不能把依赖环当成正常图放过去。"""

    from unilabos.workflow.authoring_python import _authoring_ordered_nodes

    nodes = {"a": _order_node("a"), "b": _order_node("b")}
    edges = [
        {"source_node_uuid": "a", "target_node_uuid": "b"},
        {"source_node_uuid": "b", "target_node_uuid": "a"},
    ]

    with pytest.raises(Exception) as excinfo:
        _authoring_ordered_nodes(nodes, edges)
    assert "环" in str(excinfo.value)
