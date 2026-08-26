"""job 下发接口。

service 层产出「该启动的节点 + 解析后的参数」，由 Dispatcher 落地执行：

- ``CallbackDispatcher``：回调函数适配器。接 ws_client 时把回调指向
  MessageProcessor._handle_job_start 同款载荷（device_id/action/action_type/
  action_args/job_id/task_id），即可复用 DeviceActionManager + HostNode.send_goal。
- ``RecordingDispatcher``：测试/干跑用，记录下发序列。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Callable, Dict, List, Protocol


class DispatchPayload(Dict[str, Any]):
    """job_start 形状的下发载荷（与 ws_client JobAddReq 字段对齐）。"""


class Dispatcher(Protocol):
    def dispatch(self, payload: DispatchPayload) -> None:
        ...


class CancelDispatchState(str, Enum):
    """本地执行适配器对取消请求的即时判定。"""

    NOT_SENT = "not_sent"
    REQUESTED = "requested"
    UNAVAILABLE = "unavailable"


class CallbackDispatcher:
    def __init__(
        self,
        fn: Callable[[DispatchPayload], None],
        cancel_fn: Callable[[str, Callable[[bool], None]], CancelDispatchState]
        | None = None,
    ):
        """装配回调执行适配器。

        参数：``fn`` 接收物理派发载荷；``cancel_fn`` 可选地提交本地设备取消，
        并在执行器明确接受或拒绝时回调。返回无。异常：派发或取消回调异常由调用
        方处理；没有取消能力时返回 ``UNAVAILABLE``，绝不伪造设备已停止。
        """

        self._fn = fn
        self._cancel_fn = cancel_fn

    def dispatch(self, payload: DispatchPayload) -> None:
        self._fn(payload)

    def cancel(
        self,
        job_id: str,
        on_accepted: Callable[[bool], None],
    ) -> CancelDispatchState:
        """请求取消已经派发的作业（Job）。

        参数：``job_id`` 是稳定作业身份；``on_accepted`` 接收执行器异步受理事实。
        返回：即时判定；未配置取消回调时为 ``UNAVAILABLE``。异常由取消回调原样
        传播，调度器会将其保守投影为执行未知（ExecutionUnknown）。
        """

        if self._cancel_fn is None:
            return CancelDispatchState.UNAVAILABLE
        return self._cancel_fn(job_id, on_accepted)


class RecordingDispatcher:
    def __init__(self) -> None:
        self.dispatched: List[DispatchPayload] = []

    def dispatch(self, payload: DispatchPayload) -> None:
        self.dispatched.append(payload)

    def cancel(
        self,
        job_id: str,
        on_accepted: Callable[[bool], None],
    ) -> CancelDispatchState:
        """取消干跑记录且声明作业从未越过物理执行边界。

        参数：``job_id`` 是记录中的作业身份；``on_accepted`` 不会调用，因为没有
        设备取消需要等待。返回 ``NOT_SENT``。异常：作业不在记录中也按未发送
        处理；记录器只用于测试/干跑，不能制造物理执行事实。
        """

        del job_id, on_accepted
        return CancelDispatchState.NOT_SENT


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
    "CancelDispatchState",
    "CallbackDispatcher",
    "DispatchPayload",
    "Dispatcher",
    "RecordingDispatcher",
    "build_job_start_payload",
]
