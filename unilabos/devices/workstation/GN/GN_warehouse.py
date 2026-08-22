"""GN 工站 WareHouse 基类与工厂。

写法对齐 AI4M_warehouse：支持 custom_keys / naming_mode / reverse_col_order。
所有物料位默认使用 PRCXI 兼容 content_type，前端可直接拖放 PRCXI 板/枪头/管架等。
"""

from typing import Dict, List, Optional, Union

from pylabrobot.resources import Coordinate
from pylabrobot.resources.carrier import ResourceHolder, create_homogeneous_resources

from unilabos.resources.itemized_carrier import ItemizedCarrier, ResourcePLR


LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

# 与 PRCXI9300Deck._DEFAULT_CONTENT_TYPE 对齐，并保留瓶类以便堆栈/固体加样放瓶
PRCXI_COMPAT_CONTENT_TYPES: List[str] = [
    "plate",
    "tip_rack",
    "plates",
    "tip_racks",
    "tube_rack",
    "adaptor",
    "plateadapter",
    "module",
    "trash",
    "bottle",
    "container",
    "tube",
    "bottle_carrier",
]


def warehouse_factory(
    name: str,
    num_items_x: int = 1,
    num_items_y: int = 4,
    num_items_z: int = 4,
    dx: float = 137.0,
    dy: float = 96.0,
    dz: float = 120.0,
    item_dx: float = 10.0,
    item_dy: float = 10.0,
    item_dz: float = 10.0,
    resource_size_x: float = 127.0,
    resource_size_y: float = 86.0,
    resource_size_z: float = 25.0,
    removed_positions: Optional[List[int]] = None,
    empty: bool = False,
    category: str = "warehouse",
    model: Optional[str] = None,
    col_offset: int = 0,
    layout: str = "col-major",
    custom_keys: Optional[List[Union[str, int]]] = None,
    naming_mode: str = "letter_number",
    reverse_col_order: bool = False,
    content_type: Optional[List[str]] = None,
):
    """创建 GN WareHouse。content_type 默认兼容 PRCXI 物料类型。"""
    _ = empty
    locations = []

    for layer in range(num_items_z):
        for row in range(num_items_y):
            for col in range(num_items_x):
                x = dx + col * item_dx
                if layout == "row-major":
                    y = dy + row * item_dy
                else:
                    y = dy + (num_items_y - row - 1) * item_dy
                z = dz + (num_items_z - layer - 1) * item_dz
                locations.append(Coordinate(x, y, z))

    if removed_positions:
        locations = [loc for i, loc in enumerate(locations) if i not in removed_positions]

    _sites = create_homogeneous_resources(
        klass=ResourceHolder,
        locations=locations,
        resource_size_x=resource_size_x,
        resource_size_y=resource_size_y,
        resource_size_z=resource_size_z,
        name_prefix=name,
    )

    if custom_keys:
        keys = [str(k) for k in custom_keys]
        if len(keys) != len(_sites):
            raise ValueError(f"自定义键名数量({len(keys)})与位置数量({len(_sites)})不匹配")
    elif naming_mode == "continuous_number":
        keys = []
        counter = 1 + col_offset
        for layer in range(num_items_z):
            for row in range(num_items_y):
                row_keys = []
                for col in range(num_items_x):
                    row_keys.append(str(counter))
                    counter += 1
                if reverse_col_order:
                    row_keys = list(reversed(row_keys))
                keys.extend(row_keys)
        if removed_positions:
            keys = [k for i, k in enumerate(keys) if i not in removed_positions]
    else:
        keys = []
        for layer in range(num_items_z):
            for row in range(num_items_y):
                reversed_row = num_items_y - 1 - row
                global_row = layer * num_items_y + reversed_row
                letter = LETTERS[global_row]
                for col in range(num_items_x):
                    number = col + 1 + col_offset
                    keys.append(f"{letter}{number}")
        if removed_positions:
            keys = [k for i, k in enumerate(keys) if i not in removed_positions]

    sites = {i: site for i, site in zip(keys, _sites.values())}

    return WareHouse(
        name=name,
        size_x=dx + item_dx * num_items_x,
        size_y=dy + item_dy * num_items_y,
        size_z=dz + item_dz * num_items_z,
        num_items_x=num_items_x,
        num_items_y=num_items_y,
        num_items_z=num_items_z,
        ordering_layout=layout,
        sites=sites,
        category=category,
        model=model,
        content_type=content_type,
    )


class WareHouse(ItemizedCarrier):
    """GN 堆栈 / 工位载体。序列化时把每个 site 的 content_type 写成 PRCXI 兼容类型。"""

    def __init__(
        self,
        name: str,
        size_x: float,
        size_y: float,
        size_z: float,
        num_items_x: int,
        num_items_y: int,
        num_items_z: int,
        layout: str = "x-y",
        sites: Optional[Dict[Union[int, str], Optional[ResourcePLR]]] = None,
        category: str = "warehouse",
        model: Optional[str] = None,
        ordering_layout: str = "col-major",
        content_type: Optional[List[str]] = None,
        **kwargs,
    ):
        _ = kwargs
        super().__init__(
            name=name,
            size_x=size_x,
            size_y=size_y,
            size_z=size_z,
            num_items_x=num_items_x,
            num_items_y=num_items_y,
            num_items_z=num_items_z,
            layout=layout,
            sites=sites,
            category=category,
            model=model,
        )
        self.ordering_layout = ordering_layout
        if content_type:
            self.content_type = list(content_type)
        elif isinstance(sites, list) and sites and isinstance(sites[0], dict):
            self.content_type = list(sites[0].get("content_type") or PRCXI_COMPAT_CONTENT_TYPES)
        else:
            self.content_type = list(PRCXI_COMPAT_CONTENT_TYPES)

    def serialize(self) -> dict:
        data = super().serialize()
        data["ordering_layout"] = self.ordering_layout
        data["content_type"] = list(self.content_type)
        for site in data.get("sites", []):
            site["content_type"] = list(self.content_type)
        return data

    def get_site_by_layer_position(self, row: int, col: int, layer: int) -> ResourceHolder:
        if not (0 <= layer < self.num_items_z and 0 <= row < self.num_items_y and 0 <= col < self.num_items_x):
            raise ValueError(f"无效的位置: layer={layer}, row={row}, col={col}")
        site_index = layer * self.num_items_y * self.num_items_x + row * self.num_items_x + col
        return self.sites[site_index]

    def add_rack_to_position(self, row: int, col: int, layer: int, rack) -> None:
        site = self.get_site_by_layer_position(row, col, layer)
        site.assign_child_resource(rack)

    def get_rack_at_position(self, row: int, col: int, layer: int):
        site = self.get_site_by_layer_position(row, col, layer)
        return site.resource
