"""主工作台。setup=False 时只作为模板。"""

from typing import Any

from pylabrobot.resources import Coordinate, Deck
from unilabos.registry.decorators import resource

from demo_lab.resources.carriers import finished_rack, source_rack, tip_warehouse, used_rack


@resource(id="main_deck", displayname="主工作台", category=["demo_lab", "deck"], description="称量分装演示台面。")
class MainDeck(Deck):
    def __init__(self, name: str = "main_deck", size_x: float = 1800.0, size_y: float = 900.0, size_z: float = 20.0, setup: bool = False, **kwargs: Any) -> None:
        super().__init__(name=name, size_x=size_x, size_y=size_y, size_z=size_z)
        if setup:
            self.setup()

    def setup(self) -> None:
        self.assign_child_resource(source_rack("source_rack"), location=Coordinate(x=80.0, y=80.0, z=20.0))
        self.assign_child_resource(tip_warehouse("tip_warehouse"), location=Coordinate(x=360.0, y=80.0, z=20.0))
        self.assign_child_resource(used_rack("used_rack"), location=Coordinate(x=80.0, y=320.0, z=20.0))
        self.assign_child_resource(finished_rack("finished_rack"), location=Coordinate(x=280.0, y=320.0, z=20.0))
