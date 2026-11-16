"""仿真档的设备目标解析：只放宽物理状态，不放宽软件合同。

这些用例锁定一条关键边界：没接真机时仿真必须能派发，但**动作名写错仍要被
发现**——否则「仿真」就退化成了「什么都通过」，失去验证价值。
"""

from __future__ import annotations

from typing import Any

import pytest

from unilabos.app.scheduler.device_target import (
    DeviceTargetUnavailable,
    resolve_registered_fixed_device_target,
    resolve_registered_device_target,
)

_TEMPLATE = "11111111-1111-4111-8111-111111111111"
_MATERIAL = "22222222-2222-4222-8222-222222222222"
_LOCAL = "gn_robotic_arm"
_ACTION = "execute_command"


class _Inventory:
    """最小库存替身：只回答「某物料是否属于该设备模板」。"""

    def __init__(self, known: bool = True) -> None:
        self._known = known

    def is_device_material(self, material_uuid: str, *, resource_template_uuid: str) -> bool:
        return self._known and bool(material_uuid) and bool(resource_template_uuid)


def _device(**overrides: Any) -> dict[str, Any]:
    """构造一个注册快照里的设备声明。"""

    device: dict[str, Any] = {
        "local_id": _LOCAL,
        "material_uuid": _MATERIAL,
        "name": "机械手",
        "online": True,
        "actions": [{"name": _ACTION}, {"name": "pick"}],
    }
    device.update(overrides)
    return device


def _registration(*devices: dict[str, Any], connected: bool = True) -> dict[str, Any]:
    return {"connected": connected, "devices": list(devices)}


class TestOfflineDeviceBlocksByDefault:
    """非仿真档：离线设备必须继续阻止派发。"""

    def test_dynamic_selector_rejects_offline(self) -> None:
        with pytest.raises(DeviceTargetUnavailable) as excinfo:
            resolve_registered_device_target(
                _Inventory(),
                _registration(_device(online=False)),
                resource_template_uuid=_TEMPLATE,
                action_name=_ACTION,
                busy_keys=set(),
            )
        assert excinfo.value.code == "device_offline"

    def test_fixed_selector_rejects_offline(self) -> None:
        with pytest.raises(DeviceTargetUnavailable) as excinfo:
            resolve_registered_fixed_device_target(
                _registration(_device(online=False)),
                local_device_id=_LOCAL,
                material_uuid=_MATERIAL,
                action_name=_ACTION,
                busy_keys=set(),
            )
        assert excinfo.value.code == "device_offline"


class TestSimulateAllowsOffline:
    """仿真档：离线设备放行——这正是「没接真机」的场景。"""

    def test_dynamic_selector_allows_offline(self) -> None:
        target = resolve_registered_device_target(
            _Inventory(),
            _registration(_device(online=False)),
            resource_template_uuid=_TEMPLATE,
            action_name=_ACTION,
            busy_keys=set(),
            simulate=True,
        )
        assert target.local_device_id == _LOCAL
        assert target.material_uuid == _MATERIAL

    def test_fixed_selector_allows_offline(self) -> None:
        target = resolve_registered_fixed_device_target(
            _registration(_device(online=False)),
            local_device_id=_LOCAL,
            material_uuid=_MATERIAL,
            action_name=_ACTION,
            busy_keys=set(),
            simulate=True,
        )
        assert target.local_device_id == _LOCAL

    def test_simulate_allows_unhealthy(self) -> None:
        target = resolve_registered_device_target(
            _Inventory(),
            _registration(_device(dispatch_block_reason="plc error")),
            resource_template_uuid=_TEMPLATE,
            action_name=_ACTION,
            busy_keys=set(),
            simulate=True,
        )
        assert target.local_device_id == _LOCAL

    def test_simulate_allows_pending_reconciliation(self) -> None:
        target = resolve_registered_device_target(
            _Inventory(),
            _registration(_device(unknown_command_ids=["cmd-1"])),
            resource_template_uuid=_TEMPLATE,
            action_name=_ACTION,
            busy_keys=set(),
            simulate=True,
        )
        assert target.local_device_id == _LOCAL


class TestSimulateStillChecksSoftwareContract:
    """仿真不能绕过软件一致性——否则失去验证价值。"""

    def test_unknown_action_still_rejected(self) -> None:
        with pytest.raises(DeviceTargetUnavailable) as excinfo:
            resolve_registered_device_target(
                _Inventory(),
                _registration(_device(online=False)),
                resource_template_uuid=_TEMPLATE,
                action_name="nonexistent_action",
                busy_keys=set(),
                simulate=True,
            )
        assert excinfo.value.code == "device_capability_unavailable"

    def test_wrong_template_still_rejected(self) -> None:
        with pytest.raises(DeviceTargetUnavailable):
            resolve_registered_device_target(
                _Inventory(known=False),
                _registration(_device(online=False)),
                resource_template_uuid=_TEMPLATE,
                action_name=_ACTION,
                busy_keys=set(),
                simulate=True,
            )

    def test_busy_device_still_waits(self) -> None:
        """同设备并发属于调度逻辑本身，仿真也必须照常串行。"""

        with pytest.raises(DeviceTargetUnavailable) as excinfo:
            resolve_registered_device_target(
                _Inventory(),
                _registration(_device(online=False)),
                resource_template_uuid=_TEMPLATE,
                action_name=_ACTION,
                busy_keys={f"/devices/{_LOCAL}"},
                simulate=True,
            )
        assert excinfo.value.code == "device_busy"


class TestEdgeNotRegistered:
    """Edge 完全未注册时无法确定实例身份，应明确报错而不是猜设备。"""

    def test_simulate_reports_edge_offline_with_hint(self) -> None:
        with pytest.raises(DeviceTargetUnavailable) as excinfo:
            resolve_registered_device_target(
                _Inventory(),
                None,
                resource_template_uuid=_TEMPLATE,
                action_name=_ACTION,
                busy_keys=set(),
                simulate=True,
            )
        assert excinfo.value.code == "edge_offline"
        # 错误信息要说明仿真需要什么，避免用户误以为仿真可以完全离线
        assert "仿真" in str(excinfo.value)
