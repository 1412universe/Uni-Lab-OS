"""
隐式并行的图归一化。

背景
----
作者源码模型里，同一层级的相邻语句**天然串行**（`authoring_ast._parse_sequence`
会把上一条的出口接到下一条的入口），编译器再把这些 `order_dependencies` 补成
真实的 `ready` 控制边。因此「画布上两个节点不连线」在后端并不表示并行：保存时
那条边会被自动补回来，编辑器提交的空边集合与候选图不符，直接报 3003。

作者源码表达并行的唯一形式是显式结构：

    with parallel():
        with group(name="branch_1"):
            a = dev.pick(...)
        with group(name="branch_2"):
            b = dev.place(...)

本模块把这件事自动化：**当调用方提交的图里存在若干互不相连的顶层节点时，
保存前先把它们包进上述并行结构**。于是前端只需要「不连线」，语义就变成并行，
用户不必知道 `parallel_scope` / `parallel_order` / group 节点这些内部细节。

判定规则（刻意保守）
--------------------
只处理**完全没有被任何边触及**的顶层节点（既不作为 source 也不作为 target）：

* 两个孤立节点 → 各成一个并行分支，同时开始。
* `A -> B` 这样的链 → 有边，不处理，仍是串行。
* 孤立节点与一条链并存 → 只把孤立节点彼此并行化；它们与链之间保持原有的
  先并行、后串行关系（链的首节点依赖并行块的出口）。

不做更激进的推断（例如按可达性拆分复杂链的分支），因为那会改变用户已有的
串行语义，风险远大于收益；需要更细粒度并行时应显式建立分组。

幂等性
------
已经处在并行结构里的节点带有 `parent_uuid`，不再是顶层节点；group 节点自身
类型为 `group`，同样被排除。因此反复保存不会重复包裹。
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

from unilabos.registry.template_identity import action_template_uuid

# 这些节点类型表达控制流或展示层级，不参与动作并行判定。
_NON_ACTION_TYPES = frozenset({"group", "condition", "repeat_until"})


def _is_action_node(node: Mapping[str, Any]) -> bool:
    """判断节点是否是一个可执行的顶层动作节点。

    参数：``node`` 是候选节点写模型。返回：它是动作节点且未挂在任何分组下时为
    真。异常：无。
    """

    node_type = str(node.get("type") or "").strip().lower()
    if node_type in _NON_ACTION_TYPES:
        return False
    if node.get("parent_uuid") is not None:
        return False
    # 没有模板身份的节点（纯展示/手写）不参与并行包裹。
    return bool(str(node.get("workflow_node_template_uuid") or "").strip())


def _next_free_order(nodes: Iterable[Mapping[str, Any]]) -> int:
    """返回比所有既有 ``authoring_source_order`` 更大的下一个可用序号。

    参数：``nodes`` 是当前全部节点。返回：非负整数。异常：无。
    """

    highest = -1
    for node in nodes:
        meta = node.get("meta_data")
        unilab = meta.get("unilab") if isinstance(meta, Mapping) else None
        value = unilab.get("authoring_source_order") if isinstance(unilab, Mapping) else None
        if isinstance(value, int) and not isinstance(value, bool) and value > highest:
            highest = value
    return highest + 1


def implicit_parallel_nodes(
    *,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    make_uuid,
    group_template_uuid: str | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """把互相之间没有任何连线的顶层动作节点包进 ``with parallel()``。

    参数：``nodes``/``edges`` 是调用方提交的完整图集合（不会被原地修改）；
    ``make_uuid`` 是零参可调用对象，用于生成 group 节点身份；
    ``group_template_uuid`` 可覆盖分组模板身份（默认由 host_node 派生）。
    返回：新的节点列表与「本次是否真的做了包裹」的布尔标记。

    异常：``make_uuid`` 返回空值或节点结构非法时抛 ``ValueError``——这两种情况
    属于调用方错误，静默降级会写出损坏的图。
    """

    touched: set[str] = set()
    for edge in edges:
        source = str(edge.get("source_node_uuid") or "")
        target = str(edge.get("target_node_uuid") or "")
        if source:
            touched.add(source)
        if target:
            touched.add(target)

    isolated = [
        node
        for node in nodes
        if _is_action_node(node) and str(node.get("uuid") or "") not in touched
    ]
    # 少于两个孤立节点无需并行：单个节点本来就没有并行伙伴。
    if len(isolated) < 2:
        return nodes, False

    template_uuid = group_template_uuid or action_template_uuid("host_node", "group")
    # 分支顺序、分组序号都从既有事实之外开始，避免与动作节点撞号
    # （后端会保护已应用动作的 authoring_source_order）。
    order_cursor = _next_free_order(nodes)
    scope = make_uuid()
    if not scope:
        raise ValueError("并行作用域身份生成失败")

    groups: list[dict[str, Any]] = []
    isolated_by_uuid = {str(node.get("uuid")): node for node in isolated}
    wrapped: list[dict[str, Any]] = []

    for index, node in enumerate(isolated):
        node_uuid = str(node.get("uuid") or "")
        if not node_uuid:
            raise ValueError("并行节点缺少稳定身份")
        group_uuid = make_uuid()
        if not group_uuid:
            raise ValueError("并行分组身份生成失败")
        label = str(node.get("name") or f"分支 {index + 1}").strip() or f"分支 {index + 1}"
        groups.append(
            {
                "uuid": group_uuid,
                "workflow_node_template_uuid": template_uuid,
                "parent_uuid": None,
                "name": label,
                "type": "group",
                "description": label,
                "disabled": False,
                "minimized": False,
                # 渲染器据 param.name 生成 group(name="...")，节点 name 只是画布标题。
                "param": {"name": f"branch_{index + 1}"},
                "pose": {"position": {"x": 0, "y": index * 160}},
                "meta_data": {
                    "unilab": {
                        "authoring_source_order": order_cursor + index,
                        "authoring_result_name": f"group_{order_cursor + index}",
                        "parallel_scope": scope,
                        "parallel_order": index,
                    }
                },
            }
        )
        copied = dict(node)
        meta = copied.get("meta_data")
        unilab = dict(meta.get("unilab") or {}) if isinstance(meta, Mapping) else {}
        unilab["parallel_scope"] = scope
        copied["meta_data"] = {**(meta or {}), "unilab": unilab}
        copied["parent_uuid"] = group_uuid
        wrapped.append(copied)
        isolated_by_uuid.pop(node_uuid, None)

    result: list[dict[str, Any]] = []
    wrapped_by_uuid = {str(node.get("uuid")): node for node in wrapped}
    for node in nodes:
        replacement = wrapped_by_uuid.get(str(node.get("uuid") or ""))
        result.append(replacement if replacement is not None else node)
    result.extend(groups)
    return result, True
