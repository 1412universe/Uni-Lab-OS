"""称量分装所需物料。尺寸只用于页面布局。"""

from pylabrobot.resources import Container, Resource
from unilabos.registry.decorators import resource


def _container(name: str, *, size: tuple[float, float, float], max_volume_ul: float, category: str) -> Container:
    return Container(name=name, size_x=size[0], size_y=size[1], size_z=size[2], max_volume=max_volume_ul, category=category)


@resource(id="stock_vial", displayname="标准样品母液瓶", category=["demo_lab", "container", "stock_vial"], description="已登记的标准样品母液，称量后向空瓶分装。")
def stock_vial(name: str = "StockVial") -> Container:
    return _container(name, size=(56.0, 56.0, 105.0), max_volume_ul=50_000.0, category="stock_vial")


@resource(id="aliquot_vial", displayname="分装样品瓶", category=["demo_lab", "container", "aliquot_vial"], description="空分装瓶，接收母液体积后封盖回库。")
def aliquot_vial(name: str = "AliquotVial") -> Container:
    return _container(name, size=(40.0, 40.0, 80.0), max_volume_ul=10_000.0, category="aliquot_vial")


@resource(id="tip_box", displayname="吸头盒", category=["demo_lab", "labware", "tip_box"], description="含可用吸头，供分装移液使用。")
def tip_box(name: str = "TipBox") -> Resource:
    return Resource(name=name, size_x=86.0, size_y=128.0, size_z=136.0, category="tip_box")
