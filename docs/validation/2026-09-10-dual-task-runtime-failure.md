# 双任务实际执行失败诊断

检查日期：2026-09-10（Asia/Shanghai）。本轮只读检查和无硬件隔离复现，没有取消、重试、解锁或部署。

## 当前任务

- A：`cac6a2d0-b5fe-45bf-8f81-dd3fd3b62ff2`，工作流 `e0c3103a-ac8a-555e-9dee-44c8d6083901`，Task 为 running；首个机器人动作 pending，等待 B 的旧 Job。
- B：`8f020623-82e2-45b6-a2e1-0402d8bdc03b`，工作流 `6133476e-2210-5b6b-bcdc-102ac5025344`，Task 为 running、cleanup_status=requires_attention。
- Edge Pod 为 2/2 Running，0 次重启。当前问题不是 Runtime 崩溃。

## 初次动作失败

B 的首个物料搬运动作 `transfer_material_atomic`，来源为烧杯堆栈2/L1B2，目标 S07/S0721。

- 节点 UUID：`918d7e41-56e5-476b-98fe-a972e4d0e289`。
- 首次 Job：`32fa58f9-6d3b-4b3c-b6de-5121a6aecbb4`。
- 18:53:44 失败：`缺少有效 WorkflowNodeJob execution identity；标准机械臂动作不接受业务侧幂等标识`。
- 机械臂网关在取料提交阶段返回 REJECTED，复合动作抛异常；未进入 TIP 或加液步骤。

`base_device_node.py` 的工站控制把同步动作包装为 `_station_action`，经 `ROS2DeviceNode.run_async_func` 创建 rclpy Task。该函数只在调用方捕获 Trace 上下文；`await_with_context` 在新 Task 内才调用，复制的是新 Task 的 Python Context，丢失 Workflow execution identity。

在当前 edge-runtime 容器中提取实际 `safe_task_wrapper` 和 `run_async_func` 方法，使用真实 tracing 函数、无硬件的内存 executor，在不同 Python Context 推进协程，得到：

```text
before_ros_task {"node_job_uuid": "32fa58f9-6d3b-4b3c-b6de-5121a6aecbb4", "task_uuid": "8f020623-82e2-45b6-a2e1-0402d8bdc03b"}
inside_ros_task {}
identity_preserved False
```

应在调用方仍持有完整身份时构造上下文包装，并测试跨 Context 推进、异步到线程池、两任务隔离和异常路径。

## 重试阻塞

控制命令 `d7d505fd-af7e-495e-9d4d-f5eed875e480` 已应用 `retry_current_node`：

- 旧 Job 已 failed、current_attempt=false。
- 重试 Job `76dec66a-e05d-4d5d-800f-f7c972037ef5` 为 pending、attempt=2、start_state=NOT_STARTED。
- 其 wait_reason.code=operation_lease，blocking_job_uuid 正是旧 Job。
- A 的首动作 Job `6ef42937-98ce-419f-a92b-b0b73d78c69a` 也被同一旧 Job 阻塞。
- B 的 8 把执行锁仍为 uncertain，claim_uuid=`dcb30334-43a1-40a2-8795-610a8759a004`。
- 工站 mode=RUNNING、errors=[]、无 active_session，原弹窗已关闭。

需要继续修复/验证“重试决定—旧作业资源处置—新作业准入”的衔接；不能单纯删除锁掩盖问题。尤其不应在旧资源未完成处置且新任务必然被阻塞时，把错误隐藏成普通运行等待。

前一轮数量修复和隔离并发准入验证通过，仅证明可提交和准入，没有覆盖实际 Runtime 驱动执行。本轮定位到该验证范围之外的真实执行问题。

临时原始证据均位于 `/tmp/szlab-task-problem-*`，包含两个任务的 jobs、execution-locks、工站 error-handling 和调度/Runtime 日志。
