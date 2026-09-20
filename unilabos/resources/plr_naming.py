"""把 pylabrobot 孔板/枪头架子孔名对齐到当前父资源名。"""

from __future__ import annotations

from typing import Any

_ITEMIZED_MARKERS = ("_well_", "_tip_", "_tube_", "_spot_")


def retarget_itemized_child_names(resource: Any) -> int:
    """把子孔/TipSpot 名称改成以当前板名为前缀，避免同 deck 撞名。

    板从 ``PRCXI_96_DeepWell`` 改名为 ``PRCXI_96_DeepWell5`` 后，孔名
    ``PRCXI_96_DeepWell_well_A1`` 会改成 ``PRCXI_96_DeepWell5_well_A1``。
    返回实际改名次数。
    """

    changed = 0
    seen: set[int] = set()
    stack = [resource]
    while stack:
        node = stack.pop()
        if node is None:
            continue
        ident = id(node)
        if ident in seen:
            continue
        seen.add(ident)
        children = list(getattr(node, "children", None) or [])
        stack.extend(children)
        parent_name = str(getattr(node, "name", "") or "")
        if not parent_name or not children:
            continue
        if not _looks_itemized(node):
            continue
        prefix = f"{parent_name}_"
        for child in children:
            old = str(getattr(child, "name", "") or "")
            if not old or old.startswith(prefix):
                continue
            new = prefix + _itemized_child_suffix(old)
            if new == old:
                continue
            child.name = new
            changed += 1
    return changed


def _looks_itemized(node: Any) -> bool:
    category = str(getattr(node, "category", "") or "")
    if category in {"plate", "tip_rack", "well_plate", "tube_rack", "trough"}:
        return True
    return hasattr(node, "num_items") and bool(getattr(node, "children", None))


def _itemized_child_suffix(name: str) -> str:
    lowered = name
    for marker in _ITEMIZED_MARKERS:
        index = lowered.find(marker)
        if index >= 0:
            return name[index + 1 :]
    if "_" in name:
        return name.rsplit("_", 1)[-1]
    return name
