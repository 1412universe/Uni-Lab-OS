"""模拟设备共用的构造与连接规则。"""

from __future__ import annotations

from typing import Any


def configure_sim(
    device,
    *,
    device_id: str,
    endpoint: str,
    command_timeout_seconds: float,
    auto_connect: bool,
    kwargs: dict[str, Any],
) -> None:
    del kwargs
    if command_timeout_seconds <= 0:
        raise ValueError("command_timeout_seconds 必须大于 0 秒")
    if not str(endpoint).startswith("sim://"):
        raise ValueError("演示设备只接受 sim:// 端点")
    if auto_connect:
        raise ValueError("构造阶段禁止自动连接")
    device.device_id = device_id
    device.endpoint = endpoint
    device.command_timeout_seconds = float(command_timeout_seconds)
    device._connected = False
    device.data = {"status": "Idle", "fault": False, "connected": False}


def mark_connected(device) -> dict[str, object]:
    device._connected = True
    device.data["connected"] = True
    device.data["status"] = "Idle"
    return {"success": True, "message": f"{device.device_id} 已连接模拟端点"}


def mark_disconnected(device) -> dict[str, object]:
    device._connected = False
    device.data["connected"] = False
    device.data["status"] = "Offline"
    return {"success": True, "message": f"{device.device_id} 已断开"}


def ensure_connected(device) -> None:
    if not device._connected:
        mark_connected(device)
