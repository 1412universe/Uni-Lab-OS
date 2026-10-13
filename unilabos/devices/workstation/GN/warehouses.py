"""GN 各工站 WareHouse 工厂。

工位编号对齐《PLC和机器人协议modbustcp1.2》：
    常规烘箱 303=1~5；锁紧 304=1~5；快换 305=1~5；离心机 306=1~5；
    9320 307=1~16（T1~T16，与 PRCXI 4×4 台面一致）；
    离心管液体处理 308=1~6；旋转堆栈 309 见 STACK_COLUMN_NUMBERS；
    固体加样 310=1~5；真空烘箱 311=1~5；成品放置区 314=1~16（4 层×4）。
所有 site 的 content_type 兼容 PRCXI 物料类型。
"""

from typing import List, Optional, Tuple

from unilabos.devices.workstation.GN.GN_warehouse import (
    PRCXI_COMPAT_CONTENT_TYPES,
    WareHouse,
    warehouse_factory,
)
from unilabos.registry.decorators import resource

# SBS 足迹，与 PRCXI9300Deck 默认槽位一致
_PLATE_DX = 10.0
_PLATE_DY = 10.0
_PLATE_DZ = 10.0
_PLATE_ITEM_DX = 137.5
_PLATE_ITEM_DY = 96.0
_PLATE_ITEM_DZ = 120.0
_PLATE_SIZE_X = 128.0
_PLATE_SIZE_Y = 86.0
_PLATE_SIZE_Z = 25.0

# 旋转堆栈：列号 → PLC 抓取数字（gn_stack.COLUMN_NUMBERS / PLC 309）
STACK_COLUMN_NUMBERS = {
    1: list(range(1, 9)),
    2: list(range(9, 17)),
    3: list(range(17, 25)),
    4: list(range(25, 31)),
    5: list(range(31, 41)),
    6: list(range(41, 50)),
    7: list(range(56, 60)),
    8: list(range(60, 64)),
    9: list(range(64, 69)),
    10: list(range(69, 74)),
    11: list(range(74, 79)),
    12: list(range(79, 84)),
}
STACK_MAX_LAYERS = 10
STACK_COLUMN_COUNT = 12


def _plate_warehouse(
    name: str,
    num_items_x: int,
    num_items_y: int = 1,
    custom_keys: Optional[List[str]] = None,
    naming_mode: str = "letter_number",
    layout: str = "row-major",
    model: Optional[str] = None,
    removed_positions: Optional[List[int]] = None,
) -> WareHouse:
    kwargs = dict(
        name=name,
        num_items_x=num_items_x,
        num_items_y=num_items_y,
        num_items_z=1,
        dx=_PLATE_DX,
        dy=_PLATE_DY,
        dz=_PLATE_DZ,
        item_dx=_PLATE_ITEM_DX,
        item_dy=_PLATE_ITEM_DY,
        item_dz=_PLATE_ITEM_DZ,
        resource_size_x=_PLATE_SIZE_X,
        resource_size_y=_PLATE_SIZE_Y,
        resource_size_z=_PLATE_SIZE_Z,
        category="warehouse",
        model=model or name,
        layout=layout,
        content_type=list(PRCXI_COMPAT_CONTENT_TYPES),
    )
    if custom_keys is not None:
        kwargs["custom_keys"] = custom_keys
    else:
        kwargs["naming_mode"] = naming_mode
    if removed_positions:
        kwargs["removed_positions"] = removed_positions
    return warehouse_factory(**kwargs)


def _stack_grid() -> Tuple[List[int], List[str]]:
    """12 列 × 10 层展开为前端网格：上行=高层，空层剔除。键名为 PLC 抓取数字。"""
    removed: List[int] = []
    keys: List[str] = []
    idx = 0
    for row in range(STACK_MAX_LAYERS):
        layer_from_bottom = STACK_MAX_LAYERS - row
        for col in range(STACK_COLUMN_COUNT):
            col_nums = STACK_COLUMN_NUMBERS[col + 1]
            if layer_from_bottom <= len(col_nums):
                keys.append(str(col_nums[layer_from_bottom - 1]))
            else:
                removed.append(idx)
            idx += 1
    return removed, keys


@resource(id="gn_oven_warehouse", category=["GN", "warehouse"], description="GN 常规烘箱 5 工位")
def Oven_warehouse_5x1(name: str) -> WareHouse:
    return _plate_warehouse(name, num_items_x=5, custom_keys=["1", "2", "3", "4", "5"], model="gn_oven_warehouse")


@resource(id="gn_locking_warehouse", category=["GN", "warehouse"], description="GN 锁紧模块 5 工位")
def Locking_warehouse_5x1(name: str) -> WareHouse:
    return _plate_warehouse(name, num_items_x=5, custom_keys=["1", "2", "3", "4", "5"], model="gn_locking_warehouse")


@resource(id="gn_quick_change_warehouse", category=["GN", "warehouse"], description="GN 快换模块 5 工位")
def QuickChange_warehouse_5x1(name: str) -> WareHouse:
    return _plate_warehouse(name, num_items_x=5, custom_keys=["1", "2", "3", "4", "5"], model="gn_quick_change_warehouse")


@resource(id="gn_centrifuge_warehouse", category=["GN", "warehouse"], description="GN 离心机 5 工位")
def Centrifuge_warehouse_5x1(name: str) -> WareHouse:
    return _plate_warehouse(name, num_items_x=5, custom_keys=["1", "2", "3", "4", "5"], model="gn_centrifuge_warehouse")


@resource(id="gn_vacuum_oven_warehouse", category=["GN", "warehouse"], description="GN 真空烘箱 5 工位")
def VacuumOven_warehouse_5x1(name: str) -> WareHouse:
    return _plate_warehouse(name, num_items_x=5, custom_keys=["1", "2", "3", "4", "5"], model="gn_vacuum_oven_warehouse")


@resource(
    id="gn_prcxi9320_warehouse",
    category=["GN", "warehouse"],
    description="GN PRCXI 9320 台面 4×4（T1~T16，与 PRCXI9300Deck 编号一致）",
)
def PRCXI9320_warehouse_4x4(name: str) -> WareHouse:
    return _plate_warehouse(
        name,
        num_items_x=4,
        num_items_y=4,
        custom_keys=[f"T{i}" for i in range(1, 17)],
        layout="row-major",
        model="gn_prcxi9320_warehouse",
    )


@resource(id="gn_tube_warehouse", category=["GN", "warehouse"], description="GN 离心管液体处理 6 工位")
def Tube_warehouse_6x1(name: str) -> WareHouse:
    return _plate_warehouse(
        name, num_items_x=6, custom_keys=["1", "2", "3", "4", "5", "6"], model="gn_tube_warehouse"
    )


@resource(
    id="gn_stack_warehouse",
    category=["GN", "warehouse"],
    description="GN 旋转堆栈 12 列（PLC 309 抓取数字 1~49、56~83）",
)
def Stack_warehouse_12x10(name: str) -> WareHouse:
    removed, keys = _stack_grid()
    return warehouse_factory(
        name=name,
        num_items_x=STACK_COLUMN_COUNT,
        num_items_y=STACK_MAX_LAYERS,
        num_items_z=1,
        dx=_PLATE_DX,
        dy=_PLATE_DY,
        dz=_PLATE_DZ,
        item_dx=90.0,
        item_dy=50.0,
        item_dz=_PLATE_ITEM_DZ,
        resource_size_x=_PLATE_SIZE_X,
        resource_size_y=_PLATE_SIZE_Y,
        resource_size_z=_PLATE_SIZE_Z,
        category="warehouse",
        model="gn_stack_warehouse",
        layout="row-major",
        custom_keys=keys,
        removed_positions=removed,
        content_type=list(PRCXI_COMPAT_CONTENT_TYPES),
    )


@resource(id="gn_solid_warehouse", category=["GN", "warehouse"], description="GN 固体加样 5 工位")
def Solid_warehouse_5x1(name: str) -> WareHouse:
    return _plate_warehouse(name, num_items_x=5, custom_keys=["1", "2", "3", "4", "5"], model="gn_solid_warehouse")


@resource(
    id="gn_finished_area_warehouse",
    category=["GN", "warehouse"],
    description="GN 成品放置区 4 层×4（PLC 314=1~16）",
)
def FinishedArea_warehouse_4x4(name: str) -> WareHouse:
    return _plate_warehouse(
        name,
        num_items_x=4,
        num_items_y=4,
        naming_mode="continuous_number",
        layout="row-major",
        model="gn_finished_area_warehouse",
    )
