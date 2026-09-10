"""模拟搬运机器人。"""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict

from unilabos.registry.annotations import AllowedResourceTemplates, SiteSelector
from unilabos.registry.decorators import ExecutorKind, action, device, not_action, topic_config
from unilabos.registry.placeholder_type import ResourceSlot

from demo_lab.devices._sim import configure_sim, ensure_connected, mark_connected, mark_disconnected
from demo_lab.resources.materials import aliquot_vial, stock_vial


class CommandResult(TypedDict):
    success: bool
    message: str


class AtomicMaterialTransferStatus(TypedDict):
    success: bool
    message: str
    resource: ResourceSlot
    mount_resource: ResourceSlot
    site: str
    result: Literal["success", "failed", "unknown"]


@device(
    id="transport_robot_sim",
    category=["搬运与执行", "机械臂", "simulation"],
    displayname="模拟搬运机器人",
    description="在原料区、天平、移液、封盖和回库位之间搬运母液瓶和分装瓶。",
    available_sites=[{"index": "gripper", "label": "GRIPPER", "visible": False, "meta_data": {"unilab": {"resource_role": "robot.gripper"}}}],
)
class TransportRobot:
    def __init__(self, device_id: str | None = None, endpoint: str = "sim://local", command_timeout_seconds: float = 60.0, auto_connect: bool = False, **kwargs: Any) -> None:
        configure_sim(self, device_id=device_id or "transport_robot_01", endpoint=endpoint, command_timeout_seconds=command_timeout_seconds, auto_connect=auto_connect, kwargs=kwargs)

    @not_action
    def post_init(self, ros_node: Any) -> None:
        self._ros_node = ros_node

    @action(displayname="连接", description="建立模拟会话。")
    def connect(self) -> CommandResult:
        return mark_connected(self)

    @action(displayname="断开", description="关闭模拟会话。")
    def disconnect(self) -> CommandResult:
        return mark_disconnected(self)

    @action(
        displayname="演示物料搬运",
        description="把母液瓶或分装瓶从当前库位搬到目标库位。",
        executor_kind=ExecutorKind.MATERIAL_TRANSFER,
        resource_contract={"version": 1, "transfer": {"material_param": "resource", "source_owner_param": "source_warehouse", "source_site_uuid_param": "", "source_site_name_param": "source_site", "target_owner_param": "target_warehouse", "target_site_uuid_param": "", "target_site_name_param": "target_site", "gripper_site_role": "robot.gripper"}},
    )
    def transfer_material_atomic(
        self,
        resource: Annotated[ResourceSlot, AllowedResourceTemplates(stock_vial, aliquot_vial)],
        source_warehouse: ResourceSlot,
        target_warehouse: ResourceSlot,
        target_device: str,
        source_site: str,
        target_site: Annotated[str, SiteSelector(owner="target_warehouse", occupant="resource", show_occupied=True, allow_occupied=False)],
        check_source_presence: bool = True,
        check_target_presence: bool = True,
        check_gripper_payload: bool = True,
    ) -> AtomicMaterialTransferStatus:
        if not source_site or not target_site or not target_device:
            raise ValueError("source_site、target_site 和 target_device 不能为空")
        ensure_connected(self)
        return {"success": True, "message": f"模拟搬运完成：{source_site} -> {target_site}", "resource": resource, "mount_resource": target_warehouse, "site": target_site, "result": "success"}

    @property
    @topic_config(period=2.0)
    def status(self) -> str:
        return str(self.data.get("status", "Unknown"))

    @property
    @topic_config(period=2.0)
    def fault(self) -> bool:
        return bool(self.data.get("fault", False))
