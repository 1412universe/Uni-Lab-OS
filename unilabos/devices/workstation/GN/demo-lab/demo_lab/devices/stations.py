"""称量、移液、封盖工站的模拟实现。"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from unilabos.registry.annotations import AllowedResourceTemplates
from unilabos.registry.decorators import action, device, not_action, topic_config
from unilabos.registry.placeholder_type import ResourceSlot

from demo_lab.devices._sim import configure_sim, ensure_connected, mark_connected, mark_disconnected
from demo_lab.resources.materials import aliquot_vial, stock_vial, tip_box


class CommandResult(TypedDict):
    success: bool
    message: str


class WeighResult(TypedDict):
    success: bool
    message: str
    stock_vial: ResourceSlot
    measured_mass_g: float


class AliquotResult(TypedDict):
    success: bool
    message: str
    stock_vial: ResourceSlot
    aliquot_vial: ResourceSlot
    commanded_volume_ul: int


class CapResult(TypedDict):
    success: bool
    message: str
    aliquot_vial: ResourceSlot


class _SimStation:
    def __init__(self, default_id: str, endpoint: str, command_timeout_seconds: float, auto_connect: bool, kwargs: dict[str, Any], device_id: str | None) -> None:
        configure_sim(self, device_id=device_id or default_id, endpoint=endpoint, command_timeout_seconds=command_timeout_seconds, auto_connect=auto_connect, kwargs=kwargs)

    @not_action
    def post_init(self, ros_node: Any) -> None:
        self._ros_node = ros_node

    @action(displayname="连接", description="建立模拟会话。")
    def connect(self) -> CommandResult:
        return mark_connected(self)

    @action(displayname="断开", description="关闭模拟会话。")
    def disconnect(self) -> CommandResult:
        return mark_disconnected(self)

    @property
    @topic_config(period=2.0)
    def status(self) -> str:
        return str(self.data.get("status", "Unknown"))

    @property
    @topic_config(period=2.0)
    def fault(self) -> bool:
        return bool(self.data.get("fault", False))


@device(id="balance_station_sim", category=["分析检测", "称量", "simulation"], displayname="模拟天平工站", description="称量已就位的标准样品母液瓶。")
class BalanceStation(_SimStation):
    def __init__(self, device_id: str | None = None, endpoint: str = "sim://local", command_timeout_seconds: float = 60.0, auto_connect: bool = False, **kwargs: Any) -> None:
        super().__init__("balance_station_01", endpoint, command_timeout_seconds, auto_connect, kwargs, device_id)

    @action(displayname="称量母液瓶", description="返回模拟称量质量。")
    def weigh(self, stock_vial: Annotated[ResourceSlot, AllowedResourceTemplates(stock_vial)]) -> WeighResult:
        ensure_connected(self)
        return {"success": True, "message": "模拟称量完成", "stock_vial": stock_vial, "measured_mass_g": 12.5}


@device(id="pipette_station_sim", category=["液体与反应处理", "液体处理设备", "simulation"], displayname="模拟移液工站", description="从母液瓶按目标体积移入分装瓶。")
class PipetteStation(_SimStation):
    def __init__(self, device_id: str | None = None, endpoint: str = "sim://local", command_timeout_seconds: float = 120.0, auto_connect: bool = False, **kwargs: Any) -> None:
        super().__init__("pipette_station_01", endpoint, command_timeout_seconds, auto_connect, kwargs, device_id)

    @action(displayname="分装移液", description="按微升目标体积从母液瓶移入分装瓶。")
    def aliquot(
        self,
        stock_vial: Annotated[ResourceSlot, AllowedResourceTemplates(stock_vial)],
        aliquot_vial: Annotated[ResourceSlot, AllowedResourceTemplates(aliquot_vial)],
        tip_box: Annotated[ResourceSlot, AllowedResourceTemplates(tip_box)],
        target_aliquot_volume_ul: int,
    ) -> AliquotResult:
        if not 1 <= int(target_aliquot_volume_ul) <= 5000:
            raise ValueError("target_aliquot_volume_ul 必须在 1-5000")
        ensure_connected(self)
        return {"success": True, "message": f"模拟分装 {target_aliquot_volume_ul} μL", "stock_vial": stock_vial, "aliquot_vial": aliquot_vial, "commanded_volume_ul": int(target_aliquot_volume_ul)}


@device(id="cap_station_sim", category=["搬运与执行", "封口", "simulation"], displayname="模拟封盖工站", description="封闭已分装样品瓶。")
class CapStation(_SimStation):
    def __init__(self, device_id: str | None = None, endpoint: str = "sim://local", command_timeout_seconds: float = 60.0, auto_connect: bool = False, **kwargs: Any) -> None:
        super().__init__("cap_station_01", endpoint, command_timeout_seconds, auto_connect, kwargs, device_id)

    @action(displayname="封盖分装瓶", description="封闭分装瓶。")
    def close_cap(self, aliquot_vial: Annotated[ResourceSlot, AllowedResourceTemplates(aliquot_vial)], sample_id: str = "") -> CapResult:
        ensure_connected(self)
        return {"success": True, "message": f"模拟封盖 {sample_id or 'aliquot'}", "aliquot_vial": aliquot_vial}
