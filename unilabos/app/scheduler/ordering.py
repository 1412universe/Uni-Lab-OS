"""OS 本地调度器（Scheduler）的稳定任务排序接口。

入参 = ready tasks + 资源锁状态 + 优先级；出参 = 有序 task 列表。
``StableLocalOrderer`` 按权重降序、提交时间升序和稳定节点身份排序；OS 不调用
独立 ``uni-lab-scheduler`` 服务。
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from typing import List, Protocol, Set

from unilabos.app.scheduler.models import ReadyTask


class OrderingContext:
    """一次重排的资源上下文。"""

    def __init__(self, busy_device_action_keys: Set[str]):
        # 当前被占用的 device_action_key（已下发未完结 job 持有的锁）
        self.busy_device_action_keys = busy_device_action_keys


class TaskOrderer(Protocol):
    def order(self, ready: List[ReadyTask], ctx: OrderingContext) -> List[ReadyTask]:
        """返回下发顺序（可含全部 ready；service 层负责跳过锁忙的节点）。"""
        ...


class StableLocalOrderer:
    """带优先级老化的稳定本地排序器。"""

    def __init__(
        self,
        *,
        aging_interval_seconds: float = 30.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """配置等待任务的优先级老化周期。

        参数：``aging_interval_seconds`` 每经过一个周期把有效优先级增加 1；
        ``clock`` 提供可测试的当前秒数。返回无。异常：周期不是正有限数时抛
        ``ValueError``。排序只影响尚未越过派发门禁的候选，不抢占物理动作。
        """

        interval = float(aging_interval_seconds)
        if not math.isfinite(interval) or interval <= 0:
            raise ValueError("调度优先级老化周期必须是正有限秒数")
        self._aging_interval_seconds = interval
        self._clock = clock

    def order(self, ready: List[ReadyTask], ctx: OrderingContext) -> List[ReadyTask]:
        """按有效优先级、提交时间和稳定身份排列本轮候选。

        参数：``ready`` 是依赖已满足的节点候选；``ctx`` 保留本轮资源视图。返回：
        新的有序列表，不修改输入。异常：时钟返回非有限值时抛 ``ValueError``，
        防止不确定排序越过门禁。有效优先级等于基础权重加完整等待周期数。
        """

        del ctx
        now = float(self._clock())
        if not math.isfinite(now):
            raise ValueError("调度排序时钟必须返回有限秒数")

        def ordering_key(task: ReadyTask) -> tuple[float, float, str, str]:
            """计算一个候选的稳定老化排序键。

            参数：``task`` 是待排序候选。返回：有效优先级降序及稳定兜底字段。
            异常：无；未来提交时间按零等待处理。
            """

            waited_seconds = max(0.0, now - float(task.submitted_at))
            aging_bonus = math.floor(waited_seconds / self._aging_interval_seconds)
            effective_priority = float(task.priority_weight) + aging_bonus
            return (
                -effective_priority,
                task.submitted_at,
                task.workflow_id,
                task.node.id,
            )

        return sorted(
            ready,
            key=ordering_key,
        )


__all__ = [
    "OrderingContext",
    "StableLocalOrderer",
    "TaskOrderer",
]
