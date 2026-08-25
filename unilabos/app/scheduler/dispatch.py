"""job 下发接口。

service 层产出「该启动的节点 + 解析后的参数」，由 Dispatcher 落地执行：

- ``CallbackDispatcher``：回调函数适配器。接 ws_client 时把回调指向
  MessageProcessor._handle_job_start 同款载荷（device_id/action/action_type/
  action_args/job_id/task_id），即可复用 DeviceActionManager + HostNode.send_goal。
- ``RecordingDispatcher``：测试/干跑用，记录下发序列。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Protocol


class DispatchPayload(Dict[str, Any]):
    """job_start 形状的下发载荷（与 ws_client JobAddReq 字段对齐）。"""


class Dispatcher(Protocol):
    def dispatch(self, payload: DispatchPayload) -> None:
        ...


class CallbackDispatcher:
    def __init__(self, fn: Callable[[DispatchPayload], None]):
        self._fn = fn

    def dispatch(self, payload: DispatchPayload) -> None:
        self._fn(payload)


class RecordingDispatcher:
    def __init__(self) -> None:
        self.dispatched: List[DispatchPayload] = []

    def dispatch(self, payload: DispatchPayload) -> None:
        self.dispatched.append(payload)


def build_job_start_payload(
    job_id: str,
    task_id: str,
    workflow_id: str,
    node_id: str,
    device_id: str,
    action_name: str,
    action_type: str,
    action_args: Any,
    always_free: bool = False,
) -> DispatchPayload:
    """构造与云端 ``job_start`` 同形状的执行载荷。

    参数：前七项描述作业、任务、工作流节点、设备动作及最终参数；
    ``always_free`` 表示动作不占设备排队锁。返回：可交给执行微后端的载荷。
    异常：无；身份与参数合同由调用方在越过执行边界前校验。
    """
    payload = DispatchPayload(
        job_id=job_id,
        task_id=task_id,
        node_id=node_id,
        workflow_id=workflow_id,
        device_id=device_id,
        action=action_name,
        action_type=action_type,
        action_args=action_args,
        sample_material={},
    )
    # 旧微后端与严格测试载荷没有该字段；只在动作确实免设备排队时扩展，保持
    # 普通动作 wire 形状完全不变。
    if always_free:
        payload["always_free"] = True
    return payload


__all__ = [
    "CallbackDispatcher",
    "DispatchPayload",
    "Dispatcher",
    "RecordingDispatcher",
    "build_job_start_payload",
]
