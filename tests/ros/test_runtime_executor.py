"""在实际 rclpy 上复现等待队列取到已销毁定时器的竞态。"""

from __future__ import annotations

import asyncio
import time
from threading import Event, Thread

import pytest
from rclpy.context import Context
from rclpy.node import Node

from unilabos.ros.executor import RuntimeExecutor, ensure_executor_thread_running


@pytest.fixture
def ros_executor():
    context = Context()
    context.init(args=[])
    node = Node("runtime_executor_timer_test", context=context)
    executor = RuntimeExecutor(context=context, num_threads=2)
    try:
        yield node, executor
    finally:
        executor.shutdown(timeout_sec=1)
        node.destroy_node()
        context.shutdown()


def test_destroyed_timer_in_wait_queue_does_not_run_callback(ros_executor):
    node, executor = ros_executor
    calls = []
    timer = node.create_timer(0.01, lambda: calls.append("called"))
    node.destroy_timer(timer)
    # executor 已经选中 timer 后，动作协程释放了它；等价于现场的取出顺序。
    taken = executor._take_timer(timer)
    asyncio.run(executor._execute_timer(timer, taken))
    assert calls == []


def test_live_timer_callback_still_executes(ros_executor):
    node, executor = ros_executor
    calls = []
    timer = node.create_timer(0.01, lambda: calls.append("called"))
    taken = executor._take_timer(timer)
    asyncio.run(executor._execute_timer(timer, taken))
    assert calls == ["called"]


def test_driver_callback_errors_still_propagate(ros_executor):
    node, executor = ros_executor

    def fail():
        raise ValueError("driver failure")

    timer = node.create_timer(0.01, fail)
    taken = executor._take_timer(timer)
    with pytest.raises(ValueError, match="driver failure"):
        asyncio.run(executor._execute_timer(timer, taken))


def test_executor_exit_is_detected():
    stop = Event()
    thread = Thread(target=stop.wait, name="test-executor")
    thread.start()
    try:
        ensure_executor_thread_running(thread)
    finally:
        stop.set()
        thread.join(timeout=1)
    with pytest.raises(RuntimeError, match="executor thread terminated"):
        ensure_executor_thread_running(thread)


def test_repeated_action_waits_keep_executor_running(ros_executor):
    """真实动作等待方法连续创建/销毁定时器，不得终止多线程 executor。"""
    from unilabos.ros.nodes.base_device_node import ROS2DeviceNode

    node, executor = ros_executor
    executor.add_node(node)

    async def wait_repeatedly():
        for _ in range(100):
            await ROS2DeviceNode.async_wait_for(node, 0.001)
        return "finished"

    completed = executor.create_task(wait_repeatedly())
    thread = Thread(target=executor.spin, name="timer-churn-executor", daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not completed.done() and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert thread.is_alive()
        assert completed.done()
        assert completed.result() == "finished"
    finally:
        executor.shutdown(timeout_sec=2)
        thread.join(timeout=2)
