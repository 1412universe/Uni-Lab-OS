"""设备 Runtime 的 ROS 执行器与线程存活检查。"""

from __future__ import annotations

from threading import Thread
from typing import Any

from rclpy.exceptions import InvalidHandle
from rclpy.executors import MultiThreadedExecutor
from rclpy.timer import Timer

_DESTROYED_TIMER = object()


class RuntimeExecutor(MultiThreadedExecutor):
    """允许等待队列中的定时器在回调取出前已被动作协程释放。"""

    def _take_timer(self, timer: Timer) -> Any:
        """Humble 的等待队列可能仍引用已销毁定时器，只丢弃这一过期事件。"""
        try:
            return super()._take_timer(timer)
        except InvalidHandle:
            return _DESTROYED_TIMER

    async def _execute_timer(self, timer: Timer, taken: Any) -> None:
        """过期事件不再调用用户回调；其他回调与异常保留 rclpy 原语义。"""
        if taken is not _DESTROYED_TIMER:
            await super()._execute_timer(timer, taken)


def ensure_executor_thread_running(thread: Thread) -> None:
    """执行线程退出时终止 Runtime 启动/运行循环，触发既有崩溃处理。"""

    if not thread.is_alive():
        raise RuntimeError(f"ROS executor thread terminated unexpectedly: {thread.name}")
