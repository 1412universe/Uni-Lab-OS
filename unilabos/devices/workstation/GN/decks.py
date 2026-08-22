"""GN 有机合成工作站 Deck：按从左到右工站顺序放置 WareHouse。"""

from typing import Optional

from pylabrobot.resources import Coordinate, Deck, Resource

from unilabos.devices.workstation.GN.warehouses import (
    Centrifuge_warehouse_5x1,
    FinishedArea_warehouse_4x4,
    Locking_warehouse_5x1,
    Oven_warehouse_5x1,
    PRCXI9320_warehouse_4x4,
    QuickChange_warehouse_5x1,
    Solid_warehouse_5x1,
    Stack_warehouse_12x10,
    Tube_warehouse_6x1,
    VacuumOven_warehouse_5x1,
)
from unilabos.registry.decorators import resource


@resource(
    id="GN_deck",
    category=["GN", "deck"],
    description="GN 有机合成工作站 Deck，含各工站 WareHouse；物料位兼容 PRCXI 类型",
)
class GN_deck(Deck):
    def __init__(
        self,
        name: str = "GN_deck",
        size_x: float = 2500.0,
        size_y: float = 1400.0,
        size_z: float = 1500.0,
        origin: Coordinate = Coordinate(0, 0, 0),
        category: str = "deck",
        setup: bool = False,
        **kwargs,
    ) -> None:
        _ = kwargs
        if isinstance(origin, dict):
            origin = Coordinate(**{k: origin[k] for k in ("x", "y", "z") if k in origin})
        super().__init__(name=name, size_x=size_x, size_y=size_y, size_z=size_z, origin=origin)
        self.warehouses = {}
        self.warehouse_locations = {}
        if setup:
            self.setup()

    def setup(self) -> None:
        # 工站沿 X 从左到右，与 Robot_ModuleNoSet / X轴位置 一致
        self.warehouses = {
            "常规烘箱": Oven_warehouse_5x1("常规烘箱"),
            "锁紧模块": Locking_warehouse_5x1("锁紧模块"),
            "快换模块": QuickChange_warehouse_5x1("快换模块"),
            "离心机": Centrifuge_warehouse_5x1("离心机"),
            "真空烘箱": VacuumOven_warehouse_5x1("真空烘箱"),
            "PRCXI9320": PRCXI9320_warehouse_4x4("PRCXI9320"),
            "离心管液体处理": Tube_warehouse_6x1("离心管液体处理"),
            "旋转堆栈": Stack_warehouse_12x10("旋转堆栈"),
            "固体加样": Solid_warehouse_5x1("固体加样"),
            "成品放置区": FinishedArea_warehouse_4x4("成品放置区"),
        }
        self.warehouse_locations = {
            "常规烘箱": Coordinate(20.0, 1050.0, 0.0),
            "锁紧模块": Coordinate(20.0, 850.0, 0.0),
            "快换模块": Coordinate(780.0, 1050.0, 0.0),
            "离心机": Coordinate(780.0, 850.0, 0.0),
            "真空烘箱": Coordinate(1540.0, 1050.0, 0.0),
            "PRCXI9320": Coordinate(1540.0, 550.0, 0.0),
            "离心管液体处理": Coordinate(20.0, 650.0, 0.0),
            "旋转堆栈": Coordinate(20.0, 40.0, 0.0),
            "固体加样": Coordinate(1540.0, 850.0, 0.0),
            "成品放置区": Coordinate(1540.0, 40.0, 0.0),
        }
        for warehouse_name, warehouse in self.warehouses.items():
            self.assign_child_resource(warehouse, location=self.warehouse_locations[warehouse_name])

    def assign_child_resource(
        self,
        resource: Resource,
        location: Optional[Coordinate],
        reassign: bool = True,
    ):
        super().assign_child_resource(resource, location, reassign)
        self.warehouses[resource.name] = resource
        self.warehouse_locations[resource.name] = location
