"""按冻结设备类型从当前 Edge 注册事实选择一个可执行设备实例。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from unilabos.app.scheduler.inventory.station_resource import (
    StationResourceInventory,
)


class DeviceTargetUnavailable(ValueError):
    """当前没有满足类型、动作与在线条件的设备实例。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        resources: tuple[dict[str, str], ...] = (),
    ) -> None:
        """保存稳定等待码、中文诊断与已知候选资源身份。"""

        super().__init__(message)
        self.code = code
        self.message = message
        self.resources = resources


@dataclass(frozen=True, slots=True)
class ResolvedDeviceTarget:
    """已经由注册与库存双重证明的本地设备实例。"""

    local_device_id: str
    material_uuid: str


def resolve_registered_device_target(
    inventory: StationResourceInventory,
    registration: Mapping[str, Any] | None,
    *,
    resource_template_uuid: str,
    action_name: str,
    busy_keys: set[str],
) -> ResolvedDeviceTarget:
    """按设备模板、动作能力和忙碌状态选择稳定排序的首个可用实例。

    参数：``inventory`` 只提供设备物料匹配接口；``registration`` 是执行进程
    当前注册快照；模板、动作和 ``busy_keys`` 共同限定候选。返回：稳定排序后
    首个可用设备身份。异常：选择器、注册或可用性不满足时抛
    ``DeviceTargetUnavailable``；库存读取错误原样传播并阻止派发。
    """

    try:
        template_uuid = str(UUID(str(resource_template_uuid)))
    except (AttributeError, TypeError, ValueError) as error:
        raise DeviceTargetUnavailable(
            "invalid_device_selector",
            "冻结设备选择器缺少合法资源模板 UUID",
        ) from error
    if not isinstance(registration, Mapping) or not registration.get("connected"):
        raise DeviceTargetUnavailable("edge_offline", "设备执行进程尚未在线注册")
    devices = registration.get("devices")
    if not isinstance(devices, list):
        raise DeviceTargetUnavailable("invalid_edge_registration", "设备注册快照损坏")
    candidates: list[ResolvedDeviceTarget] = []
    for device in devices:
        if not isinstance(device, Mapping):
            continue
        local_id = str(device.get("local_id") or "").strip()
        material_uuid = str(device.get("material_uuid") or "").strip()
        actions = device.get("actions")
        if not local_id or not material_uuid or not isinstance(actions, list):
            continue
        if not any(
            isinstance(action, Mapping)
            and str(action.get("name") or "").strip() == action_name
            for action in actions
        ):
            continue
        if not inventory.is_device_material(
            material_uuid,
            resource_template_uuid=template_uuid,
        ):
            continue
        candidates.append(
            ResolvedDeviceTarget(
                local_device_id=local_id,
                material_uuid=material_uuid,
            )
        )
    if not candidates:
        raise DeviceTargetUnavailable(
            "device_capability_unavailable",
            "当前没有匹配设备类型和动作能力的在线实例",
        )
    candidates.sort(key=lambda item: (item.local_device_id, item.material_uuid))
    for candidate in candidates:
        if (
            not {
                f"/devices/{candidate.local_device_id}",
                f"/devices/{candidate.local_device_id}/{action_name}",
                f"/devices/{candidate.material_uuid}",
            }
            & busy_keys
        ):
            return candidate
    raise DeviceTargetUnavailable(
        "device_busy",
        "匹配设备当前全部忙碌，等待下一轮调度",
        resources=tuple(
            {
                "scope": "device",
                "device_id": candidate.material_uuid,
            }
            for candidate in candidates
        ),
    )


def make_registered_device_target_resolver(
    inventory: StationResourceInventory,
    registration_reader: Callable[[], Mapping[str, Any] | None],
) -> Callable[[Mapping[str, Any], str, set[str]], ResolvedDeviceTarget]:
    """绑定库存与 Edge 注册读取端口，生成调度器动态设备解析器。

    参数：``inventory`` 是工站资源窄接口；``registration_reader`` 每轮返回最新
    执行进程注册快照。返回：供调度器调用的动态设备解析函数。异常：构造不读取
    外部状态；调用时的选择错误由内部函数原样传播。
    """

    def resolve(
        selector: Mapping[str, Any],
        action_name: str,
        busy_keys: set[str],
    ) -> ResolvedDeviceTarget:
        """用本轮最新注册和忙碌快照解析一个动态设备目标。

        参数：``selector`` 是冻结设备模板选择器；``action_name`` 是动作能力；
        ``busy_keys`` 是当前不可用资源键。返回：具体设备身份。异常：选择器模式
        非法或没有可用设备时抛 ``DeviceTargetUnavailable``。
        """

        if selector.get("mode") != "resource_template":
            raise DeviceTargetUnavailable(
                "invalid_device_selector",
                "动态设备选择器模式非法",
            )
        return resolve_registered_device_target(
            inventory,
            registration_reader(),
            resource_template_uuid=str(selector.get("resource_template_uuid") or ""),
            action_name=action_name,
            busy_keys=busy_keys,
        )

    return resolve


__all__ = [
    "DeviceTargetUnavailable",
    "ResolvedDeviceTarget",
    "make_registered_device_target_resolver",
    "resolve_registered_device_target",
]
