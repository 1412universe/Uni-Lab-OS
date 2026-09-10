# SZLab 全工作流核查（2026-09-07）

结论：当前不能认定全部工作流通过。清单共 26 条；本次全量测试套件为 331 passed / 13 failed。测试命令使用 mamba unilab Python，禁用 launch_testing、launch_ros 插件。

## 检查层次

- 运行中 SZLab 正式零写入预检：补齐标准物料转运的必填参数后，24/26 通过，2/26 阻塞。预检中设备在线与派发门禁检查被延后，不能把 runnable_now 当作完整运行通过。
- 从默认部署图构建的隔离生产编译环境：21/26 源码编译通过；另有 2 条缺少执行器实例，3 条找不到已发布的标准物料转运子流程。21 条中，s06_material 在输入准备阶段失败，其余 20 条输入准备通过。
- 当前代码的实际执行证据覆盖 4 条不同工作流：选择来源库位全工艺两轮；条件分支真假两次；RepeatUntil 和条件嵌套 RepeatUntil 各一次。其他条目未进行本轮完整设备运行。

## 已确认的问题

1. `s06_material`：prepare_task_input 报“计划目标输入存在多个提供者”，正式预检可复现，不是缺少试剂数量。
2. `beaker_transfer_chain`：BEAKER1 的 owner_material_uuid 为 b14fa65f-72d5-5698-8268-0a47d06e92e1，该资源没有库位。源码将移液设备当作仓库，需要与真实 s09_process_warehouse 的库位归属对齐。
3. `scheduler_composite_a/b`：默认和当前运行部署图均没有 channel_a、channel_b、region 三个设备；只有独立图片段。隔离编译报 invalid_executor_binding。不能因为在线目录中有条目且预检延后检查就认定可运行。
4. 三条组合流程在隔离编译环境缺少 material_transfer 的发布依赖；在线环境已有目录状态不能替代从默认部署启动的可复现验证。
5. 13 个测试失败中，8 个涉及目录数量、能力集合或动作覆盖断言；3 个涉及子流程发布依赖；2 个涉及综合并行工作流的执行器绑定。不能简单把所有失败都视作代码运行错误，也不能忽略它们。

`material_transfer` 不填写输入时提示缺必填参数，属于正常合同要求。提供实际 ResourceSlot、仓库和库位参数后，正式预检通过，此项不计为代码故障。

## 逐条结果

| 工作流文件 | 隔离源码编译 | 当前正式预检 | 实际运行证据 |
| --- | --- | --- | --- |
| magnetic_stirring | 通过 | 通过 | 未验证 |
| photoshotting | 通过 | 通过 | 未验证 |
| robot_action | 通过 | 通过 | 未验证 |
| s04_robot_stirring | 通过 | 通过 | 未验证 |
| s06_robot | 通过 | 通过 | 未验证 |
| s06_material | 通过 | 阻塞 | 未验证 |
| s07_robot | 通过 | 通过 | 未验证 |
| s07_solid_addition | 通过 | 通过 | 未验证 |
| s08_cap | 通过 | 通过 | 未验证 |
| s09_pipetting | 通过 | 通过 | 未验证 |
| stack_s05_s06 | 通过 | 通过 | 未验证 |
| szlab_mixer | 通过 | 通过 | 未验证 |
| szlab_mixer_pump_production | 通过 | 通过 | 未验证 |
| s07_material_dosing | 通过 | 通过 | 未验证 |
| material_transfer | 通过 | 通过（补齐输入） | 未验证 |
| single_sample_atomic_material | composite_child_not_found | 通过 | 未验证 |
| beaker_transfer_chain | composite_child_not_found | 阻塞 | 未验证 |
| single_sample_atomic_attachment_flow | composite_child_not_found | 通过 | 未验证 |
| single_sample_atomic_attachment_robot_atomic | 通过 | 通过 | 未验证 |
| single_sample_atomic_attachment_robot_atomic_task_b | 通过 | 通过 | 未验证 |
| single_sample_atomic_attachment_robot_atomic_select_sites | 通过 | 通过 | 通过（PLC-Sim 两轮，35/35） |
| control_flow_condition | 通过 | 通过 | 通过（Edge 软件探针） |
| control_flow_repeat | 通过 | 通过 | 通过（Edge 软件探针） |
| control_flow_condition_repeat | 通过 | 通过 | 通过（Edge 软件探针） |
| scheduler_composite_a | invalid_executor_binding | 通过 | 未验证 |
| scheduler_composite_b | invalid_executor_binding | 通过 | 未验证 |

## 本次实际控制流任务

- condition_true: `c04d9334-fd8b-492e-b87a-4fce689c7f74`，succeeded；未选分支按合同 skipped，活动执行锁为空。
- condition_false: `b9fd0f86-a600-4a75-98a3-d8cfa57b8543`，succeeded；未选分支按合同 skipped，活动执行锁为空。
- repeat: `b9b57ecf-8b96-48dc-a41f-60e5f8b8e365`，succeeded；未选分支按合同 skipped，活动执行锁为空。
- condition_repeat: `4874905d-e195-4dd3-bf8f-5ae056a1286f`，succeeded；未选分支按合同 skipped，活动执行锁为空。

选择来源库位完整流程详情见 [两轮完整工艺报告](../select-sites-full-flow-2026-09-07/REPORT.md)。本目录保留全量测试输出、逐条预检、隔离编译结果、控制流运行结果及库位错误细节。

本次为核查，没有修改工作流源码、部署图或清空库存。
