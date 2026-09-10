# Edge 节点错误与手动处置实现

设计来源：[节点错误处置方案](https://dptechnology.feishu.cn/wiki/P799w0gQZibfgvk1IhBc2grmnPg)。实现范围为 Python EdgeScheduler、本地 WorkflowStore、Edge 执行协议与现有前端。

## 使用流程

1. 普通节点失败或执行结果未知，Edge 暂停工站的新普通动作；已实际开始的动作继续回传结果。
2. 全局处置弹窗提供取消当前任务、重试当前节点、进入手动处理。只有第三项创建处置会话。
3. 手动处理中选择设备及其动作，按动作 Schema 填参执行。手动动作不检查逻辑锁、占用或 UNKNOWN 条件。每次操作形成独立任务与执行命令，并关联来源 task/job/session、原 task trace 和新的执行 span。
4. 可登记人工上下料位置及释放选中的逻辑占用。库存修改保存原事实版本与操作者；逻辑释放不表示设备已停止。
5. “处理完成”撤销能够证明尚未开始的手动命令，回到同一会话的节点决定阶段，工站仍暂停。已开始和 UNKNOWN 动作保留真实执行及停止入口。
6. 最终选择重试当前节点或取消当前任务，可显式确认同时恢复工站。已知的其他错误、在途动作和 UNKNOWN 不作为人工恢复的限制条件；未决源任务自身仍等待决定。新错误再次暂停工站。

## 执行与恢复约束

- 实际开始屏障位于设备驱动调用之前；仅接收 ROS Goal 不标记已经开始。JSON ResourceSlot 解析完成后才取得开始许可，异步驱动在自身事件循环实际调用前取得许可。
- 重试新增 attempt 和独立命令 ID，沿用冻结节点合同、输入及已有前置结果；旧 job 与回执保留。停止操作精确指向一条执行命令。
- 同一错误/未知事实去重；旧 UNKNOWN 的迟到结果更新执行证据，不替代人的节点决定、不覆盖新 attempt，也不覆盖较新的人工库存位置。
- 工站版本、错误代次、决定版本、会话版本与幂等键共同约束并发提交。已经持久受理的命令如果应用失败，页面展示错误并允许按原 command UUID 再次应用。
- 人工等待期间的 task pause/resume 不能抹去 error_waiting。处置决定生效后按用户当前的 normal/step 模式恢复任务控制状态。
- 进程重启先恢复工站、当前 attempt、DAG/循环事实及结果路由，再重放回执和未开始的原命令。实际开始或结果未知的动作不自动重发。
- 取消中的作业重启后沿用原回执截止时间，不重发停止。取消超时产生独立的 `cancel_unknown` 错误；超时检查与真实结果回调使用同一控制屏障，已先完成的正常取消不会再触发错误暂停。
- 同一个 Fence 的共享逻辑占用人工释放后，同步更新持久记录与所有参与 job 的内存占用；晚到结果不能重新建立该占用。设备托管释放绑定原托管 UUID，避免旧卸载结果释放后来重新取得的设备托管。

## 数据与接口

仅新增三张控制表：`workflow_station_control`、`workflow_station_control_command`、`workflow_error_session`。错误决定、任务、job、反馈、结果、资源占用、设备托管和库存事件复用现有表。既有表的字段/索引升级随本地 Store 初始化执行。

接口前缀：`/api/v1/stations/local`。

| 用途 | 接口 |
| --- | --- |
| 权威快照 | `GET /error-handling` |
| 错误三选项 | `POST /errors/{decision_id}/decision` |
| 会话读取/完成/返回 | `GET /sessions/{id}`、`POST /sessions/{id}/complete`、`POST /sessions/{id}/return` |
| 设备动作目录 | `GET /sessions/{id}/devices/{device_id}/actions` |
| 手动执行 | `POST /sessions/{id}/manual-actions` |
| 执行详情/停止 | `GET /manual-actions/{command_id}`、`POST /manual-actions/{command_id}/stop` |
| 控制命令查询/应用 | `GET /control-commands/{id}`、`POST /control-commands/{id}/apply` |
| 人工恢复 | `POST /resume` |
| 库存读取/更正 | `GET /sessions/{id}/inventory`、`POST /sessions/{id}/inventory-corrections` |
| 占用读取/释放 | `GET /resource-dispositions`、`POST /resource-dispositions` |

产生新意图的写操作携带 `Idempotency-Key`；状态相关操作携带预期版本。HTTP 受理与命令实际应用、设备实际执行分别展示。前端通过现有 Services port 访问接口，全局 SSE 只触发 REST 权威快照重读。

## 代码入口

- 控制状态与幂等：`unilabos/workflow/error_control.py`
- 单动作应用服务：`unilabos/workflow/error_handling.py`
- HTTP 适配：`unilabos/app/error_handling_api.py`
- DAG 与物理执行协调：`unilabos/workflow/task_scheduler_bridge.py`、`unilabos/app/scheduler/service.py`
- 实际开始屏障：`unilabos/app/edge_control/execution_start.py`、`unilabos/ros/nodes/base_device_node.py`
- 前端：`packages/services/src/stationErrorHandling.ts`；`packages/workflow-editor/src/components/StationErrorHandling.tsx`、`StationManualHandling.tsx`、`StationErrorDecisionPanel.tsx`，由 kernel-web 组合根挂载。

## 验证方式

Python 关键回归覆盖 scheduler_core、错误控制、人工库存、设备单动作 API、设备托管、Edge 协议与本地执行权威。前端浏览器用真实 Python API/EdgeScheduler/WorkflowStore/InventoryStore，物理执行适配器只记录命令。

在前端仓库运行：

```bash
UNILAB_OS_ROOT=/path/to/Uni-Lab-OS UNILAB_OS_PYTHON=/path/to/python \
  pnpm exec playwright test --config e2e/station-error.playwright.config.ts
```

该用例覆盖三选项、全局暂停、十动作目录填参、会话刷新恢复、来源 trace、处理完成后原地选择、原节点重试及继续多 task。物理 ROS2 环境需要另行运行 `tests/ros/nodes/test_base_device_json_resource_slots.py` 并完成设备联调；本地 Python 环境缺少 `rclpy`，不能据此声称实机链路已经验收。

## 本地验证结果（2026-09-10）

| 检查 | 结果 |
| --- | --- |
| 本次改动涉及的 Python 测试（不含 ROS 环境测试） | 309 项通过；2 项已在干净基线复现的旧失败单独排除 |
| 50 task × 8 job 压力用例 | 400 节点完成，无重复派发；单独复测通过，耗时约 57 秒 |
| workflow-editor 完整单测 | 56 个文件、371 项通过 |
| 本功能真实 OS 浏览器流程 | 1 项通过，未使用接口路由 mock |
| Web / Desktop 构建 | 两项通过 |
| 新增 Python 控制、应用、API、开始屏障模块 | Ruff 检查通过；语法检查通过 |
| 类型检查 | services、workflow-editor、kernel-web 等通过；desktop 被原有类型错误阻断 |
| 双轴代码复核 | 需求与规范复核所发现的问题均已处理；取消回执竞态补有双线程回归 |

全量 Python 测试首次运行有 2957 项通过、96 项失败、43 个收集错误。将失败用例与干净 `6da5d66e` 基线逐项对照后，52 项失败和全部 43 个收集错误在基线仍可复现；另外 44 项已修复或按新产品规则更新，并包含在上述最终回归中。没有把未通过的全量检查记作通过。

仓库既有阻断包括：ROS2/消息包及部分可选依赖缺失；废弃运行模式相关旧测试；desktop 的 `preload/index.ts` 调用了 `ManagedRuntimeInstallationApi` 尚未声明的 `chooseEnvironment`；前端全仓质量检查存在其他文件的复杂度、体积及样式违规。本次新组件已通过相应检查，原有工作流浏览器测试还被其 OS fixture 导入缺失的 `RESOURCE_TEMPLATE_UUID` 阻断。

真实设备动作没有在本次实现过程中执行。上线前仍需在完整 ROS2 与设备环境验证实际开始屏障、设备停止支持和真实回执。上述测试覆盖的是软件控制合同与本地 API 集成。
