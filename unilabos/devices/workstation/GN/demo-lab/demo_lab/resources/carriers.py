"""仓库与工艺位。available_sites 必须是 AST 可读取的字面量数组。"""

from __future__ import annotations

from collections.abc import Iterable

from pylabrobot.resources import Coordinate, ResourceHolder
from unilabos.registry.decorators import resource
from unilabos.resources.itemized_carrier import BottleCarrier

SiteSpec = tuple[str, float, float, float, list[str]]

SOURCE_SITES = [
    {"index": "S01", "label": "S01", "description": "标准样品母液位", "position": {"x": 10.0, "y": 10.0, "z": 0.0}, "size": {"width": 90.0, "height": 90.0, "depth": 140.0}, "content_type": ["stock_vial"], "visible": True},
    {"index": "E01", "label": "E01", "description": "空分装瓶位", "position": {"x": 110.0, "y": 10.0, "z": 0.0}, "size": {"width": 90.0, "height": 90.0, "depth": 140.0}, "content_type": ["aliquot_vial"], "visible": True},
]
TIP_SITES = [
    {"index": "T01", "label": "T01", "description": "吸头盒位", "position": {"x": 10.0, "y": 10.0, "z": 0.0}, "size": {"width": 90.0, "height": 90.0, "depth": 140.0}, "content_type": ["tip_box"], "visible": True},
]
BALANCE_SITES = [
    {"index": "BAL1", "label": "BAL1", "description": "称量位", "position": {"x": 0.0, "y": 0.0, "z": 0.0}, "size": {"width": 90.0, "height": 90.0, "depth": 140.0}, "content_type": ["stock_vial"], "visible": True},
]
PIPETTE_SITES = [
    {"index": "PIP_S", "label": "PIP_S", "description": "移液母液位", "position": {"x": 0.0, "y": 0.0, "z": 0.0}, "size": {"width": 90.0, "height": 90.0, "depth": 140.0}, "content_type": ["stock_vial"], "visible": True},
    {"index": "PIP_T", "label": "PIP_T", "description": "移液分装位", "position": {"x": 120.0, "y": 0.0, "z": 0.0}, "size": {"width": 90.0, "height": 90.0, "depth": 140.0}, "content_type": ["aliquot_vial"], "visible": True},
]
CAP_SITES = [
    {"index": "CAP1", "label": "CAP1", "description": "封盖位", "position": {"x": 0.0, "y": 0.0, "z": 0.0}, "size": {"width": 90.0, "height": 90.0, "depth": 140.0}, "content_type": ["aliquot_vial"], "visible": True},
]
USED_SITES = [
    {"index": "U01", "label": "U01", "description": "已用母液位", "position": {"x": 10.0, "y": 10.0, "z": 0.0}, "size": {"width": 90.0, "height": 90.0, "depth": 140.0}, "content_type": ["stock_vial"], "visible": True},
]
FINISHED_SITES = [
    {"index": "F01", "label": "F01", "description": "成品分装位", "position": {"x": 10.0, "y": 10.0, "z": 0.0}, "size": {"width": 90.0, "height": 90.0, "depth": 140.0}, "content_type": ["aliquot_vial"], "visible": True},
]


def _rack(name: str, *, size: tuple[float, float, float], sites: Iterable[SiteSpec], category: str) -> BottleCarrier:
    holders: dict[str, ResourceHolder] = {}
    for label, x, y, z, content_type in sites:
        holder = ResourceHolder(name=f"{name}_{label}", size_x=90.0, size_y=90.0, size_z=140.0)
        holder.location = Coordinate(x=x, y=y, z=z)
        holder.unilabos_extra = {"content_type": content_type}
        holders[label] = holder
    carrier = BottleCarrier(name=name, size_x=size[0], size_y=size[1], size_z=size[2], sites=holders, category=category)
    carrier.num_items_x = len(holders)
    carrier.num_items_y = 1
    carrier.num_items_z = 1
    return carrier


@resource(id="source_rack", displayname="原料载架", category=["demo_lab", "warehouse"], description="母液瓶和空分装瓶来源。", available_sites=SOURCE_SITES)
def source_rack(name: str = "source_rack") -> BottleCarrier:
    return _rack(name, size=(220.0, 120.0, 160.0), category="source_rack", sites=(("S01", 10.0, 10.0, 0.0, ["stock_vial"]), ("E01", 110.0, 10.0, 0.0, ["aliquot_vial"])))


@resource(id="tip_warehouse", displayname="吸头仓", category=["demo_lab", "warehouse"], description="吸头盒来源。", available_sites=TIP_SITES)
def tip_warehouse(name: str = "tip_warehouse") -> BottleCarrier:
    return _rack(name, size=(140.0, 150.0, 160.0), category="tip_warehouse", sites=(("T01", 10.0, 10.0, 0.0, ["tip_box"]),))


@resource(id="balance_process_rack", displayname="称量工艺位", category=["demo_lab", "warehouse"], description="天平工站母液位。", available_sites=BALANCE_SITES)
def balance_process_rack(name: str = "balance_process_rack") -> BottleCarrier:
    return _rack(name, size=(120.0, 120.0, 160.0), category="balance_process_rack", sites=(("BAL1", 0.0, 0.0, 0.0, ["stock_vial"]),))


@resource(id="pipette_process_rack", displayname="移液工艺位", category=["demo_lab", "warehouse"], description="移液母液位和分装位。", available_sites=PIPETTE_SITES)
def pipette_process_rack(name: str = "pipette_process_rack") -> BottleCarrier:
    return _rack(name, size=(240.0, 120.0, 160.0), category="pipette_process_rack", sites=(("PIP_S", 0.0, 0.0, 0.0, ["stock_vial"]), ("PIP_T", 120.0, 0.0, 0.0, ["aliquot_vial"])))


@resource(id="cap_process_rack", displayname="封盖工艺位", category=["demo_lab", "warehouse"], description="分装瓶封盖位。", available_sites=CAP_SITES)
def cap_process_rack(name: str = "cap_process_rack") -> BottleCarrier:
    return _rack(name, size=(120.0, 120.0, 160.0), category="cap_process_rack", sites=(("CAP1", 0.0, 0.0, 0.0, ["aliquot_vial"]),))


@resource(id="used_rack", displayname="已用母液区", category=["demo_lab", "warehouse"], description="称量分装后的母液回收位。", available_sites=USED_SITES)
def used_rack(name: str = "used_rack") -> BottleCarrier:
    return _rack(name, size=(140.0, 120.0, 160.0), category="used_rack", sites=(("U01", 10.0, 10.0, 0.0, ["stock_vial"]),))


@resource(id="finished_rack", displayname="成品区", category=["demo_lab", "warehouse"], description="封盖后的分装瓶回库位。", available_sites=FINISHED_SITES)
def finished_rack(name: str = "finished_rack") -> BottleCarrier:
    return _rack(name, size=(140.0, 120.0, 160.0), category="finished_rack", sites=(("F01", 10.0, 10.0, 0.0, ["aliquot_vial"]),))
