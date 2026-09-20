"""PRCXI 台面耗材。类型与 GN-20260803 ``registry/resources/prcxi/*.yaml`` 对齐。"""

from __future__ import annotations

from unilabos.devices.workstation.GN.liquid_handling.prcxi.prcxi_labware import (
    PRCXI9300Plate,
    PRCXI9300TipRack,
    PRCXI_1250uL_Tips as _PRCXI_1250uL_Tips,
    PRCXI_96_DeepWell as _PRCXI_96_DeepWell,
    PRCXI_nest_1_troughplate as _PRCXI_nest_1_troughplate,
    PRCXI_trash as _PRCXI_trash,
)
from unilabos.registry.decorators import resource


@resource(
    id="prcxi_nest_1_troughplate",
    displayname="储液槽",
    category=["gn_lab", "prcxi", "plates"],
    description="储液槽 (Code: ZX-58-10000)",
)
def prcxi_nest_1_troughplate(name: str = "PRCXI_nest_1_troughplate") -> PRCXI9300Plate:
    return _PRCXI_nest_1_troughplate(name)


@resource(
    id="prcxi_96_deepwell",
    displayname="96深孔板",
    category=["gn_lab", "prcxi", "plates"],
    description="96深孔板 (Code: q2)",
)
def prcxi_96_deepwell(name: str = "PRCXI_96_DeepWell") -> PRCXI9300Plate:
    return _PRCXI_96_DeepWell(name)


@resource(
    id="prcxi_1250ul_tips",
    displayname="1250μL Tip头",
    category=["gn_lab", "prcxi", "tip_racks"],
    description="1250μL Tip头 (Code: ZX-001-1250)",
)
def prcxi_1250ul_tips(name: str = "PRCXI_1250uL_Tips") -> PRCXI9300TipRack:
    return _PRCXI_1250uL_Tips(name)


@resource(
    id="prcxi_trash",
    displayname="废液槽",
    category=["gn_lab", "prcxi", "trash"],
    description="9320 废液槽 / 卸枪头位（现场 T1）。",
)
def prcxi_trash(name: str = "trash"):
    return _PRCXI_trash(name)
